"""HR Desk engine — the ONE call the HR Desk agent makes per employee question.

    result = await engine.handle(ctx, question, session_id=…, history=[…], client=…, model=…, effort=…,
                                 today=date, org_name=str, caller={location, grade, employment_type, role},
                                 defer_unmatched=False, agentic=None)

Order of operations (deterministic rails first, model last):
  1. hard router (desk.router)        → fixed template + IC referral / ticket; model NOT called — EVERY plan
  2. intent                           → pay-change (payslip diff, engine computes, model narrates) | policy
  3. policy: ACL + as-of + retrieval  → abstain below threshold / about another person
                                        (``defer_unmatched=True`` → kind "defer" and NOTHING is stored: the
                                        caller runs the tool-calling assistant instead — desk.dispatch)
  4. model answer over cached prefix  → deterministic validation (citations ⊆ retrieved) → redaction
  5. hand-off triggers                → ticket with transcript, citations, confidence, reason
  6. turn + session + append-only audit (mirrored into the agentic action log); CSAT prompt

Add-on gating (phase 2, P2-C — ``agentic_state(org).enabled``): the hard router and grounded cited answers run
for everyone (legal safety, not an upsell). The ADD-ON gates what EXECUTES: automatic hand-off tickets (else
"Ask HR" instructions — a sexual-harassment referral is still sealed and a wellbeing emergency still opens its urgent
ticket), model narration of the pay-change structure (else the templated rendering), CSAT prompts, the
hr_desk run per session, value-ledger outcomes, weekly report, dashboard.

Result (TurnResult as dict) — the phase-2 contract:
  {session_id, turn_id, run_id, kind: answer|abstain|pay_change|hard_route|refused|defer, reply, citations[], confidence,
   abstained, abstain_reason, hard_class, ticket{id, number, link, category, sla_hours} | None, ic_referral: bool,
   handoff_reason, ask_human: bool, ask_hr: {link, text} | None, addon: bool, disclosure, csat: {turn_id, question,
   options} | None, usage{…}, model, mode: live|rules|fixed, intent, structure (pay-change only), narrated, links[]}
"""
from __future__ import annotations

import re
import sys
import time
from datetime import date

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.config import settings as app_settings  # noqa: E402

import claude_client as LLM  # noqa: E402
import copilot as CP  # noqa: E402
import retrieval  # noqa: E402
from shield import Shield  # noqa: E402

from desk import agent as AG  # noqa: E402
from desk import answer as AN  # noqa: E402
from desk import audit as AU  # noqa: E402
from desk import handoff as HO  # noqa: E402
from desk import metrics as MX  # noqa: E402
from desk import payslip_diff as PD  # noqa: E402
from desk import policy_store as PS  # noqa: E402
from desk import router as RT  # noqa: E402
from desk import settings as DS  # noqa: E402
from desk.policy_models import DeskSession, DeskTurn  # noqa: E402

_PAY_Q = re.compile(r"\b(pay ?stub|payslip|salary|pay|paycheque|paycheck|net pay|take[- ]home|income tax|cpp|cpp2|qpp|ei|qpip|deduction|overtime|"
                    r"vacation pay|stat pay|salaire|paie|cut|less|more|increase|decrease|change|different|differ|why)\b", re.IGNORECASE)
_PAY_CHANGE = re.compile(r"\b(why|explain|how come|what changed|less|more|different|differ|change|reduced|increased|cut|pourquoi|moins|plus)\b", re.IGNORECASE)
_PAY_NOUN = re.compile(r"\b(pay ?stub|payslip|salary|pay|paycheque|paycheck|net|take[- ]home|tax|cpp|cpp2|qpp|ei|qpip|deduction|overtime|salaire|paie)\b", re.IGNORECASE)
_LEAVE_Q = re.compile(r"\b(leave|holiday|stat holiday|vacation|absence|sick|personal day|bereavement|parental|wfh|work from home|cong[ée]|vacances)\b", re.IGNORECASE)
_MONTH = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])\b")

