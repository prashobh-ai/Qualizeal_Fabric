"""Stage-2 Section C: pipeline runs, connector admin, continuous refresh scheduler."""

import time
import unittest

from knowledge_fabric.connectors import admin, registry
from knowledge_fabric.ingestion import runs, scheduler
from tests.util import seeded

T = "test-fabric"
OTHER = "isolation-check"
INTERVAL = 600
# a fixed "now" far past the seed's real wall-clock syncs, so freshness maths is exact
NOW = 4_000_000_000.0

# the seed already synced REL-42/43 (cursor 1730); these are newer → a real delta
NEW_JIRA = [
    {
        "project": "REL",
        "key": "REL-44",
        "summary": "Contract tests for refresh scheduler",
        "status": "Open",
        "updated": 1800,
        "acl": ["public"],
        "description": "The refresh scheduler must ingest Jira issues on an interval and "
        "report freshness for the release readiness review.",
    },
]
NEW_GITHUB = [
    {
        "repo": "qualizeal/kf-platform",
        "path": "docs/refresh.md",
        "updated_at": 1800,
        "commit": "0f0f0f",
        "mime": "text/markdown",
        "content": "# Refresh\n\nConnectors are refreshed on a schedule; a failing sync backs off.",
    },
]


class Base(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T, OTHER])


