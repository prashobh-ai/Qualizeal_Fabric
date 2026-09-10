"""Governance kernel: policy, budget, rate-limit, audit (Section 12).

One place decides Allow / Deny / Clarify and holds the budget. Budget spend
uses an ATOMIC conditional UPDATE (Runbook 6.2): 100 concurrent requests
cannot each read "remaining > 0" and collectively blow the cap — the row is
decremented under a lock and refused when it would exceed.
"""

from __future__ import annotations

import time

from ..contracts.types import Decision, PolicyResult, Principal
from ..stores.db import Database

# role -> actions it may perform (surfaces enforce these too)
ROLE_ACTIONS = {
    "asker": {"ask"},
    "curator": {"ask", "curate", "promote", "reingest"},
    "admin": {"ask", "curate", "promote", "reingest", "admin", "set_budget", "config"},
    "agent": {"ask"},
}


class PolicyEngine:
    def __init__(self, db: Database):
        self.db = db

    # ---- budgets -------------------------------------------------------
    def set_budget(self, tenant: str, cap: float) -> None:
        self.db.execute(
            "INSERT INTO budgets(tenant,cap,spent) VALUES(?,?,0) "
            "ON CONFLICT(tenant) DO UPDATE SET cap=excluded.cap",
            (tenant, cap),
        )

    def set_agent_budget(self, tenant: str, agent: str, cap: float) -> None:
        self.db.execute(
            "INSERT INTO agent_budgets(tenant,agent,cap,spent) VALUES(?,?,?,0) "
            "ON CONFLICT(tenant,agent) DO UPDATE SET cap=excluded.cap",
            (tenant, agent, cap),
        )

    def budget_remaining(self, tenant: str) -> float:
        r = self.db.one("SELECT cap,spent FROM budgets WHERE tenant=?", (tenant,))
        if not r:
            return float("inf")
        return r["cap"] - r["spent"]

    def try_spend(self, tenant: str, amount: float, agent: str | None = None) -> bool:
        """Atomic reserve. Returns False (refuse) rather than exceed a cap (I12)."""
        with self.db._lock:
            r = self.db.one("SELECT cap,spent FROM budgets WHERE tenant=?", (tenant,))
            if r is not None and r["spent"] + amount > r["cap"] + 1e-12:
                return False
            if agent is not None:
                ar = self.db.one(
                    "SELECT cap,spent FROM agent_budgets WHERE tenant=? AND agent=?",
                    (tenant, agent),
                )
                if ar is not None and ar["spent"] + amount > ar["cap"] + 1e-12:
                    return False
            if r is not None:
                self.db.execute("UPDATE budgets SET spent=spent+? WHERE tenant=?", (amount, tenant))
            if agent is not None:
                self.db.execute(
                    "UPDATE agent_budgets SET spent=spent+? WHERE tenant=? AND agent=?",
                    (amount, tenant, agent),
                )
            return True

    def spent(self, tenant: str) -> float:
        r = self.db.one("SELECT spent FROM budgets WHERE tenant=?", (tenant,))
        return r["spent"] if r else 0.0

    # ---- rate limiting (per user) -------------------------------------
    def rate_check(
        self, tenant: str, subject: str, limit: int = 60, window_s: float = 60.0
    ) -> bool:
        with self.db._lock:
            now = time.time()
            r = self.db.one(
                "SELECT window_start,count FROM rate_limit WHERE tenant=? AND subject=?",
                (tenant, subject),
            )
            if not r or now - r["window_start"] > window_s:
                self.db.execute(
                    "INSERT INTO rate_limit(tenant,subject,window_start,count) VALUES(?,?,?,1) "
                    "ON CONFLICT(tenant,subject) DO UPDATE SET window_start=?, count=1",
                    (tenant, subject, now, now),
                )
                return True
            if r["count"] >= limit:
                return False
            self.db.execute(
                "UPDATE rate_limit SET count=count+1 WHERE tenant=? AND subject=?",
                (tenant, subject),
            )
            return True

    # ---- access -------------------------------------------------------
    def check(self, principal: Principal, action: str, context: dict) -> PolicyResult:
        allowed = set()
        for role in principal.roles:
            allowed |= ROLE_ACTIONS.get(role, set())
        if action not in allowed:
            return PolicyResult(Decision.DENY, f"role(s) {principal.roles} may not '{action}'")
        if action == "ask" and self.budget_remaining(principal.tenant) <= 0:
            return PolicyResult(Decision.DENY, "tenant budget exhausted")
        return PolicyResult(Decision.ALLOW, "ok")
