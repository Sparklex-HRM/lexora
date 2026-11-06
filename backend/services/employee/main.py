"""Lexora HRM — Employee Service."""
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

sys.path.append("/app")
from database import init_db  # noqa: E402
import models  # noqa: E402,F401  (registers employees + data_protection_state on Base)
import masters_models  # noqa: E402,F401  (registers master tables on Base)
from masters_routers import router as masters_router  # noqa: E402
from routers import router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from database import engine as _engine
    # D1 DDL runs FIRST (own short transactions, lock_timeout) — before any init opens a read session that
    # could hold ACCESS SHARE on employees and make an ALTER wait forever.
    await _cf_init(_engine)   # D1: cf_definitions + custom_data JSONB on employees / hire_candidates / employee_assets
    await hiring_d2_init(_engine)   # D2: hire_documents.file_path (ADD COLUMN IF NOT EXISTS) — DDL before any read session
    await lifecycle_b_init()   # Stream B: extra columns, opening salary rows, due career changes, daily tick
    await lifecycle_c_init()   # Stream C: exit tables + the last-working-day tick
    yield


app = FastAPI(title="Lexora HRM — Employee Service", version="0.2.0", lifespan=lifespan)
from custom_fields_routers import router as custom_fields_router, init_custom_fields as _cf_init  # noqa: E402  (D1 — before /employees/{id})
app.include_router(custom_fields_router)
# ── 2026-10-16 employee portal batch 3: /employees/ess/* — BEFORE routers.router (/employees/{id} catch-all) ──
import ess_requests_models  # noqa: E402,F401  (ess_request_types / ess_requests / ess_todos on Base before init_db)
import ess_docs_models  # noqa: E402,F401  (emp_policy_docs / emp_policy_acks on Base before init_db)
from ess_requests_routers import router as ess_requests_router  # noqa: E402
from ess_docs_routers import router as ess_docs_router, summary_router as ess_docs_summary_router  # noqa: E402
app.include_router(ess_requests_router)
app.include_router(ess_docs_router)
app.include_router(ess_docs_summary_router)
# 2026-10-20 global search: GET /employees/search — BEFORE routers.router (/employees/{id} catch-all)
from search_routes import router as search_router  # noqa: E402
app.include_router(search_router)
# 2026-10-22 fixed-term / contract employees: /employees/fixed-term* — BEFORE routers.router (/employees/{id} catch-all)
import contracts_models  # noqa: E402,F401  (employee_contract_terms on Base before init_db)
from contracts_routers import router as contracts_router  # noqa: E402
app.include_router(contracts_router)
app.include_router(router)
app.include_router(masters_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "employee"}

# ── Stream G6: Canada migration kit — bulk employee upsert/restore + salary history ──
from import_canada.router import router as import_canada_router  # noqa: E402
app.include_router(import_canada_router)

# ── Stream B (lifecycle): salary history · career changes + probation · the letter engine ──
from comp_routers import router as comp_router  # noqa: E402
from career_routers import router as career_router, lifecycle_init as lifecycle_b_init  # noqa: E402
from letters_routers import router as letters_router  # noqa: E402
app.include_router(comp_router)
app.include_router(career_router)
app.include_router(letters_router)