# ==========================================================================
# runs
# ==========================================================================
class TestRuns(Base):
    def test_start_step_finish_and_list(self):
        rid = runs.start_run(self.p, T, "jira")
        self.assertTrue(rid.startswith("run_"))
        self.assertEqual([r["id"] for r in runs.active_runs(self.p, T)], [rid])
        runs.step(self.p, rid, "detect", "ok", count=1, ms=1.5)
        runs.step(self.p, rid, "detect", "skipped", count=1, ms=0.5)
        runs.step(self.p, rid, "embed", "ok", count=1, ms=3.25, detail="384-d")
        runs.finish(self.p, rid, "ok", items=2)

        listed = runs.list_runs(self.p, T)
        self.assertEqual(len(listed), 1)
        run = listed[0]
        self.assertEqual(run["id"], rid)
        self.assertEqual(run["source"], "jira")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["items"], 2)
        self.assertIsNotNone(run["finished_at"])
        self.assertGreaterEqual(run["duration_ms"], 0)
        # raw steps are appended in order
        self.assertEqual([s["name"] for s in run["steps"]], ["detect", "detect", "embed"])
        self.assertEqual(run["steps"][2]["detail"], "384-d")
        self.assertEqual(run["steps"][0]["ms"], 1.5)
        # rollup folds by name: summed counts/ms, worst status, first-seen order
        stages = {s["name"]: s for s in run["stages"]}
        self.assertEqual([s["name"] for s in run["stages"]], ["detect", "embed"])
        self.assertEqual(stages["detect"]["count"], 2)
        self.assertEqual(stages["detect"]["ms"], 2.0)
        self.assertEqual(stages["detect"]["status"], "ok")  # ok beats skipped
        self.assertEqual(stages["detect"]["entries"], 2)
        self.assertEqual(runs.active_runs(self.p, T), [])
        self.assertEqual(runs.get_run(self.p, T, rid)["items"], 2)
        self.assertIsNone(runs.get_run(self.p, OTHER, rid))

    def test_validation_and_lifecycle_errors(self):
        rid = runs.start_run(self.p, T, "jira")
        with self.assertRaises(ValueError):
            runs.step(self.p, rid, "detect", "bogus")
        with self.assertRaises(ValueError):
            runs.step(self.p, rid, "", "ok")
        with self.assertRaises(KeyError):
            runs.step(self.p, "run_doesnotexist", "detect", "ok")
        with self.assertRaises(ValueError):
            runs.finish(self.p, rid, "running", 0)
        runs.finish(self.p, rid, "error", 0)
        with self.assertRaises(KeyError):  # registry entry released on finish
            runs.step(self.p, rid, "detect", "ok")
        with self.assertRaises(ValueError):  # addressed explicitly: already finished
            runs.step(self.p, rid, "detect", "ok", tenant=T)
        with self.assertRaises(ValueError):
            runs.finish(self.p, rid, "ok", 0, tenant=T)
        with self.assertRaises(KeyError):  # wrong tenant cannot touch the run
            runs.finish(self.p, rid, "ok", 0, tenant=OTHER)
        with self.assertRaises(PermissionError):
            runs.start_run(self.p, "", "jira")
        with self.assertRaises(ValueError):
            runs.start_run(self.p, T, "")

    def test_active_binding_and_step_if_active(self):
        self.assertIsNone(runs.current_run_id())
        self.assertFalse(runs.step_if_active(self.p, "detect"))
        rid = runs.start_run(self.p, T, "github")
        with runs.active(rid):
            self.assertEqual(runs.current_run_id(), rid)
            self.assertTrue(runs.step_if_active(self.p, "detect", "ok", count=1, ms=2))
            # the telemetry hook shape: ingest.<stage> spans map to steps, others ignored
            self.assertTrue(runs.step_from_span(self.p, "ingest.convert", {"status": "ok"}, 4.0))
            self.assertTrue(runs.step_from_span(self.p, "ingest.detect", {"status": "noop"}, 1.0))
            self.assertTrue(runs.step_from_span(self.p, "ingest.embed", {"status": "error"}, 1.0))
            self.assertFalse(runs.step_from_span(self.p, "answer", {}, 1.0))
            self.assertFalse(runs.step_from_span(self.p, "ingest.", {}, 1.0))
        self.assertIsNone(runs.current_run_id())
        runs.finish(self.p, rid, "ok", 1)
        run = runs.get_run(self.p, T, rid)
        names = [(s["name"], s["status"]) for s in run["steps"]]
        self.assertEqual(
            names, [("detect", "ok"), ("convert", "ok"), ("detect", "skipped"), ("embed", "error")]
        )
        stages = {s["name"]: s["status"] for s in run["stages"]}
        self.assertEqual(stages, {"detect": "ok", "convert": "ok", "embed": "error"})
        # the hook may be handed the telemetry adapter (has .db) instead of the platform
        rid2 = runs.start_run(self.p, T, "github")
        with runs.active(rid2):
            self.assertTrue(runs.step_from_span(self.p.telemetry, "ingest.chunk", {}, 0.5))
        runs.finish(self.p.telemetry, rid2, "ok", 0)
        self.assertEqual(runs.get_run(self.p, T, rid2)["steps"][0]["name"], "chunk")

    def test_list_runs_ordering_limit_and_tenant_isolation(self):
        ids = []
        for i in range(3):
            rid = runs.start_run(self.p, T, "jira")
            runs.finish(self.p, rid, "ok", i)
            ids.append(rid)
        other = runs.start_run(self.p, OTHER, "files")
        listed = runs.list_runs(self.p, T)
        self.assertEqual([r["id"] for r in listed], list(reversed(ids)))  # newest first
        self.assertEqual(len(runs.list_runs(self.p, T, limit=2)), 2)
        self.assertEqual([r["id"] for r in runs.list_runs(self.p, OTHER)], [other])
        self.assertEqual([r["id"] for r in runs.active_runs(self.p, OTHER)], [other])
        self.assertEqual(runs.active_runs(self.p, T), [])
        with self.assertRaises(PermissionError):
            runs.list_runs(self.p, "")


