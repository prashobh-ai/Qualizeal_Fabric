"""Shared test setup.

T117 gates the passwordless ``{tenant, subject}`` ``POST /login`` path behind
``KF_DEV_LOGIN=1`` so the deployed build has no open-door entry. The existing
HTTP-harness tests sign in through that dev path to obtain a token, so enable
it for the test process here (``setdefault`` — a real env override still wins).
"""

import os

os.environ.setdefault("KF_DEV_LOGIN", "1")
