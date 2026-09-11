"""Stage-2 Section F: licence manifest + gate, and OTLP/JSON export of the telemetry spine.

Proves (a) the shipped runtime is permissively licensed and the gate fails on
anything that is not, and (b) the spans we already record — tier, tokens
in/out, cost, why, cache savings, per user/role, model, complexity — leave the
platform in the OpenTelemetry wire format over plain urllib.
"""

import copy
import importlib.util
import json
import os
import pathlib
import re
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from unittest import mock

from knowledge_fabric.adapters import otel_export as ox
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.tenants import demo
from tests.util import seeded

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ci" / "licence_manifest.json"
GATE = ROOT / "scripts" / "licence_gate.py"
T = "test-fabric"
T2 = "isolation-check"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _load_gate():
    spec = importlib.util.spec_from_file_location("licence_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


def _attrs(span: dict) -> dict:
    """Flatten an OTLP attribute list into {key: python value}."""

    def val(v):
        if "arrayValue" in v:
            return [val(x) for x in v["arrayValue"]["values"]]
        if "kvlistValue" in v:
            return {kv["key"]: val(kv["value"]) for kv in v["kvlistValue"]["values"]}
        return next(iter(v.values()))

    return {kv["key"]: val(kv["value"]) for kv in span["attributes"]}


# ==========================================================================
# licence manifest + gate
# ==========================================================================
class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.m = gate.load_manifest(str(MANIFEST))

    def test_manifest_is_valid_and_covers_every_layer(self):
        self.assertEqual(gate.validate_manifest(self.m), [])
        roles = " ".join(c["role"].lower() for c in self.m["components"])
        for layer in (
            "runtime",
            "store",
            "vector",
            "lexical",
            "object store",
            "queue",
            "identity",
            "embedding",
            "llm serving",
            "converter",
            "infrastructure as code",
            "ci",
            "dashboards",
            "observability",
        ):
            self.assertIn(layer, roles, f"manifest lacks a component for layer {layer!r}")

    def test_cited_licences_are_concrete(self):
        by = {c["name"]: c for c in self.m["components"]}
        self.assertEqual(by["LangSmith"]["licence"], "LicenseRef-Proprietary")
        self.assertEqual(by["LangFuse"]["licence"], "MIT")
        self.assertEqual(
            by["OpenTelemetry Collector (OTLP/HTTP receiver)"]["licence"], "Apache-2.0"
        )
        self.assertEqual(by["Grafana"]["licence"], "AGPL-3.0-only")
        self.assertEqual(by["MinIO"]["licence"], "AGPL-3.0-only")
        self.assertEqual(by["OpenTofu"]["licence"], "MPL-2.0")
        self.assertEqual(by["Keycloak"]["licence"], "Apache-2.0")
        self.assertEqual(by["vLLM"]["licence"], "Apache-2.0")
        self.assertEqual(by["pgvector"]["licence"], "PostgreSQL")
        self.assertEqual(by["Docling"]["licence"], "MIT")
        self.assertEqual(by["bge-m3 (BAAI)"]["licence"], "MIT")

    def test_copyleft_only_as_external_service_and_rejected_products_not_approved(self):
        for c in self.m["components"]:
            cls = gate.licence_class(self.m["policy"], c["licence"])
            if cls in ("network_copyleft", "strong_copyleft") and c["status"] != "rejected":
                self.assertIn(c["linkage"], ("external-service", "tooling"), c["name"])
                self.assertNotIn(c["linkage"], ("runtime", "optional-runtime"), c["name"])
        rejected = {c["name"]: c for c in self.m["components"] if c["status"] == "rejected"}
        for name in ("LangSmith", "LangFuse", "Elasticsearch", "Terraform (HashiCorp)"):
            self.assertIn(name, rejected)
            self.assertFalse(rejected[name]["approved"])

    def test_runtime_rule_is_permissive_only(self):
        rules = self.m["policy"]["linkage_rules"]
        self.assertEqual(rules["runtime"], ["permissive"])
        self.assertEqual(rules["optional-runtime"], ["permissive"])
        self.assertIn("network_copyleft", rules["external-service"])
        self.assertIn("weak_copyleft", rules["tooling"])


class GateTests(unittest.TestCase):
    def setUp(self):
        self.m = gate.load_manifest(str(MANIFEST))

    def _with(self, **component) -> dict:
        m = copy.deepcopy(self.m)
        base = {
            "name": "x",
            "role": "test",
            "linkage": "runtime",
            "licence": "MIT",
            "approved": True,
            "status": "in-use",
            "import_names": [],
            "distributions": [],
        }
        base.update(component)
        m["components"].append(base)
        return m

    @staticmethod
    def _codes(report: dict) -> list[str]:
        return sorted(v["code"] for v in report["violations"])

    def test_repository_passes_the_gate(self):
        rep = gate.run(str(ROOT), str(MANIFEST))
        self.assertTrue(rep["ok"], rep["violations"])
        # the only third-party imports in shipped code are guarded optional deps:
        # the cloud drivers (boto3, pg8000), OIDC verification (jwt), the MCP
        # server (mcp, T31) and the T41 document engines (docling, openpyxl,
        # pytesseract, PIL, svglib, reportlab) plus pydantic (declared runtime,
        # used for the image-description schema) — each imported inside a
        # function, never at module load.
        self.assertEqual(
            rep["summary"]["third_party_imports"],
            [
                "PIL",
                "boto3",
                "docling",
                "jwt",
                "mcp",
                "openpyxl",
                "pg8000",
                "pydantic",
                "pytesseract",
                "reportlab",
                "svglib",
            ],
        )
        self.assertIn("RESULT: PASS", gate.render(rep))

    def test_agpl_runtime_dependency_fails(self):
        rep = gate.evaluate(self._with(name="minio-py", licence="AGPL-3.0-only", linkage="runtime"))
        self.assertFalse(rep["ok"])
        self.assertEqual(self._codes(rep), ["linkage_denied"])
        self.assertIn("minio-py", gate.render(rep))

    def test_agpl_external_service_is_allowed(self):
        rep = gate.evaluate(
            self._with(name="grafana-2", licence="AGPL-3.0-only", linkage="external-service")
        )
        self.assertTrue(rep["ok"], rep["violations"])

    def test_mpl_ok_for_tooling_but_not_runtime(self):
        self.assertTrue(
            gate.evaluate(self._with(name="tofu-2", licence="MPL-2.0", linkage="tooling"))["ok"]
        )
        rep = gate.evaluate(self._with(name="mpl-lib", licence="MPL-2.0", linkage="runtime"))
        self.assertEqual(self._codes(rep), ["linkage_denied"])

    def test_proprietary_and_source_available_never_pass_any_linkage(self):
        for lic in ("LicenseRef-Proprietary", "SSPL-1.0", "BUSL-1.1"):
            for linkage in (
                "runtime",
                "optional-runtime",
                "tooling",
                "external-service",
                "model-weights",
            ):
                rep = gate.evaluate(
                    self._with(name=f"{lic}-{linkage}", licence=lic, linkage=linkage)
                )
                self.assertFalse(rep["ok"], (lic, linkage))

    def test_unknown_licence_and_bad_approval_flags(self):
        m = self._with(name="mystery", licence="Made-Up-1.0")
        self.assertTrue(any("unclassified licence" in p for p in gate.validate_manifest(m)))
        rep = gate.evaluate(self._with(name="nope", approved=False))
        self.assertEqual(self._codes(rep), ["not_approved"])
        rep = gate.evaluate(self._with(name="sneaky", status="rejected", approved=True))
        self.assertEqual(self._codes(rep), ["rejected_but_approved"])

    def test_undeclared_rejected_and_unguarded_imports(self):
        with tempfile.TemporaryDirectory() as d:
            pkg = pathlib.Path(d) / "app"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("")
            (pkg / "a.py").write_text("import os\nimport requests\n")
            (pkg / "b.py").write_text("from langsmith import Client\n")
            (pkg / "c.py").write_text("import boto3\n")
            (pkg / "d.py").write_text(
                "try:\n    import pg8000\nexcept ImportError:\n    pg8000 = None\n"
            )
            (pkg / "e.py").write_text(
                "def connect():\n    import pg8000.dbapi as drv\n    return drv\n"
            )
            imports = gate.scan_imports(d, ["app"])
            self.assertEqual(sorted(imports), ["boto3", "langsmith", "pg8000", "requests"])
            self.assertFalse(imports["boto3"][0]["guarded"])
            self.assertTrue(all(s["guarded"] for s in imports["pg8000"]))
            rep = gate.evaluate(self.m, imports)
            self.assertFalse(rep["ok"])
            self.assertEqual(
                self._codes(rep),
                ["rejected_import", "undeclared_import", "unguarded_optional_import"],
            )
            by = {v["code"]: v for v in rep["violations"]}
            self.assertEqual(by["undeclared_import"]["component"], "requests")
            self.assertEqual(by["rejected_import"]["component"], "LangSmith")
            self.assertEqual(by["unguarded_optional_import"]["component"], "boto3 / botocore")

    def test_requirements_file_must_match_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "requirements.txt").write_text(
                "# pinned\nboto3==1.34.0\nlangfuse>=2\nsome-unknown-lib[extra]~=1.0\n-r other.txt\n"
            )
            reqs = gate.scan_requirements(d, ["requirements.txt"])
            self.assertEqual(sorted(reqs), ["boto3", "langfuse", "some-unknown-lib"])
            rep = gate.evaluate(self.m, {}, reqs)
            self.assertEqual(self._codes(rep), ["requirements_rejected", "requirements_undeclared"])

    def test_cli_exit_codes_and_json(self):
        out = StringIO()
        with mock.patch("sys.stdout", out):
            rc = gate.main(["--repo", str(ROOT), "--manifest", str(MANIFEST), "--json"])
        self.assertEqual(rc, 0)
        rep = json.loads(out.getvalue())
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["violations"], [])
        with tempfile.TemporaryDirectory() as d:
            bad = pathlib.Path(d) / "m.json"
            m = self._with(name="agpl-runtime", licence="AGPL-3.0-only")
            bad.write_text(json.dumps(m))
            with mock.patch("sys.stdout", StringIO()):
                self.assertEqual(gate.main(["--repo", str(ROOT), "--manifest", str(bad)]), 1)
            bad.write_text("{not json")
            with mock.patch("sys.stderr", StringIO()):
                self.assertEqual(gate.main(["--repo", str(ROOT), "--manifest", str(bad)]), 2)
            with mock.patch("sys.stderr", StringIO()):
                self.assertEqual(
                    gate.main(["--manifest", str(pathlib.Path(d) / "missing.json")]), 2
                )

    def test_docs_exist_and_state_the_decision(self):
        tech = (ROOT / "docs" / "TECH_STACK.md").read_text()
        dec = (ROOT / "docs" / "OBSERVABILITY_DECISION.md").read_text()
        for needle in (
            "PSF-2.0",
            "AGPL",
            "MPL-2.0",
            "OpenTofu",
            "MinIO",
            "Grafana",
            "licence_gate",
        ):
            self.assertIn(needle, tech)
        self.assertIn("Decision: No.", dec)
        for needle in (
            "LangSmith",
            "LangFuse",
            "Apache-2.0",
            "gen_ai.usage.input_tokens",
            "KF_OTLP_ENDPOINT",
            "savings_by_technique",
            "curation_queue",
        ):
            self.assertIn(needle, dec)


