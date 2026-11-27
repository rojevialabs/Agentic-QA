# AutoPilot-QA evaluation — all suite

132 trials (60 benign UI changes, 72 real defects).

| Metric | Value |
|---|---|
| Healing success rate | 91% |
| False bug-report rate (benign) | 7% |
| Defect detection rate | 58% |
| False heal rate (defects, applied) | 18% |
| False committed heal rate (survived the re-run) | 4% |
| Heal precision (59 applied) | 73% |
| Confidence calibration: Brier / ECE (n=112) | 0.223 / 0.276 |

## Per change

| change | label | trials | correct | with failures | healed | rolled back | reported | all pass |
|---|---|---|---|---|---|---|---|---|
| catalog.add_button_renamed | benign_ui_change | 12 | 11 | 12 | 12 | 0 | 1 | 11 |
| catalog.search_input_renamed | benign_ui_change | 12 | 12 | 9 | 9 | 0 | 0 | 12 |
| cart.checkout_button_renamed | benign_ui_change | 12 | 12 | 9 | 9 | 0 | 0 | 12 |
| order.place_order_renamed | benign_ui_change | 12 | 9 | 9 | 7 | 0 | 3 | 9 |
| payment.pay_button_renamed | benign_ui_change | 12 | 12 | 6 | 6 | 0 | 0 | 12 |
| catalog.add_button_wishlist | real_defect | 12 | 10 | 12 | 8 | 7 | 12 | 0 |
| catalog.add_button_compare | real_defect | 12 | 11 | 12 | 2 | 2 | 12 | 0 |
| cart.checkout_button_missing | real_defect | 12 | 9 | 9 | 3 | 3 | 9 | 3 |
| cart.total_doubled | real_defect | 12 | 3 | 3 | 0 | 0 | 3 | 9 |
| payment.expired_card_accepted | real_defect | 12 | 3 | 3 | 0 | 0 | 3 | 9 |
| catalog.out_of_stock_addable | real_defect | 12 | 3 | 3 | 0 | 0 | 3 | 9 |

## Reliability (raw LLM confidence)

| confidence bin | n | mean confidence | accuracy |
|---|---|---|---|
| 0.0–0.2 | 0 | — | — |
| 0.2–0.4 | 3 | 0.20 | 0.00 |
| 0.4–0.6 | 50 | 0.50 | 0.06 |
| 0.6–0.8 | 0 | — | — |
| 0.8–1.0 | 59 | 0.87 | 0.73 |

## Agent latency

| agent | tasks | median s | max s |
|---|---|---|---|
| test_executor | 430 | 2.11 | 14.47 |
| triage_healer | 181 | 19.78 | 65.27 |

## Manual baseline suite (no healing)

| change | failing tests |
|---|---|
| (none) | 0/5 |
| catalog.add_button_renamed | 1/5 |
| catalog.search_input_renamed | 1/5 |
| cart.checkout_button_renamed | 2/5 |
| order.place_order_renamed | 2/5 |
| payment.pay_button_renamed | 2/5 |
| catalog.add_button_wishlist | 1/5 |
| cart.checkout_button_missing | 2/5 |
| cart.total_doubled | 1/5 |
| payment.expired_card_accepted | 1/5 |
| catalog.out_of_stock_addable | 1/5 |
| catalog.add_button_compare | 1/5 |

# AutoPilot-QA evaluation — reference suite

33 trials (15 benign UI changes, 18 real defects).

| Metric | Value |
|---|---|
| Healing success rate | 80% |
| False bug-report rate (benign) | 20% |
| Defect detection rate | 100% |
| False heal rate (defects, applied) | 33% |
| False committed heal rate (survived the re-run) | 17% |
| Heal precision (22 applied) | 64% |
| Confidence calibration: Brier / ECE (n=43) | 0.262 / 0.317 |

## Per change

