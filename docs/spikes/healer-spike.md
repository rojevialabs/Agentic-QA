# Healer spike — findings (Phases 3b and 8)

**Question:** can a local model on this laptop (Intel Iris Xe, CPU-only, 20 GB RAM) heal the v1→v2 catalog change
(`#add-to-cart-btn` → `#add-to-bag-btn`, "Add to Bag") at confidence ≥ 0.80, **and** refuse a real defect where the
add button has been replaced by a control with a different purpose ("Add to Wishlist")?

**Setup:** saved DOM snapshots (`autopilot-qa/backend/tests/fixtures/dom/`), the failing locator scoped to the
"Wireless Headphones" product card, the element description taken from the seed selector map, 20 trials per model
and scenario at temperature 0.7 with seed = trial index. Every proposal is judged independently in headless
Chromium. **Correct** on v2 means it would auto-apply and resolves to the add button in the target card *and* in a
second card (generic). **Correct** on the decoy means it would not auto-apply. Raw results are in
`evaluation/spikes/healer-3b/*.jsonl`, and latencies were measured while other development work ran, so they are
indicative only.

## Round 1 — v0 prompt, single shot (Phase 3b)

| model | v2 correct | v2 locator right (any confidence) | decoy correct | decoy false heals | median s |
|---|---|---|---|---|---|
| llama3.2:3b | 9/20 | 9/20 | 14/20 | 6/20 | 10.4 |
| **llama3.1:8b** | **17/20** | 19/20 | 13/20 | 7/20 | 22.4 |
| qwen2.5-coder:7b | 11/20 | 13/20 | 14/20 | 6/20 | 25.2 |

Failure taxonomy (incorrect trials):

| model | old locator re-proposed | right locator, conf < 0.80 | several selectors joined by `\|` | product-specific / other | no parseable answer | decoy picked |
|---|---|---|---|---|---|---|
| llama3.2:3b | 8 | – | 1 | – | 2 | 6 |
| llama3.1:8b | 1 | 2 | – | – | – | 7 |
| qwen2.5-coder:7b | – | 2 | 2 | 5 | – | 6 |

Verification (unique in scope, generic across cards) blocked 9, 6 and 2 confident but invalid proposals
respectively ("rescued by verify"). It removes hallucinated locators, but it cannot catch a wrong-*purpose* control:
the wishlist button is unique and generic.

**Decision:** llama3.1:8b becomes the healer model. It's the only model close to the target, and the 3B model's
dominant error (anchoring on the failing locator) needs structural fixes, not a bigger model. qwen2.5-coder stays the
code generator.

## Round 2 — engine with prompt v1 (Phase 8)

Changes: the answer must name a numbered candidate and copy one of its suggested selectors. The failing locator is
rejected deterministically, with **one feedback round** for any verification failure. The rubric is anchored at
0.85–1.0 for "same element and job, only naming changed". The prompt's examples of a different job avoid the decoy's
wording.

| model | v2 correct | v2 locator right | decoy correct | decoy false heals | mean rounds | median s |
|---|---|---|---|---|---|---|
| llama3.1:8b | **18/20** ✅ | 20/20 | 8/20 ❌ | 12/20 | 1.00 | 23.6 |

v2 now meets the exit criterion (≥ 18/20). The two misses proposed the correct, verified locator but at confidence
0.5, so the gate correctly held them for manual review rather than auto-applying. On the decoy, v1 made things **worse**: 12/20 false heals,
against 7/20 for v0. The anchored rubric made the model confident and consistent, including when it was wrong.
Its THOUGHT lines show why: when it reasons "the missing element is a button, this candidate is a button with a
similar action", it heals. When it actually compares the labels ("'Add to Wishlist' suggests a different action"),
it refuses.

## Round 3 — engine with prompt v2

Change: the THOUGHT is structured to force the comparison. The model must quote the missing element's label and the
best candidate's label, then answer "Same effect for the user? yes/no, because …", and a "no" must produce a null
ACTION.

| model | v2 correct | decoy correct | decoy false heals |
|---|---|---|---|
| llama3.1:8b | 2/6 (stopped early) | **9/10** | 1/10 |

The comparison makes the 8B model literal. It decides "Add to Bag" is not "Add to Cart" in about half the benign
trials ("the candidate adds to bag, but the missing element adds to cart"), while it now refuses the wishlist almost
always. So v2 buys precision with recall. A small local model can't reliably separate a synonym (Bag) from a different
target (Wishlist) using only the static page.

## Decision

1. **Healer:** llama3.1:8b, **prompt v1** by default (`HEALER_PROMPT_VERSION=v1`; v2 remains selectable).
2. **Transactional healing** (Phase 8) closes the precision gap without giving up recall. A heal is committed only
   if the script's re-run passes; otherwise the healed selector map is rolled back and a bug report is raised. A
   wishlist pick fails the next assertion ("Added … to cart") and is rolled back. The layers split the work: the LLM
   proposes, DOM verification checks structure, and the re-run checks behaviour.
3. The final evaluation reports both the *applied* false-heal rate and the *committed* one (after rollback), on the
   held-out decoy as well as the development one.

Not yet measured (overnight batch): the engine with llama3.2:3b, and v1-vs-v2 end-to-end with rollback.

## Methodology note

The wishlist decoy served as the *development* decoy while the prompt was iterated, so it no longer measures
generalisation. The final evaluation therefore includes a second decoy that was never used during prompt development
(`catalog.add_button_compare`, "Add to Compare"), flagged `held_out: true` in `evaluation/seeded-bugs/registry.json`.
Report both, and treat the held-out one as the honest number.