ASK_HR_LINK = "/engage/helpdesk?new=1"
ASK_HR_TEXT = ("To take this further with a person, raise a ticket under Engage → Helpdesk → Ask HR (your question is kept with it); "
               "HR answers inside the category's response time.")
DEFER_REASONS = ("below_threshold", "no_approved_policy")


def intent_of(question: str) -> str:
    q = question or ""
    if _PAY_NOUN.search(q) and _PAY_CHANGE.search(q):
        return "pay_change"
    if _PAY_NOUN.search(q):
        return "pay"
    if _LEAVE_Q.search(q):
        return "leave"
    return "policy"


def _jaccard(a: str, b: str) -> float:
    x, y = set(retrieval.tokens(a)), set(retrieval.tokens(b))
    return len(x & y) / len(x | y) if (x | y) else 0.0


def ask_hr(category: str | None = None) -> dict:
    link = ASK_HR_LINK + (f"&category={category}" if category else "")
    return {"link": link, "text": ASK_HR_TEXT}


async def _session(ctx, session_id: str | None) -> DeskSession:
    async with pm.session() as s:
        row = None
        if session_id:
            row = await s.get(DeskSession, session_id)
            if row and (row.org_id != ctx.org_id or (row.user_id or "") != (ctx.user_id or "")):
                row = None                                   # never continue someone else's session
        if row is None:
            row = DeskSession(org_id=ctx.org_id, user_id=ctx.user_id or None, employee_id=ctx.employee_id or None, role=ctx.role or None,
                              channel=ctx.channel or "chat")
            s.add(row)
            await s.flush()
        await s.commit()
        return row


async def _last_turn(session_id: str) -> DeskTurn | None:
    from sqlalchemy import select
    async with pm.session() as s:
        return (await s.execute(select(DeskTurn).where(DeskTurn.session_id == session_id).order_by(DeskTurn.seq.desc()).limit(1))).scalars().first()


async def _caller_profile(ctx) -> dict:
    """ACL attributes of the caller (server-side, from the employee record — never from the prompt)."""
    out = {"role": ctx.role, "employee_id": ctx.employee_id}
    if not ctx.employee_id:
        return out
    data, err = await CP._get(f"{app_settings.EMPLOYEE_URL}/employees/{ctx.employee_id}/profile", ctx.headers)
    if err or not data:
        return out
    e = data.get("employee") or data
    out.update({"location": e.get("location") or e.get("branch") or e.get("work_location"), "grade": e.get("grade") or e.get("band") or e.get("level"),
                "employment_type": e.get("employment_type"), "department": e.get("department")})
    return out


async def _two_payslips(ctx, month: str | None) -> tuple[dict | None, dict | None, dict | None, dict | None, str | None]:
    """(curr_detail, curr_summary, prev_detail, prev_summary, error) for the caller only (service RBAC + x-employee-id)."""
    data, err = await CP._get(f"{app_settings.PAYROLL_URL}/payroll/payslips", ctx.headers)
    if err:
        return None, None, None, None, err
    rows = sorted((data or []), key=lambda x: x.get("pay_date") or "", reverse=True)
    if ctx.employee_id:
        rows = [r for r in rows if not r.get("employee_id") or str(r.get("employee_id")) == str(ctx.employee_id)]
    if month:
        idx = next((i for i, r in enumerate(rows) if (r.get("pay_date") or "").startswith(month)), None)
        if idx is None:
            return None, None, None, None, f"No payslip for {month}."
        rows = rows[idx:]
    if not rows:
        return None, None, None, None, "No payslip yet."
    cur, _ = await CP._get(f"{app_settings.PAYROLL_URL}/payroll/payslips/{rows[0]['stub_id']}", ctx.headers)
    prev = None
    if len(rows) > 1:
        prev, _ = await CP._get(f"{app_settings.PAYROLL_URL}/payroll/payslips/{rows[1]['stub_id']}", ctx.headers)
    if not cur:
        return None, None, None, None, "The payroll service did not return the payslip."
    return cur, rows[0], prev, (rows[1] if len(rows) > 1 else None), None


