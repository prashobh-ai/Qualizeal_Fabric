# T160 — Admin user management with role + designation (PR #5 of the live-LLM series)

_The Admin "Users & Access" panel becomes a real, per-visitor user store: add a user
with a **role** and a **designation** (which conditions how the fabric answers, per
T27), edit, disable, reset budget, delete, and bulk-import from CSV. Signing in as an
added user carries the designation, so the answers are shaped for that persona._

## What it does

`scripts/showcase/engine.js` — `userMutation(body)` is now a full CRUD + upsert:

- **add / upsert** captures `roles`, `scopes`, **`designation`**, `email`,
  `department`, `team`, `daily_cap` — editing is saving the same subject again (the
  previous code dropped `designation` and refused to update an existing subject);
- **disable / enable** flips `status`; a **disabled** user is refused at sign-in
  (`resolveLogin` returns 403 for a disabled promoted user);
- **reset_budget** zeroes the user's spend;
- **delete** removes them.

Persisted per visitor in `kf.users`; the `promoted`-login path already mints with the
stored `designation`, so an added Tester signs in and answers are conditioned by the
`quality` persona (T27) — no server needed.

## The panel (`admin_ui.py` + `admin_ui__js.js`)

- **Role** dropdown (`asker`, `curator`, `admin`, `agent`) and **Designation**
  dropdown (`Developer`, `Tester`, `Architect`, `Delivery Manager`, `Delivery Head`,
  `Sales`, `HR`, `Executive`), plus email, department, team and a daily-cap input.
- The users table shows designation, roles, scopes, email and status, with per-row
  **Edit / Disable / Reset budget / Remove** actions (Edit repopulates the form).
- **CSV import** (`subject,designation,role,email,department,team,daily_cap`) with a
  **validated preview** (per-row ok/error) before it upserts each row.
- An **access matrix** — a small role × scope grid counting users — computed live from
  the list.

## Gate

- `scripts/showcase/user_runner.js` + `tests/unit/test_user_management.py` — drive the
  shipped engine as an admin: adding a `role=asker, designation=Tester` user makes it
  appear in `/admin/users` with its effective principals; signing in as that user
  carries the designation and the answer's `role_view.persona` is `quality`
  (tester-shaped); edit upserts; disable blocks sign-in and enable restores it; reset
  and delete work; a bulk upsert of two users lands. Also asserts the built Admin page
  carries both dropdowns, all eight designations, the CSV import and the access matrix.

## Verification

`node --check` clean (engine + admin JS + runner); ruff + format + notices clean;
`test_user_management` passes; `test_showcase` + `test_t47_surfaces` + `test_stage2_ui`
(the words/copy audit) pass; full `build_showcase` + `verify_showcase` clean.

## Series

This completes the punch-list from the two spec files: T166 (live routed Claude),
T156/T158 (live analytics + cache + cost-saved), T157 (golden answers), T159 (curator
mutations) and T160 (user management). The remaining dashboard-wiring items (T155/T167)
are covered by the live-analytics merge in the T156 PR.
