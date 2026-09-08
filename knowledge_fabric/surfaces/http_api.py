"""HTTP surfaces (Section 13 / roadmap WS3): Ask, Curator, Admin (incl. bulk
upload + connector sync), the Agent path, the telemetry Dashboard, and the
/api/analytics economics API. One governed path for everyone (I7): every
endpoint authenticates a Principal and runs the same gate.

Standard library only — runs with zero dependencies.
"""
from __future__ import annotations

import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from ..answer.service import AnswerService
from ..app import Platform
from ..connectors import registry
from ..health import metrics as health
from ..ingestion.intake import Intake, IngestWorker
from ..ingestion.sync import SyncManager
from ..tenants import demo
from .dashboard import DASHBOARD_HTML

_platform: Platform | None = None
_svc: AnswerService | None = None


def platform() -> Platform:
    global _platform, _svc
    if _platform is None:
        _platform = Platform(db_path=os.environ.get("KF_DB", "./data/kf.db"))
        if not _platform.documents.list("acme-assurance"):
            demo.seed(_platform)
        _svc = AnswerService(_platform)
    return _platform


ASK_HTML = """<!doctype html><meta charset=utf-8><title>Knowledge Fabric — Ask</title>
<style>body{font:15px Inter,system-ui;margin:0;background:#0b1020;color:#e8ecf7}
header{padding:14px 20px;background:linear-gradient(90deg,#0E1A45,#132257);border-bottom:1px solid #26356b}
main{max-width:820px;margin:0 auto;padding:20px}
input,select,button{font:15px Inter,system-ui;padding:9px;border-radius:8px;border:1px solid #26356b;background:#0f1a3a;color:#e8ecf7}
input{width:58%} button{background:#4f7cff;border:0;cursor:pointer;font-weight:600}
a{color:#4bd6e5}.cite{background:#0f1a3a;border:1px solid #26356b;border-radius:8px;padding:8px 12px;margin:6px 0;font-size:13px}
.kind{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;font-weight:600}
.answer{background:#14331f} .clarify{background:#3a3115} .gap{background:#3a1520}
.why{background:#101a3e;border-left:3px solid #4f7cff;padding:8px 12px;margin:8px 0;font-size:13px;color:#cdd8f5}
#ans{margin-top:18px;line-height:1.55}</style>
<header><b>QualiZeal Knowledge Fabric</b> — Ask · grounded, cited, governed · <a href="/dashboard">Telemetry dashboard →</a></header>
<main>
<div><select id=user></select> <input id=q placeholder="Ask a grounded question..."
 value="why does a component with an open defect block its dependent releases?"> <button onclick=ask()>Ask</button></div>
<div id=ans></div></main>
<script>
const users=[["acme-assurance","asha.asker"],["acme-assurance","carl.curator"],
["acme-assurance","rana.restricted"],["qualizeal","asha.asker"]];
const sel=document.getElementById('user');
users.forEach(u=>{let o=document.createElement('option');o.value=u.join('|');o.text=u[1]+' @ '+u[0];sel.add(o)});
async function ask(){
 const [tenant,subject]=sel.value.split('|');
 const t=await (await fetch('/login',{method:'POST',body:JSON.stringify({tenant,subject})})).json();
 const r=await (await fetch('/ask',{method:'POST',headers:{'Authorization':'Bearer '+t.token},
   body:JSON.stringify({question:document.getElementById('q').value})})).json();
 let h=`<span class="kind ${r.kind}">${r.kind.toUpperCase()}</span> grounding ${r.grounding_score} · confidence ${r.confidence} · tier ${r.tier} · cost $${r.cost} · lang ${r.lang}`;
 if(r.why&&r.why.explain) h+=`<div class=why><b>Why this route:</b> ${r.why.explain}${(r.why.reasons||[]).length?' · '+r.why.reasons.map(x=>x.code).join(', '):''}${r.cache_hit?' · <b>cache hit</b> (saved $'+r.cost_saved+')':''}</div>`;
 h+=`<div><p>${r.answer_text}</p>`;
 (r.citations||[]).forEach((c,i)=>h+=`<div class=cite>[${i+1}] <b>${c.document_title}</b> — ${c.coordinate_render}<br>${c.snippet}</div>`);
 if(r.clarify_back) h+=`<div class=cite>${r.clarify_back}</div>`;
 document.getElementById('ans').innerHTML=h+'</div>';
}
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _principal(self):
        return platform().idp.authenticate({"Authorization": self.headers.get("Authorization", "")})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _require(self, action):
        """Authenticate + authorise; returns (principal, error_response_sent?)."""
        try:
            prin = self._principal()
        except PermissionError as e:
            self._send(401, {"error": str(e)}); return None
        if platform().policy.check(prin, action, {}).decision.value != "allow":
            self._send(403, {"error": f"'{action}' requires a higher role"}); return None
        return prin

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); p = platform()
        if u.path in ("/", "/ask"):
            return self._send(200, ASK_HTML, "text/html; charset=utf-8")
        if u.path == "/dashboard":
            return self._send(200, DASHBOARD_HTML, "text/html; charset=utf-8")
        if u.path == "/health":
            return self._send(200, {"status": "ok", "model": p.model_available(),
                                    "connectors": registry.available()})
        if u.path == "/connectors":
            return self._send(200, {"connectors": registry.available()})
        if u.path == "/metrics":
            return self._send(200, p.telemetry.metrics(q.get("tenant", ["acme-assurance"])[0]))
        if u.path == "/api/analytics":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, p.telemetry.analytics(
                prin.tenant, q.get("window", ["7d"])[0],
                q.get("subject", [None])[0], q.get("role", [None])[0]))
        if u.path == "/api/trace":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, {"spans": p.telemetry.trace(q.get("trace_id", [""])[0])})
        if u.path == "/admin/sources":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, {"sources": SyncManager(p).source_health(prin.tenant)})
        if u.path == "/curator/gaps":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, {"gaps": p.curation.list(prin.tenant, "gap"),
                                    "contradictions": p.curation.list(prin.tenant, "contradiction"),
                                    "review_queue": p.curation.list(prin.tenant, "low-confidence"),
                                    "risk_register": health.risk_register(p, prin.tenant)})
        if u.path == "/admin/audit":
            prin = self._require("admin")
            if not prin:
                return
            return self._send(200, {"audit": p.audit.for_tenant(prin.tenant, 50)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path); p = platform()
        if u.path == "/login":
            b = self._body()
            try:
                prin = demo.principal_for(p, b["tenant"], b["subject"])
            except KeyError as e:
                return self._send(404, {"error": str(e)})
            return self._send(200, {"token": p.idp.mint(prin), "subject": prin.subject,
                                    "roles": prin.roles, "scopes": prin.scopes})
        if u.path in ("/ask", "/agent/ask"):
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            return self._send(200, _svc.ask(prin, self._body().get("question", "")).to_dict())
        if u.path == "/admin/upload":                      # bulk upload (WS1)
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            intake, worker = Intake(p), IngestWorker(p, None); worker.intake = intake
            for f in b.get("files", []):
                data = (base64.b64decode(f["content_b64"]) if "content_b64" in f
                        else f.get("text", "").encode())
                intake.upload(prin.tenant, f["filename"], data, acl=f.get("acl", ["public"]),
                              ontology=b.get("ontology", "quality-assurance"))
            res = worker.drain()
            return self._send(200, {"uploaded": len(b.get("files", [])),
                                    "ingested": len([r for r in res if r["status"] in ("ok", "updated")]),
                                    "noops": len([r for r in res if r["status"] == "noop"]),
                                    "sources": SyncManager(p).source_health(prin.tenant)})
        if u.path == "/admin/sync":                        # trigger a connector sync (WS1)
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            kw = {"records": b["records"]} if "records" in b else {}
            summary = SyncManager(p).sync(prin.tenant, b["source"], b.get("config", {}),
                                          b.get("ontology", "quality-assurance"), **kw)
            return self._send(200, summary)
        if u.path == "/admin/budget":
            prin = self._require("set_budget")
            if not prin:
                return
            b = self._body()
            p.policy.set_budget(prin.tenant, float(b["cap"]))
            return self._send(200, {"tenant": prin.tenant, "cap": b["cap"], "spent": p.policy.spent(prin.tenant)})
        return self._send(404, {"error": "not found"})


def serve(host="0.0.0.0", port=8080):
    platform()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Knowledge Fabric on http://{host}:{port}  (Ask: /  ·  Dashboard: /dashboard)")
    srv.serve_forever()


if __name__ == "__main__":
    serve(port=int(os.environ.get("KF_PORT", "8080")))
