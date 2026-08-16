#!/usr/bin/env python3
"""
Tests for the deterministic half of the Pre-Consensus Radar.

Nothing here touches the network. These assert that the classifier maps real
announcement language to the right framework parameter, that routine filings
are rejected as noise, and that the convergence rule, decay curve and mandate
filter behave exactly as the framework specifies.

    python test_pcr.py
"""

import sys
from datetime import date, timedelta

import pcr_collect as C

P, F = 0, 0


def ok(cond, what):
    global P, F
    if cond:
        P += 1
    else:
        F += 1
        print(f"  FAIL  {what}")


def params(text):
    return {p for _, p, _, _ in C.classify(text)}


# -------------------------------------------------------------- classifier +
CASES_POSITIVE = [
    ("Intimation of grant of Environmental Clearance for expansion of manufacturing "
     "capacity from 62,000 TPA to 104,000 TPA", "A2"),
    ("Board approves capacity expansion at Unit II with capital expenditure of Rs 210 crore", "A1"),
    ("Commencement of commercial production at the new Halol facility", "A5"),
    ("Acquisition of land parcel admeasuring 24 acres for the proposed plant", "A4"),
    ("Allotment of equity shares on preferential basis to strategic investor", "D3"),
    ("Company bags an order worth Rs 148 crore from a leading OEM", "B2"),
    ("Receipt of Letter of Award from Ministry of Railways", "B4"),
    ("Receipt of first export order from a customer in Germany", "B5"),
    ("Order book stands at Rs 1,240 crore as on 30 June 2026", "B1"),
    ("USFDA completes inspection of the Unit III facility with zero observations", "C1"),
    ("Grant of BIS licence for the new product category", "C2"),
    ("Approval under the Production Linked Incentive scheme", "C3"),
    ("Patent granted by the Indian Patent Office for the process", "C4"),
    ("Disclosure under SAST Regulation 29 - acquisition of shares crossed 5%", "D4"),
    ("Promoter group has acquired 1,20,000 equity shares from the open market", "D1"),
    ("Revocation of pledge of shares held by the promoters", "D2"),
    ("Appointment of Chief Financial Officer with effect from 1 September", "F1"),
    ("Incorporation of a wholly-owned subsidiary for the electronics business", "G1"),
    ("Government notifies Quality Control Order for the product category", "H1"),
    ("QIP of Rs 300 crore to fund the expansion programme", "A6"),
]

for text, want in CASES_POSITIVE:
    ok(want in params(text), f"{want} should match: {text[:58]}")

# -------------------------------------------------------------- classifier -
CASES_NOISE = [
    "Closure of trading window for the quarter ended 30 June 2026",
    "Newspaper publication of the unaudited financial results",
    "Intimation of loss of share certificate",
    "Certificate under Regulation 74(5) of SEBI (Depositories) Regulations",
    "Reconciliation of Share Capital Audit Report",
    "Notice of Annual General Meeting and record date",
    "Submission of compliance certificate under Regulation 7(3)",
    "Voting results of the postal ballot and scrutinizer report",
    "Analyst / Institutional Investor Meet intimation",
]
for text in CASES_NOISE:
    ok(C.classify(text) == [], f"should be noise: {text[:52]}")

ok(C.classify("") == [], "empty text is noise")
ok(C.classify(None) == [], "None text is noise")

# a real filing often fires two families at once - that is the point
multi = params("Board approved capacity expansion and a preferential allotment to fund it")
ok("A1" in multi and "D3" in multi, "one filing can fire two families")

# ------------------------------------------- regressions from the live 16 Aug
# 2026 run. These are VERBATIM strings the collector pulled off NSE. Four of
# the five candidates that run produced were the same defect: a capital-raising
# filing counted once as funding (A6) and again as ownership (D3), so a single
# fact looked like two families converging.

LIVE_RATNAVEER = ("Monitoring Agency Report Monitoring Agency Report for the quarter "
                  "ended 30th June, 2026 in respect of utilization of proceeds")
ok(C.classify(LIVE_RATNAVEER) == [],
   "routine monitoring agency report is noise, not a signal")

LIVE_GRETEX = ("Allotment of Securities Gretex Corporate Services Limited has informed "
               "the Exchange regarding allotment of 1201000 securities")
g = params(LIVE_GRETEX)
ok("A6" not in g, "a bare allotment is NOT a capex signal")

