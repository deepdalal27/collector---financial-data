#!/usr/bin/env python3
"""
Pre-Consensus Radar — source adapters.

Written to the same rules as FinDeependence-SME-Screener/pipeline/sources.py:
  * Every event carries the name of the source it came from. Nothing is invented.
  * An adapter that cannot read a field omits it. It never guesses.
  * Endpoints change without notice, so every adapter is tolerant and every
    failure is recorded in ERRORS rather than silently swallowed.
  * probe_all() tells you which sources work FROM THE MACHINE YOU ARE ON.
    This matters more here than anywhere else: NSE, BSE and the government
    portals answer an Indian IP and refuse a datacentre one.

READ THIS BEFORE TRUSTING A GITHUB-HOSTED RUN
---------------------------------------------
Your own SME Screener doctor run (2 Aug 2026) measured, from GitHub Actions:
    BSE results API .............. blocked
    NSE quote / results API ...... blocked
    XBRL discovery ............... blocked (depends on the two above)
    Public CORS relays ........... all four blocked or rate-limited
    NSE archives, BSE scrip list.. OK
So a GitHub-hosted runner gets the universe, prices, policy, regulatory,
ratings and news sweeps, and is likely to get nothing from the four exchange
sweeps. Those four carry most of the signal value.

The fix costs nothing and changes no code: register a GitHub SELF-HOSTED
RUNNER on your own PC and flip one line in the workflow. Same schedule, same
pipeline, your Indian IP. See README section "When the doctor says blocked".
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

IST = timezone(timedelta(hours=5, minutes=30))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Relays fetch from their own IPs when a direct call fails. Your SME Screener
# measured all four as blocked or uselessly rate-limited from GitHub. They are
# kept because they cost nothing when they fail fast, and the Worker is yours.
WORKER_BRIDGE = "https://fd-license.deepuploads27.workers.dev/data?u="
RELAYS = [
    ("worker", lambda u: WORKER_BRIDGE + quote(u, safe="")),
    ("allorigins", lambda u: "https://api.allorigins.win/raw?url=" + quote(u, safe="")),
    ("codetabs", lambda u: "https://api.codetabs.com/v1/proxy?quest=" + quote(u, safe="")),
]
RELAY_FAIL_LIMIT = 6
_relay_state = {}

ERRORS = []


def note_error(where, detail):
    ERRORS.append({"where": str(where)[:90], "detail": str(detail)[:200]})


def relay_stats():
    return {k: dict(v) for k, v in _relay_state.items()}


def today_ist():
    return datetime.now(IST).date()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _sess(referer=None, accept="application/json, text/plain, */*"):
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    })
    if referer:
        s.headers["Referer"] = referer
    return s


def get(url, session=None, timeout=25, retries=1, via_relay_on_fail=True, referer=None):
    """GET with retry; on repeated failure optionally retry through a relay."""
    s = session or _sess(referer)
    last = None
    for attempt in range(retries + 1):
        try:
            r = s.get(url, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = repr(e)
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    if via_relay_on_fail:
        for name, build in RELAYS:
            st = _relay_state.setdefault(name, {"fails": 0, "wins": 0, "off": False})
            if st["off"]:
                continue
            try:
                r = requests.get(build(url), headers={"User-Agent": UA}, timeout=timeout + 10)
                if r.status_code == 200 and r.content and len(r.content) > 10:
                    body = r.content[:200].lstrip().lower()
                    if not body.startswith(b"<!doctype html") or b"<html" not in body:
                        st["wins"] += 1
                        st["fails"] = 0
                        return r
                last = f"{name} HTTP {r.status_code}"
            except Exception as e:
                last = f"{name} {e!r}"
            st["fails"] += 1
            if st["wins"] == 0 and st["fails"] >= RELAY_FAIL_LIMIT:
                st["off"] = True
                note_error(f"relay:{name}", f"disabled after {st['fails']} failures")
    note_error(url, last)
    return None


def get_json(url, **kw):
    r = get(url, **kw)
    if r is None:
        return None
    try:
        return r.json()
    except Exception:
        try:
            return json.loads(re.sub(r"^[^\[{]*", "", r.text, count=1))
        except Exception as e:
            note_error(url, f"bad json: {e!r}")
            return None


def get_rss(url, **kw):
    """Parse an RSS/Atom feed into [{title, link, published, summary}]. []  on failure."""
    r = get(url, accept_xml=True, **kw) if False else get(url, **kw)
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except Exception as e:
        note_error(url, f"bad xml: {e!r}")
        return []
    out = []
    for it in root.iter():
        tag = it.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        d = {}
        for ch in it:
            t = ch.tag.split("}")[-1].lower()
            if t == "title":
                d["title"] = (ch.text or "").strip()
            elif t == "link":
                d["link"] = (ch.text or ch.attrib.get("href", "")).strip()
            elif t in ("pubdate", "published", "updated"):
                d["published"] = (ch.text or "").strip()
            elif t in ("description", "summary", "content"):
                d["summary"] = re.sub(r"<[^>]+>", " ", ch.text or "").strip()
        if d.get("title"):
            out.append(d)
    return out


def nse_session():
    """NSE refuses its APIs without cookies from the homepage first."""
    s = _sess("https://www.nseindia.com/")
    try:
        s.get("https://www.nseindia.com/", timeout=20)
        time.sleep(0.8)
        s.get("https://www.nseindia.com/market-data/live-equity-market", timeout=20)
        time.sleep(0.4)
    except Exception as e:
        note_error("nse cookie warmup", e)
    return s


def bse_session():
    return _sess("https://www.bseindia.com/")


# --------------------------------------------------------------------------
# Source registry — every sweep declares where it fetches from and whether it
# is expected to survive a datacentre IP. The doctor measures the truth.
# --------------------------------------------------------------------------

SRC = {
    # sweep 1 — exchange announcements
    "bse_ann": dict(
        sweep=1, needs_indian_ip=True, label="BSE corporate announcements",
        url=("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
             "?pageno=1&strCat=-1&strPrevDate={frm}&strScrip=&strSearch=P"
             "&strToDate={to}&strType=C&subcategory=-1"),
        referer="https://www.bseindia.com/corporates/ann.html", fmt="%Y%m%d"),
    "nse_ann": dict(
        sweep=1, needs_indian_ip=True, label="NSE corporate announcements",
        url=("https://www.nseindia.com/api/corporate-announcements"
             "?index=equities&from_date={frm}&to_date={to}"),
        referer="https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        fmt="%d-%m-%Y", nse=True),
    # sweep 7 — ownership
    "nse_pit": dict(
        sweep=7, needs_indian_ip=True, label="NSE insider trading (PIT) disclosures",
        url=("https://www.nseindia.com/api/corporates-pit"
             "?index=equities&from_date={frm}&to_date={to}"),
        referer="https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
        fmt="%d-%m-%Y", nse=True),
    "nse_sast": dict(
        sweep=7, needs_indian_ip=True, label="NSE SAST substantial acquisition disclosures",
        url=("https://www.nseindia.com/api/corporate-sast-reg29"
             "?index=equities&from_date={frm}&to_date={to}"),
        referer="https://www.nseindia.com/companies-listing/corporate-filings-sast",
        fmt="%d-%m-%Y", nse=True),
    "nse_bulk": dict(
        sweep=7, needs_indian_ip=True, label="NSE bulk deals",
        url=("https://www.nseindia.com/api/historical/bulk-deals"
             "?from={frm}&to={to}"),
        referer="https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
        fmt="%d-%m-%Y", nse=True),
    # universe — these two DID work from GitHub in your SME Screener doctor run
    "nse_universe": dict(
        sweep=0, needs_indian_ip=False, label="NSE equity list (archives)",
        url="https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", raw=True),
    "bse_universe": dict(
        sweep=0, needs_indian_ip=False, label="BSE active equity scrip list",
        url=("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
             "?Group=&Scripcode=&industry=&segment=Equity&status=Active"),
        referer="https://www.bseindia.com/"),
    # sweep 5 — regulatory
    "openfda": dict(
        sweep=5, needs_indian_ip=False, label="openFDA drug approvals",
        url=("https://api.fda.gov/drug/drugsfda.json?search=submissions.submission_status_date:"
             "[{frm}+TO+{to}]&limit=100"), fmt="%Y%m%d"),
    # sweep 6 — policy
    "pib": dict(
        sweep=6, needs_indian_ip=False, label="PIB press releases (RSS)",
        url="https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", rss=True),
    "dgtr": dict(
        sweep=6, needs_indian_ip=False, label="DGTR anti-dumping notifications",
        url="https://www.dgtr.gov.in/", raw=True),
    # sweep 9 / cross-cutting — the highest-yield feed that is NOT IP blocked
    "gnews": dict(
        sweep=9, needs_indian_ip=False, label="Google News India (RSS)",
        url=("https://news.google.com/rss/search?q={q}+when:8d&hl=en-IN&gl=IN&ceid=IN:en"),
        rss=True),
}


def build_url(key, frm=None, to=None, q=None):
    s = SRC[key]
    u = s["url"]
    if "{frm}" in u or "{to}" in u:
        f = s.get("fmt", "%Y-%m-%d")
        u = u.replace("{frm}", (frm or today_ist() - timedelta(days=7)).strftime(f))
        u = u.replace("{to}", (to or today_ist()).strftime(f))
    if "{q}" in u:
        u = u.replace("{q}", quote(q or "", safe="+"))
    return u


def fetch(key, frm=None, to=None, q=None, session=None):
    """Fetch one source. Returns parsed JSON, RSS list, or raw text. None on failure."""
    s = SRC[key]
    url = build_url(key, frm, to, q)
    sess = session
    if sess is None and s.get("nse"):
        sess = nse_session()
    if s.get("rss"):
        return get_rss(url, session=sess, referer=s.get("referer")) or None
    if s.get("raw"):
        r = get(url, session=sess, referer=s.get("referer"))
        return r.text if r is not None else None
    return get_json(url, session=sess, referer=s.get("referer"))


# --------------------------------------------------------------------------
# Doctor — measure, never assume
# --------------------------------------------------------------------------

def probe_all(verbose=True):
    """Probe every registered source from THIS machine. Writes nothing."""
    report = {"run_at": datetime.now(IST).isoformat(), "results": {}}
    ok_blocked = 0
    for key, s in SRC.items():
        t0 = time.time()
        try:
            data = fetch(key, q="capacity expansion" if key == "gnews" else None)
            n = (len(data) if isinstance(data, (list, dict, str)) else 0)
            ok = data is not None and n > 0
        except Exception as e:
            note_error(f"probe:{key}", e)
            ok, n = False, 0
        report["results"][key] = {
            "label": s["label"], "sweep": s["sweep"],
            "needs_indian_ip": s["needs_indian_ip"],
            "reachable": ok, "size": n, "seconds": round(time.time() - t0, 1),
        }
        if not ok and s["needs_indian_ip"]:
            ok_blocked += 1
        if verbose:
            print(f"  {'OK    ' if ok else 'FAIL  '} {s['label']:<52} "
                  f"{'(needs Indian IP)' if s['needs_indian_ip'] else ''}")
    report["indian_ip_sources_failing"] = ok_blocked
    report["verdict"] = (
        "Exchange sweeps are blocked from this machine. Register a self-hosted "
        "runner on your PC — see README." if ok_blocked >= 3 else
        "Exchange sweeps reachable. This machine can run the full pipeline.")
    report["relays"] = relay_stats()
    report["errors"] = ERRORS[-40:]
    if verbose:
        print("\n  " + report["verdict"])
    return report


if __name__ == "__main__":
    print("PCR source doctor — probing every source from this machine\n")
    rep = probe_all()
    with open("data/source_report.json", "w") as f:
        json.dump(rep, f, indent=2)
    print("\nwrote data/source_report.json")