async def pay_change_structure(ctx, month: str | None = None, meta: PD.RunMeta | None = None) -> tuple[dict | None, str | None]:
    """The deterministic diff of the caller's payslip vs the previous one. ``meta`` None = read the real RunMeta
    from payroll data (TD1 claim amounts between the runs, salary revision effective dates)."""
    cur, cs, prev, ps, err = await _two_payslips(ctx, month)
    if err:
        return None, err
    if meta is None:
        from desk import payroll_meta as PMETA
        meta = await PMETA.run_meta(ctx, cur, cs, prev, ps)
    fc = PD.facts_from_stub(cur, cs)
    fp = PD.facts_from_stub(prev, ps) if prev else None
    out = PD.diff(fp, fc, meta or PD.RunMeta())
    out["run_meta"] = {"td1_changed": meta.td1_changed, "td1_note": meta.td1_note,
                       "revision_effective": meta.revision_effective, "revision_prorated": meta.revision_prorated, "arrears_from": meta.arrears_from,
                       "revision_note": meta.revision_note}
    return out, None


async def handle(ctx, question: str, *, session_id: str | None = None, history: list[dict] | None = None, client=None, model: str = "",
                 effort: str = "low", today: date | None = None, org_name: str = "", caller: dict | None = None, language_hint: str = "",
                 defer_unmatched: bool = False, agentic: dict | None = None) -> dict:
    t0 = time.monotonic()
    q = (question or "").strip()[:2000]
    cfg = await DS.load(ctx.org_id)
    st = agentic if agentic is not None else await AG.state(ctx.org_id)
    addon = bool(st.get("enabled"))
    today = today or date.today()
    disclosure = cfg.get("disclosure") or AN.DISCLOSURE_DEFAULT
    history = [h for h in (history or []) if h.get("role") in ("user", "assistant") and h.get("content")][-12:]
    transcript = history + [{"role": "user", "content": q}]
    sh = Shield()
    q_sh = sh.protect(q)

    # 1. hard router — before anything else, no model, every plan
    dec = RT.classify(q)
    if dec.hard and dec.klass:
        sess = await _session(ctx, session_id)
        return await _hard_route(ctx, sess, dec, cfg, transcript, t0, st)

    intent = intent_of(q)
    usage: dict = {}
    used_model = None
    mode = "rules"
    ticket = None
    ask = None
    result: dict

    # 2. pay change — engine computes, model narrates (narration = add-on; else the templated rendering)
    if intent == "pay_change":
        m = _MONTH.search(q)
        structure, err = await pay_change_structure(ctx, m.group(0) if m else None)
        if err:
            result = {"kind": "abstain", "reply": f"I could not read your payslips: {err}", "citations": [], "confidence": 0.0, "abstained": True,
                      "abstain_reason": "payslip_unavailable", "structure": None}
        else:
            if client and model and addon:
                nar = await AN.narrate_pay_change(client, model=model, effort=effort, structure=structure, question_shielded=q_sh, org_name=org_name,
                                                  today=today.isoformat(), role=ctx.role)
                usage, used_model = nar.get("usage") or {}, nar.get("model")
                mode = "live" if nar.get("narrated") else "rules"
                text = sh.restore(nar["text"]) if nar.get("narrated") else nar["text"]
            else:
                nar = {"narrated": False, "fallback_reason": "no_model" if not (client and model) else "addon_off"}
                text = PD.render_plain(structure)
            conf = 1.0 if not structure.get("needs_payroll_review") else 0.6
            result = {"kind": "pay_change", "reply": AN.redact_identifiers(text), "citations": [{"label": f"Payslip {structure['period']}" + (f" vs {structure['prev_period']}" if structure.get("prev_period") else ""),
                      "deep_link": (structure.get("links") or [None])[0], "policy": "payroll", "clause_id": structure.get("run_id") or structure["period"], "version": 1}],
                      "confidence": conf, "abstained": False, "abstain_reason": None, "structure": structure, "narrated": bool(nar.get("narrated")),
                      "fallback_reason": nar.get("fallback_reason")}
    elif dec.wants_human and RT.bare_human_request(q):
        # 3a. a bare request for a person: no clause lookup (nothing to answer), straight to the hand-off below
        result = {"kind": "answer", "reply": "Of course — let me get you to a person in HR.", "citations": [], "confidence": None, "abstained": False,
                  "abstain_reason": None, "validation": "human_requested", "retrieved": []}
    else:
        # 3. policy — ACL before retrieval
        who = caller or await _caller_profile(ctx)
        pctx = await PS.answer_context_db(ctx.org_id, who, q, today, threshold=float(cfg.get("abstain_threshold") or 0.35), k=int(cfg.get("top_k") or 5),
                                          about_other_person=dec.about_other_person)
        if pctx["abstain"] and defer_unmatched and pctx["abstain_reason"] in DEFER_REASONS and not dec.wants_human:
            # nothing stored: the question is not a policy question — the caller's tool assistant takes it
            return {"kind": "defer", "session_id": session_id, "turn_id": None, "run_id": None, "reply": "", "citations": [], "confidence": pctx["best"],
                    "abstained": False, "abstain_reason": pctx["abstain_reason"], "hard_class": None, "ticket": None, "ic_referral": False,
                    "handoff_reason": None, "ask_human": True, "ask_hr": None, "addon": addon, "disclosure": disclosure, "csat": None, "usage": {},
                    "model": None, "mode": "rules", "intent": intent, "structure": None, "narrated": None, "links": [], "duration_ms": 0}
        if pctx["abstain"]:
            a = AN.other_person_answer(disclosure=disclosure) if pctx["abstain_reason"] == "about_other_person" else AN.abstain_answer(pctx["abstain_reason"], disclosure=disclosure)
            result = {"kind": "refused" if pctx["abstain_reason"] == "about_other_person" else "abstain", "reply": a["text"], "citations": [], "confidence": pctx["best"],
                      "abstained": True, "abstain_reason": pctx["abstain_reason"], "retrieved": pctx["clauses"]}
        else:
            clauses = [{**c, "text": sh.protect(c.get("text") or "")} for c in pctx["clauses"]]   # the model never sees raw identifiers
            if client and model:
                try:
                    ga = await AN.grounded_answer(client, model=model, effort=effort, question_shielded=q_sh, clauses=clauses, org_name=org_name,
                                                  today=today.isoformat(), role=ctx.role, language_hint=language_hint)
                    usage, used_model, mode = ga["usage"], ga["model"], "live"
                    v = AN.validate(ga["text"], ga["tags"]) if not ga.get("refusal") else {"ok": False, "abstain": True, "reason": "refusal", "citations": []}
                except LLM.LlmError as e:
                    v = {"ok": False, "abstain": True, "reason": f"llm:{e.kind}", "citations": []}
                    ga = {"text": ""}
                if v["ok"] and not v["abstain"]:
                    fin = AN.finalize(sh.restore(ga["text"]), v["citations"], disclosure=disclosure)
                    result = {"kind": "answer", "reply": fin["text"], "citations": fin["citations"], "confidence": pctx["best"], "abstained": False,
                              "abstain_reason": None, "retrieved": pctx["clauses"], "validation": "ok"}
                else:
                    a = AN.abstain_answer(v["reason"], disclosure=disclosure)
                    result = {"kind": "abstain", "reply": a["text"], "citations": [], "confidence": pctx["best"], "abstained": True,
                              "abstain_reason": v["reason"], "retrieved": pctx["clauses"], "validation": ("rejected:" if not v["ok"] else "abstain:") + str(v["reason"])}
            else:
                # honest no-model mode: the clause text itself, cited — nothing generated
                disclosure = AN.DISCLOSURE_EXTRACTIVE
                top = pctx["clauses"][:2]
                cits = [{"tag": f"C{i}", "policy": c["policy"], "policy_id": c["policy_id"], "clause_id": c["clause_id"], "version": c["version"],
                         "effective_from": c["effective_from"], "deep_link": c["deep_link"], "clause_pk": c["id"],
                         "label": f"{c['policy']}, clause {c['clause_id']}, v{c['version']}, effective {c['effective_from'] or 'n/a'}"} for i, c in enumerate(top, 1)]
                body = "\n\n".join(f"{c['title']}: {c['text']} [C{i}]" for i, c in enumerate(top, 1))
                fin = AN.finalize(body, cits, disclosure=disclosure)
                result = {"kind": "answer", "reply": fin["text"], "citations": cits, "confidence": pctx["best"], "abstained": False, "abstain_reason": None,
                          "retrieved": pctx["clauses"], "validation": "ok:extractive"}

    sess = await _session(ctx, session_id)
    last = await _last_turn(sess.id)
    repeat = bool(last and last.question_shielded and _jaccard(last.question_shielded, q_sh) >= 0.7)
    run_id = await AG.ensure_run(ctx.org_id, sess.id, ctx.user_id, st)

    # 4. counters + hand-off (automatic ticket = add-on; otherwise "Ask HR" instructions)
    abstained = bool(result.get("abstained"))
    unresolved_inc = 1 if (abstained or repeat) else 0
    new_unresolved = int(sess.unresolved or 0) + unresolved_inc
    new_abstains = int(sess.abstains or 0) + (1 if abstained else 0)
    trig = HO.decide(wants_human=dec.wants_human, unresolved=new_unresolved, abstains=new_abstains, hard_class=None,
                     needs_payroll_review=bool((result.get("structure") or {}).get("needs_payroll_review")), cfg=cfg, intent=intent)
    handoff_reason = None
    if trig.fire and not sess.ticket_id:
        handoff_reason = trig.reason
        if addon:
            body = HO.ticket_body(reason=trig.reason, transcript=transcript + [{"role": "assistant", "content": result["reply"]}], citations=result.get("citations") or [],
                                  confidence=result.get("confidence"), session_id=sess.id)
            data, err = await HO.open_ticket(ctx.headers, category=trig.category, subject=HO.ticket_subject(trig.reason, q, None), body=body)
            if data and not err:
                ticket = {"id": data.get("id"), "number": data.get("number"), "link": f"/engage/helpdesk?id={data.get('id')}", "category": trig.category,
                          "sla_hours": data.get("sla_hours")}
                result["reply"] += f"\n\nI have passed this to a person: ticket {data.get('number') or ''} ({trig.category}) — HR will reply within {data.get('sla_hours') or '—'} hours."
            else:
                ask = ask_hr(trig.category)
                result["reply"] += "\n\nI tried to open a ticket for a person to take over but the helpdesk did not answer — please use Engage → Helpdesk → Ask HR."
        else:
            ask = ask_hr(trig.category)
            result["reply"] += "\n\n" + ask["text"]

    # 5. persist turn, session, audit (+ agentic mirror), value
    ms = int((time.monotonic() - t0) * 1000)
    reply_sh = sh.protect(result["reply"])
    label = _intent_label(intent, result)
    async with pm.session() as s:
        row = await s.get(DeskSession, sess.id)
        row.turns = int(row.turns or 0) + 1
        row.answered = int(row.answered or 0) + (0 if abstained else 1)
        row.abstains, row.unresolved = new_abstains, new_unresolved
        row.last_at = pm.now_utc()
        if ticket:
            row.ticket_id, row.handoff_reason, row.outcome, row.ended_at = ticket.get("id") or ticket.get("number"), handoff_reason, "handed_off", pm.now_utc()
        elif handoff_reason:
            row.handoff_reason = handoff_reason                  # asked to see a person, no ticket (add-on off) — stays a candidate session
            if row.outcome == "self_solved":
                row.outcome = "unresolved"
        elif row.outcome == "open" and not abstained:
            row.outcome = "self_solved"                      # provisional; a later thumbs-down / ticket changes it
        turn = DeskTurn(org_id=ctx.org_id, session_id=sess.id, seq=row.turns, intent=label, hard_routed=False,
                        question_shielded=q_sh, answer_shielded=reply_sh, abstained=abstained, cited=not abstained,
                        confidence=result.get("confidence"), duration_ms=ms)
        s.add(turn)
        await s.flush()
        turn_id = turn.id
        await s.commit()
    await AU.write(org=ctx.org_id, actor_user_id=ctx.user_id, employee_id=ctx.employee_id, session_id=sess.id, turn_id=turn_id,
                   intent_class=label, retrieved=result.get("retrieved") or [], model=used_model, prompt_hash=AN.prompt_hash(),
                   inputs_tokenised=q_sh, answer_digest=AN.digest(result["reply"]), citations=result.get("citations") or [],
                   confidence=result.get("confidence"), validation=result.get("validation") or ("abstain:" + str(result.get("abstain_reason")) if abstained else "ok"),
                   escalation_reason=handoff_reason, ticket_id=(ticket or {}).get("id"), run_id=run_id, usage=usage)
    await AG.record_turn_value(ctx.org_id, turn_id=turn_id, kind=("human_requested" if result.get("validation") == "human_requested" else result["kind"]),
                               abstained=abstained, ticket=ticket, ic_referral=False, run_id=run_id, st=st)    # a bare "talk to a person" resolves nothing
    if ticket and run_id:
        await AG.finish_session_run(ctx.org_id, sess.id, reason="handed_off")
    return {"session_id": sess.id, "turn_id": turn_id, "run_id": run_id, "kind": result["kind"], "reply": result["reply"], "citations": result.get("citations") or [],
            "confidence": result.get("confidence"), "abstained": abstained, "abstain_reason": result.get("abstain_reason"), "hard_class": None,
            "ticket": ticket, "ic_referral": False, "handoff_reason": handoff_reason, "ask_human": True, "ask_hr": ask, "addon": addon, "disclosure": disclosure,
            "csat": MX.csat_prompt(turn_id) if addon else None, "usage": usage, "model": used_model, "mode": mode, "intent": intent,
            "structure": result.get("structure"), "narrated": result.get("narrated"), "fallback_reason": result.get("fallback_reason"),
            "links": [c.get("deep_link") for c in (result.get("citations") or []) if c.get("deep_link")], "duration_ms": ms}