# ==========================================================================
# OTLP/JSON export of the telemetry spine
# ==========================================================================
class _Receiver(BaseHTTPRequestHandler):
    """Minimal OTLP/HTTP receiver: records the request, answers with a configured status."""

    status = 200
    received: list = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n)
        type(self).received.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(body.decode())}
        )
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):  # keep test output quiet
        pass


class OtlpExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = seeded([T, T2])
        cls.svc = AnswerService(cls.p)
        asha = demo.principal_for(cls.p, T, "asker.public")
        carl = demo.principal_for(cls.p, T, "curator")
        cls.a1 = cls.svc.ask(asha, "what must a release achieve before promotion?")
        cls.a2 = cls.svc.ask(carl, "what is the acceptance criteria for coverage?")
        cls.a3 = cls.svc.ask(asha, "what must a release achieve before promotion?")  # cache path
        cls.payload = ox.export(cls.p, T, None)

    def _spans(self, payload=None):
        payload = payload or self.payload
        return payload["resourceSpans"][0]["scopeSpans"][0]["spans"]

    def _root(self, answer):
        thex = ox.trace_id_hex(answer.trajectory_id)
        roots = [s for s in self._spans() if s["traceId"] == thex and s["name"] == "answer"]
        self.assertEqual(len(roots), 1)
        return roots[0]

    def test_resource_spans_shape(self):
        rs = self.payload["resourceSpans"]
        self.assertEqual(len(rs), 1)  # one tenant → one resource
        res = _attrs({"attributes": rs[0]["resource"]["attributes"]})
        self.assertEqual(res["service.name"], "knowledge-fabric")
        self.assertEqual(res["kf.tenant"], T)
        scope = rs[0]["scopeSpans"][0]["scope"]
        self.assertEqual(scope["name"], "knowledge_fabric.telemetry")
        for s in self._spans():
            self.assertRegex(s["traceId"], HEX32)
            self.assertRegex(s["spanId"], HEX16)
            self.assertEqual(s["kind"], ox.SPAN_KIND_INTERNAL)
            self.assertTrue(s["startTimeUnixNano"].isdigit() and s["endTimeUnixNano"].isdigit())
            self.assertLessEqual(int(s["startTimeUnixNano"]), int(s["endTimeUnixNano"]))
            self.assertIn(s["status"]["code"], (ox.STATUS_CODE_OK, ox.STATUS_CODE_ERROR))
            for kv in s["attributes"]:
                self.assertEqual(set(kv), {"key", "value"})
                self.assertEqual(len(kv["value"]), 1)
                self.assertIn(
                    next(iter(kv["value"])),
                    {
                        "stringValue",
                        "boolValue",
                        "intValue",
                        "doubleValue",
                        "arrayValue",
                        "kvlistValue",
                    },
                )

    def test_answer_trace_carries_the_leadership_fields(self):
        a = self.a1
        root = self._root(a)
        at = _attrs(root)
        self.assertEqual(at["kf.model.tier"], a.tier)
        self.assertEqual(at["gen_ai.response.model"], a.model_name)
        self.assertEqual(int(at["gen_ai.usage.input_tokens"]), a.tokens_in)
        self.assertEqual(int(at["gen_ai.usage.output_tokens"]), a.tokens_out)
        self.assertAlmostEqual(at["kf.cost.usd"], a.cost, places=9)
        self.assertAlmostEqual(at["kf.cost.saved_usd"], a.cost_saved, places=9)
        self.assertIn("kf.cache.hit", at)
        self.assertEqual(at["user.id"], "asker.public")
        self.assertEqual(at["user.roles"], ["asker"])
        self.assertEqual(at["kf.complexity"], a.complexity)
        self.assertEqual(at["kf.selector.level_name"], a.why["level_name"])
        self.assertEqual(at["kf.selector.why.reasons"], [r["code"] for r in a.why["reasons"]])
        self.assertEqual(at["kf.selector.why.explain"], a.why["explain"])
        self.assertAlmostEqual(at["kf.grounding.score"], a.grounding_score, places=3)
        self.assertEqual(int(at["kf.citations.count"]), len(a.citations))
        self.assertEqual(at["kf.answer.kind"], "answer")
        self.assertEqual(at["kf.lang"], a.lang)
        self.assertEqual(int(at["kf.dataset.version"]), a.dataset_version)
        self.assertTrue(any(k.startswith("kf.grounding.signal.") for k in at))
        self.assertTrue(at["kf.trajectory.selected"])
        self.assertEqual(at["kf.trace_id"], a.trajectory_id)
        self.assertEqual(
            root["traceId"], a.trajectory_id.split("_", 1)[1]
        )  # joins back to /api/trace

    def test_stage_spans_are_children_of_the_answer_span(self):
        root = self._root(self.a1)
        kids = [s for s in self._spans() if s.get("parentSpanId") == root["spanId"]]
        self.assertEqual(
            [k["name"] for k in kids],
            ["answer.retrieve", "answer.graph", "answer.ground", "answer.compose"],
        )
        self.assertNotIn("parentSpanId", root)
        for k in kids:
            self.assertEqual(k["traceId"], root["traceId"])
            self.assertGreaterEqual(int(k["startTimeUnixNano"]), int(root["startTimeUnixNano"]) - 1)

    def test_per_role_and_cache_savings_are_visible(self):
        at2 = _attrs(self._root(self.a2))
        self.assertEqual(at2["user.id"], "curator")
        self.assertEqual(at2["user.roles"], ["curator"])
        at3 = _attrs(self._root(self.a3))
        self.assertTrue(at3["kf.cache.hit"])
        self.assertEqual(at3["kf.cache.technique"], "answer_cache")
        self.assertEqual(at3["kf.selector.level"], "cache")
        self.assertGreaterEqual(at3["kf.cost.saved_usd"], 0.0)

    def test_matches_store_and_is_deterministic(self):
        rows = ox.read_spans(self.p, T)
        self.assertEqual(len(self._spans()), len(rows))
        self.assertTrue(all(r["tenant"] == T for r in rows))
        traced = self.p.telemetry.trace(self.a1.trajectory_id)
        one = ox.export_trace(self.p, T, self.a1.trajectory_id)
        self.assertEqual(len(one["resourceSpans"][0]["scopeSpans"][0]["spans"]), len(traced))
        self.assertEqual(ox.to_otlp(rows), ox.to_otlp(list(reversed(rows))))
        self.assertEqual(
            json.dumps(ox.to_otlp(rows), sort_keys=True),
            json.dumps(ox.to_otlp(rows), sort_keys=True),
        )

    def test_tenant_isolation(self):
        other = ox.export(self.p, T2, None)
        names = {s["name"] for s in other["resourceSpans"][0]["scopeSpans"][0]["spans"]}
        self.assertNotIn("answer", names)  # isolation tenant asked nothing
        tenants = {
            _attrs({"attributes": r["resource"]["attributes"]})["kf.tenant"]
            for r in other["resourceSpans"]
        }
        self.assertEqual(tenants, {T2})
        with self.assertRaises(PermissionError):
            ox.export(self.p, "", None)
        with self.assertRaises(PermissionError):
            ox.read_spans(self.p, None)
        mixed = ox.to_otlp(ox.read_spans(self.p, T) + ox.read_spans(self.p, T2))
        self.assertEqual(
            [
                _attrs({"attributes": r["resource"]["attributes"]})["kf.tenant"]
                for r in mixed["resourceSpans"]
            ],
            sorted([T, T2]),
        )

    def test_empty_and_unknown_inputs(self):
        self.assertEqual(ox.to_otlp([]), {"resourceSpans": []})
        self.assertRegex(ox.trace_id_hex("not-a-uuid"), HEX32)
        self.assertEqual(ox.traces_url("http://c:4318"), "http://c:4318/v1/traces")
        self.assertEqual(ox.traces_url("http://c:4318/v1/traces/"), "http://c:4318/v1/traces")

    def test_posts_to_an_otlp_http_receiver_with_urllib(self):
        _Receiver.received, _Receiver.status = [], 200
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            with mock.patch.dict(
                os.environ, {ox.HEADERS_ENV: "Authorization=Bearer t0k,X-Tenant=qualizeal"}
            ):
                res = ox.export(self.p, T, url, headers={"X-Extra": "1"})
            self.assertTrue(res["ok"])
            self.assertEqual(res["http_status"], 200)
            self.assertEqual(res["endpoint"], url + "/v1/traces")
            self.assertEqual(res["spans"], len(self._spans()))
            self.assertEqual(len(_Receiver.received), 1)
            got = _Receiver.received[0]
            self.assertEqual(got["path"], "/v1/traces")
            self.assertEqual(got["headers"]["Content-Type"], "application/json")
            self.assertEqual(got["headers"]["Authorization"], "Bearer t0k")
            self.assertEqual(got["headers"]["X-Extra"], "1")
            self.assertEqual(got["body"], self.payload)
            # env var path: KF_OTLP_ENDPOINT drives export when no explicit endpoint is given
            with mock.patch.dict(os.environ, {ox.ENDPOINT_ENV: url}):
                self.assertTrue(ox.export(self.p, T)["ok"])
                self.assertIn("resourceSpans", ox.export(self.p, T, ""))  # "" forces a dry run
            self.assertEqual(len(_Receiver.received), 2)
            _Receiver.status = 503
            with self.assertRaises(ox.OtlpExportError):
                ox.export(self.p, T, url)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_unreachable_endpoint_raises(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(ox.OtlpExportError):
            ox.export(self.p, T, f"http://127.0.0.1:{port}", timeout_s=1.0)


if __name__ == "__main__":
    unittest.main()
