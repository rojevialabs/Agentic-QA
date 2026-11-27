# Evaluation

How AutoPilot-QA is measured, and why the design looks the way it does.

## Ground truth: seeded changes

`seeded-bugs/registry.json` lists 11 changes that ShopLite can switch on at runtime (`POST /api/__config`) or at
start-up (`SEEDED_CHANGES=`):

- **5 benign UI changes** (behaviour unchanged; the healer should heal): add button renamed (the spec's v2), search box
  id, checkout button id, Place Order → "Confirm Order", Pay → "Pay now". Each lists its acceptable locators.
- **6 real defects** (behaviour changed; must be reported, never healed): add button replaced by "Add to Wishlist"
  (development decoy) or by "Add to Compare" (**held-out decoy**, never used during prompt development), checkout
  button missing, cart totals doubled, expired cards accepted, out-of-stock product addable.

## Two suites

| Suite | What it is | What it measures |
|---|---|---|
| **R — reference** | 5 hand-written scripts in `autopilot-qa/tests-generated/reference/`, in exactly the generated-script format (logical selector keys via `sel()`), covering search → cart → checkout → payment, expired card, out of stock, empty cart, login | **The healer**, isolated from generator quality. Every seeded change is exercised. |
| **G — generated** | Scripts the agents generated for the registry requirements | **The whole autonomous pipeline**, including how many generated scripts are usable at all |

A script enters a suite only if **every** execution of it passed on the clean platform **without healing**
(`run_experiment.py revalidate` re-applies this rule).

This rule came from a real finding. A generated script asserted the cart total while still on the catalog page (a
test bug). On the clean baseline the healer "healed" `cart.cartTotal` onto a catalog element showing "$59.99", the
re-run passed, and the broken script was counted as valid. Two fixes followed: (1) the validity rule above, and (2) a
triage guard: an element that belongs to another page (`cart.cartTotal` only exists on `/cart.html`) is a `wrong_page`
script error and is never healed.

## Protocol (`run_experiment.py`)

`baseline` / `baseline-reference` → `trials --trials N` → `metrics/compute_metrics.py`. Each trial: archive live heals
(back to selector map v1) → switch one seeded change on → execute a fresh copy of every valid baseline script through
the normal coordinator (triage, healing, transactional rollback) → record an `experiments` document. Scripts aren't
regenerated between trials, so every change faces the same tests. `manual` runs the hand-written Playwright suite in
`manual-baseline/` (raw selectors, no healing) under each change, as the control.

## Metrics (per suite and overall)

- healing success rate (benign trials with a change-induced failure that ended all-pass via a heal)
- false bug-report rate (benign trials that produced a bug report)
- defect detection rate (defect trials that produced a bug report)
- false heal rate, **applied** vs **committed** (a heal that survived its re-run instead of being rolled back)
- heal precision (applied heals whose locator is in the change's acceptable set)
- confidence calibration of the raw LLM confidence against ground truth (Brier score, ECE, reliability bins)
- median and maximum agent latency

Outputs: `out/baseline.json`, `out/manual-baseline.json`, `metrics/out/metrics.json`, `metrics/out/report.md`.

## Known threats to validity

- Prompt development used the wishlist decoy (and the v2 rename) as its development set, so report the held-out
  compare decoy separately.
- CPU-only inference: latencies are for an Intel Iris Xe laptop, and other work ran concurrently at times.
- Generated-suite validity is low (about a quarter of generated scripts pass cleanly), so Suite G covers fewer
  seeded changes than Suite R.
- 3 trials per change is small; the runner takes `--trials N` for a larger run.
