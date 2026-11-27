# AutoPilot-QA — Demo guide

About 15 minutes live. Everything runs on this laptop (CPU-only), so the one slow step, generating a test suite
(about 10 minutes), is done **before** the audience arrives. The live part is the self-healing, which takes 1–2
minutes per scenario.

> Don't run the demo while `evaluation/run_experiment.py` is running: it switches seeded changes on the same services.

---

## A. The day before or an hour before: prepare

### A1. Start the five components (one PowerShell window each)

| # | Window | Command | Ready when you see |
|---|---|---|---|
| 1 | Ollama | the Ollama app is running (system tray), or `ollama serve` | `ollama list` shows `llama3.1:8b` and `qwen2.5-coder:7b` |
| 2 | ShopLite | `cd C:\BITS\autopilot-qa-project\target-platform; npm run start:all` | five `... listening on http://localhost:400x` lines, `catalog ... (button v1)` |
| 3 | API | `cd C:\BITS\autopilot-qa-project\autopilot-qa\backend; .\.venv\Scripts\python -m uvicorn main:app --port 8000` | `Application startup complete` |
| 4 | Dashboard | `cd C:\BITS\autopilot-qa-project\autopilot-qa\frontend; npm run dev` | `Local: http://localhost:5173/` |
| 5 | Helper (spare window) | `cd C:\BITS\autopilot-qa-project` | used for the commands below |

MongoDB must be running as the Windows service (it is by default on this machine).

### A2. Warm up the models (the first call loads a model into memory, 15–25 s each)

```powershell
ollama run llama3.1:8b "Say OK"
ollama run qwen2.5-coder:7b "Say OK"
```

### A3. Clean state

```powershell
cd C:\BITS\autopilot-qa-project\target-platform;         npm run seed -- none
cd C:\BITS\autopilot-qa-project\autopilot-qa\backend;   .\.venv\Scripts\python -m scripts.demo_state --reset
```
Expected: every service prints `(normal)`, and `demo_state` shows only `v1 active seed (seed selectors)`.

### A4. Generate the demo run (about 10 minutes)

1. Open **http://localhost:5173** in the browser.
2. Keep the first example, *"User can search for a product, add it to cart, and complete checkout."*, and click
   **Run AutoPilot-QA**.