# ── Stream D (lifecycle): hiring (requisition → offer → onboarding) · performance (goals → appraisal → increment proposal) ──
import hiring_models  # noqa: E402,F401  (new hire_* tables on Base)
import performance_models  # noqa: E402,F401  (new perf_* tables on Base)
from hiring_routers import router as hiring_router  # noqa: E402
from hiring_offers import router as hiring_offers_router  # noqa: E402
from hiring_public import router as hiring_public_router  # noqa: E402
from performance_routers import router as performance_router  # noqa: E402
# ── D2 (recruiting depth): careers page + job boards + scheduling + scorecards + offer chain + referrals + comms ──
import hiring_d2_models  # noqa: E402,F401  (new hire_* tables on Base)
from hiring_approvals import router as hiring_approvals_router  # noqa: E402
from hiring_boards import router as hiring_boards_router  # noqa: E402
from hiring_careers import hiring_d2_init, router as hiring_careers_router  # noqa: E402
from hiring_comms import router as hiring_comms_router  # noqa: E402
from hiring_referrals import router as hiring_referrals_router  # noqa: E402
from hiring_scheduling import router as hiring_scheduling_router  # noqa: E402
from hiring_scorecards import router as hiring_scorecards_router  # noqa: E402
app.include_router(hiring_careers_router)  # /hiring/public/careers/{org}/logo|feed + /interview/{token} BEFORE /careers/{org}/{posting}
app.include_router(hiring_public_router)   # /hiring/public/* first, so nothing shadows it
app.include_router(hiring_scheduling_router)   # static /hiring/interviews/send-reminders before /hiring/interviews/{iv_id}
app.include_router(hiring_boards_router)       # /hiring/postings/export.csv before /hiring/postings/{posting_id}
app.include_router(hiring_referrals_router)
app.include_router(hiring_scorecards_router)
app.include_router(hiring_approvals_router)
app.include_router(hiring_comms_router)
app.include_router(hiring_offers_router)
# ── G6 (2026-10-23): résumé parsing, JD drafts, advisory fit scores — /hiring/ai/* (before the generic hiring routes) ──
import hiring_ai_models  # noqa: E402,F401  (hire_candidate_profiles / hire_match_scores on Base)
from hiring_ai_routers import router as hiring_ai_router  # noqa: E402
app.include_router(hiring_ai_router)
app.include_router(hiring_router)
# ── G4 (2026-10-23): objectives & key results · 360 feedback · improvement plans · 1:1s — /performance/{okrs,feedback,pips,one-on-ones} ──
import perf_g4_models  # noqa: E402,F401  (new perf_okr_* / perf_fb_* / perf_pip_* / perf_one_* tables on Base)
from okr_routers import router as okr_router  # noqa: E402
from feedback360_routers import router as feedback360_router  # noqa: E402
from pip_routers import router as pip_router  # noqa: E402
from oneonone_routers import router as oneonone_router  # noqa: E402
app.include_router(okr_router)
app.include_router(feedback360_router)
app.include_router(pip_router)
app.include_router(oneonone_router)
app.include_router(performance_router)

# ── Stream D5 (parity): compensation planning cycles — /performance/comp/* (new comp_cycle_* tables) ──
import comp_cycle_models  # noqa: E402,F401  (registers comp_cycle_* tables on Base)
from comp_cycle_routers import router as comp_cycle_router  # noqa: E402
app.include_router(comp_cycle_router)

# ── Stream C (lifecycle): exit cases (resignation → notice → clearance → settlement → letters) · rehire ──
import exit_models  # noqa: E402,F401  (new exit_* / employee_assets / employment_spells tables on Base)
from exit_routers import router as exit_router  # noqa: E402
from exit_people import router as exit_people_router, exit_init as _exit_init  # noqa: E402
app.include_router(exit_people_router)   # static /exit/* paths before /exit/cases/{id}
app.include_router(exit_router)


async def lifecycle_c_init():
    from database import engine
    await _exit_init(engine)
    await _assets_init(engine)   # Stream G4: asset register columns on employee_assets + asset_events


# ── Stream G4: asset register (category, serial, warranty, assign/acknowledge/return, photos) — /assets/* ──
from assets_routers import router as assets_router, init_assets_schema as _assets_init  # noqa: E402
app.include_router(assets_router)

# ── Stream U1: Employee 360 — one aggregated read behind /employees/{id}/profile ──
from profile_routers import router as profile_router  # noqa: E402
app.include_router(profile_router)

# S3 production runtime: /health + /ready, JSON logs with request id, PII log filter, prod secret gate.
from shared.ops_runtime import install as _ops_install  # noqa: E402
_ops_install(app, "employee")
