"""Sensitive-topic HARD ROUTER — runs BEFORE any model call; the model is never consulted.

A policy decision, not a model decision: keyword + pattern lists per language (English and
Canadian French). A hit returns a fixed, non-generative template (org-configurable text) and a
routing instruction:

  sexual_harassment → sealed desk_ic_referrals row (org, employee, time, channel — no text), visible only to
                      the designated recipient(s) named in the workplace harassment policy
  mental_health     → immediate human + helpline text (+ confidential ticket)
  harassment, medical, grievance, disciplinary, whistleblowing, legal, salary_dispute
                    → helpdesk ticket in the matching category, transcript attached, SLA by class
  other_person_data → refuse + explain (no ticket)

The free text of a hard-routed turn is NEVER written to the general transcript store
(desk_turns keeps class + timestamp only).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# order = priority when several classes match (self-harm first, sexual harassment before generic harassment)
CLASSES = ("mental_health", "sexual_harassment", "harassment", "medical", "whistleblowing", "legal", "disciplinary",
           "grievance", "other_person_data", "salary_dispute")

_W = r"(?<![a-z0-9])"   # soft word boundaries that work for words glued to punctuation
_E = r"(?![a-z0-9])"


def _rx(*alts: str) -> re.Pattern:
    return re.compile(_W + "(?:" + "|".join(alts) + ")" + _E, re.IGNORECASE)


PATTERNS: dict[str, list[re.Pattern]] = {
    "mental_health": [
        _rx(r"suicid\w*", r"kill myself", r"end (?:my|it all|everything)", r"self[- ]?harm", r"hurt myself", r"want to die",
            r"don'?t want to live", r"no reason to live", r"cut myself", r"overdose", r"jump off",
            r"me suicider", r"envie de mourir", r"me faire du mal", r"en finir"),
        _rx(r"(?:severe|extreme) (?:depression|anxiety|panic)", r"breakdown", r"can'?t cope", r"hopeless", r"depress(?:ed|ion)",
            r"panic attack", r"mental health", r"therapist", r"counsel(?:l)?ing", r"burn(?:ed|t)? out", r"burnout",
            r"sant[ée] mentale", r"d[ée]pression", r"[ée]puisement professionnel"),
    ],
    "sexual_harassment": [
        _rx(r"sexual(?:ly)? (?:harass\w*|assault\w*|abus\w*|advanc\w*|favou?r\w*|remark\w*|comment\w*|joke\w*)",
            r"molest\w*", r"grop\w*", r"inappropriate(?:ly)? touch\w*", r"touched me", r"touching me",
            r"unwanted (?:touch\w*|advanc\w*|attention|messag\w*|hug\w*)", r"lewd", r"obscene (?:messag\w*|pictur\w*|video\w*|gestur\w*)",
            r"sent me (?:nude|porn|dirty|vulgar)", r"ask(?:ed|ing)? (?:me )?for (?:sexual )?favou?rs?", r"quid pro quo",
            r"stalk\w*", r"staring at my", r"comments? (?:on|about) my body", r"sexual",
            r"harc[eè]lement sexuel", r"attouchement\w*", r"agression sexuelle"),
    ],
    "harassment": [
        _rx(r"harass\w*", r"bull(?:y|ied|ying)", r"discriminat\w*", r"humiliat\w*", r"intimidat\w*", r"abus(?:ed|ive|ing)",
            r"shout(?:s|ed|ing) at me", r"threaten\w*", r"victimi[sz]\w*", r"racis[tm]", r"sexis[tm]", r"homophob\w*", r"transphob\w*",
            r"because (?:i am|i'?m|of my) (?:a )?(?:woman|female|pregnant|muslim|jewish|christian|sikh|indigenous|black|gay|lesbian|trans\w*|disab\w*|old\w*|religion|gender|race)",
            r"hostile", r"hostile work environment", r"target(?:ed|ing) me", r"toxic (?:manager|boss|team)", r"workplace violence",
            r"harc[eè]lement", r"intimidation", r"discrimination"),
    ],
    "medical": [
        _rx(r"disabilit\w*", r"accommodat\w*", r"reasonable adjustment\w*", r"wheelchair", r"chronic (?:illness|condition|pain)",
            r"medical (?:condition|certificate|note|leave for|ground\w*|reason\w*)", r"doctor (?:said|advised|advice|has asked)", r"diagnos\w*",
            r"cancer", r"surgery", r"chemo\w*", r"dialysis", r"epilep\w*", r"pregnan\w*", r"miscarriage", r"fertility treatment",
            r"ivf", r"hiv", r"mental illness", r"bipolar", r"schizo\w*", r"autis\w*", r"adhd", r"work from home (?:for|on|due to) (?:medical|health)",
            r"short[- ]term disability", r"long[- ]term disability", r"wsib claim", r"workers'? comp\w*",
            r"maladie", r"invalidit[ée]", r"enceinte", r"accommodement"),
    ],
    "whistleblowing": [
        _rx(r"whistle[- ]?blow\w*", r"fraud\w*", r"embezzl\w*", r"bribe\w*", r"kickback\w*", r"corrupt\w*", r"money laundering",
            r"fake (?:bills?|invoices?|receipts?|timesheets?)", r"forg(?:ed|ery|ing)", r"misappropriat\w*", r"insider trading", r"report (?:a )?(?:misconduct|wrongdoing|violation)",
            r"fraude", r"pot-de-vin", r"d[ée]tournement"),
    ],
    "legal": [
        _rx(r"lawyer", r"legal (?:action|notice|case|proceedings?)", r"sue", r"suing", r"court", r"labou?r board",
            r"employment standards (?:claim|complaint|officer)", r"human rights (?:complaint|tribunal|commission)", r"file (?:a )?(?:claim|case|complaint with the police)",
            r"police", r"tribunal", r"wrongful (?:termination|dismissal)", r"constructive dismissal",
            r"avocat", r"poursuite", r"tribunal administratif", r"cnesst"),
    ],
    "disciplinary": [
        _rx(r"terminat\w*", r"fired", r"firing", r"dismiss\w*", r"\bpip\b", r"performance improvement plan",
            r"warning letter", r"written warning", r"disciplinary", r"suspend(?:ed|sion)", r"investigation (?:against|into) me",
            r"forced (?:to )?resign\w*", r"asked to resign", r"laid off", r"lay[- ]?off", r"let go",
            r"cong[ée]di[ée]", r"mise? [àa] pied", r"avertissement"),
    ],
    "grievance": [
        _rx(r"complain(?:t|ts|ing)? (?:about|against) (?:my |the )?(?:manager|boss|lead|colleague|co-?worker|supervisor|hr|team ?lead|[A-Z][a-z]+)",
            r"(?:my |the )?(?:manager|boss|lead|supervisor|colleague|co-?worker) (?:is|has been|keeps|always|never) (?:\w+ ){0,3}(?:rude|unfair|biased|partial|ignor\w*|insult\w*|shout\w*|mistreat\w*|harass\w*|misbehav\w*)",
            r"grievance", r"unfair(?:ly)? treat\w*", r"favou?ritism", r"misbehav\w*", r"misconduct", r"raise (?:a )?(?:formal )?complaint",
            r"plainte", r"grief"),
    ],
    "other_person_data": [
        _rx(r"(?:salary|pay|pay ?stub|payslip|sin|social insurance|bank|address|phone|dob|date of birth|leave balance|attendance|appraisal|rating|medical|performance|raise|t4)\w* (?:of|for) (?:my )?(?:colleague|co-?worker|manager|boss|team ?mate|friend|him|her|them|[A-Z][a-z]+(?: [A-Z][a-z]+)?)",
            r"(?:colleague|co-?worker|manager|boss|team ?mate|friend|his|her|their|[A-Z][a-z]+'s) (?:salary|pay|pay ?stub|payslip|sin|social insurance|bank|address|phone|dob|date of birth|leave balance|attendance|appraisal|rating|medical|raise|t4)",
            r"how much (?:does|do) (?:my )?(?:colleague|co-?worker|manager|boss|team ?mate|friend|[A-Z][a-z]+) (?:earn|get|make)",
            r"(?:someone else|another employee|other employee|other person)'?s? (?:salary|pay|data|record|details|pay ?stub|payslip|leave)",
            r"(?:le )?salaire (?:de|d')(?:mon|ma|un|une) (?:coll[èe]gue|gestionnaire)"),
    ],
    "salary_dispute": [
        _rx(r"(?:wrong|incorrect|short|less|missing|not (?:paid|deposited|received)|underpaid|unpaid|cheat\w*|dispute\w*|mistake|error).{0,40}(?:salary|pay|wages?|overtime|vacation pay|stat pay|bonus|raise|final pay|retro\w*)",
            r"(?:salary|pay|wages?|overtime|vacation pay|stat pay|bonus|raise|final pay|retro\w*).{0,40}(?:wrong|incorrect|short|less|missing|not (?:paid|deposited|received)|underpaid|unpaid|dispute\w*|mistake|error|cheat\w*)",
            r"(?:salaire|paie).{0,30}(?:erreur|manquant|pas (?:re[çc]u|pay[ée]))"),
    ],
}

# "why is my salary less this month" is a payslip-explain question, NOT a dispute: the engine answers
# with the deterministic diff and only disputes what it cannot reconcile. These phrasings are exempt.
_EXPLAIN_NOT_DISPUTE = re.compile(r"\b(why|explain|understand|how|what changed|difference|compare|pourquoi|expliqu\w*)\b", re.IGNORECASE)
# a request for a human / HR
_FILLER = {"want", "wanna", "would", "like", "need", "please", "can", "could", "you", "the", "talk", "speak", "with", "person", "human",
           "someone", "somebody", "real", "agent", "people", "connect", "transfer", "get", "call", "contact", "reach", "now",
           "ticket", "raise", "create", "open", "parler", "personne", "quelqu", "une", "des", "avec", "veux", "voudrais", "aimerais",
           "team", "just", "hello", "help", "for", "and", "this", "that", "about", "from", "here", "there", "okay", "thanks", "thank"}


def bare_human_request(text: str) -> bool:
    """"I want to talk to a person" with nothing else of substance — go straight to the hand-off, no clause lookup
    (phase-3 e2e: BM25 matched 'person' / 'colleague' in the harassment clause and quoted the complaint procedure back)."""
    if not _HUMAN.search(text or ""):
        return False
    words = [w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _FILLER and w not in ("manager",)]
    return not words


_HUMAN = re.compile(r"\b(talk to|speak to|speak with|connect me|transfer me|want|need|get me|call|contact|reach|raise it with|escalate)\b.{0,30}\b"
                    r"(human|person|someone|real person|hr|hr team|agent|people|somebody|manager)\b"
                    r"|\b(human|hr|person|agent)\b.{0,12}\b(please|now)\b"
                    r"|\bparler (?:[àa]|avec) (?:quelqu'un|une personne|les rh|rh)\b"
                    r"|\bticket (?:raise|create|open)\b|\braise (?:a )?ticket\b",
                    re.IGNORECASE)


@dataclass
class RouteDecision:
    hard: bool
    klass: str | None = None
    matched: list[str] = field(default_factory=list)
    wants_human: bool = False
    about_other_person: bool = False
    note: str = ""


def _hits(klass: str, text: str) -> list[str]:
    out = []
    for rx in PATTERNS[klass]:
        for m in rx.finditer(text):
            out.append(m.group(0))
    return out


def classify(text: str) -> RouteDecision:
    """Deterministic: regex lists only, priority order; never a model."""
    t = (text or "").strip()
    if not t:
        return RouteDecision(hard=False)
    low = t.lower()
    wants_human = bool(_HUMAN.search(t))
    other = bool(_hits("other_person_data", t))
    for klass in CLASSES:
        hits = _hits(klass, t)
        if not hits:
            continue
        if klass == "salary_dispute" and _EXPLAIN_NOT_DISPUTE.search(low) and not re.search(r"\b(wrong|cheat|dispute|erreur)\b", low):
            continue                                         # an explain question — the payslip diff answers it
        if klass == "other_person_data":
            return RouteDecision(hard=True, klass=klass, matched=hits, wants_human=wants_human, about_other_person=True)
        return RouteDecision(hard=True, klass=klass, matched=hits, wants_human=wants_human, about_other_person=other)
    return RouteDecision(hard=False, wants_human=wants_human, about_other_person=other)


# ── fixed, non-generative templates ────────────────────────────────────────────

DEFAULT_TEMPLATES: dict[str, str] = {
    "sexual_harassment": (
        "I'm sorry you are dealing with this. Sexual harassment complaints go to the person named in the company's workplace "
        "harassment policy to receive them — not to this assistant, and never to the person you are complaining about.\n\n"
        "• Designated recipient: {presiding_officer_name}{presiding_officer_contact}\n"
        "• You can report verbally or in writing; the policy explains how it is investigated and how you are protected from reprisal.\n"
        "• Information is kept confidential except where it is needed to investigate, to take corrective action or as the law requires.\n"
        "• You may also contact your provincial human rights body or the employment standards / health and safety regulator.\n\n"
        "I have told the designated recipient that someone wishes to reach them (only that a request was made, and when). "
        "Nothing you wrote here is stored in the general conversation record, and HR or your manager cannot see this exchange."
    ),
    "mental_health": (
        "Thank you for telling me — you don't have to handle this alone. If you are in immediate danger, please call 911.\n\n"
        "• {helpline_name}: {helpline_number}\n"
        "• A person from HR will reach out to you confidentially today.\n\n"
        "This conversation is not stored in the general record; only that a confidential request was made has been passed on."
    ),
    "harassment": (
        "What you describe should be looked at by a person, not an assistant. I have opened a confidential grievance ticket for HR with "
        "what you wrote; a named HR person will contact you within {sla_hours} hours. If any part of it is sexual in nature, you can also "
        "go directly to the designated recipient in the workplace harassment policy (ask me 'sexual harassment complaint' for the contact)."
    ),
    "medical": (
        "Medical and disability matters need a person who can look at your situation properly. I have opened a confidential ticket for HR; "
        "someone will contact you within {sla_hours} hours to discuss accommodation, leave or support options. I will not record medical details here."
    ),
    "grievance": (
        "A complaint about a specific person has to be handled by HR, not an assistant. I have opened a confidential grievance ticket with "
        "what you wrote; HR will respond within {sla_hours} hours. You can add detail or documents to the ticket at any time."
    ),
    "disciplinary": (
        "Questions about termination, performance improvement plans or disciplinary steps need a person from HR. I have opened a confidential "
        "ticket with your message; HR will respond within {sla_hours} hours. You may also want to read the company's disciplinary policy (ask me "
        "'show the disciplinary policy')."
    ),
    "whistleblowing": (
        "Thank you for raising this. Reports of fraud or misconduct are handled under the company's whistle-blower process by a designated person, "
        "and your identity is protected. I have opened a confidential ticket; the designated person will respond within {sla_hours} hours. "
        "I have not recorded the details anywhere else."
    ),
    "legal": (
        "I can't advise on legal matters. I have opened a confidential ticket so a person from HR can respond within {sla_hours} hours. "
        "You are free to seek independent legal advice at any time."
    ),
    "salary_dispute": (
        "I can explain how your pay was worked out, but a figure you believe is wrong must be checked by the payroll team. I have opened a payroll "
        "ticket with your message and the pay stub reference; payroll will respond within {sla_hours} hours (before the next payroll cut-off where possible)."
    ),
    "other_person_data": (
        "I can only show you your own records. Another employee's pay, leave, attendance or personal details are not available through this "
        "assistant to anyone, whatever their role. If you need something about a team member for a legitimate reason, use the team screens you are "
        "already authorised for, or raise a request with HR."
    ),
}

# the helpdesk category each class lands in (engage DEFAULT_CATEGORIES keys; 'grievance' is confidential)
TICKET_CATEGORY: dict[str, str | None] = {
    "sexual_harassment": None, "mental_health": "grievance", "harassment": "grievance", "medical": "grievance", "grievance": "grievance",
    "disciplinary": "grievance", "whistleblowing": "grievance", "legal": "grievance", "salary_dispute": "payroll", "other_person_data": None,
}


def render_template(klass: str, cfg: dict) -> str:
    """Org override or default, with the designated-recipient / helpline / SLA placeholders filled. Never raises on a missing key."""
    text = ((cfg.get("templates") or {}).get(klass)) or DEFAULT_TEMPLATES.get(klass, "")
    ic = cfg.get("ic") or {}
    hl = cfg.get("helpline") or {}
    po_name = ic.get("presiding_officer_name") or "(not yet named — see the workplace harassment policy or ask HR)"
    po_contact = f" — {ic['presiding_officer_contact']}" if ic.get("presiding_officer_contact") else ""
    vals = {"presiding_officer_name": po_name, "presiding_officer_contact": po_contact,
            "helpline_name": hl.get("name") or "Talk Suicide Canada / 9-8-8", "helpline_number": hl.get("number") or "9-8-8 (call or text)",
            "sla_hours": (cfg.get("sla_hours") or {}).get(klass, 48)}
    try:
        return text.format(**vals)
    except (KeyError, IndexError, ValueError):
        return text
