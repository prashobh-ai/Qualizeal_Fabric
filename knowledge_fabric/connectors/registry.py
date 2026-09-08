"""Connector registry (roadmap WS1: "plug and play").

Sources are wired by NAME + config, never by editing the pipeline. Adding a
source is: implement the Connector contract, register it here, list it in a
tenant's source set. The same registry serves whether the deployment reads
from GitHub, a standalone machine's drop folder, or an AWS account — only the
config differs, never the application code.
"""
from __future__ import annotations

from .files import FilesConnector
from .github import GitHubConnector
from .jira import JiraConnector

REGISTRY = {
    "files": FilesConnector,
    "github": GitHubConnector,
    "jira": JiraConnector,
    # additive: "confluence": ConfluenceConnector, "sharepoint": ..., "drive": ...
}


def build(source: str, tenant: str, config: dict, **kwargs):
    if source not in REGISTRY:
        raise KeyError(f"unknown connector '{source}'. Registered: {sorted(REGISTRY)}")
    return REGISTRY[source](tenant, config, **kwargs)


def available() -> list[str]:
    return sorted(REGISTRY)
