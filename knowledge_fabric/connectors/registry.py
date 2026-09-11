"""Connector registry (roadmap WS1: "plug and play").

Sources are wired by NAME + config, never by editing the pipeline. Adding a
source is: implement the Connector contract, register it here, list it in a
tenant's source set. The same registry serves whether the deployment reads
from GitHub, a standalone machine's drop folder, or an AWS account — only the
config differs, never the application code.
"""

from __future__ import annotations

from .confluence import ConfluenceConnector
from .files import FilesConnector
from .github import GitHubConnector
from .github_live import GitHubLiveConnector
from .jira import JiraConnector
from .jira_live import JiraLiveConnector
from .website import WebsiteConnector

REGISTRY = {
    "files": FilesConnector,
    "github": GitHubConnector,  # replay (in-memory records) — offline demos and tests
    "github_live": GitHubLiveConnector,  # T37: GitHub REST + GraphQL (facts, activity, clone)
    "website": WebsiteConnector,  # bounded same-host crawl of the public site
    "jira": JiraConnector,  # replay (in-memory records) — offline demos and tests
    "jira_live": JiraLiveConnector,  # T41: Jira Cloud REST (JQL window, comments, facts)
    "confluence": ConfluenceConnector,  # T41: Confluence REST v2 (pages, attachments, facts)
    # additive: "sharepoint": ..., "drive": ...
}


def build(source: str, tenant: str, config: dict, **kwargs):
    if source not in REGISTRY:
        raise KeyError(f"unknown connector '{source}'. Registered: {sorted(REGISTRY)}")
    return REGISTRY[source](tenant, config, **kwargs)


def available() -> list[str]:
    return sorted(REGISTRY)