| change | label | trials | correct | with failures | healed | rolled back | reported | all pass |
|---|---|---|---|---|---|---|---|---|
| catalog.add_button_renamed | benign_ui_change | 3 | 2 | 3 | 3 | 0 | 1 | 2 |
| catalog.search_input_renamed | benign_ui_change | 3 | 3 | 3 | 3 | 0 | 0 | 3 |
| cart.checkout_button_renamed | benign_ui_change | 3 | 3 | 3 | 3 | 0 | 0 | 3 |
| order.place_order_renamed | benign_ui_change | 3 | 1 | 3 | 2 | 0 | 2 | 1 |
| payment.pay_button_renamed | benign_ui_change | 3 | 3 | 3 | 3 | 0 | 0 | 3 |
| catalog.add_button_wishlist | real_defect | 3 | 1 | 3 | 3 | 2 | 3 | 0 |
| catalog.add_button_compare | real_defect | 3 | 2 | 3 | 1 | 1 | 3 | 0 |
| cart.checkout_button_missing | real_defect | 3 | 3 | 3 | 2 | 2 | 3 | 0 |
| cart.total_doubled | real_defect | 3 | 3 | 3 | 0 | 0 | 3 | 0 |
| payment.expired_card_accepted | real_defect | 3 | 3 | 3 | 0 | 0 | 3 | 0 |
| catalog.out_of_stock_addable | real_defect | 3 | 3 | 3 | 0 | 0 | 3 | 0 |

## Reliability (raw LLM confidence)

| confidence bin | n | mean confidence | accuracy |
|---|---|---|---|
| 0.0–0.2 | 0 | — | — |
| 0.2–0.4 | 0 | — | — |
| 0.4–0.6 | 21 | 0.50 | 0.10 |
| 0.6–0.8 | 0 | — | — |
| 0.8–1.0 | 22 | 0.87 | 0.64 |

# AutoPilot-QA evaluation — generated suite

99 trials (45 benign UI changes, 54 real defects).

| Metric | Value |
|---|---|
| Healing success rate | 97% |
| False bug-report rate (benign) | 2% |
| Defect detection rate | 44% |
| False heal rate (defects, applied) | 13% |
| False committed heal rate (survived the re-run) | 0% |
| Heal precision (37 applied) | 78% |
| Confidence calibration: Brier / ECE (n=69) | 0.198 / 0.250 |

## Per change

| change | label | trials | correct | with failures | healed | rolled back | reported | all pass |
|---|---|---|---|---|---|---|---|---|
| catalog.add_button_renamed | benign_ui_change | 9 | 9 | 9 | 9 | 0 | 0 | 9 |
| catalog.search_input_renamed | benign_ui_change | 9 | 9 | 6 | 6 | 0 | 0 | 9 |
| cart.checkout_button_renamed | benign_ui_change | 9 | 9 | 6 | 6 | 0 | 0 | 9 |
| order.place_order_renamed | benign_ui_change | 9 | 8 | 6 | 5 | 0 | 1 | 8 |
| payment.pay_button_renamed | benign_ui_change | 9 | 9 | 3 | 3 | 0 | 0 | 9 |
| catalog.add_button_wishlist | real_defect | 9 | 9 | 9 | 5 | 5 | 9 | 0 |
| catalog.add_button_compare | real_defect | 9 | 9 | 9 | 1 | 1 | 9 | 0 |
| cart.checkout_button_missing | real_defect | 9 | 6 | 6 | 1 | 1 | 6 | 3 |
| cart.total_doubled | real_defect | 9 | 0 | 0 | 0 | 0 | 0 | 9 |
| payment.expired_card_accepted | real_defect | 9 | 0 | 0 | 0 | 0 | 0 | 9 |
| catalog.out_of_stock_addable | real_defect | 9 | 0 | 0 | 0 | 0 | 0 | 9 |

## Reliability (raw LLM confidence)

| confidence bin | n | mean confidence | accuracy |
|---|---|---|---|
| 0.0–0.2 | 0 | — | — |
| 0.2–0.4 | 3 | 0.20 | 0.00 |
| 0.4–0.6 | 29 | 0.50 | 0.03 |
| 0.6–0.8 | 0 | — | — |
| 0.8–1.0 | 37 | 0.87 | 0.78 |