LIVE_SHALIMAR = ("Qualified Institutional Placement Shalimar Paints Limited has informed "
                 "the Exchange about qualified Institutional Placement")
s_ = params(LIVE_SHALIMAR)
ok("A6" not in s_, "a bare QIP does not fire the capex family")
ok("D3" in s_, "a bare QIP is still an ownership fact")

WITH_CONTEXT = "Board approves QIP of Rs 300 crore to fund the capacity expansion programme"
wc = params(WITH_CONTEXT)
ok("A6" in wc and "D3" in wc,
   "a fundraise tied to expansion legitimately fires both families")

LIVE_JUNIPER_ORDER = ("Bagging/Receiving of orders/contracts Juniper Green Energy Limited "
                      "has informed the Exchange about Receipt of Letter of Award")
LIVE_JUNIPER_COD = ("Commencement of commercial production/operations Juniper Green Energy "
                    "Limited has informed the Exchange about Commencement")
ok("B4" in params(LIVE_JUNIPER_ORDER), "a letter of award is a tender-award signal")
ok("A5" in params(LIVE_JUNIPER_COD), "commencement of commercial production is Clock 3")

# the whole point: the good candidate survives, the double-counted ones do not
noisy = C.dedupe(C.to_events([
    {"company": "XYZ Engineering Ltd", "text": LIVE_GRETEX, "date": "2026-08-14",
     "link": "u", "source": "NSE announcement", "sweep": 1, "grade": "A"},
    {"company": "XYZ Engineering Ltd", "text": LIVE_SHALIMAR, "date": "2026-08-10",
     "link": "v", "source": "NSE announcement", "sweep": 1, "grade": "A"}],
    {"XYZENG": {"name": "XYZ Engineering Ltd"}}))
ok(C.find_convergence(noisy, date(2026, 8, 16)) == [],
   "two financing filings alone no longer fake a convergence")

clean = C.dedupe(C.to_events([
    {"company": "XYZ Engineering Ltd", "text": LIVE_JUNIPER_ORDER, "date": "2026-08-15",
     "link": "u", "source": "NSE announcement", "sweep": 1, "grade": "A"},
    {"company": "XYZ Engineering Ltd", "text": LIVE_JUNIPER_COD, "date": "2026-08-12",
     "link": "v", "source": "NSE announcement", "sweep": 1, "grade": "A"}],
    {"XYZENG": {"name": "XYZ Engineering Ltd"}}))
ok(len(C.find_convergence(clean, date(2026, 8, 16))) == 1,
   "an order win plus a plant going live still converges correctly")

# -------------------------------------------------------------- name matching
uni = {"ABCIND": {"name": "ABC Industries Limited"},
       "XYZENG": {"name": "XYZ Engineering Ltd"}}
ok(C.match_company("ABC Industries Ltd.", uni) == "ABCIND", "name match ignores Ltd/punctuation")
ok(C.match_company("ABC  INDUSTRIES  LIMITED", uni) == "ABCIND", "name match ignores case/spacing")
ok(C.match_company("Totally Unknown Corp", uni) is None, "unknown name returns None, never a guess")
ok(C.match_company(None, uni) is None, "None name returns None")

# -------------------------------------------------------------- mandate
u = {"A": {"mcap_cr": 900}, "B": {"mcap_cr": 120}, "C": {"mcap_cr": 41000}, "D": {}}
ok(C.in_mandate("A", u) is True, "900 Cr is inside the mandate")
ok(C.in_mandate("B", u) is False, "120 Cr is below the mandate")
ok(C.in_mandate("C", u) is False, "41,000 Cr is above the mandate")
ok(C.in_mandate("D", u) is True, "unknown mcap is surfaced for review, not dropped")

# -------------------------------------------------------------- decay
ok(C.decay_weight("E", 0) == 1.0, "a fresh signal carries full weight")
ok(abs(C.decay_weight("E", 45) - 0.5) < 1e-9, "microstructure halves in 45 days")
ok(abs(C.decay_weight("A", 540) - 0.5) < 1e-9, "capex halves in 540 days")
ok(C.decay_weight("A", 45) > C.decay_weight("E", 45), "capex decays slower than microstructure")
ok(C.decay_weight("B", 360) < C.decay_weight("B", 180), "decay is monotonic")

