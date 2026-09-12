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

# T115 — ONE source key per connector, everywhere (card, API, admin, scheduler,
# registry): the five keys ``website / files / github / jira / confluence``. The
# ``github`` and ``jira`` keys resolve to the **live** connector classes for real
# syncs; records-injection (offline demos and tests) selects the replay classes
# instead — see :func:`build`. The live class *names* are unchanged.
REGISTRY = {
    "files": FilesConnector,
    "github": GitHubLiveConnector,  # T37: GitHub REST + GraphQL (facts, activity, clone)
    "website": WebsiteConnector,  # bounded same-host crawl of the public site
    "jira": JiraLiveConnector,  # T41: Jira Cloud REST (JQL window, comments, facts)
    "confluence": ConfluenceConnector,  # T41: Confluence REST v2 (pages, attachments, facts)
    # additive: "sharepoint": ..., "drive": ...
}

#: the replay (in-memory records) classes, chosen when ``records=`` is injected
#: so offline demos and tests never touch the network.
REPLAY = {
    "github": GitHubConnector,
    "jira": JiraConnector,
}


def build(source: str, tenant: str, config: dict, **kwargs):
    if source not in REGISTRY:
        raise KeyError(f"unknown connector '{source}'. Registered: {sorted(REGISTRY)}")
    cls = REGISTRY[source]
    # records-injection (offline replay) picks the replay class for github/jira,
    # so a real sync uses the live client and a test/demo uses the records.
    if kwargs.get("records") is not None and source in REPLAY:
        cls = REPLAY[source]
    return cls(tenant, config, **kwargs)


def available() -> list[str]:
    return sorted(REGISTRY)
