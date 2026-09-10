"""Sign-in page (L1.4) — served at ``/signin``.

A white product sign-in: the lockup, a user id + password form against the
local IdP, and a disabled SSO placeholder ("available with the corporate
directory"). The token is kept in memory + ``sessionStorage``; every console
redirects here when it has no session.

The showcase build adds a labelled demo identity picker (Asker · Restricted
asker · Curator · Admin) that signs in through the same ``/login`` endpoint —
in the live product the corporate directory replaces it (config only).
"""

from __future__ import annotations

import json

from .ui_common import (
    _FAVICON,
    _LOCKUP,
    _VERSION,
    BRAND_CSS,
    RUNTIME_JS,
    _read_asset,
    demo_directory,
)

__all__ = ["SIGNIN_HTML"]

_CSS = _read_asset("signin_ui__css.css")

_BODY = _read_asset("signin_ui__body.html")

_JS = _read_asset("signin_ui__js.js")

_SHELL = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QualiZeal Knowledge Fabric — Sign in</title>
<link rel="icon" href="__FAVICON__">
<style>__CSS__
__EXTRA_CSS__</style></head><body>
__BODY__
<script>window.KF_DIRECTORY=__DIRECTORY__;</script>
<script>__RUNTIME__</script>
<script>__SCRIPT__</script>
</body></html>"""

SIGNIN_HTML = (
    _SHELL.replace("__CSS__", BRAND_CSS)
    .replace("__EXTRA_CSS__", _CSS)
    .replace("__BODY__", _BODY.replace("__LOCKUP__", _LOCKUP).replace("__VERSION__", _VERSION))
    .replace("__FAVICON__", _FAVICON)
    .replace("__DIRECTORY__", json.dumps(demo_directory(), sort_keys=True).replace("</", "<\\/"))
    .replace("__RUNTIME__", RUNTIME_JS)
    .replace("__SCRIPT__", _JS)
)