def _intent_label(intent: str, result: dict) -> str:
    if intent.startswith("pay"):
        return intent
    cits = result.get("citations") or []
    if intent == "policy" and cits and cits[0].get("policy"):
        key = "policy:" + re.sub(r"[^a-z0-9]+", "_", str(cits[0]["policy"]).lower()).strip("_")
        return key if len(key) <= 40 else key[:40].rsplit("_", 1)[0]          # cut at a word, not mid-word ("…harassment p")
    return intent


async def _hard_route(ctx, sess: DeskSession, dec: RT.RouteDecision, cfg: dict, transcript: list[dict], t0: float, st: dict) -> dict:
    klass = dec.klass
    addon = bool(st.get("enabled"))
    text = RT.render_template(klass, cfg)
    ticket, ic, ask = None, False, None
    run_id = await AG.ensure_run(ctx.org_id, sess.id, ctx.user_id, st)
    if klass == "sexual_harassment":
        rid = await HO.create_ic_referral(ctx.org_id, ctx.employee_id, ctx.user_id, ctx.channel)     # sealed, every plan
        ic = rid is not None
    else:
        trig = HO.decide(wants_human=False, unresolved=0, abstains=0, hard_class=klass, cfg=cfg)
        if trig.fire and (addon or klass == "mental_health"):           # a wellbeing emergency always reaches a person today
            body = HO.ticket_body(reason=trig.reason, transcript=transcript, citations=[], confidence=None, session_id=sess.id, hard_class=klass,
                                  extra=("URGENT: the person may be at risk — contact them today and share the helpline." if klass == "mental_health" else ""))
            data, err = await HO.open_ticket(ctx.headers, category=trig.category, subject=HO.ticket_subject(trig.reason, "", klass), body=body,
                                             priority=HO.PRIORITY.get(klass, "high"))
            if data and not err:
                ticket = {"id": data.get("id"), "number": data.get("number"), "link": f"/engage/helpdesk?id={data.get('id')}", "category": trig.category,
                          "sla_hours": data.get("sla_hours")}
        elif trig.fire:
            ask = ask_hr(trig.category)
            text += "\n\n" + ask["text"]
    ms = int((time.monotonic() - t0) * 1000)
    async with pm.session() as s:
        row = await s.get(DeskSession, sess.id)
        row.turns = int(row.turns or 0) + 1
        row.outcome, row.ended_at, row.last_at = "hard_routed", pm.now_utc(), pm.now_utc()
        row.handoff_reason = f"hard_route:{klass}"
        if ticket:
            row.ticket_id = ticket.get("id") or ticket.get("number")
        # class + timestamp ONLY — no question, no answer text in the general transcript store
        turn = DeskTurn(org_id=ctx.org_id, session_id=sess.id, seq=row.turns, intent=klass, hard_routed=True, question_shielded=None, answer_shielded=None,
                        abstained=False, cited=False, confidence=None, duration_ms=ms)
        s.add(turn)
        await s.flush()
        turn_id = turn.id
        await s.commit()
    await AU.write(org=ctx.org_id, actor_user_id=ctx.user_id, employee_id=ctx.employee_id, session_id=sess.id, turn_id=turn_id, intent_class=klass,
                   retrieved=[], model=None, prompt_hash=None, inputs_tokenised=None, answer_digest=AN.digest(text), citations=[], confidence=None,
                   validation=f"hard_route:{klass}", escalation_reason=f"hard_route:{klass}", ticket_id=(ticket or {}).get("id"), run_id=run_id)
    await AG.record_turn_value(ctx.org_id, turn_id=turn_id, kind="hard_route", abstained=False, ticket=ticket, ic_referral=ic, run_id=run_id, st=st)
    if run_id:
        await AG.finish_session_run(ctx.org_id, sess.id, reason="hard_route")
    return {"session_id": sess.id, "turn_id": turn_id, "run_id": run_id, "kind": "hard_route", "reply": text, "citations": [], "confidence": None, "abstained": False,
            "abstain_reason": None, "hard_class": klass, "ticket": ticket, "ic_referral": ic, "handoff_reason": f"hard_route:{klass}", "ask_human": True,
            "ask_hr": ask, "addon": addon,
            "disclosure": "This is a fixed message from your company, not an AI-generated answer.", "csat": None, "usage": {}, "model": None,
            "mode": "fixed", "intent": klass, "structure": None, "narrated": None, "links": [], "duration_ms": ms, "model_called": False}