# -------------------------------------------------------------- dedupe
dup = [{"symbol": "X", "param": "A1", "date": "2026-08-10", "company": "X Ltd"},
       {"symbol": "X", "param": "A1", "date": "2026-08-10", "company": "X Ltd"},
       {"symbol": "X", "param": "A1", "date": "2026-08-11", "company": "X Ltd"}]
ok(len(C.dedupe(dup)) == 2, "same param on the same day dedupes; a new day does not")

# -------------------------------------------------------------- convergence
TODAY = date(2026, 8, 16)


def ev(sym, fam, param, days_ago, grade="A", clock=2):
    return {"symbol": sym, "company": sym, "family": fam, "param": param,
            "date": (TODAY - timedelta(days=days_ago)).isoformat(),
            "grade": grade, "clock": clock}


one_family = [ev("P", "A", "A1", 3), ev("P", "A", "A2", 5)]
ok(C.find_convergence(one_family, TODAY) == [], "one family alone never converges")

no_physical = [ev("Q", "D", "D1", 3), ev("Q", "F", "F1", 5)]
ok(C.find_convergence(no_physical, TODAY) == [],
   "two soft families without A/B/C do not converge")

good = [ev("R", "A", "A2", 3), ev("R", "D", "D1", 20)]
res = C.find_convergence(good, TODAY)
ok(len(res) == 1 and res[0]["family_count"] == 2, "capex + ownership converges")

stale = [ev("S", "A", "A2", 200), ev("S", "D", "D1", 10)]
ok(C.find_convergence(stale, TODAY) == [],
   "a signal older than the 90-day window drops out of convergence")

graded = [ev("T", "A", "A2", 2, grade="C"), ev("T", "B", "B2", 4, grade="A")]
ok(C.find_convergence(graded, TODAY)[0]["best_grade"] == "A", "best evidence grade wins")

clocks = [ev("U", "A", "A2", 2, clock=3), ev("U", "B", "B2", 4, clock=1)]
ok(C.find_convergence(clocks, TODAY)[0]["earliest_clock"] == 1, "earliest clock is reported")

# independence: two families from ONE filing is one fact, not a convergence
same_item = [dict(ev("Y", "A", "A6", 2), item_id="one"),
             dict(ev("Y", "D", "D3", 2), item_id="one")]
ok(C.find_convergence(same_item, TODAY) == [],
   "one filing firing two families does not fake convergence")
two_items = [dict(ev("Z", "A", "A6", 2), item_id="one"),
             dict(ev("Z", "D", "D3", 9), item_id="two")]
ok(len(C.find_convergence(two_items, TODAY)) == 1,
   "the same two families from two separate filings do converge")

ranked = C.find_convergence(
    [ev("V", "A", "A1", 1), ev("V", "B", "B2", 2), ev("V", "D", "D1", 3),
     ev("W", "A", "A1", 1), ev("W", "D", "D1", 2)], TODAY)
ok(ranked[0]["key"] == "V", "more families fired ranks higher")

ok(all("events" in c for c in ranked), "every candidate carries its evidence trail")
ok(C.find_convergence([], TODAY) == [], "no events means no candidates")

# -------------------------------------------------------------- end to end
raw = [{"company": "ABC Industries Limited",
        "text": "Grant of Environmental Clearance for capacity expansion",
        "date": TODAY.isoformat(), "link": "http://x", "source": "BSE announcement",
        "sweep": 1, "grade": "A"},
       {"company": "ABC Industries Limited",
        "text": "Promoter group has acquired 50,000 equity shares from the open market",
        "date": TODAY.isoformat(), "link": "http://y", "source": "NSE insider (PIT)",
        "sweep": 7, "grade": "A"},
       {"company": "ABC Industries Limited",
        "text": "Closure of trading window",
        "date": TODAY.isoformat(), "link": "http://z", "source": "BSE announcement",
        "sweep": 1, "grade": "A"}]
evs = C.dedupe(C.to_events(raw, uni))
ok(all(e["symbol"] == "ABCIND" for e in evs), "events attach to the matched symbol")
ok(not any("trading window" in e["text"].lower() for e in evs), "noise never becomes an event")
cands = C.find_convergence(evs, TODAY)
ok(len(cands) == 1 and set(cands[0]["families_fired"]) >= {"A", "D"},
   "end to end: two sweeps converge into one candidate")
ok(all(e["link"] for e in cands[0]["events"]), "every event keeps its source link")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
