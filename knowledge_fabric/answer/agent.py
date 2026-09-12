"""The answering agent — a tool-using loop for everything Level 0 cannot settle (T43).

``run`` drives ``claude-sonnet-4-6`` (the large tier) through the Messages API
tool-use protocol over the read-only tools in ``answer/tools.py``:

* the request carries a STABLE system block (role profile + tool rules) marked
  ``cache_control``, then the SEMI-STABLE two-turn context, then the VOLATILE
  question as the user message;
* ``stop_reason == "tool_use"`` → every ``tool_use`` block is executed, all
  results go back in ONE user message of ``tool_result`` blocks;
* every step is one ledgered model call (``purpose="agent_step"``), one
  ``answer.step`` telemetry record per tool, one ``on_step`` callback so a
  chatbot can stream ``Checked <tool> · <n> results``, and one
  ``policy.try_spend`` — the run stops with a decline naming the cap when the
  budget would be exceeded;
* the final message is filtered by ``compose.keep_cited``: a sentence survives
  only if it cites a collected ``[n]``;
* no blind gap: the loop enforces facts → retrieval → table → code → live API
  (when available) → ``ask_user`` before a decline; a decline names the sources
  checked with the real numbers from facts.

Without a provider that speaks the Messages API (``KF_MODEL_MODE=extractive`` /
``mock``) ``run`` raises ``AgentUnavailableError`` — the queue and the server
report it rather than pretending.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from ..adapters.model import ProviderError, model_for_tier, resolve_models
from ..contracts.types import Answer, AnswerKind, new_id, now_ms
from ..facts import load_facts
from . import aggregate, compose, personas, tools

MAX_STEPS = 8

RULES = """You are the QualiZeal Knowledge Fabric answering agent.
You answer ONLY from tool results.

