#!/usr/bin/env python3
"""
Pre-Consensus Radar — collector.

Harvests the weekly sweeps, classifies every raw item into the signal families
of the framework, applies the convergence rule and the decay curve, and writes
data/signals.json for the Sunday Claude run to reason over.

Division of labour, deliberately:
  * This script COLLECTS, CLASSIFIES and FILTERS. It is deterministic and testable.
  * It does NOT decide. It never scores a company 0-100, never applies the
    obscurity multiplier, never writes a thesis. Reading what an announcement
    actually means is the judgment layer, and that runs in the Sunday session.

Usage:
    python pcr_collect.py --doctor          probe every source from this machine
    python pcr_collect.py --days 7          harvest the last 7 days
    python pcr_collect.py --days 7 --dry    harvest, print, write nothing
"""

import argparse
import csv
import io
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

import pcr_sources as S

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SIGNALS = os.path.join(DATA, "signals.json")
UNIVERSE = os.path.join(DATA, "universe.json")

MCAP_MIN, MCAP_MAX = 300.0, 25000.0        # Rs Cr, mainboard mandate
CONVERGENCE_WINDOW_DAYS = 90
PHYSICAL_FAMILIES = ("A", "B", "C")        # at least one must fire

HALF_LIFE = {"A": 540, "B": 180, "C": 365, "D": 270,
             "E": 45, "F": 365, "G": 540, "H": 365}

# --------------------------------------------------------------------------
# The classifier — raw announcement text to a framework parameter.
#
# Order matters: the first rule that matches wins, so the specific rules sit
# above the general ones. Every rule names the parameter it maps to, so an
# event can always be traced back to the framework that justified keeping it.
# --------------------------------------------------------------------------

R = re.compile