async def record_external_turn(ctx, question: str, reply: str, *, session_id: str | None = None, intent: str = "tools", cited: bool = False,
                               tools_used: list[str] | None = None, usage: dict | None = None, model: str | None = None,
                               agentic: dict | None = None) -> dict:
    """A turn answered OUTSIDE the desk engine (the tool-calling assistant: leave balance, holidays, approvers …) still
    belongs to the employee's desk session — so metrics, the run and the value ledger cover the whole conversation.
    Stores the shielded question / answer like any other turn; never raises past the turn id."""
    st = agentic if agentic is not None else await AG.state(ctx.org_id)
    addon = bool(st.get("enabled"))
    sh = Shield()
    q_sh, r_sh = sh.protect((question or "")[:2000]), sh.protect((reply or "")[:8000])
    sess = await _session(ctx, session_id)
    run_id = await AG.ensure_run(ctx.org_id, sess.id, ctx.user_id, st)
    label = ("tools:" + re.sub(r"[^a-z0-9_]+", "_", (intent or "tools").lower()))[:40]
    async with pm.session() as s:
        row = await s.get(DeskSession, sess.id)
        row.turns = int(row.turns or 0) + 1
        row.answered = int(row.answered or 0) + (1 if cited else 0)
        row.last_at = pm.now_utc()
        if row.outcome == "open" and cited:
            row.outcome = "self_solved"
        turn = DeskTurn(org_id=ctx.org_id, session_id=sess.id, seq=row.turns, intent=label, hard_routed=False, question_shielded=q_sh,
                        answer_shielded=r_sh, abstained=not cited, cited=cited, confidence=1.0 if cited else None, duration_ms=0)
        s.add(turn)
        await s.flush()
        turn_id = turn.id
        await s.commit()
    await AU.write(org=ctx.org_id, actor_user_id=ctx.user_id, employee_id=ctx.employee_id, session_id=sess.id, turn_id=turn_id, intent_class=label,
                   retrieved=[], model=model, prompt_hash=None, inputs_tokenised=q_sh, answer_digest=AN.digest(reply or ""), citations=[],
                   confidence=1.0 if cited else None, validation=("tools:" + ",".join(tools_used or []))[:40] or "tools", escalation_reason=None,
                   ticket_id=None, run_id=run_id, usage=usage or {}, event_type="desk.tools")
    await AG.record_turn_value(ctx.org_id, turn_id=turn_id, kind="tools", abstained=not cited, ticket=None, ic_referral=False, run_id=run_id, st=st)
    return {"session_id": sess.id, "turn_id": turn_id, "run_id": run_id, "kind": "tools", "addon": addon, "csat": MX.csat_prompt(turn_id) if addon else None,
            "disclosure": (await DS.load(ctx.org_id)).get("disclosure") or AN.DISCLOSURE_DEFAULT}