# ==========================================================================
# connector admin
# ==========================================================================
class TestConnectorAdmin(Base):
    def test_defaults_when_unconfigured(self):
        self.assertIsNone(admin.get(self.p, T, "jira"))
        self.assertTrue(admin.is_enabled(self.p, T, "jira"))
        self.assertTrue(admin.is_enabled(self.p, T, "confluence"))  # unknown → enabled
        listed = admin.list_all(self.p, T)
        self.assertEqual([c["source"] for c in listed], registry.available())
        jira = next(c for c in listed if c["source"] == "jira")
        self.assertTrue(jira["enabled"])
        self.assertTrue(jira["registered"])
        self.assertEqual(jira["allow"], [])
        self.assertEqual(jira["config"], {})
        self.assertEqual(jira["scopes"], ["jira:read"])
        self.assertEqual(jira["declared_scopes"], ["jira:read"])
        self.assertIsNone(jira["updated_at"])
        self.assertEqual(
            admin.effective_config(self.p, T, "jira", {"projects": ["REL"]}), {"projects": ["REL"]}
        )
        self.assertTrue(admin.check_scopes(self.p, T, "jira")["ok"])

    def test_upsert_disable_partial_update_and_audit(self):
        rec = admin.upsert(
            self.p,
            T,
            "jira",
            enabled=False,
            allow=["REL", "REL"],
            config={"base_url": "https://jira.example.com"},
            by_subject="admin",
        )
        self.assertFalse(rec["enabled"])
        self.assertEqual(rec["allow"], ["REL"])  # de-duplicated, order kept
        self.assertEqual(rec["config"], {"base_url": "https://jira.example.com"})
        self.assertEqual(rec["scopes"], ["jira:read"])  # declared scopes by default
        self.assertIsNotNone(rec["updated_at"])
        self.assertTrue(rec["registered"])
        self.assertFalse(admin.is_enabled(self.p, T, "jira"))
        # partial update: only the given field changes
        rec2 = admin.upsert(self.p, T, "jira", enabled=True)
        self.assertTrue(rec2["enabled"])
        self.assertEqual(rec2["allow"], ["REL"])
        self.assertEqual(rec2["config"], {"base_url": "https://jira.example.com"})
        self.assertEqual(admin.disable(self.p, T, "jira")["enabled"], False)
        self.assertEqual(admin.enable(self.p, T, "jira")["enabled"], True)
        stored = admin.get(self.p, T, "jira")
        self.assertEqual(
            {k: stored[k] for k in ("enabled", "allow", "config", "scopes")},
            {
                "enabled": True,
                "allow": ["REL"],
                "config": {"base_url": "https://jira.example.com"},
                "scopes": ["jira:read"],
            },
        )
        audit = [a for a in self.p.audit.for_tenant(T) if a["action"] == "connector.upsert"]
        self.assertEqual(len(audit), 4)
        self.assertEqual(audit[-1]["subject"], "admin")
        self.assertEqual(audit[-1]["resource"], "connector:jira")
        self.assertEqual(audit[-1]["decision"], "disabled")
        self.assertEqual(audit[0]["decision"], "enabled")

    def test_list_all_merges_registry_and_configured(self):
        admin.upsert(
            self.p, T, "confluence", enabled=True, allow=["QA"], scopes=["confluence:read"]
        )
        admin.upsert(self.p, T, "github", allow=["qualizeal/kf-platform"])
        listed = {c["source"]: c for c in admin.list_all(self.p, T)}
        self.assertEqual(sorted(listed), sorted(set(registry.available()) | {"confluence"}))
        self.assertFalse(listed["confluence"]["registered"])
        self.assertEqual(listed["confluence"]["declared_scopes"], [])
        self.assertEqual(listed["confluence"]["scopes"], ["confluence:read"])
        self.assertTrue(listed["github"]["registered"])
        self.assertEqual(listed["github"]["allow"], ["qualizeal/kf-platform"])
        self.assertTrue(listed["files"]["enabled"])  # untouched registry entry

    def test_effective_config_applies_admin_allow_list_last(self):
        admin.upsert(
            self.p,
            T,
            "github",
            config={"token_ref": "secret://gh"},
            allow=["qualizeal/kf-platform"],
        )
        cfg = admin.effective_config(
            self.p, T, "github", {"repos": ["qualizeal/other-repo"], "depth": 2}
        )
        self.assertEqual(
            cfg, {"token_ref": "secret://gh", "depth": 2, "repos": ["qualizeal/kf-platform"]}
        )
        self.assertEqual(admin.allow_key("jira"), "projects")
        self.assertEqual(admin.allow_key("confluence"), "allow")
        # an empty admin allow-list imposes no restriction
        admin.upsert(self.p, T, "github", allow=[])
        self.assertEqual(
            admin.effective_config(self.p, T, "github", {"repos": ["qualizeal/other-repo"]}),
            {"token_ref": "secret://gh", "repos": ["qualizeal/other-repo"]},
        )

    def test_check_scopes_reports_missing(self):
        admin.upsert(self.p, T, "jira", scopes=[])
        chk = admin.check_scopes(self.p, T, "jira")
        self.assertEqual(chk["declared"], ["jira:read"])
        self.assertEqual(chk["granted"], [])
        self.assertEqual(chk["missing"], ["jira:read"])
        self.assertFalse(chk["ok"])

    def test_validation(self):
        with self.assertRaises(ValueError):
            admin.upsert(self.p, T, "bad source")
        with self.assertRaises(TypeError):
            admin.upsert(self.p, T, "jira", allow="REL")
        with self.assertRaises(TypeError):
            admin.upsert(self.p, T, "jira", allow=[""])
        with self.assertRaises(TypeError):
            admin.upsert(self.p, T, "jira", config=["x"])
        with self.assertRaises(PermissionError):
            admin.is_enabled(self.p, "", "jira")
        with self.assertRaises(PermissionError):
            admin.list_all(self.p, None)

    def test_tenant_isolation(self):
        admin.upsert(self.p, T, "jira", enabled=False, allow=["REL"])
        self.assertFalse(admin.is_enabled(self.p, T, "jira"))
        self.assertTrue(admin.is_enabled(self.p, OTHER, "jira"))
        self.assertIsNone(admin.get(self.p, OTHER, "jira"))
        other_jira = next(c for c in admin.list_all(self.p, OTHER) if c["source"] == "jira")
        self.assertEqual(other_jira["allow"], [])
        self.assertTrue(other_jira["enabled"])


