[VERDICT] stage=2 sha=090df19d857a659359cc2a6c19e943c7aad6d286 result=REJECT

# Verdict 2 — stage 2 (candidate 2) — closed, superseded by candidates 3 (a6497b4) and 4 (3550522)

- Candidate: 090df19 (main, "[PLAN] stage-2 candidate 2"); clean clone C:/countersign/tmp/aud-s2-v2; `stage-1/` identical to c0f2b7b
- Product delta since b4e805f: WI-13 (R-42). Artifacts: `evidence/stage-2/verdicts/verdict-2-artifacts/`
- Steps 5 and 8 were not run: the candidate was superseded while step 6 finished (step 5 would have followed).

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass (healthy 0.05 s, egress blocked, 4 screens HTML, no external refs) | RUN.md build; internal network, 2 CPU / 2 GiB | 0 |
| 2 | acceptance suites | pass (HTTP 170 + 19 browser-skipped; browser 20/20) | `PREV_IMG=auditor-s1-c5 run_oracle.sh auditor-s2-c2 …` | 0 |
| 3 | differential runner (seed 1 ×50; fresh 371951 ×50) | seed 1 OK (4050 ops); 371951 mismatch = model defect, recorded by R-43 | `run_diff.sh auditor-s2-c2 … <seed> 50 80` | 1 |
| 4 | attacks | fail — 775 checks green, R-43 attacks (added after the ruling) 5 failures (F-1) | `run_attacks.sh … --rounds 3`; `… --groups combo` | 1 |
| 5 | mutation | not run (superseded) | — | — |
| 6 | race proofs | G1–G5 red-without/green-with; G6 red-without, its green-with run failed only on the F-1 checks in the combo group | `race.py`, `race_detect.sh` | — |
| 7 | user-facing checks | pass (116) | `PREV_IMG=auditor-s1-c5 run_ui.sh auditor-s2-c2 …` | 0 |
| 8 | holdout | skip | step 4 red | n/a |

## Findings
- F-1 (product; fixed by WI-15 in candidate 4)
  - clause: R-43 — "When both table_id and table_ids are present, the response is 422 validation_failed whatever the JSON type of either field." (C2.47, C2.54, C2.58)
  - request: POST `{table_ids: ["c_1"], table_id: 5}` and `table_id: null`; PATCH `{table_id: 5, table_ids: ["c_4"]}` and `table_id: null`; move item `{reference, table_id: 5, table_ids: ["c_4"]}`
  - expected: 422 `validation_failed`; actual: 400 `malformed_request` ("table_id must be a string") — 5 cases (a wrong-typed `table_ids` alongside `table_id` already gave 422)
  - reproduce: `bash stage-2/verify/audit/run_attacks.sh auditor-s2-c2 C:/countersign/seats/auditor <out> --groups combo --rounds 1` ("R-43 …" checks)
- Step 3 (fresh seed 371951): model ordered the `table_ids` type check before "both fields"; R-43 confirms the service → Oracle (fixed in f1ec500).

## Counts
- mutation: not run (superseded); race proofs: 6 guards, 6/6 red-without, 5/6 green-with clean (G6 green-with polluted by F-1 only); holdout: not run

## Closure
Candidate 3 (a6497b4: RUN.md retitle + Oracle R-43) was superseded by candidate 4 (3550522: + WI-15) before evaluation; not run.
