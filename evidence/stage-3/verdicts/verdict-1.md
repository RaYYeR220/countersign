[VERDICT] stage=3 sha=1aaececb8efac6444becfd7ec00650e26adcf407 result=REJECT

# Verdict 1 — stage 3 (candidate 1)

- Candidate: 1aaececb8efac6444becfd7ec00650e26adcf407 (main, "[PLAN] stage-3 candidate 1"); clean clone C:/countersign/tmp/aud-s3-v1
- `stage-1/` = c0f2b7b and `stage-2/` = aa63cd2 outside verify (diffs empty); upgrade sources: `auditor-s1-c5`, `auditor-s2-c5`
- Battery: seat/auditor (stage-3/verify/audit @ a634ef1); Oracle stage-3/verify/oracle from the clone (245 HTTP incl. 19 browser-skipped, 20 browser)
- Contract: C3.1–C3.53, C2.1–C2.59, C1.1–C1.134; rulings R-1…R-60
- Artifacts: `evidence/stage-3/verdicts/verdict-1-artifacts/`

## Battery
| step | check | result | command | exit |
|------|-------|--------|---------|------|
| 1 | build and boot | pass (RUN.md literally: `docker build -t tablekeeper-stage2 .`; healthy 0.11 s; egress blocked; screens HTML, no external refs) | — | 0 |
| 2 | acceptance suites | pass (HTTP 226/226 with stage-1 + stage-2 upgrade sources; browser 20/20) | `PREV_IMG=auditor-s1-c5 PREV2_IMG=auditor-s2-c5 run_oracle.sh auditor-s3-c1 …` | 0 |
| 3 | differential runner (seed 1 ×50; fresh 312570 ×50) | fail — seed 1: POST /series `anchor_reference: ""` → model 422, service 404; R-60 confirms the model (F-1); 312570 OK (4050 ops) | `run_diff.sh auditor-s3-c1 …` | 1 |
| 4 | attacks | fail — 1059 checks; only F-1 (2 checks: empty and 65-char anchor_reference) after fixing one harness bug (upgrade3 interval) | `PREV_IMG=… PREV2_IMG=… run_attacks.sh auditor-s3-c1 … --rounds 3` | 1 |
| 5 | mutation (16/21 = 76.2 %) | pass (target 75 %); 22 of 787 evaluated in the 20-min box (stage-3 killers are slow) | `mutate.py … --seed 31 --groups …(18 groups) --oracle …` | 0 |
| 6 | race proofs (8 guards) | pass — G1–G8 red-without; green-with for all, G7/G8 green-with differing only by the F-1 checks | `race.py` (G1–G4, G6–G8), `race_detect.sh` (G5) | 0 |
| 7 | user-facing checks (stage-2 browser regression) | pass (116) | `PREV_IMG=auditor-s1-c5 run_ui.sh auditor-s3-c1 …` | 0 |
| 8 | holdout | skip | steps 3–4 red (product finding) | n/a |

## Outputs (short)
```
step 4 per group: core 94, auth 59, availability 37, create 88, reads 7, cancel 15, patch 59, dst 37, idem 23, moves 57, burst 45, export 96,
  combo 147, comboburst 30, upgrade 18, explain 20, policies 108, history 37, revision 30, series 53 (2 F-1), s3moves 11, s3burst 21, upgrade3 12
  (upgrade3 first run: [stage 1] adoption 409 — harness bug: a weekly series from an imported stage-1 anchor collides with the fixture's
   seeds one week later; fixed to interval 2, re-run 12/12)
step 6: G1 write lock → crash; G2 signup → B8; G3 idempotency → B3/B4; G4 batch (apply(st, changes[:1], now)) → failed batch left changes;
  G5 read lock → 16 data races vs 0; G6 member-wide occupancy → overlaps; G7 already_in_series removed → R3 two adoptions;
  G8 adoption writes partially (planOccurrences returns the planned prefix) → "failed adoption created nothing", first-failing-occurrence checks red
step 5 survivors: integrity.go:93, :153, :195, :202 (import refusals: receipt status / body, policy duration 1440, series revision 1,
  duplicate occurrence) → Oracle; reservations.go:144 (sort `<`→`<=` over unique references) equivalent
```

## Findings
- F-1 (product → WI-21)
  - clause: R-60 — "In POST /series, an anchor_reference that is empty or longer than 64 characters → 422 validation_failed in the value
    pass … before the anchor's 404" (C3.37)
  - request: POST /series `{anchor_reference: "", count: 3, interval_weeks: 2}`; `{anchor_reference: "A"×65, count: 4, interval_weeks: 1}`
  - expected: 422 `validation_failed`; actual: 404 `not_found`
  - reproduce: `bash stage-3/verify/audit/run_attacks.sh auditor-s3-c1 C:/countersign/seats/auditor <out> --groups series --rounds 1`; differential seed 1
- Observation (→ WI-22, docs): `stage-3/RUN.md` is still the stage-2 text (title, tag `tablekeeper-stage2`); the command works.

## Work items for the Oracle (surviving mutants)
- O-14 C3.48 / R-55 / R-24 — import refusals for a stage-3 state: a receipt with a non-2xx status but valid body; a policy with duration
  exactly 1440 (accepted) vs 1441 (refused); a series with revision 1 (accepted, boundary); a series listing one occurrence twice.

## Counts
- mutation: tool mutate.py, killed 16, total 21 (76.2 %; 787 generated, 22 evaluated, 1 invalid)
- race proofs: 8 guards, 8/8 red-without, 8/8 green-with (G7/G8 modulo F-1)
- holdout: not run

## Result
result=REJECT — one product finding (F-1, R-60 → WI-21); everything else green.