Rules:
1. Check sources in this order until one settles the question: query_facts (exact counts, \
inventories, as-of figures) -> search_passages (documents) -> describe_table + run_table_query \
(spreadsheets) -> search_code / get_symbol (code) -> github_api / jira_search / confluence_search \
(live sources, when available) -> ask_user (a clarifying question with 2-4 options). Never decline \
before trying the next source in this order.
2. Every sentence of your final answer must end with a citation marker [n] that refers to a \
citation number returned by a tool result. Sentences without a valid [n] are deleted before \
display, so do not write them. Do not invent numbers, names or sources.
3. Prefer exact figures with their as-of time. Use calculate for any arithmetic or date math.
4. When two tools disagree, say so and cite both.
5. Keep the answer short: the figures, the names, the source. No preamble, no apology.
6. If nothing covers the question after the sources above, reply with exactly: NO_EVIDENCE."""


class AgentUnavailableError(RuntimeError):
    """No provider that speaks the Messages API is configured (extractive/mock mode)."""


def _stable_prompt(designation: str) -> str:
    prof = personas.profile_for(designation)
    role = (
        f"Reader profile: {prof['lens']} (depth {prof['depth']}, emphasis {prof['emphasis']}). "
        f"{prof['note']}".strip()
    )
    tool_list = "\n".join(f"- {t['name']}: {t['description']}" for t in tools.TOOL_SPECS)
    return f"{RULES}\n\n{role}\n\nTools:\n{tool_list}"


def _context_prompt(context: dict | None) -> str:
    if not context:
        return ""
    turns = context.get("turns") or [{"question": q} for q in (context.get("history") or [])]
    lines = []
    for t in list(turns)[-2:]:
        if isinstance(t, dict):
            q = t.get("question", "")
            docs = ", ".join(str(d) for d in (t.get("answer_docs") or [])[:4])
            lines.append(f"- previous question: {q}" + (f" (answer cited: {docs})" if docs else ""))
    return (
        "Conversation so far (resolve 'it'/'there' against these):\n" + "\n".join(lines)
        if lines
        else ""
    )


def _text_of(content: list) -> str:
    return "\n".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    )


def _tool_uses(content: list) -> list[dict]:
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def _decline_text(topic: str) -> str:
    checked = aggregate.sources_summary(load_facts())
    return f"Checked {checked} — nothing covers {topic.strip() or 'this'}."


def _next_untried(tried: set[str], live: set[str]) -> str | None:
    for name in tools.SEARCH_ORDER:
        if name in tried:
            continue
        if name in ("github_api", "jira_search", "confluence_search") and name not in live:
            continue
        return name
    return None


def run(
    platform,
    principal,
    question: str,
    context: dict | None = None,
    *,
    budget_usd: float | None = None,
    max_steps: int = MAX_STEPS,
    on_step=None,
    live_jql=None,
    live_cql=None,
    github_transport=None,
    temperature: float = 0.0,
) -> Answer:
    """Answer ``question`` for ``principal`` with the tool loop; see the module doc."""
    p = platform
    model_client = p.model
    if not model_client.available() or not hasattr(model_client, "messages"):
        raise AgentUnavailableError(
            "the answering agent needs the Anthropic provider (KF_MODEL_MODE=anthropic with "
            "ANTHROPIC_API_KEY); the extractive core answers without it"
        )
    _small, large = resolve_models()
    tenant = principal.tenant
    trace_id = new_id("traj_")
    qhash = hashlib.sha1(question.encode("utf-8")).hexdigest()[:16]
    ctx = tools.ToolContext(
        platform=p,
        principal=principal,
        context=context,
        live_jql=live_jql,
        live_cql=live_cql,
        github_transport=github_transport,
    )
    live = tools.available_live(ctx)
    system = [
        {
            "type": "text",
            "text": _stable_prompt(principal.designation),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    semi = _context_prompt(context)
    if semi:
        system.append({"type": "text", "text": semi})
    messages: list[dict] = [{"role": "user", "content": question}]
    body_base = {"model": large, "max_tokens": 4096, "system": system, "tools": tools.TOOL_SPECS}
    if temperature is not None:
        body_base["temperature"] = float(temperature)

    citations: list[dict] = []
    steps: list[dict] = []
    tried: set[str] = set()
    cost = 0.0
    tokens_in = tokens_out = 0
    model_name = large
    last_text = ""
    outcome = "steps_exhausted"
    clarify = None
    injected = 0

    with p.telemetry.span(
        "answer",
        {
            "tenant": tenant,
            "trace_id": trace_id,
            "stage": "answer",
            "subject": principal.subject,
            "roles": principal.roles,
            "lang": "en",
            "persona": personas.persona_for(principal.designation),
            "designation": principal.designation or "",
            "scope": "restricted" if "restricted" in (principal.scopes or []) else "public",
            "context_resolved": 1 if context else 0,
            "agent": 1,
        },
    ) as span:
        for step_no in range(1, int(max_steps) + 1):
            body = {**body_base, "messages": messages}
            data = model_client.messages(body, purpose="agent_step", question_hash=qhash)
            usage = data.get("usage") or {}
            step_cost = float(data.get("cost_usd") or 0.0)
            tokens_in += int(usage.get("input_tokens", 0) or 0)
            tokens_out += int(usage.get("output_tokens", 0) or 0)
            model_name = data.get("model") or large
            # budget: the ledger cost of this step, reserved atomically; the
            # per-question cap when the caller set one.
            cap_hit = None
            if budget_usd is not None and cost + step_cost > float(budget_usd) + 1e-12:
                cap_hit = f"the per-question cap of ${float(budget_usd):.4f}"
            elif not p.policy.try_spend(
                tenant, step_cost, principal.subject if principal.agent else None
            ):
                remaining = max(0.0, p.policy.budget_remaining(tenant))
                cap_hit = f"the tenant budget (remaining ${remaining:.4f})"
            if cap_hit:
                cost += 0.0
                outcome = "budget"
                last_text = (
                    f"Stopped after {step_no} step(s): {cap_hit} would be exceeded "
                    f"(this step costs ${step_cost:.4f})."
                )
                break
            cost += step_cost
            content = data.get("content") or []
            stop = data.get("stop_reason")
            uses = _tool_uses(content)
            if stop == "tool_use" and uses:
                messages.append({"role": "assistant", "content": content})
                results = []
                for u in uses:
                    name = str(u.get("name", ""))
                    inp = u.get("input") if isinstance(u.get("input"), dict) else {}
                    tried.add(name)
                    ts = time.perf_counter()
                    is_error = False
                    try:
                        out = tools.execute(name, inp, ctx)
                    except tools.ToolError as e:
                        out, is_error = {"result": {"error": str(e)}, "citations": []}, True
                    except Exception as e:  # a tool crashed — the model sees it, the run continues
                        out, is_error = (
                            {"result": {"error": f"{type(e).__name__}: {e}"}, "citations": []},
                            True,
                        )
                    tl = (time.perf_counter() - ts) * 1000.0
                    n = 0 if is_error else tools.n_results(name, out)
                    numbered = []
                    for c in out.get("citations") or []:
                        citations.append(c)
                        numbered.append(
                            {
                                "n": len(citations),
                                "title": c.get("title", ""),
                                "url": c.get("url", ""),
                            }
                        )
                    payload = {"result": out.get("result"), "citations": numbered}
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": u.get("id", ""),
                            "content": json.dumps(payload, default=str)[:20000],
                            "is_error": is_error,
                        }
                    )
                    steps.append(
                        {
                            "tool": name,
                            "n": n,
                            "latency_ms": round(tl, 2),
                            "step": step_no,
                            "error": is_error,
                        }
                    )
                    p.telemetry.record(
                        "answer.step",
                        {
                            "tenant": tenant,
                            "trace_id": trace_id,
                            "stage": "agent",
                            "tool": name,
                            "n_results": n,
                            "duration_ms": tl,
                            "cost": step_cost if u is uses[0] else 0.0,
                            "tokens_in": int(usage.get("input_tokens", 0) or 0)
                            if u is uses[0]
                            else 0,
                            "tokens_out": int(usage.get("output_tokens", 0) or 0)
                            if u is uses[0]
                            else 0,
                            "subject": principal.subject,
                            "roles": principal.roles,
                            "model_name": model_name,
                            "tier": "escalation",
                            "level": "agent",
                        },
                    )
                    if on_step is not None:
                        try:
                            on_step(
                                {"tool": name, "n": n, "latency_ms": round(tl, 2), "step": step_no}
                            )
                        except Exception:
                            pass
                    if name == "ask_user" and not is_error:
                        r = out["result"]
                        clarify = {"question": r["question"], "options": r["options"]}
                messages.append({"role": "user", "content": results})
                if clarify:
                    outcome = "clarify"
                    break
                continue
            # end_turn (or max_tokens): the model wants to answer — or decline.
            text = _text_of(content).strip()
            kept = compose.keep_cited(text, citations) if citations else ""
            declined = (not kept) or text.strip().upper().startswith("NO_EVIDENCE")
            nxt = _next_untried(tried, live)
            if declined and nxt is not None and step_no < int(max_steps):
                # No blind gap: try the next untried source before declining.
                injected += 1
                messages.append(
                    {
                        "role": "assistant",
                        "content": content or [{"type": "text", "text": text or "…"}],
                    }
                )
                instr = (
                    f"You have not yet checked `{nxt}`. Call the `{nxt}` tool now for this "
                    "question before answering; only decline after every source in the order "
                    "has been tried."
                )
                if nxt == "ask_user":
                    instr = (
                        "The sources you tried do not settle this. Call `ask_user` with a "
                        "clarifying "
                        "question and 2-4 options before declining."
                    )
                messages.append({"role": "user", "content": instr})
                continue
            last_text = kept if kept else text
            outcome = "declined" if declined else "answered"
            break

        # ---- assemble the Answer -------------------------------------------------
        cites = [aggregate.citation_from_dict(c) for c in citations]
        why = {
            "level_name": "agent",
            "explain": (
                f"Agent ran {len(steps)} tool call(s) over "
                f"{len({s['tool'] for s in steps})} tool(s); "
                f"outcome: {outcome}."
            ),
            "reasons": [{"code": "agent", "detail": outcome, "signal": outcome == "answered"}],
            "steps": [
                {"tool": s["tool"], "n": s["n"], "latency_ms": s["latency_ms"]} for s in steps
            ],
            "tools_tried": sorted(tried),
            "injected": injected,
            "complexity": "complex",
            "model_name": model_name,
            "outcome": outcome,
        }
        common = {
            "tenant": tenant,
            "level": 4,
            "why": why,
            "lang": "en",
            "model_name": model_name,
            "complexity": "complex",
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            # T95 — every tool-loop turn but the final synthesis is reasoning
            # ("thinking"); the final step's output is the answer text.
            "thinking_tokens": max(
                0, (tokens_in + tokens_out) - int((steps[-1] or {}).get("tokens_out", 0) or 0)
            )
            if steps
            else 0,
        }
        if outcome == "clarify" and clarify:
            span.set(
                kind="clarify",
                level="clarify",
                tier="escalation",
                cost=cost,
                tokens=tokens_in + tokens_out,
                citations_count=0,
                why=why,
                model_name=model_name,
                complexity="complex",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            p.audit.write(
                tenant,
                principal.subject,
                principal.agent,
                "ask",
                "answer",
                "clarify:agent",
                trace_id,
                now_ms(),
            )
            return Answer(
                AnswerKind.CLARIFY,
                "",
                [],
                0.0,
                trace_id,
                cost,
                tokens_in + tokens_out,
                "escalation",
                grounding_score=0.0,
                clarify_back=clarify["question"],
                suggestions=clarify["options"],
                **common,
            )
        if outcome == "answered":
            g = min(1.0, 0.6 + 0.1 * len(cites))
            span.set(
                kind="answer",
                level="agent",
                tier="escalation",
                cost=cost,
                tokens=tokens_in + tokens_out,
                citations_count=len(cites),
                sources=[c.document_title for c in cites],
                why=why,
                grounding=g,
                model_name=model_name,
                complexity="complex",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            p.audit.write(
                tenant,
                principal.subject,
                principal.agent,
                "ask",
                "answer",
                "answered:agent",
                trace_id,
                now_ms(),
            )
            return Answer(
                AnswerKind.ANSWER,
                last_text,
                cites,
                round(g, 3),
                trace_id,
                cost,
                tokens_in + tokens_out,
                "escalation",
                grounding_score=g,
                **common,
            )
        # declined / budget / steps exhausted → an honest gap naming what was checked
        topic = (
            re.sub(
                r"^(what|which|who|how many|how|when|where|why|is|are|do|does|did)\b\s*",
                "",
                question.strip(),
                flags=re.I,
            ).rstrip("?")
            or question
        )
        if outcome == "budget":
            text = last_text
        else:
            text = _decline_text(topic)
            if last_text and outcome == "declined" and cites:
                text = last_text + "\n\n" + text
        span.set(
            kind="gap",
            level="gap",
            tier="escalation",
            cost=cost,
            tokens=tokens_in + tokens_out,
            citations_count=len(cites),
            why=why,
            model_name=model_name,
            complexity="complex",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        p.audit.write(
            tenant,
            principal.subject,
            principal.agent,
            "ask",
            "answer",
            f"gap:agent:{outcome}",
            trace_id,
            now_ms(),
        )
        return Answer(
            AnswerKind.GAP,
            text,
            cites if outcome == "declined" else [],
            0.0,
            trace_id,
            cost,
            tokens_in + tokens_out,
            "escalation",
            grounding_score=0.0,
            **common,
        )


__all__ = ["AgentUnavailableError", "MAX_STEPS", "ProviderError", "model_for_tier", "run"]
