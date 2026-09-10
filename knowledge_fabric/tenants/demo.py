"""QualiZeal Knowledge Fabric — the single in-house tenant (F0.1).

There is exactly one tenant, ``qualizeal`` (display name *QualiZeal*). The
tenant guard mechanism (invariant I5) and the ``tenant`` column stay in
place everywhere, but no Q-* vertical tenants are shipped. Departments,
teams and roles are attributes of *users* (F2.3), not tenants.

Test-only fixtures that need to prove cross-tenant isolation use an
ad-hoc, unseeded tenant string (see the test files); nothing shipped in
this module is user-visible beyond ``qualizeal``.

Every identifier here is drawn from ranges the issuing authorities
reserve for documentation (RFC 2606 domains, RFC 5737 IPs, 555-01xx
phone numbers), and ``validate_identifiers`` fails the build if a
shipped identifier could resolve to a real entity.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.types import Principal


@dataclass
class TenantConfig:
    tenant: str
    display: str
    ontology: str
    budget: float


# The one and only product tenant (F0.1).
DEMO_TENANTS = [
    TenantConfig("qualizeal", "QualiZeal", "quality-assurance", 5.0),
]

# Role-based user ids (F0.1 keeps P0.3's role slugs). No people's names —
# the subject is the role. `asker.public` cannot see `restricted`;
# `asker.restricted` is the elevated asker who can (P1.6 direction).
#
# Each row is (subject, roles, scopes, designation). `roles` is the ACCESS tier
# (asker/curator/admin/agent — what the user may retrieve, ACL before ranking);
# `designation` is the user's ORGANISATIONAL title (T27), captured by the admin
# at access-grant time, which conditions how an answer is framed and pitched but
# never widens what is retrievable. The demo askers below carry a spread of
# designations so the showcase can sign in as a developer, a tester, a delivery
# lead or a CTO and watch the same question come back pitched for each persona.
_ROLE_USERS = [
    ("asker.public", ["asker"], ["public"], ""),
    ("asker.restricted", ["asker"], ["public", "restricted"], ""),
    ("developer", ["asker"], ["public"], "Developer"),
    ("tester", ["asker"], ["public"], "QA Engineer"),
    ("architect", ["asker"], ["public", "restricted"], "Solution Architect"),
    ("delivery", ["asker"], ["public", "restricted"], "Delivery Head"),
    ("cto", ["asker"], ["public", "restricted"], "CTO"),
    ("curator", ["curator"], ["public", "restricted"], "Knowledge Curator"),
    ("admin", ["admin"], ["public", "restricted"], "Platform Admin"),
    ("qa-agent", ["agent"], ["public"], "Automation Agent"),
]
DEMO_USERS = {"qualizeal": list(_ROLE_USERS)}


def user_fields(row) -> tuple[str, list, list, str]:
    """Unpack a directory row tolerantly. Rows are (subject, roles, scopes,
    designation); older callers and add-user payloads may omit the designation,
    so it defaults to "" — keeping the directory forward/backward compatible."""
    subject, roles, scopes = row[0], list(row[1]), list(row[2])
    designation = row[3] if len(row) > 3 else ""
    return subject, roles, scopes, designation


def validate_identifiers() -> list[str]:
    """No documents are shipped in the product fabric (L0.2), so there are no
    shipped identifiers to validate. The synthetic corpus that used to live
    here now lives in ``tests/fixtures/synthetic_corpus.py`` and validates
    itself in the test suite. Kept so the CI identifier-safety gate keeps a
    stable entry point."""
    return []


def seed(platform, tenants: list[str] | None = None) -> dict:
    """Seed the QualiZeal fabric configuration and budget ONLY — no documents.

    Documents enter ``qualizeal`` exclusively via ``make load-corpus`` (the
    48-document QualiZeal corpus). This keeps every synthetic fixture out of
    the product fabric (L0.2 / defect D1). Users come from the static
    ``DEMO_USERS`` directory and need no seeding.
    """
    chosen = tenants or [t.tenant for t in DEMO_TENANTS]
    summary = {}
    for cfg in DEMO_TENANTS:
        if cfg.tenant not in chosen:
            continue
        platform.policy.set_budget(cfg.tenant, cfg.budget)
        summary[cfg.tenant] = {"documents": 0, "ingested": 0, "passages": 0}
    return summary


def principal_for(platform, tenant: str, subject: str) -> Principal:
    """Mint a signed principal for ``tenant/subject``.

    For the product tenant the roles/scopes come from ``DEMO_USERS``. Any
    other tenant is treated as a test-only isolation fixture: the same
    role slugs are honoured (matched by prefix — ``asker.public``,
    ``asker.restricted``, ``curator``, ``admin``, ``qa-agent``), so the
    isolation tests never need to mutate the shipped directory.
    """
    for row in DEMO_USERS.get(tenant, []):
        s, roles, scopes, designation = user_fields(row)
        if s == subject:
            token = platform.idp.mint(
                Principal(
                    subject=s,
                    tenant=tenant,
                    roles=roles,
                    scopes=scopes,
                    agent="agent" in roles,
                    designation=designation,
                )
            )
            return platform.idp.authenticate({"token": token})
    # test-only isolation path — synthesize from the role template
    for row in _ROLE_USERS:
        s, roles, scopes, designation = user_fields(row)
        if s == subject:
            token = platform.idp.mint(
                Principal(
                    subject=s,
                    tenant=tenant,
                    roles=roles,
                    scopes=scopes,
                    agent="agent" in roles,
                    designation=designation,
                )
            )
            return platform.idp.authenticate({"token": token})
    raise KeyError(f"no demo user {subject} in {tenant}")
