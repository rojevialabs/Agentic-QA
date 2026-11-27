# Healer spike 20260927T170745Z

Mode engine, prompt v2, temperature 0.7, 10 trials, seeds 0..9.

Threshold 0.8. `correct` = heal auto-applies with a verified generic locator (v2) / no auto-apply (decoy). `locator ok` ignores confidence.

| model | scenario | n | parsed | correct | locator ok | would apply | LLM refused | rescued by verify | mean rounds | mean conf | median s | p90 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| llama3.1:8b | decoy_wishlist | 10 | 10/10 | **9/10** | 0/10 | 1/10 | 9/10 | 0/10 | 1.00 | 0.17 | 22.5 | 26.0 |