SIGNAL_RULES = [
    # ---- A. Capacity & capex footprint
    ("A", "A2", "Environmental clearance / consent to establish", 2,
     R(r"\b(environment(al)? clearance|consent to (establish|operate)|EC granted|"
       r"terms of reference|TOR granted|expert appraisal committee)\b", re.I)),
    ("A", "A1", "Board-approved capex / capacity expansion", 1,
     R(r"\b(capacity expansion|expansion of (its |the )?(manufacturing |production )?capacity|"
       r"brownfield|greenfield|debottleneck\w*|new (manufacturing )?(unit|plant|facility|line)|"
       r"setting up (of )?a? ?(new )?(plant|unit|facility)|capital expenditure|capex plan)\b", re.I)),
    ("A", "A5", "Commissioning / commencement of commercial production", 3,
     R(r"\b(commenc\w+ of commercial (production|operations)|commission(ed|ing) of|"
       r"trial (production|run)s? (commenc|start)|plant (is )?operational)\b", re.I)),
    ("A", "A4", "Land acquisition / state MoU", 1,
     R(r"\b(acquisition of land|land (parcel|purchase|allot\w+)|"
       r"(MoU|memorandum of understanding) with (the )?(govt|government|state)|"
       r"industrial development corporation)\b", re.I)),
    # A6 only counts when the money is tied to building something. A bare
    # QIP or warrant allotment is a financing fact, not a capex signal — it
    # belongs to family D and nowhere else. See EXPANSION_CONTEXT below.
    ("A", "A6", "Fundraise earmarked for expansion", 1,
     R(r"\b(qip|qualified institution\w* placement|preferential (issue|allotment)|"
       r"rights issue|fund ?rais\w+|term loan sanction\w*)\b", re.I)),

    # ---- B. Demand & order book
    ("B", "B4", "Government / PSU tender award", 3,
     R(r"\b(letter of award|LOA|work order|tender (award|win)|"
       r"awarded by (the )?(ministry|railway|NHAI|ordnance|defence|DRDO|BHEL|NTPC|"
       r"ONGC|IOCL|BPCL|HPCL|PowerGrid|BSNL))\b", re.I)),
    ("B", "B2", "Large single order", 3,
     R(r"\b(bags?|receiv\w+|secur\w+|win(s|ning)?|award\w*) (an? )?(new |large |repeat )?"
       r"(export )?(order|contract|purchase order)\b", re.I)),
    ("B", "B5", "First / new export order", 2,
     R(r"\b(first export|export order|entered? (the )?\w+ market|"
       r"export registration|new geograph\w+)\b", re.I)),
    ("B", "B1", "Order book update", 3,
     R(r"\border ?book\b", re.I)),

    # ---- C. Regulatory & IP unlocks
    ("C", "C1", "Regulatory approval / successful inspection", 3,
     R(r"\b(USFDA|US FDA|EU ?GMP|EDQM|WHO ?GMP|CEP|ANDA|DMF|CDSCO|"
       r"(establishment inspection report|EIR)|zero[- ]observation|"
       r"(approval|clearance) (from|by) (the )?(FDA|CDSCO|DCGI))\b", re.I)),
    ("C", "C2", "Standards licence / homologation", 2,
     R(r"\b(BIS (licen[cs]e|certification)|ARAI|homologation|type approval|"
       r"AERB|NABL accredit\w+|ISO ?\d{4,5} certif\w+)\b", re.I)),
    ("C", "C3", "PLI / scheme approval", 2,
     R(r"\b(production linked incentive|PLI scheme|scheme (approval|beneficiar\w+)|"
       r"incentive (approved|disbursed|sanctioned))\b", re.I)),
    ("C", "C4", "Patent granted / new trademark class", 1,
     R(r"\b(patent (granted|issued|awarded)|granted a patent|trademark (filed|registered))\b", re.I)),
    ("C", "C5", "New business licence", 2,
     R(r"\b(licen[cs]e (from|granted by) (the )?(RBI|IRDAI|SEBI|TRAI|DoT)|"
       r"mining lease|NBFC licen[cs]e)\b", re.I)),

    # ---- D. Ownership & smart money
    ("D", "D1", "Promoter open-market buying", 2,
     R(r"\b(promoter\w*|promoter group).{0,40}\b(acquir\w+|purchas\w+|bought|increas\w+ stake)\b", re.I)),
    ("D", "D2", "Promoter pledge release", 2,
     R(r"\b(revocation of pledge|release of pledge|pledge released|unpledg\w+)\b", re.I)),
    ("D", "D3", "Preferential allotment / QIP to a named investor", 2,
     R(r"\b(preferential (allotment|issue|basis)|allotment of (equity )?shares to|"
       r"qualified institution\w* placement|qip|"
       r"strategic investor|anchor investor)\b", re.I)),
    ("D", "D4", "Substantial acquisition / institutional entry", 2,
     R(r"\b(SAST|regulation 29|substantial acquisition|acquisition of shares|"
       r"crossed \d+(\.\d+)? ?%|stake of \d+(\.\d+)? ?%)\b", re.I)),

    # ---- F. People & organisation
    ("F", "F1", "Senior appointment", 1,
     R(r"\b(appoint\w+ (of|as) (the )?(chief executive|CEO|chief financial|CFO|"
       r"managing director|whole[- ]time director|chief operating|COO|chief technolog\w+|"
       r"plant head|president))\b", re.I)),
    ("F", "F3", "Auditor change / governance", 1,
     R(r"\b(appointment of (statutory |joint )?auditor|change (in|of) auditor)\b", re.I)),

    # ---- G. Structure & strategy
    ("G", "G1", "New subsidiary / JV / acquisition", 1,
     R(r"\b(incorporation of (a )?(wholly[- ]owned )?subsidiar\w+|new subsidiar\w+|"
       r"joint venture|JV agreement|acquisition of (a )?(company|business|stake in)|"
       r"scheme of (arrangement|amalgamation)|demerger)\b", re.I)),
    ("G", "G4", "Name / business change", 1,
     R(r"\b(change (of|in) name|change in (the )?(object|main object)|"
       r"alteration of (the )?memorandum)\b", re.I)),

    # ---- H. Industry & policy
    ("H", "H1", "Import substitution / duty action", 2,
     R(r"\b(quality control order|QCO|anti[- ]dumping|countervailing duty|"
       r"safeguard duty|customs duty (hike|increase|revision)|import restriction)\b", re.I)),
    ("H", "H2", "Scheme / budget allocation to a niche", 1,
     R(r"\b(cabinet approves|scheme (launched|notified)|outlay of (rs|₹)|"
       r"allocation of (rs|₹))\b", re.I)),
]

