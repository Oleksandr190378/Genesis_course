# Eval results

## Tier 2/3 (execution / output quality) -- anthropic (claude-haiku-4-5-20251001)

### e2e_fasting_pl_cs

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 4/4 (all pass) | 1 | decision_addressed=3, caveats_preserved=4, concrete_recommendation=2, unsupported_claims_absent=5 |
| 2 | 4/4 (all pass) | 1 | decision_addressed=2, caveats_preserved=4, concrete_recommendation=2, unsupported_claims_absent=3 |
| 3 | 4/4 (all pass) | 1 | decision_addressed=3, caveats_preserved=4, concrete_recommendation=2, unsupported_claims_absent=4 |

### e2e_fasting_add_german_refinement

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 2/3 (failed: no_refetch_for) | 4 | decision_addressed=5, caveats_preserved=5, concrete_recommendation=4, unsupported_claims_absent=4 |
| 2 | 2/3 (failed: no_refetch_for) | 4 | decision_addressed=5, caveats_preserved=5, concrete_recommendation=4, unsupported_claims_absent=5 |
| 3 | 2/3 (failed: no_refetch_for) | 4 | decision_addressed=3, caveats_preserved=5, concrete_recommendation=2, unsupported_claims_absent=5 |

### e2e_astronomy_uk

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 3/3 (all pass) | 1 | decision_addressed=5, caveats_preserved=4, concrete_recommendation=5, unsupported_claims_absent=5 |
| 2 | 3/3 (all pass) | 1 | decision_addressed=5, caveats_preserved=4, concrete_recommendation=4, unsupported_claims_absent=5 |
| 3 | 3/3 (all pass) | 1 | decision_addressed=5, caveats_preserved=4, concrete_recommendation=4, unsupported_claims_absent=4 |

### e2e_english_language_3langs

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 2/2 (all pass) | 1 | - |
| 2 | 2/2 (all pass) | 1 | - |
| 3 | 2/2 (all pass) | 1 | - |

### e2e_unsupported_weekly_refinement

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 2/2 (all pass) | 1 | decision_addressed=4, caveats_preserved=5, concrete_recommendation=4, unsupported_claims_absent=5 |
| 2 | 2/2 (all pass) | 1 | decision_addressed=5, caveats_preserved=5, concrete_recommendation=5, unsupported_claims_absent=5 |
| 3 | 1/2 (failed: no_hallucinated_flag) | 3 | decision_addressed=5, caveats_preserved=4, concrete_recommendation=4, unsupported_claims_absent=5 |


## Tier 2/3 (execution / output quality) -- openai (gpt-5-mini)

### e2e_fasting_pl_cs

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 4/4 (all pass) | 1 | decision_addressed=4, caveats_preserved=5, concrete_recommendation=3, unsupported_claims_absent=4 |
| 2 | 4/4 (all pass) | 1 | decision_addressed=4, caveats_preserved=5, concrete_recommendation=3, unsupported_claims_absent=4 |
| 3 | 4/4 (all pass) | 1 | decision_addressed=5, caveats_preserved=5, concrete_recommendation=5, unsupported_claims_absent=5 |

### e2e_fasting_add_german_refinement

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 2/3 (failed: no_refetch_for) | 4 | decision_addressed=5, caveats_preserved=5, concrete_recommendation=4, unsupported_claims_absent=5 |
| 2 | 2/3 (failed: no_refetch_for) | 4 | decision_addressed=5, caveats_preserved=5, concrete_recommendation=4, unsupported_claims_absent=5 |
| 3 | 2/3 (failed: no_refetch_for) | 4 | decision_addressed=3, caveats_preserved=4, concrete_recommendation=3, unsupported_claims_absent=5 |

### e2e_astronomy_uk

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 3/3 (all pass) | 1 | decision_addressed=4, caveats_preserved=5, concrete_recommendation=3, unsupported_claims_absent=5 |
| 2 | 3/3 (all pass) | 1 | decision_addressed=3, caveats_preserved=5, concrete_recommendation=2, unsupported_claims_absent=5 |
| 3 | 3/3 (all pass) | 1 | decision_addressed=5, caveats_preserved=5, concrete_recommendation=5, unsupported_claims_absent=5 |

### e2e_english_language_3langs

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 2/2 (all pass) | 1 | - |
| 2 | 2/2 (all pass) | 1 | - |
| 3 | 2/2 (all pass) | 1 | - |

### e2e_unsupported_weekly_refinement

| repeat | assertions | tool calls | judge scores |
|---|---|---|---|
| 1 | 1/2 (failed: no_hallucinated_flag) | 2 | decision_addressed=1, caveats_preserved=2, concrete_recommendation=1, unsupported_claims_absent=5 |
| 2 | 1/2 (failed: no_hallucinated_flag) | 6 | - |
| 3 | 1/2 (failed: no_hallucinated_flag) | 2 | decision_addressed=3, caveats_preserved=5, concrete_recommendation=4, unsupported_claims_absent=5 |