async def feedback(ctx, session_id: str, turn_id: str | None, rating: str, comment: str = "") -> dict:
    """Thumbs on a turn: feeds CSAT, the self-solved outcome, the unresolved counter and the value ledger
    (thumbs-up confirms the provisional query_resolved row; thumbs-down compensates it)."""
    from desk.policy_models import DeskFeedback
    rating = "up" if rating == "up" else "down"
    async with pm.session() as s:
        row = await s.get(DeskSession, session_id)
        if not row or row.org_id != ctx.org_id or (row.user_id or "") != (ctx.user_id or ""):
            return {"ok": False, "error": "Session not found"}
        s.add(DeskFeedback(org_id=ctx.org_id, session_id=session_id, turn_id=turn_id, user_id=ctx.user_id or None, rating=rating,
                           comment_shielded=Shield().protect(comment or "")[:1000] or None))
        if turn_id:
            t = await s.get(DeskTurn, turn_id)
            if t and t.session_id == session_id:
                t.rating = rating
        row.csat = rating
        if rating == "down":
            row.unresolved = int(row.unresolved or 0) + 1
            if row.outcome == "self_solved":
                row.outcome = "unresolved"
        await s.commit()
        out = {"ok": True, "unresolved": row.unresolved, "outcome": row.outcome}
    out["value"] = await AG.on_feedback(ctx.org_id, turn_id=turn_id, rating=rating, user_id=ctx.user_id)
    return out
