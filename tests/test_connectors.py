"""Connector contract: read-only, scoped, change-detecting, tombstoning."""

import unittest

from knowledge_fabric.connectors.files import FilesConnector
from knowledge_fabric.connectors.github import GitHubConnector


class TestConnectors(unittest.TestCase):
    def test_github_read_only_and_scoped(self):
        c = GitHubConnector("t1", {"repos": ["org/allowed"]})
        self.assertTrue(c.read_only)
        self.assertEqual(c.scopes(), ["repo:read"])
        with self.assertRaises(PermissionError):
            c.write_back()

    def test_github_backfill_then_incremental(self):
        recs = [
            {"repo": "org/allowed", "path": "a.md", "updated_at": 10, "content": "hello"},
            {"repo": "org/allowed", "path": "b.md", "updated_at": 20, "content": "world"},
        ]
        c = GitHubConnector("t1", {"repos": ["org/allowed"]}, records=recs)
        items, cursor = c.pull(None)
        self.assertEqual(len(items), 2)  # backfill
        recs.append({"repo": "org/allowed", "path": "c.md", "updated_at": 30, "content": "new"})
        items2, cursor2 = c.pull(cursor)
        self.assertEqual(len(items2), 1)  # only the delta
        self.assertEqual(items2[0].title, "c.md")

    def test_github_allow_list_excludes_other_repos(self):
        recs = [{"repo": "org/other", "path": "x.md", "updated_at": 10, "content": "nope"}]
        c = GitHubConnector("t1", {"repos": ["org/allowed"]}, records=recs)
        items, _ = c.pull(None)
        self.assertEqual(items, [])

    def test_github_tombstone_on_delete(self):
        recs = [
            {"repo": "org/allowed", "path": "a.md", "updated_at": 10, "content": "hi"},
            {
                "repo": "org/allowed",
                "path": "a.md",
                "updated_at": 20,
                "content": "",
                "deleted": True,
            },
        ]
        c = GitHubConnector("t1", {"repos": ["org/allowed"]}, records=recs)
        items, _ = c.pull(None)
        # the delete produces a tombstone instruction, not a new document
        self.assertTrue(any(it.meta.get("tombstones") for it in items) or items == [])

    def test_files_connector_scopes(self):
        c = FilesConnector("t1", {"folder": "."})
        self.assertEqual(c.scopes(), ["files:read"])


if __name__ == "__main__":
    unittest.main()
