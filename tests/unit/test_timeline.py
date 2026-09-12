"""T54 — ingestion timeline: per-month stacks + drill-down over the curation log."""

import datetime
import unittest

from knowledge_fabric import curation
from tests.util import seeded

T = "test-fabric"
OTHER = "isolation-check"
# A fixed, parametrised year — deliberately not the real calendar date, so the
# test is deterministic regardless of when it runs.
YEAR = 2019


def ts_for(year: int, month: int, day: int = 15) -> int:
    """Epoch milliseconds at midday UTC on the given calendar month (deterministic)."""
    dt = datetime.datetime(year, month, day, 12, 0, tzinfo=datetime.UTC)
    return int(dt.timestamp() * 1000)


class TimelineBase(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T, OTHER])

    def _log(self, action: str, month: int, *, year: int = YEAR, tenant: str = T, **kw) -> str:
        return curation.log_event(
            self.p,
            tenant,
            action,
            source=kw.get("source", "notes"),
            mode=kw.get("mode", "manual"),
            document_id=kw.get("document_id", "doc_x"),
            title=kw.get("title", "Doc X"),
            review=kw.get("review", "rid_x"),
            ts=ts_for(year, month),
        )


class TestBuckets(TimelineBase):
    def test_twelve_months_with_correct_per_action_stacks(self):
        self._log("ingested", 1)
        self._log("ingested", 1)
        self._log("accepted", 1)
        self._log("ingested", 3)
        self._log("rejected", 3)
        self._log("auto_kept", 7)
        self._log("auto_kept", 7)
        self._log("deleted", 12)
        # a prior-year event so more than one year is present
        self._log("ingested", 6, year=YEAR - 1)

        tl = curation.timeline(self.p, T, year=YEAR)

        self.assertEqual(tl["year"], YEAR)
        self.assertEqual([m["month"] for m in tl["months"]], list(range(1, 13)))
        jan = tl["months"][0]
        self.assertEqual((jan["ingested"], jan["accepted"]), (2, 1))
        mar = tl["months"][2]
        self.assertEqual((mar["ingested"], mar["rejected"]), (1, 1))
        self.assertEqual(tl["months"][6]["auto_kept"], 2)
        self.assertEqual(tl["months"][11]["deleted"], 1)
        # an empty month stacks to all zeros
        feb = tl["months"][1]
        self.assertEqual(
            (feb["ingested"], feb["accepted"], feb["rejected"], feb["deleted"], feb["auto_kept"]),
            (0, 0, 0, 0, 0),
        )
        # drill-down carries only this year's eight events, each with its month
        self.assertEqual(len(tl["rows"]), 8)
        self.assertTrue(all(1 <= r["month"] <= 12 for r in tl["rows"]))
        self.assertTrue(all(r["ts"] for r in tl["rows"]))
        # both years present are listed
        self.assertEqual(tl["years"], [YEAR - 1, YEAR])

    def test_ingested_event_appears_in_its_month_bar_and_rows(self):
        month = 9
        lid = self._log("ingested", month, document_id="doc_now", title="Now Doc", review="rid_now")
        tl = curation.timeline(self.p, T, year=YEAR)
        self.assertEqual(tl["months"][month - 1]["ingested"], 1)
        hit = [r for r in tl["rows"] if r["id"] == lid]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["month"], month)
        self.assertEqual(hit[0]["title"], "Now Doc")

    def test_default_year_is_latest_present(self):
        self._log("ingested", 5)
        self._log("ingested", 4, year=YEAR + 2)
        tl = curation.timeline(self.p, T)  # no year -> latest present
        self.assertEqual(tl["year"], YEAR + 2)
        self.assertEqual(tl["months"][3]["ingested"], 1)
        self.assertEqual(len(tl["rows"]), 1)


class TestFilters(TimelineBase):
    def test_source_and_mode_filters(self):
        self._log("ingested", 2, source="notes", mode="manual")
        self._log("auto_kept", 2, source="jira", mode="automated")

        both = curation.timeline(self.p, T, year=YEAR)
        self.assertEqual(len(both["rows"]), 2)

        notes = curation.timeline(self.p, T, year=YEAR, source="notes")
        self.assertEqual([r["source"] for r in notes["rows"]], ["notes"])
        self.assertEqual(notes["months"][1]["ingested"], 1)
        self.assertEqual(notes["months"][1]["auto_kept"], 0)

        automated = curation.timeline(self.p, T, year=YEAR, mode="automated")
        self.assertEqual([r["mode"] for r in automated["rows"]], ["automated"])
        self.assertEqual(automated["months"][1]["auto_kept"], 1)


class TestScoping(TimelineBase):
    def test_timeline_is_tenant_scoped(self):
        self._log("ingested", 1, tenant=T)
        self._log("ingested", 1, tenant=OTHER)
        self.assertEqual(len(curation.timeline(self.p, T, year=YEAR)["rows"]), 1)
        self.assertEqual(len(curation.timeline(self.p, OTHER, year=YEAR)["rows"]), 1)
        with self.assertRaises(PermissionError):
            curation.timeline(self.p, "", year=YEAR)

    def test_empty_log_is_safe(self):
        tl = curation.timeline(self.p, T)
        self.assertEqual(tl["rows"], [])
        self.assertEqual(tl["years"], [])
        self.assertEqual(len(tl["months"]), 12)
        self.assertTrue(
            all(sum(m[a] for a in curation.ACTIONS if a in m) == 0 for m in tl["months"])
        )


if __name__ == "__main__":
    unittest.main()