# A fundraise only counts as a capex signal (A6) when the filing ties the money
# to building something. Without these words it is a financing fact, which
# belongs to family D alone. Learned from the 16 Aug 2026 run, where bare QIP
# and warrant-allotment filings were firing A6 and D3 together and faking
# convergence on their own.
EXPANSION_CONTEXT = R(
    r"\b(expansion|expand\w*|capacity|capex|capital expenditure|greenfield|brownfield|"
    r"new (plant|unit|facility|line|project)|setting up|debottleneck\w*|"
    r"augment\w+|modernis\w+|moderniz\w+)\b", re.I)

# Items that superficially match but carry no information. Checked FIRST.
NOISE = R(
    r"\b(trading window|closure of trading window|newspaper (publication|advertisement)|"
    r"loss of share certificate|duplicate share certificate|investor (presentation|meet) "
    r"(intimation|schedule)|analyst(s)?[/ ]institutional investor meet|"
    r"compliance certificate|reg(ulation)? ?(74|40|7\(3\))|corrigendum|"
    r"postal ballot|annual general meeting|record date|book closure|"
    r"outcome of board meeting$|submission of|intimation (of|under) regulation|"
    r"disclosure under regulation 30 of|scrutinizer|voting results|"
    r"certificate under|reconciliation of share capital|"
    # added after the 16 Aug 2026 run — routine post-IPO and ESOP paperwork
    r"monitoring agency report|utili[sz]ation of (the )?(issue )?proceeds|"
    r"statement of deviation|allotment of (equity shares )?under (the )?(esop|esos|employee)|"
    r"conversion of warrants into equity$|loss of certificate)\b", re.I)


def classify(text):
    """Return [(family, param, label, clock)] for one raw item. [] if noise."""
    if not text:
        return []
    t = " ".join(str(text).split())
    if NOISE.search(t):
        return []
    hits, seen = [], set()
    for fam, param, label, clock, rx in SIGNAL_RULES:
        if rx.search(t) and param not in seen:
            seen.add(param)
            hits.append((fam, param, label, clock))
    # A6 without expansion language is double-counting a financing fact that
    # D3 already records. Drop it rather than let one filing fire two families.
    if "A6" in seen and not EXPANSION_CONTEXT.search(t):
        hits = [h for h in hits if h[1] != "A6"]
    return hits


# --------------------------------------------------------------------------
# Universe
# --------------------------------------------------------------------------

def load_universe():
    if os.path.exists(UNIVERSE):
        with open(UNIVERSE) as f:
            return json.load(f)
    return {}


def refresh_universe():
    """NSE archives + BSE scrip list. Both worked from GitHub in your SME doctor run.
    Market cap is left null when it cannot be sourced — never estimated."""
    uni = load_universe()
    txt = S.fetch("nse_universe")
    if txt:
        for row in csv.DictReader(io.StringIO(txt)):
            sym = (row.get("SYMBOL") or "").strip()
            if not sym:
                continue
            uni.setdefault(sym, {})
            uni[sym].update({
                "symbol": sym,
                "name": (row.get("NAME OF COMPANY") or "").strip(),
                "isin": (row.get(" ISIN NUMBER") or row.get("ISIN NUMBER") or "").strip(),
                "exchange": "NSE",
                "listed_on": (row.get(" DATE OF LISTING") or row.get("DATE OF LISTING") or "").strip(),
                "source": "NSE archives EQUITY_L.csv",
            })
    else:
        S.note_error("universe", "NSE equity list unreachable")
    return uni