3. Watch it finish: the **Pipeline** shows every agent Done, and the status is **Completed**.
4. **Copy the address bar**: it ends in `#run=<run id>`. This is your demo run. Bookmark it.
5. In **Execution results**, the scripts should be ✓ Pass on map v1. If a generated script failed on the clean app
   (that happens, since generation isn't perfect), just run the requirement again and keep the better run.

Then reset the healing state once more: `.\.venv\Scripts\python -m scripts.demo_state --reset`.

---

## B. Live demo

Every live step can be done **from the dashboard**, using the **Demo controls** panel at the top: a *UI change on
ShopLite* picker with **Apply change**, **Reset healing**, and a *Healing threshold* box with **Re-execute with this
threshold**. The panel also shows which change ShopLite is running and the selector-map versions. The terminal
equivalents are given under each step, as a fallback.

### B1. Introduce the system (2 minutes)

- Show the architecture slide or `autopilot-qa/docs/architecture.md`: four agents plus an orchestrator, a MongoDB
  message bus, Ollama running locally, and Playwright.
- Open the bookmarked run. Walk through **Test cases** (generated from one English sentence), **Execution results**
  (all pass on v1), and the **Pipeline**.
- Open **http://localhost:4002/catalog.html**. Point at the **"Add to Cart"** buttons.

### B2. A UI change breaks the tests, and the system heals itself (the headline, about 2 minutes)

1. Simulate an independent deploy of the catalog service that renames the button. **UI:** in Demo controls, pick
   *"Add to Cart button renamed: id add-to-bag-btn, label 'Add to Bag'…"* and click **Apply change**. **Terminal:**
   ```powershell
   cd C:\BITS\autopilot-qa-project\target-platform; npm run seed -- catalog.add_button_renamed
   ```
   Refresh the catalog page: the buttons now say **"Add to Bag"** (new id `add-to-bag-btn`). Nothing else changed.
2. In the dashboard (your demo run), click **Re-execute** under Execution results.
3. Narrate as it streams (Reasoning trace, left panel):
   - *Test Executor*: the script **fails**: `#add-to-cart-btn` no longer exists.
   - *Triage & Healer*: `Triage: locator_missing at catalog.addToCartButton`, a healable locator failure (not an app bug).
   - `THOUGHT: …` lines appear **live**: the model reasoning about the candidates on the page.
   - The **decision card**: `#add-to-cart-btn → #add-to-bag-btn`, a confidence bar against the **threshold marker 0.80**,
     **Applied**, and the verification line ("1 match in scope; … containers").
   - *Test Executor*: the same script re-runs on **selector map v2** and **passes**.
4. Show the evidence:
   - **Execution results**: a ✓ Pass row with the badge **Healed → #add-to-bag-btn**, next to the failed row with its
     screenshot.
   - **Coverage & healing**: the "Healed re-runs" tile, and a filled dot above the threshold line in the confidence chart.
   - In the helper window: `.\.venv\Scripts\python -m scripts.demo_state` (from `autopilot-qa\backend`) shows
     `v2 active healer catalog.addToCartButton: #add-to-cart-btn -> #add-to-bag-btn`.
5. Key message: *the test code was never edited. The healer changed one entry in a versioned selector map, logged
   its full reasoning, and every later test in the run benefits from it.*

### B3. Below the threshold, it asks a human instead (about 2 minutes)

1. Reset the heal. **UI:** **Reset healing**. **Terminal:** `.\.venv\Scripts\python -m scripts.demo_state --reset`.
2. **UI:** type **0.99** in *Healing threshold* and click **Re-execute with this threshold**. That applies to this
   re-execution only, with no restart. (Terminal alternative: restart the API with
   `$env:HEALING_CONFIDENCE_THRESHOLD='0.99'`, then click Re-execute.)
3. Watch the result. Expected: the same reasoning, but the decision card says **Not applied** (confidence
   0.85 < threshold 0.99). **Bug reports** shows *"Possible benign UI change requiring manual confirmation: proposed
   #add-to-bag-btn …"*, and `demo_state` shows the proposal as `pending_review` while v1 stays active.
4. Key message: *below the threshold nothing is changed automatically; it becomes a reviewable proposal.*
5. Put the threshold back: Ctrl+C, `Remove-Item Env:HEALING_CONFIDENCE_THRESHOLD`, start uvicorn again, then
   `demo_state --reset`.

### B4. A real defect is not "healed" away (about 2 minutes)

1. Replace the button with one that does something different. **UI:** **Reset healing**, pick *"Add to Cart replaced
   by an 'Add to Wishlist' control…"* and click **Apply change**. **Terminal:**
   ```powershell
   npm run seed -- catalog.add_button_wishlist
   ```
   The catalog now shows **"Add to Wishlist"**, and clicking it does **not** add to the cart.
2. Click **Re-execute**. Either outcome is correct, and both are worth explaining:
   - The model **refuses** ("different action"): no heal, and a bug report is raised directly.
   - Or the model proposes the wishlist button. The heal is applied **provisionally**, and the re-run fails at
     *"Added … to cart"*. The trace then says **"Rolled back selector map v2: the re-run still failed"**, followed by
     a bug report.
3. Key message: *healing is transactional. The model proposes, DOM checks verify structure, and the re-run verifies
   behaviour. A heal that doesn't make the test pass never sticks.*

### B5. Other defects are reported with the right root cause (optional, 1 minute each)

```powershell
npm run seed -- cart.total_doubled             # -> bug report, root cause "data" (wrong total), no healing attempted
npm run seed -- payment.expired_card_accepted  # -> caught by the expired-card test, if the run has one
```
Run `demo_state --reset` before each, then click Re-execute.

### B6. Results (3 minutes)

- `evaluation/metrics/out/report.md`: healing success rate, false bug reports, defect detection, applied vs
  committed false heals, heal precision, and confidence calibration, per suite (reference and generated).
- `evaluation/out/manual-baseline.json`: the traditional hand-written suite fails on **every** benign rename,
  where AutoPilot-QA heals.
- `docs/spikes/healer-spike.md`: the model comparison (3 local models) and the prompt v1/v2 precision-recall
  trade-off. Be upfront about the limits: CPU-only, a small model, and the development vs held-out decoy.

### B7. Finish: restore the normal app

**UI:** pick *Normal app (no change)*, then **Apply change** and **Reset healing**. **Terminal:**

```powershell
cd C:\BITS\autopilot-qa-project\target-platform; npm run seed -- none
cd C:\BITS\autopilot-qa-project\autopilot-qa\backend; .\.venv\Scripts\python -m scripts.demo_state --reset
```

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| Dashboard shows `feed: reconnecting` | The API (window 3) stopped: restart it. The feed replays history automatically. |
| A step takes minutes with no output | The model is loading or the CPU is busy. Keep talking; the first LLM call after a restart is slowest (warm up in A2). |
| Re-execute passes without healing | A heal from an earlier step is still active: run `demo_state --reset`, then Re-execute. |
| Re-execute fails but no THOUGHT appears | Check the triage line: only `locator_missing` is healed. Other categories go straight to a bug report, which is correct. |
| ShopLite errors / `service not available` | Window 2 stopped: `npm run start:all` again, then `npm run seed -- <change>` again (changes live in memory). |
| Nothing live works at all | Open a prepared run by URL (`http://localhost:5173/#run=<id>`). The dashboard replays every step from the message bus. |
