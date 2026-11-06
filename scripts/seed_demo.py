"""Lexora HRM — local demo: a 14-person company across ON/BC/AB, mixed pay
frequencies, run through the real net-pay engine. Prints a payroll register +
a CRA remittance preview + a full-year YTD cap check.

Run:  PYTHONPATH="backend:backend/services/payroll" python scripts/seed_demo.py
"""
import sys

sys.path.insert(0, "backend")
sys.path.insert(0, "backend/services/payroll")

from calc import calculate_paystub  # noqa: E402
from shared.utils.enums import periods_per_year  # noqa: E402

# name, province, pay_frequency, pay_type, annual_salary  (hourly -> annualized)
DEMO = [
    ("Aisha Khan",        "ON", "biweekly",     "salary",  92000),
    ("Marc Tremblay",     "ON", "biweekly",     "salary",  61000),
    ("Priya Nair",        "ON", "semi_monthly", "salary", 118000),   # crosses CPP2
    ("Jordan Lee",        "BC", "biweekly",     "salary",  78000),
    ("Sofia Rossi",       "BC", "weekly",       "hourly",  32.50 * 40 * 52),
    ("Daniel Okafor",     "BC", "monthly",      "salary", 135000),   # high earner
    ("Emily Chen",        "AB", "biweekly",     "salary",  71000),
    ("Liam Murphy",       "AB", "semi_monthly", "salary",  54000),
    ("Fatima Ali",        "AB", "weekly",       "hourly",  27.00 * 40 * 52),
    ("Noah Williams",     "ON", "biweekly",     "salary",  48000),
    ("Grace Park",        "ON", "monthly",      "salary",  99000),
    ("Hassan Malik",      "BC", "biweekly",     "salary",  67500),
    ("Olivia Brown",      "AB", "biweekly",     "salary",  83000),
    ("Wei Zhang",         "ON", "semi_monthly", "salary", 145000),   # high earner
]


def money(x):
    return f"${x:,.2f}"


print("=" * 96)
print("  NORTHBRIDGE LOGISTICS INC.  —  Lexora HRM payroll register (one period, 2026)")
print("=" * 96)
print(f"  {'Employee':18}{'Prov':5}{'Frequency':13}{'Gross':>11}{'CPP':>9}{'CPP2':>7}"
      f"{'EI':>8}{'Tax':>10}{'Net':>11}")
print("  " + "-" * 92)

tot = {"gross": 0, "cpp": 0, "cpp2": 0, "ei": 0, "tax": 0, "net": 0, "er_cpp": 0, "er_ei": 0}
for name, prov, freq, ptype, annual in DEMO:
    per = periods_per_year(freq)
    gross = round(annual / per, 2)
    r = calculate_paystub(gross, freq, prov)
    d, e = r["deductions"], r["employer_cost"]
    print(f"  {name:18}{prov:5}{freq:13}{money(gross):>11}{money(d['cpp']):>9}"
          f"{money(d['cpp2']):>7}{money(d['ei']):>8}{money(d['income_tax']):>10}"
          f"{money(r['net_pay']):>11}")
    tot["gross"] += gross; tot["cpp"] += d["cpp"]; tot["cpp2"] += d["cpp2"]
    tot["ei"] += d["ei"]; tot["tax"] += d["income_tax"]; tot["net"] += r["net_pay"]
    tot["er_cpp"] += e["cpp"] + e["cpp2"]; tot["er_ei"] += e["ei"]

print("  " + "-" * 92)
print(f"  {'TOTALS':18}{'':5}{'':13}{money(tot['gross']):>11}{money(tot['cpp']):>9}"
      f"{money(tot['cpp2']):>7}{money(tot['ei']):>8}{money(tot['tax']):>10}{money(tot['net']):>11}")

# CRA remittance = income tax + CPP(ee+er) + EI(ee+er)
remit = tot["tax"] + tot["cpp"] * 2 + tot["cpp2"] * 2 + tot["ei"] + tot["er_ei"]
print("\n  CRA source-deduction remittance for this run (income tax + CPP ee+er + EI ee+er):")
print(f"    income tax {money(tot['tax'])}  |  CPP+CPP2 (ee+er) {money((tot['cpp']+tot['cpp2'])*2)}"
      f"  |  EI (ee+er) {money(tot['ei']+tot['er_ei'])}")
print(f"    >>> TOTAL TO REMIT TO CRA: {money(remit)}")

# Full-year YTD cap check on the CPP2 earner
print("\n  YTD cap check — Wei Zhang ($145k, semi-monthly, ON), 24 periods:")
ytd = None
gp = round(145000 / 24, 2)
for _ in range(24):
    rr = calculate_paystub(gp, "semi_monthly", "ON", ytd); ytd = rr["ytd"]
print(f"    CPP {money(ytd['cpp'])} (cap $4,230.45) | CPP2 {money(ytd['cpp2'])} (cap $416.00) "
      f"| EI {money(ytd['ei'])} (cap $1,123.07)")
print("=" * 96)
