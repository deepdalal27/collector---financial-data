# Pre-Consensus Radar — automated collector

Harvests the weekly signal sweeps, classifies every item into the framework's
eight signal families, applies the convergence rule and the decay curve, and
writes `data/signals.json` for the Sunday Claude run to reason over.

**Division of labour, deliberately.** The collector gathers, classifies and
filters — deterministic, testable, 61 assertions. It never scores a company,
never applies the obscurity multiplier, never writes a thesis. Reading what an
announcement *means* is judgment, and that runs in the Sunday session.

---

## The one thing to know before you trust a cloud run

GitHub's runners live in datacentres. **NSE, BSE and the government portals
refuse datacentre IPs.** This is not speculation — your own SME Screener doctor
run measured it on 2 August 2026:

| Source | From GitHub Actions | From your PC |
|---|---|---|
| NSE archives, BSE scrip list | OK | OK |
| BSE results / announcement API | **blocked** | works |
| NSE quote / results / PIT APIs | **blocked** | works |
| Public CORS relays (Worker, allorigins, codetabs, thingproxy) | **all four blocked** | n/a |

Sweeps 1, 2, 3 and 7 — exchange announcements, environmental clearances, tender
awards, insider and shareholding disclosures — carry most of the signal value in
this model. A GitHub-hosted run will still give you the policy, regulatory,
ratings and news sweeps, and the universe. It will probably give you nothing
from the four that matter most.

So the collector **measures rather than assumes**, and tells you plainly:

```bash
python pcr_collect.py --doctor      # writes data/source_report.json
```

If it reports blocks, `signals.json` carries a `coverage_warning` and the
Sunday note says coverage was partial. It never pretends a quiet week when the
truth is a blocked sweep.

### When the doctor says blocked — the fix costs nothing

Register a **self-hosted GitHub runner** on your own PC. Same workflow, same
schedule, same code, same GitHub UI — it just executes on your machine, from an
Indian IP.

1. GitHub repo → **Settings → Actions → Runners → New self-hosted runner**
2. Pick Windows, then paste the three commands it shows into PowerShell
3. Run `.\run.cmd` once to test, then `.\svc.cmd install` and `.\svc.cmd start`
   so it runs as a Windows service and survives reboots
4. In `.github/workflows/pcr-scan.yml`, comment out `runs-on: ubuntu-latest`
   and uncomment `runs-on: self-hosted`

Your PC needs to be on at 22:00 IST Saturday. If it's asleep, GitHub queues the
job and runs it when the machine comes back — you lose punctuality, not data.

---

## Setup, once

1. Create a repo (private is fine) and upload this `collector/` folder plus
   `.github/workflows/pcr-scan.yml`.
2. **Settings → Actions → General → Workflow permissions → Read and write.**
   Without this the bot cannot commit `signals.json` and nothing will ever update.
3. **Actions → PCR weekly scan → Run workflow → mode: `doctor`.** Read the run
   summary table. That tells you, measured rather than guessed, what your runner
   can reach.
4. If the exchange sources show FAIL, do the self-hosted runner step above.
5. Then run it again with mode `scan`.

After that it runs itself every Saturday at 22:00 IST.

---

## What comes out

`data/signals.json`

```jsonc
{
  "generated_at": "...",
  "window": {"from": "...", "to": "..."},
  "counts": {"raw": 0, "events": 0, "new": 0, "candidates": 0},
  "coverage_warning": null,        // set when a sweep was blocked
  "candidates": [                  // companies that passed the convergence rule
    {
      "company": "...", "symbol": "...",
      "families_fired": ["A", "D"],
      "earliest_clock": 2,
      "best_grade": "A",
      "events": [ /* the full evidence trail, each with its source link */ ]
    }
  ],
  "events": [ /* every classified event, accumulating across runs */ ],
  "errors": [ /* every failure, recorded not swallowed */ ]
}
```

`data/source_report.json` — which sources this machine could actually reach.

---

## The rules the code enforces

- **Convergence.** Two independent families within 90 days, at least one of
  them physical (A, B or C). One filing that fires two families is one fact, not
  a convergence — the independence test uses source item ids, not just family
  counts. There is a test for exactly this.
- **Noise rejection.** Trading-window closures, newspaper publications, AGM
  notices, compliance certificates and the rest of the routine filing traffic
  are rejected before classification. Roughly 90% of exchange announcement
  volume is this.
- **Decay.** Every signal loses half its weight over its family's half-life —
  microstructure in 45 days, capex in 540.
- **Mandate.** ₹300 Cr – ₹25,000 Cr. A name whose market cap cannot be sourced
  is **surfaced for review, never silently dropped** and never estimated.
- **No guessed tickers.** A company name that does not match the universe keeps
  its raw name and is flagged `in_universe: false`. It is never attached to a
  plausible-looking symbol.

---

## Commands

```bash
python test_pcr.py                    # 61 assertions, no network
python pcr_collect.py --doctor        # probe every source from this machine
python pcr_collect.py --days 7        # harvest and write signals.json
python pcr_collect.py --days 7 --dry  # harvest, print, write nothing
```

---

## Adding a new signal rule

One line in `SIGNAL_RULES` in `pcr_collect.py`:

```python
("A", "A3", "CWIP step-up disclosed", 2,
 R(r"\b(capital work[- ]in[- ]progress|CWIP)\b", re.I)),
```

Tuple is `(family, parameter, label, clock_stage, regex)`. Order matters — the
specific rules sit above the general ones. Add a positive case to
`CASES_POSITIVE` in `test_pcr.py` at the same time; the workflow runs the tests
before every harvest and will not publish a dataset if they fail.

---

Educational and research tooling. Produces a research queue, not
recommendations. No buy/sell/hold call or target price is expressed or implied.
