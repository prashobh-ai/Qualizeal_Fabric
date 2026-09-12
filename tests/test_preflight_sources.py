"""T124a — the source preflight distinguishes credential failure kinds and
stops the build when a set-secret source fails, offline via a mocked fetch."""

from __future__ import annotations

import unittest

from scripts import preflight_sources as pf

SOURCES = [
    {"key": "website", "kind": "website", "url": "https://qualizeal.com", "required_secrets": []},
    {
        "key": "github_qualizeal",
        "kind": "github",
        "org": "Qualizeal",
        "required_secrets": ["KF_GITHUB_TOKEN"],
    },
    {
        "key": "jira",
        "kind": "jira",
        "site_env": "JIRA_URL",
        "dashboards": ["10001"],
        "boards": ["34"],
        "required_secrets": ["JIRA_URL", "JIRA_EMAIL", "JIRA_TOKEN"],
    },
    {
        "key": "confluence",
        "kind": "confluence",
        "site_env": "CONFLUENCE_URL",
        "pages": ["1703938"],
        "required_secrets": ["CONFLUENCE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN"],
    },
]

FULL_ENV = {
    "KF_GITHUB_TOKEN": "ghp_x",
    "JIRA_URL": "https://q.atlassian.net",
    "JIRA_EMAIL": "a@b.co",
    "JIRA_TOKEN": "jt",
    "CONFLUENCE_URL": "https://c.atlassian.net/wiki",
    "CONFLUENCE_EMAIL": "a@b.co",
    "CONFLUENCE_TOKEN": "ct",
}


def _by(results):
    return {r["key"]: r for r in results}


class TestAllReachable(unittest.TestCase):
    def _fetch(self, method, url, headers, timeout=20):
        if "sitemap.xml" in url:
            return 200, b"<urlset/>", {}
        if "api.github.com/orgs/Qualizeal/repos" in url:
            link = '<https://api.github.com/orgs/Qualizeal/repos?per_page=1&page=12>; rel="last"'
            return 200, b'[{"full_name":"Qualizeal/app"}]', {"Link": link}
        if url.endswith("/rest/api/3/myself"):
            return 200, b'{"accountId":"1"}', {}
        if "/rest/api/3/dashboard/10001" in url:
            return 200, b'{"id":"10001","name":"QA"}', {}
        if "/rest/agile/1.0/board/34" in url:
            return 200, b'{"id":34}', {}
        if "/api/v2/pages/1703938" in url:
            return 200, b'{"id":"1703938","body":{"storage":{"value":"<p>x</p>"}}}', {}
        return 404, b"{}", {}

    def test_all_four_pass(self):
        rep = pf.run_preflight(SOURCES, FULL_ENV, self._fetch)
        self.assertEqual(rep["reachable"], 4)
        self.assertEqual(rep["failed"], [])
        by = _by(rep["results"])
        self.assertIn("12 repos visible", by["github_qualizeal"]["detail"])


class TestFailureKinds(unittest.TestCase):
    def test_jira_401_is_auth_pairing(self):
        def fetch(m, url, h, timeout=20):
            if url.endswith("/rest/api/3/myself"):
                return 401, b"{}", {}
            return 200, b"{}", {}

        r = pf.check_jira(SOURCES[2], FULL_ENV, fetch)
        self.assertFalse(r["ok"])
        self.assertIn("401", r["detail"])
        self.assertIn("pairing", r["detail"])

    def test_jira_403_dashboard_not_shared(self):
        def fetch(m, url, h, timeout=20):
            if url.endswith("/myself"):
                return 200, b"{}", {}
            if "dashboard/10001" in url:
                return 403, b"{}", {}
            return 200, b"{}", {}

        r = pf.check_jira(SOURCES[2], FULL_ENV, fetch)
        self.assertFalse(r["ok"])
        self.assertIn("403", r["detail"])
        self.assertIn("dashboard 10001", r["detail"])

    def test_confluence_404_wrong_site(self):
        def fetch(m, url, h, timeout=20):
            return 404, b"{}", {}

        r = pf.check_confluence(SOURCES[3], FULL_ENV, fetch)
        self.assertFalse(r["ok"])
        self.assertIn("404", r["detail"])

    def test_github_403_org_not_approved(self):
        def fetch(m, url, h, timeout=20):
            return 403, b"{}", {}

        r = pf.check_github(SOURCES[1], FULL_ENV, fetch)
        self.assertFalse(r["ok"])
        self.assertIn("403", r["detail"])
        self.assertIn("approve", r["detail"])

    def test_github_200_empty_is_warn_not_fail(self):
        def fetch(m, url, h, timeout=20):
            return 200, b"[]", {}

        r = pf.check_github(SOURCES[1], FULL_ENV, fetch)
        self.assertTrue(r["ok"])
        self.assertTrue(r["warn"])
        self.assertIn("0 repos", r["detail"])

    def test_network_error(self):
        def fetch(m, url, h, timeout=20):
            return 0, b"", {}

        r = pf.check_jira(SOURCES[2], FULL_ENV, fetch)
        self.assertFalse(r["ok"])
        self.assertIn("network", r["detail"])


class TestExitAndSkip(unittest.TestCase):
    def test_failed_source_makes_run_report_failure(self):
        def fetch(m, url, h, timeout=20):
            if "sitemap" in url:
                return 200, b"x", {}
            if "api.github.com" in url:
                return 200, b'[{"full_name":"Qualizeal/a"}]', {}
            if url.endswith("/myself"):
                return 401, b"{}", {}  # jira auth fails
            return 200, b'{"body":{"storage":{"value":"x"}}}', {}

        rep = pf.run_preflight(SOURCES, FULL_ENV, fetch)
        self.assertTrue(rep["failed"])
        self.assertEqual({r["key"] for r in rep["failed"]}, {"jira"})

    def test_missing_secret_is_skipped_not_failed(self):
        env = {"KF_GITHUB_TOKEN": "x"}  # no jira/confluence secrets

        def fetch(m, url, h, timeout=20):
            if "sitemap" in url:
                return 200, b"x", {}
            if "api.github.com" in url:
                return 200, b'[{"full_name":"Qualizeal/a"}]', {}
            return 200, b"{}", {}

        rep = pf.run_preflight(SOURCES, env, fetch)
        by = _by(rep["results"])
        self.assertTrue(by["jira"]["skipped"])
        self.assertTrue(by["confluence"]["skipped"])
        self.assertEqual(rep["failed"], [])  # skipped never fails the build
        self.assertEqual(rep["total"], 2)  # website + github are active


if __name__ == "__main__":
    unittest.main()
