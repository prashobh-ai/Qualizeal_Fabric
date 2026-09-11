"""T35 — provider pinning + loud failure; T36 — the API call ledger.

The Anthropic API is never called: ``urllib.request.urlopen`` is patched with a
fake transport that answers ``/v1/models`` and ``/v1/messages`` (or fails with
a chosen HTTP status), so every exit code, ledger row and dashboard sum is
exercised without a network or a key. The ledger writes under a temp
``KF_DATA_ROOT``.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="kf-t35-")
os.environ["KF_DATA_ROOT"] = _TMP
os.environ.setdefault("KF_MODEL_MODE", "off")

from knowledge_fabric.adapters import model  # noqa: E402
from knowledge_fabric.telemetry import api_ledger  # noqa: E402


class _Resp:
    def __init__(self, payload, request_id="req_abc123"):
        self._data = json.dumps(payload).encode()
        self.headers = {"request-id": request_id}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._data


def _http_error(url, code, body):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body.encode()))


def _transport(models=None, models_status=200, ping_status=200, ping_usage=None):
    """A fake urlopen: routes on the URL; raises HTTPError on non-200."""
    models = models if models is not None else ["claude-sonnet-4-6", "claude-haiku-4-5"]
    ping_usage = ping_usage or {"input_tokens": 9, "output_tokens": 2}
    calls = []

    def fake(req, timeout=None):
        url = req.full_url
        calls.append(json.loads(req.data.decode()) if req.data else {"GET": url})
        if url.endswith("/v1/models?limit=100"):
            if models_status != 200:
                raise _http_error(url, models_status, '{"error":"unauthorized"}')
            return _Resp({"data": [{"id": m} for m in models]})
        if url.endswith("/v1/messages"):
            if ping_status != 200:
                raise _http_error(url, ping_status, '{"error":{"message":"boom"}}')
            body = json.loads(req.data.decode())
            return _Resp(
                {
                    "model": body["model"],
                    "content": [{"type": "text", "text": "pong"}],
                    "usage": {
                        **ping_usage,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "stop_reason": "end_turn",
                }
            )
        raise AssertionError(f"unexpected url {url}")

    fake.calls = calls
    return fake


class _Env(unittest.TestCase):
    KEYS = ("ANTHROPIC_API_KEY", "KF_MODEL_SMALL", "KF_MODEL_LARGE", "GITHUB_STEP_SUMMARY")

    def setUp(self):
        self._snap = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)
        # a fresh provider_status per test
        try:
            os.remove(os.path.join(_TMP, "provider_status.json"))
        except OSError:
            pass

    def tearDown(self):
        for k, v in self._snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestDoctorRequireAnthropic(_Env):
    def _run(self, transport=None, **env):
        from scripts import doctor

        os.environ.update(env)
        out = []
        if transport is None:
            return doctor.require_anthropic(out=out.append), out
        with mock.patch("urllib.request.urlopen", transport):
            return doctor.require_anthropic(out=out.append), out

    def test_exit_2_when_key_empty_names_the_secret(self):
        code, out = self._run()
        self.assertEqual(code, 2)
        self.assertIn("ANTHROPIC_API_KEY", out[0])

    def test_exit_3_when_key_rejected(self):
        code, out = self._run(_transport(models_status=401), ANTHROPIC_API_KEY="sk-bad")
        self.assertEqual(code, 3)
        self.assertIn("Key rejected", out[0])

    def test_exit_4_when_model_outside_allowed_set(self):
        code, out = self._run(
            _transport(), ANTHROPIC_API_KEY="sk-ok", KF_MODEL_LARGE="claude-opus-5"
        )
        self.assertEqual(code, 4)
        self.assertIn("outside the allowed set", out[0])

    def test_exit_5_when_sonnet_not_available(self):
        code, out = self._run(_transport(models=["claude-haiku-4-5"]), ANTHROPIC_API_KEY="sk-ok")
        self.assertEqual(code, 5)
        self.assertIn("Sonnet 4.6 not available", out[0])

    def test_exit_6_when_ping_fails_with_body(self):
        code, out = self._run(_transport(ping_status=500), ANTHROPIC_API_KEY="sk-ok")
        self.assertEqual(code, 6)
        self.assertIn("boom", out[0])

    def test_success_pins_models_writes_status_and_summary(self):
        summary = os.path.join(_TMP, "step_summary.md")
        open(summary, "w").close()
        t = _transport()
        code, out = self._run(t, ANTHROPIC_API_KEY="sk-ok-1234", GITHUB_STEP_SUMMARY=summary)
        self.assertEqual(code, 0, out)
        # small = haiku because the key lists it; large = sonnet
        st = json.load(open(os.path.join(_TMP, "provider_status.json")))
        self.assertEqual(st["model_small"], "claude-haiku-4-5")
        self.assertEqual(st["model_large"], "claude-sonnet-4-6")
        self.assertEqual(st["ping_usage"], {"input_tokens": 9, "output_tokens": 2})
        self.assertNotIn("sk-ok", json.dumps(st))  # never the key, only a fingerprint
        # the ping used the small model with max_tokens 8
        ping = [c for c in t.calls if "model" in c][0]
        self.assertEqual((ping["model"], ping["max_tokens"]), ("claude-haiku-4-5", 8))
        # the provider line in the log and in the run summary
        self.assertTrue(out[-1].startswith("Provider: Anthropic · key …"))
        self.assertIn("small claude-haiku-4-5 · large claude-sonnet-4-6 · ping 9/2", out[-1])
        self.assertIn("Provider: Anthropic", open(summary).read())
        # the runtime now resolves the doctor's pin
        self.assertEqual(model.resolve_models(), ("claude-haiku-4-5", "claude-sonnet-4-6"))

    def test_small_falls_back_to_sonnet_when_key_lacks_haiku(self):
        code, _ = self._run(_transport(models=["claude-sonnet-4-6"]), ANTHROPIC_API_KEY="sk-ok")
        self.assertEqual(code, 0)
        st = json.load(open(os.path.join(_TMP, "provider_status.json")))
        self.assertEqual(st["model_small"], "claude-sonnet-4-6")


class TestProviderIsLoud(_Env):
    def test_messages_raises_with_status_and_body(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ok"
        c = model.AnthropicModelClient()
        with mock.patch("urllib.request.urlopen", _transport(ping_status=429)):
            with self.assertRaises(model.ProviderError) as cm:
                c.messages(
                    {"model": "claude-sonnet-4-6", "max_tokens": 8, "messages": []},
                    purpose="answer_bake",
                )
        self.assertEqual(cm.exception.status, 429)
        self.assertIn("boom", cm.exception.body)

    def test_model_outside_allowed_set_never_sent(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ok"
        c = model.AnthropicModelClient()
        with self.assertRaises(model.ModelNotAllowed):
            c.messages({"model": "claude-opus-5", "max_tokens": 8, "messages": []})

    def test_answer_service_propagates_provider_error(self):
        # T35: a provider failure is not silently replaced by the extractive
        # draft — ask() raises, so a bake or a queued answer fails visibly.
        from knowledge_fabric.answer.service import AnswerService
        from knowledge_fabric.app import Platform
        from knowledge_fabric.evaluation import quality

        class Failing:
            def available(self):
                return True

            def complete(self, *a, **k):
                raise model.ProviderError(500, '{"error":"overloaded"}', "claude-sonnet-4-6")

        os.environ["KF_MODEL_MODE"] = "off"
        p = Platform(db_path=":memory:", blob_root=os.path.join(_TMP, "blobs"))
        quality.build_eval_fabric(p)
        p.model = Failing()
        svc = AnswerService(p)
        prin = quality._principal(p, quality.EVAL_TENANT)
        # Force a model tier: the selector normally answers this at Level 1
        # without the model, so pin its decision to the `fast` tier.
        from knowledge_fabric.answer import selector as _sel

        real = _sel.classify

        def forced(*a, **k):
            return {**real(*a, **k), "tier": "fast"}

        with mock.patch.object(_sel, "classify", forced):
            with self.assertRaises(model.ProviderError):
                svc.ask(prin, "what is QMentisAI", k=6)


class TestLedger(_Env):
    def test_cost_from_prices_and_row_shape(self):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 100_000,
            "cache_read_input_tokens": 200_000,
            "cache_creation_input_tokens": 50_000,
        }
        # sonnet: 3 + 1.5 + 0.06 + 0.1875 = 4.7475
        self.assertAlmostEqual(api_ledger.cost_for("claude-sonnet-4-6", usage), 4.7475, 6)
        self.assertEqual(api_ledger.cost_for("unpriced-model", usage), 0.0)
        row = api_ledger.record(
            purpose="repo_summary",
            model="claude-haiku-4-5",
            usage=usage,
            latency_ms=12.5,
            request_id="req_1",
            repo="acme/app",
        )
        for k in (
            "ts",
            "run_id",
            "workflow",
            "purpose",
            "model",
            "repo",
            "doc_id",
            "question_hash",
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "latency_ms",
            "cost_usd",
            "request_id",
        ):
            self.assertIn(k, row)
        self.assertAlmostEqual(row["cost_usd"], 1 + 0.5 + 0.02 + 0.0625, 6)
        self.assertEqual(row["purpose"], "repo_summary")
        self.assertIn(row, api_ledger.rows(1))

    def test_unknown_purpose_is_flagged_not_dropped(self):
        row = api_ledger.record(purpose="wat", model="claude-haiku-4-5", usage={}, latency_ms=1)
        self.assertEqual(row["purpose"], "unknown:wat")

    def test_dashboard_totals_equal_ledger_sums(self):
        before = api_ledger.summary(api_ledger.rows(1))
        for _i in range(3):
            api_ledger.record(
                purpose="answer_bake",
                model="claude-sonnet-4-6",
                usage={"input_tokens": 100, "output_tokens": 10},
                latency_ms=5,
                repo="r1",
            )
        c = api_ledger.consumption(1)
        t = c["totals"]
        self.assertEqual(t["calls"], before["calls"] + 3)
        self.assertEqual(t["input_tokens"], before["input_tokens"] + 300)
        # every breakdown sums back to the totals
        for by in ("by_day", "by_purpose", "by_model", "by_repo", "by_workflow"):
            self.assertEqual(sum(v["calls"] for v in c[by].values()), t["calls"], by)
            self.assertAlmostEqual(sum(v["cost_usd"] for v in c[by].values()), t["cost_usd"], 6, by)
        self.assertLessEqual(len(c["last_calls"]), 50)
        self.assertIn("claude-sonnet-4-6", c["prices"])

    def test_step_summary_line_counts_this_run_only(self):
        api_ledger.record(
            purpose="doctor_ping",
            model="claude-haiku-4-5",
            usage={"input_tokens": 9, "output_tokens": 2},
            latency_ms=3,
        )
        line = api_ledger.step_summary_line()
        self.assertTrue(line.startswith("API calls: "))
        self.assertIn("model claude-haiku-4-5", line)
        self.assertNotIn("API calls: 0 ", line)

    def test_build_summary_exit_7_when_anthropic_made_no_calls(self):
        import scripts.build_showcase as b

        # Scope the summary to a run that made no calls. Under Actions the
        # ambient GITHUB_RUN_ID is set — the explicit KF_RUN_ID must win over
        # it, or the summary would count every call the job has made so far.
        env = {"KF_RUN_ID": "run-with-no-calls", "KF_MODEL_MODE": "anthropic"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(b._api_summary(), 7)
            # the doctor's ping alone does not count as the bake using the key
            api_ledger.record(
                purpose="doctor_ping",
                model="claude-haiku-4-5",
                usage={"input_tokens": 9, "output_tokens": 2},
                latency_ms=1,
            )
            self.assertEqual(b._api_summary(), 7)
            api_ledger.record(
                purpose="answer_bake",
                model="claude-sonnet-4-6",
                usage={"input_tokens": 50, "output_tokens": 5},
                latency_ms=1,
            )
            self.assertEqual(b._api_summary(), 0)
            os.environ["KF_MODEL_MODE"] = "off"
            self.assertEqual(b._api_summary(), 0)

    def test_explicit_run_id_wins_over_ambient_actions_id(self):
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "999", "KF_RUN_ID": "mine"}):
            self.assertEqual(api_ledger.run_id(), "mine")
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "999"}, clear=False):
            os.environ.pop("KF_RUN_ID", None)
            self.assertEqual(api_ledger.run_id(), "999")


if __name__ == "__main__":
    unittest.main()


class TestAdminModelsRoute(unittest.TestCase):
    """Admin → Models: the provider card + API consumption, served from the
    doctor's provider_status.json and the ledger (T35/T36), admin-gated."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen

        from knowledge_fabric.answer.service import AnswerService
        from knowledge_fabric.app import Platform
        from knowledge_fabric.surfaces import http_api
        from knowledge_fabric.tenants import demo

        os.environ["KF_MODEL_MODE"] = "off"
        cls.p = Platform(db_path=":memory:", blob_root=os.path.join(_TMP, "route-blobs"))
        demo.seed(cls.p, ["qualizeal"])
        http_api._platform = cls.p
        http_api._svc = AnswerService(cls.p)
        cls.http_api = http_api
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

        def login(subject):
            r = urlopen(
                Request(
                    f"http://127.0.0.1:{cls.port}/login",
                    data=json.dumps({"tenant": "qualizeal", "subject": subject}).encode(),
                    headers={"Content-Type": "application/json"},
                ),
                timeout=10,
            )
            return json.loads(r.read())["token"]

        cls.tok = {u: login(u) for u in ("admin", "asker.public")}
        with open(os.path.join(_TMP, "provider_status.json"), "w") as f:
            json.dump(
                {
                    "provider": "anthropic",
                    "key_fingerprint": "abcdef0123456789",
                    "model_small": "claude-haiku-4-5",
                    "model_large": "claude-sonnet-4-6",
                    "models_available": ["claude-haiku-4-5", "claude-sonnet-4-6"],
                    "ping_usage": {"input_tokens": 9, "output_tokens": 2},
                },
                f,
            )
        api_ledger.record(
            purpose="answer_bake",
            model="claude-sonnet-4-6",
            usage={"input_tokens": 50, "output_tokens": 5},
            latency_ms=7,
        )

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.http_api._platform = None
        cls.http_api._svc = None

    def _get(self, path, who):
        from urllib.request import Request, urlopen

        req = Request(f"http://127.0.0.1:{self.port}{path}")
        req.add_header("Authorization", "Bearer " + self.tok[who])
        try:
            with urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, {}

    def test_admin_sees_provider_card_and_consumption(self):
        status, d = self._get("/admin/models?days=1", "admin")
        self.assertEqual(status, 200)
        self.assertEqual(d["provider"]["model_large"], "claude-sonnet-4-6")
        self.assertEqual(d["allowed_models"], ["claude-sonnet-4-6", "claude-haiku-4-5"])
        c = d["consumption"]
        self.assertGreaterEqual(c["totals"]["calls"], 1)
        self.assertEqual(c["totals"]["calls"], sum(v["calls"] for v in c["by_purpose"].values()))
        self.assertIn("claude-sonnet-4-6", c["prices"])

    def test_askers_are_refused(self):
        status, _ = self._get("/admin/models", "asker.public")
        self.assertIn(status, (401, 403))