def name_key(s):
    s = re.sub(r"\b(limited|ltd|private|pvt|india|the)\b", " ", (s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def match_company(raw_name, uni, index=None):
    """Map a free-text company name to a universe symbol. None when unsure —
    an unmatched event is kept and flagged, never attached to a guessed ticker."""
    if not raw_name:
        return None
    if index is None:
        index = {name_key(v.get("name", "")): k for k, v in uni.items() if v.get("name")}
    return index.get(name_key(raw_name))


def in_mandate(sym, uni):
    """True when the name is inside the mandate, or when market cap is unknown
    (unknown is surfaced for review, not silently dropped)."""
    rec = uni.get(sym) or {}
    mc = rec.get("mcap_cr")
    if mc is None:
        return True
    return MCAP_MIN <= mc <= MCAP_MAX


# --------------------------------------------------------------------------
# Sweeps — each returns a list of raw items {company, text, date, link, source}
# --------------------------------------------------------------------------

def _d(x, default=None):
    for f in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y", "%Y-%m-%d",
              "%d %b %Y", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(str(x).strip()[:len(datetime.now().strftime(f))], f).date().isoformat()
        except Exception:
            continue
    return default or S.today_ist().isoformat()


def sweep_exchange_announcements(frm, to):
    out = []
    data = S.fetch("bse_ann", frm, to)
    rows = data.get("Table", []) if isinstance(data, dict) else (data or [])
    for r in rows if isinstance(rows, list) else []:
        out.append({
            "company": r.get("SLONGNAME") or r.get("NEWSSUB", "")[:60],
            "text": " ".join(filter(None, [r.get("NEWSSUB"), r.get("HEADLINE"), r.get("MORE")])),
            "date": _d(r.get("NEWS_DT")),
            "link": ("https://www.bseindia.com/xml-data/corpfiling/AttachLive/" + r["ATTACHMENTNAME"])
                    if r.get("ATTACHMENTNAME") else "https://www.bseindia.com/corporates/ann.html",
            "source": "BSE announcement", "sweep": 1, "grade": "A",
        })
    data = S.fetch("nse_ann", frm, to)
    for r in (data or []) if isinstance(data, list) else []:
        out.append({
            "company": r.get("sm_name") or r.get("symbol"),
            "symbol": r.get("symbol"),
            "text": " ".join(filter(None, [r.get("desc"), r.get("attchmntText")])),
            "date": _d(r.get("an_dt")),
            "link": r.get("attchmntFile") or
                    "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "source": "NSE announcement", "sweep": 1, "grade": "A",
        })
    return out


def sweep_ownership(frm, to):
    out = []
    for key, src, lbl in (("nse_pit", "NSE insider (PIT)", "D1"),
                          ("nse_sast", "NSE SAST Reg 29", "D4"),
                          ("nse_bulk", "NSE bulk deals", "D4")):
        data = S.fetch(key, frm, to)
        rows = data.get("data", data) if isinstance(data, dict) else data
        for r in (rows or []) if isinstance(rows, list) else []:
            who = r.get("acqName") or r.get("anex1_personName") or r.get("clientName") or ""
            out.append({
                "company": r.get("company") or r.get("symbol") or r.get("name"),
                "symbol": r.get("symbol"),
                "text": f"{src}: {who} {r.get('acqMode') or r.get('buySell') or ''} "
                        f"{r.get('secAcq') or r.get('quantity') or ''} "
                        f"{r.get('afterAcqSharesPer') or r.get('postHolding') or ''}".strip(),
                "date": _d(r.get("date") or r.get("exchange") or r.get("acqfromDt")),
                "link": "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
                "source": src, "sweep": 7, "grade": "A",
                "force": lbl,
            })
    return out


def sweep_policy_and_regulatory():
    out = []
    for it in S.fetch("pib") or []:
        out.append({"company": None, "text": f"{it.get('title','')} {it.get('summary','')}",
                    "date": _d(it.get("published")), "link": it.get("link", ""),
                    "source": "PIB", "sweep": 6, "grade": "A"})
    return out


NEWS_QUERIES = [
    '"capacity expansion" India listed company',
    '"environmental clearance" granted India plant expansion',
    '"bags order" OR "wins order" India smallcap crore',
    '"commencement of commercial production" India',
    '"preferential allotment" India smallcap stake',
    '"promoter" "increased stake" India smallcap',
    'USFDA approval India pharma company',
    '"quality control order" OR "anti-dumping duty" India domestic manufacturers',
    '"letter of award" India company crore',
    'India company "new plant" investment crore capex',
]


def sweep_news():
    out = []
    for q in NEWS_QUERIES:
        for it in S.fetch("gnews", q=q) or []:
            out.append({"company": None, "text": it.get("title", ""),
                        "date": _d(it.get("published")), "link": it.get("link", ""),
                        "source": "Google News India", "sweep": 9, "grade": "C",
                        "query": q})
    return out


# --------------------------------------------------------------------------
# Assemble
# --------------------------------------------------------------------------

def to_events(raw_items, uni):
    """Classify raw items into framework signal events. Unclassifiable items drop."""
    index = {name_key(v.get("name", "")): k for k, v in uni.items() if v.get("name")}
    events = []
    for n, it in enumerate(raw_items):
        hits = classify(it.get("text"))
        if it.get("force") and not hits:
            fam = it["force"][0]
            hits = [(fam, it["force"], "Ownership disclosure", 2)]
        if not hits:
            continue
        sym = it.get("symbol") or match_company(it.get("company"), uni, index)
        # One filing that fires two families is ONE piece of evidence, not two.
        # The item id carries that through to the convergence rule.
        item_id = f"{it.get('source','')}|{it.get('date','')}|{hash(str(it.get('text'))[:200]) & 0xffffff}"
        for fam, param, label, clock in hits:
            events.append({
                "item_id": item_id,
                "date": it.get("date"),
                "company": it.get("company"),
                "symbol": sym,
                "family": fam, "param": param, "label": label, "clock": clock,
                "text": " ".join(str(it.get("text", "")).split())[:400],
                "link": it.get("link", ""), "source": it.get("source"),
                "sweep": it.get("sweep"), "grade": it.get("grade", "C"),
                "in_universe": bool(sym),
            })
    return events


def dedupe(events):
    seen, out = set(), []
    for e in events:
        k = (e.get("symbol") or (e.get("company") or "")[:28], e["param"], e["date"])
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def decay_weight(family, age_days):
    hl = HALF_LIFE.get(family, 365)
    return round(0.5 ** (max(0, age_days) / hl), 4)


def find_convergence(events, asof=None):
    """Apply the framework's convergence rule. Returns candidate companies."""
    asof = asof or S.today_ist()
    by_co = defaultdict(list)
    for e in events:
        key = e.get("symbol") or e.get("company")
        if key:
            by_co[key].append(e)
    cands = []
    for key, evs in by_co.items():
        recent = []
        for e in evs:
            try:
                age = (asof - datetime.fromisoformat(e["date"]).date()).days
            except Exception:
                age = 0
            if 0 <= age <= CONVERGENCE_WINDOW_DAYS:
                e = dict(e, age_days=age, decay=decay_weight(e["family"], age))
                recent.append(e)
        fams = {e["family"] for e in recent}
        # Independence test: the families must come from at least two DIFFERENT
        # source items. A single preferential-allotment filing that matches both
        # a funding rule and an ownership rule is one fact, not a convergence.
        items_per_family = defaultdict(set)
        for e in recent:
            items_per_family[e["family"]].add(e.get("item_id") or id(e))
        distinct_items = set().union(*items_per_family.values()) if items_per_family else set()
        independent = len(fams) >= 2 and len(distinct_items) >= 2
        if independent and fams & set(PHYSICAL_FAMILIES):
            cands.append({
                "key": key,
                "symbol": next((e["symbol"] for e in recent if e.get("symbol")), None),
                "company": next((e["company"] for e in recent if e.get("company")), key),
                "families_fired": sorted(fams),
                "family_count": len(fams),
                "earliest_clock": min(e["clock"] for e in recent),
                "best_grade": min((e["grade"] for e in recent), key="ABCD".index),
                "event_count": len(recent),
                "events": sorted(recent, key=lambda x: x["date"], reverse=True),
            })
    cands.sort(key=lambda c: (-c["family_count"], c["earliest_clock"], -c["event_count"]))
    return cands


def load_history():
    if os.path.exists(SIGNALS):
        try:
            with open(SIGNALS) as f:
                return json.load(f)
        except Exception:
            pass
    return {"events": [], "runs": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)

    if a.doctor:
        rep = S.probe_all()
        if not a.dry:
            with open(os.path.join(DATA, "source_report.json"), "w") as f:
                json.dump(rep, f, indent=2)
        return

    to = S.today_ist()
    frm = to - timedelta(days=a.days)
    print(f"PCR harvest {frm} -> {to}")

    uni = refresh_universe()
    print(f"  universe: {len(uni)} listed names")

    raw = []
    for name, fn in (("exchange announcements", lambda: sweep_exchange_announcements(frm, to)),
                     ("ownership disclosures", lambda: sweep_ownership(frm, to)),
                     ("policy / regulatory", sweep_policy_and_regulatory),
                     ("news", sweep_news)):
        try:
            got = fn() or []
        except Exception as e:
            S.note_error(f"sweep:{name}", e)
            got = []
        print(f"  {name:<26} {len(got):>5} raw items")
        raw += got

    events = dedupe(to_events(raw, uni))
    print(f"  classified into {len(events)} signal events")

    hist = load_history()
    known = {(e.get("symbol") or e.get("company"), e["param"], e["date"]) for e in hist["events"]}
    fresh = [e for e in events if (e.get("symbol") or e.get("company"), e["param"], e["date"]) not in known]
    all_events = hist["events"] + fresh
    print(f"  {len(fresh)} new since last run")

    cands = [c for c in find_convergence(all_events)
             if not c["symbol"] or in_mandate(c["symbol"], uni)]
    print(f"  {len(cands)} companies meet the convergence rule")

    out = {
        "generated_at": datetime.now(S.IST).isoformat(),
        "window": {"from": frm.isoformat(), "to": to.isoformat()},
        "counts": {"raw": len(raw), "events": len(events), "new": len(fresh),
                   "candidates": len(cands), "universe": len(uni)},
        "coverage_warning": None,
        "candidates": cands[:60],
        "events": all_events[-4000:],
        "errors": S.ERRORS[-60:],
    }
    blocked = [k for k, v in S.SRC.items()
               if v["needs_indian_ip"] and any(k in e["where"] or v["label"][:12] in e["where"]
                                               for e in S.ERRORS)]
    if len(blocked) >= 3 or not any(e["sweep"] == 1 for e in events):
        out["coverage_warning"] = (
            "No exchange-announcement events were collected. This machine is very likely "
            "IP-blocked by NSE and BSE. Sweeps 1, 2, 3 and 7 carry most of the signal value. "
            "Register a self-hosted GitHub runner on your PC — see README.")
        print("\n  WARNING: " + out["coverage_warning"])

    if a.dry:
        print(json.dumps(out["counts"], indent=2))
        for c in cands[:10]:
            print(f"   - {c['company']}  families={','.join(c['families_fired'])}  clock={c['earliest_clock']}")
        return

    with open(SIGNALS, "w") as f:
        json.dump(out, f, indent=1)
    with open(UNIVERSE, "w") as f:
        json.dump(uni, f)
    print(f"\nwrote {SIGNALS}")


if __name__ == "__main__":
    main()