# ==========================================================================
# scheduler
# ==========================================================================
class TestScheduler(Base):
    def _schedule_jira(self, now=NOW, interval=INTERVAL, enabled=True):
        return scheduler.set_schedule(
            self.p, T, "jira", interval, {"projects": ["REL"]}, enabled=enabled, now=now
        )

    def test_set_schedule_and_due(self):
        s = self._schedule_jira()
        self.assertEqual(
            s,
            {
                "source": "jira",
                "interval_s": INTERVAL,
                "next_run": NOW,
                "last_run": None,
                "last_status": None,
                "error_count": 0,
                "enabled": True,
                "config": {"projects": ["REL"]},
            },
        )
        self.assertEqual(scheduler.due(self.p, T, NOW - 1), [])
        self.assertEqual(scheduler.due(self.p, T, NOW), ["jira"])
        listed = scheduler.schedules(self.p, T)
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["connector_enabled"])
        # a paused schedule is never due
        scheduler.enable_schedule(self.p, T, "jira", False)
        self.assertEqual(scheduler.due(self.p, T, NOW + 10), [])
        scheduler.enable_schedule(self.p, T, "jira", True)
        self.assertEqual(scheduler.due(self.p, T, NOW + 10), ["jira"])
        self.assertTrue(scheduler.remove_schedule(self.p, T, "jira"))
        self.assertFalse(scheduler.remove_schedule(self.p, T, "jira"))
        self.assertEqual(scheduler.schedules(self.p, T), [])
        with self.assertRaises(ValueError):
            scheduler.set_schedule(self.p, T, "jira", 0, {})
        with self.assertRaises(TypeError):
            scheduler.set_schedule(self.p, T, "jira", 60, ["x"])
        with self.assertRaises(PermissionError):
            scheduler.due(self.p, "", NOW)

    def test_run_due_ingests_records_and_reports_health(self):
        self._schedule_jira()
        self.assertIsNone(self.p.documents.by_source_uri(T, "jira", "jira://REL/REL-44"))
        results = scheduler.run_due(self.p, T, NOW, {"jira": NEW_JIRA})
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["source"], "jira")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["pulled"], 1)
        self.assertEqual(r["ingested"], 1)
        self.assertEqual(r["next_run"], NOW + INTERVAL)
        self.assertEqual(r["error_count"], 0)
        self.assertIsNotNone(self.p.documents.by_source_uri(T, "jira", "jira://REL/REL-44"))

        # schedule advanced, last_run stamped
        s = scheduler.get_schedule(self.p, T, "jira")
        self.assertEqual(s["last_run"], NOW)
        self.assertEqual(s["next_run"], NOW + INTERVAL)
        self.assertEqual(s["last_status"], "ok")
        self.assertEqual(scheduler.due(self.p, T, NOW), [])
        self.assertEqual(scheduler.due(self.p, T, NOW + INTERVAL), ["jira"])

        # the run is recorded with its steps
        run = runs.get_run(self.p, T, r["run_id"])
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["items"], 1)
        names = [s["name"] for s in run["steps"]]
        # the scheduler's coarse steps are present, and (once the telemetry hook is
        # wired) the 7 pipeline stages are mirrored into the same run for the UI
        for coarse in ("sync", "tombstone", "ingest"):
            self.assertIn(coarse, names)
        for stage in ("detect", "convert", "chunk", "extract", "graph", "embed", "health"):
            self.assertIn(stage, names)
        by_name = {s["name"]: s for s in run["steps"]}
        self.assertEqual(by_name["sync"]["count"], 1)
        self.assertEqual(by_name["tombstone"]["status"], "skipped")
        self.assertEqual(by_name["ingest"]["count"], 1)
        self.assertEqual(runs.list_runs(self.p, T)[0]["id"], r["run_id"])

        # health: fresh, within SLA, cumulative items from the connector cursor
        h = {x["source"]: x for x in scheduler.health(self.p, T, now=NOW + 60)}
        self.assertIn("jira", h)
        self.assertIn("github", h)  # synced by the seed → has a cursor
        j = h["jira"]
        self.assertTrue(j["enabled"])
        self.assertEqual(j["freshness_minutes"], 1.0)
        self.assertEqual(j["last_status"], "ok")
        self.assertEqual(j["error_count"], 0)
        self.assertEqual(j["next_run"], NOW + INTERVAL)
        self.assertEqual(j["interval_s"], INTERVAL)
        self.assertEqual(j["items"], 3)  # 2 from the seed + 1 now
        self.assertFalse(j["sla_breach"])
        # an unscheduled-but-synced source has no SLA
        self.assertFalse(h["github"]["enabled"])
        self.assertIsNone(h["github"]["interval_s"])
        self.assertFalse(h["github"]["sla_breach"])
        # a second run with nothing new is a cheap no-op (idempotent-by-hash)
        r2 = scheduler.run_due(self.p, T, NOW + INTERVAL, {"jira": NEW_JIRA})[0]
        self.assertEqual((r2["status"], r2["pulled"], r2["ingested"]), ("ok", 0, 0))

    def test_run_due_skips_disabled_connector(self):
        self._schedule_jira()
        admin.upsert(self.p, T, "jira", enabled=False)
        results = scheduler.run_due(self.p, T, NOW, {"jira": NEW_JIRA})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "skipped")
        self.assertEqual(results[0]["reason"], "connector disabled")
        self.assertIsNone(results[0]["run_id"])
        self.assertEqual(runs.list_runs(self.p, T), [])  # no run opened
        self.assertIsNone(self.p.documents.by_source_uri(T, "jira", "jira://REL/REL-44"))
        s = scheduler.get_schedule(self.p, T, "jira")
        self.assertEqual(s["next_run"], NOW + INTERVAL)  # still advances
        self.assertIsNone(s["last_run"])
        self.assertEqual(s["last_status"], "skipped:connector disabled")
        h = next(x for x in scheduler.health(self.p, T, now=NOW) if x["source"] == "jira")
        self.assertFalse(h["enabled"])
        self.assertFalse(h["connector_enabled"])
        self.assertTrue(h["schedule_enabled"])
        self.assertFalse(h["sla_breach"])
        # re-enabling lets the next due tick ingest
        admin.upsert(self.p, T, "jira", enabled=True)
        r = scheduler.run_due(self.p, T, NOW + INTERVAL, {"jira": NEW_JIRA})[0]
        self.assertEqual((r["status"], r["ingested"]), ("ok", 1))

    def test_error_path_increments_error_count_and_backs_off(self):
        # 'confluence' is not registered → SyncManager raises inside the run
        scheduler.set_schedule(self.p, T, "confluence", INTERVAL, {}, now=NOW)
        expected_factor = [2, 3, 4, 4, 4]  # min(4, 1 + error_count)
        now = NOW
        for i, factor in enumerate(expected_factor, start=1):
            with self.assertLogs("knowledge_fabric.refresh", level="WARNING") as logged:
                r = scheduler.run_due(self.p, T, now)
            self.assertEqual(len(logged.records), 1)
            self.assertIn("confluence", logged.output[0])
            self.assertEqual(len(r), 1)
            self.assertEqual(r[0]["status"], "error")
            self.assertIn("unknown connector", r[0]["error"])
            self.assertEqual(r[0]["error_count"], i)
            self.assertEqual(r[0]["backoff_factor"], factor)
            self.assertEqual(r[0]["next_run"], now + INTERVAL * factor)
            s = scheduler.get_schedule(self.p, T, "confluence")
            self.assertEqual(s["error_count"], i)
            self.assertTrue(s["last_status"].startswith("error:"))
            self.assertIn("unknown connector", s["last_status"])
            self.assertEqual(s["next_run"], now + INTERVAL * factor)
            self.assertIsNone(s["last_run"])  # never succeeded
            self.assertEqual(scheduler.due(self.p, T, s["next_run"] - 1), [])
            now = s["next_run"]
        listed = runs.list_runs(self.p, T)
        self.assertEqual(len(listed), 5)
        self.assertTrue(all(r["status"] == "error" for r in listed))
        self.assertEqual(listed[0]["steps"][0]["status"], "error")
        self.assertIn("unknown connector", listed[0]["steps"][0]["detail"])
        h = next(x for x in scheduler.health(self.p, T, now=now) if x["source"] == "confluence")
        self.assertIsNone(h["freshness_minutes"])
        self.assertEqual(h["error_count"], 5)
        self.assertTrue(h["sla_breach"])  # enabled, never succeeded, failing
        # editing the schedule re-arms it (clears the back-off) but keeps the error history
        s = scheduler.set_schedule(self.p, T, "confluence", 60, {}, now=now)
        self.assertEqual(s["next_run"], now)
        self.assertEqual(s["error_count"], 5)

    def test_admin_allow_list_enforced_on_scheduled_sync(self):
        self._schedule_jira()
        admin.upsert(self.p, T, "jira", allow=["OTHER"])  # REL is not allowed any more
        r = scheduler.run_due(self.p, T, NOW, {"jira": NEW_JIRA})[0]
        self.assertEqual((r["status"], r["pulled"], r["ingested"]), ("ok", 0, 0))
        self.assertIsNone(self.p.documents.by_source_uri(T, "jira", "jira://REL/REL-44"))

    def test_health_sla_breach_after_stale_interval(self):
        self._schedule_jira()
        scheduler.run_due(self.p, T, NOW, {"jira": NEW_JIRA})

        def at(now):
            return next(x for x in scheduler.health(self.p, T, now=now) if x["source"] == "jira")

        self.assertFalse(at(NOW + INTERVAL)["sla_breach"])  # 10 min < 20 min
        self.assertFalse(at(NOW + 2 * INTERVAL)["sla_breach"])  # exactly 2× is not a breach
        stale = at(NOW + 3 * INTERVAL)
        self.assertEqual(stale["freshness_minutes"], 30.0)
        self.assertTrue(stale["sla_breach"])
        # no SLA while the schedule is paused or the connector disabled
        scheduler.enable_schedule(self.p, T, "jira", False)
        self.assertFalse(at(NOW + 3 * INTERVAL)["sla_breach"])
        scheduler.enable_schedule(self.p, T, "jira", True)
        admin.disable(self.p, T, "jira")
        self.assertFalse(at(NOW + 3 * INTERVAL)["sla_breach"])
        # health_for gives a neutral card for a never-seen source
        self.assertEqual(
            scheduler.health_for(self.p, T, "sharepoint", now=NOW)["freshness_minutes"], None
        )
        self.assertFalse(scheduler.health_for(self.p, T, "sharepoint", now=NOW)["sla_breach"])
        self.assertEqual(
            scheduler.health_for(self.p, T, "jira", now=NOW + 60)["freshness_minutes"], 1.0
        )

    def test_sync_now_with_and_without_schedule(self):
        # without a schedule: runs, records a run, touches no schedule
        r = scheduler.sync_now(
            self.p, T, "github", {"repos": ["qualizeal/kf-platform"]}, records=NEW_GITHUB, now=NOW
        )
        self.assertEqual((r["status"], r["ingested"]), ("ok", 1))
        self.assertIsNone(r["next_run"])
        self.assertEqual(runs.get_run(self.p, T, r["run_id"])["source"], "github")
        self.assertIsNone(scheduler.get_schedule(self.p, T, "github"))
        self.assertIsNotNone(
            self.p.documents.by_source_uri(
                T, "github", "github://qualizeal/kf-platform/docs/refresh.md"
            )
        )
        # with a schedule: behaves like a due run (last_run/next_run updated)
        self._schedule_jira()
        r2 = scheduler.sync_now(self.p, T, "jira", records=NEW_JIRA, now=NOW + 5)
        self.assertEqual((r2["status"], r2["ingested"]), ("ok", 1))
        s = scheduler.get_schedule(self.p, T, "jira")
        self.assertEqual(s["last_run"], NOW + 5)
        self.assertEqual(s["next_run"], NOW + 5 + INTERVAL)
        # disabled connector → skipped, no run
        admin.disable(self.p, T, "jira")
        r3 = scheduler.sync_now(self.p, T, "jira", records=NEW_JIRA, now=NOW + 10)
        self.assertEqual(r3["status"], "skipped")
        self.assertIsNone(r3["run_id"])

    def test_tenant_isolation(self):
        self._schedule_jira()
        self.assertEqual(scheduler.schedules(self.p, OTHER), [])
        self.assertEqual(scheduler.due(self.p, OTHER, NOW), [])
        self.assertEqual(scheduler.run_due(self.p, OTHER, NOW, {"jira": NEW_JIRA}), [])
        self.assertEqual(scheduler.health(self.p, OTHER, now=NOW), [])  # no schedules, no cursors
        self.assertEqual(runs.list_runs(self.p, OTHER), [])
        self.assertIsNone(self.p.documents.by_source_uri(OTHER, "jira", "jira://REL/REL-44"))
        self.assertEqual(scheduler.get_schedule(self.p, T, "jira")["next_run"], NOW)  # untouched
        with self.assertRaises(PermissionError):
            scheduler.health(self.p, "")
        with self.assertRaises(PermissionError):
            scheduler.RefreshLoop(self.p, "", tick_s=1)

    def test_refresh_loop_thread_runs_due_schedules(self):
        self._schedule_jira(now=time.time() - 1)
        loop = scheduler.RefreshLoop(self.p, T, tick_s=0.02, records_by_source={"jira": NEW_JIRA})
        self.assertFalse(loop.running)
        self.assertTrue(loop.start())
        self.assertFalse(loop.start())  # already running
        deadline = time.time() + 5
        while (
            time.time() < deadline
            and self.p.documents.by_source_uri(T, "jira", "jira://REL/REL-44") is None
        ):
            time.sleep(0.01)
        self.assertTrue(loop.stop(timeout=5))
        self.assertFalse(loop.running)
        self.assertGreaterEqual(loop.ticks, 1)
        self.assertIsNone(loop.last_error)
        self.assertIsNotNone(self.p.documents.by_source_uri(T, "jira", "jira://REL/REL-44"))
        s = scheduler.get_schedule(self.p, T, "jira")
        self.assertEqual(s["last_status"], "ok")
        self.assertGreater(s["next_run"], time.time() + INTERVAL - 30)
        listed = runs.list_runs(self.p, T)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["status"], "ok")
        # context-manager form and run_once
        with scheduler.RefreshLoop(self.p, T, tick_s=1) as l2:
            self.assertTrue(l2.running)
        self.assertFalse(l2.running)
        self.assertEqual(l2.run_once(now=s["next_run"] - 1), [])
        with self.assertRaises(ValueError):
            scheduler.RefreshLoop(self.p, T, tick_s=0)


if __name__ == "__main__":
    unittest.main()
