[VERDICT] stage=2 sha=3550522c51ffd3c6f6ec64dfe1a7666dc2abf234 result=REJECT

# Verdict 3 — stage 2 (candidate 4) — closed, superseded by candidate 5 (aa63cd2)

- Candidate: 3550522 (main, "[PLAN] stage-2 candidate 4"); clean clone C:/countersign/tmp/aud-s2-v3; `stage-1/` identical to c0f2b7b
- Product delta since 090df19: RUN.md retitled for stage 2 (tag `tablekeeper-stage2`) and WI-15 (R-43). Artifacts: `verdict-3-artifacts/`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass (RUN.md literally: `docker build -t tablekeeper-stage2 .`; healthy 0.03 s; egress blocked; 4 screens HTML, no external refs) | — | 0 |
| 2 | acceptance suites | pass (HTTP 170 + 19 browser-skipped; browser 20/20) | `PREV_IMG=auditor-s1-c5 run_oracle.sh auditor-s2-c4 …` | 0 |
| 3 | differential runner (seed 1 ×50; 371951 ×5; fresh 976792 ×50) | fail — seeds 1 and 976792: PATCH with both table fields on a foreign/unknown reference → model 422, service 404; R-44 confirms the model → product finding F-1; 371951 OK | `run_diff.sh auditor-s2-c4 …` | 1 |
| 4 | attacks | pass (786 checks) before R-44; with the R-44 attacks added in this verdict, combo 137 checks, 4 failures = F-1 | `run_attacks.sh …`; `… --groups combo` | 1 |
| 5 | mutation (39/42 = 92.9 %) | pass (target 75 %) — run hit the 1700 s wrapper timeout after its 1200 s budget (one confirmation container hung); counts rebuilt from the per-mutant log (`mutation-reconstructed.json`) | `mutate.py … --seed 23 …` | 124 |
| 6 | race proofs (6 guards) | pass (6/6 red-without, 6/6 green-with; G5 race detector 21 vs 0) | `race.py`, `race_detect.sh` | 0 |
| 7 | user-facing checks | pass (116) | `PREV_IMG=auditor-s1-c5 run_ui.sh auditor-s2-c4 …` | 0 |
| 8 | holdout | skip | step 3 red (product finding) | n/a |

## Findings
- F-1 (product → WI-16; fixed in candidate 5 per the Foreman)
  - clause: R-44 — "R-43's both-fields check runs where the type pass runs. In PATCH: 401 → 400 body or other fields' types → 422 both fields
    → 404 not the caller's → … In moves, step (b) runs per item in input order (both fields → 422, otherwise types → 400), before (c) the
    per-item 404." (C2.54, C2.58)
  - request: PATCH /reservations/COMBO1 as a non-owner with `{table_id: "c_3", table_ids: ["c_3","c_4"]}`; PATCH /reservations/ZZZZZZ with
    both fields; a move item on another user's reference with both fields; moves `[{reference: "ZZZZZZ"}, {own ref, table_id + table_ids}]`
  - expected: 422 `validation_failed`; actual: 404 `not_found` (4 cases; differential seeds 1 and 976792)
  - reproduce: `bash stage-2/verify/audit/run_attacks.sh auditor-s2-c4 C:/countersign/seats/auditor <out> --groups combo --rounds 1` ("R-44 …")

## Counts
- mutation: tool mutate.py, killed 39, total 42 (92.9 %; 570 generated, 45 evaluated, 3 invalid; reconstructed from log)
- race proofs: 6 guards, 6/6 red-without, 6/6 green-with
- holdout: not run
