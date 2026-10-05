[VERDICT] stage=4 sha=b26757c3d1c4a7e4fbd88cecc6071f2bf7863c51 result=REJECT

# Verdict 3 — stage 4 (candidate 3)

- Candidate: b26757c3d1c4a7e4fbd88cecc6071f2bf7863c51 (main, "[PLAN] stage-4 candidate 3"); supersedes candidate 2 (e00f6bb, verdict 2)
- Clean clone: C:/countersign/tmp/aud-s4-v3; image `docker build --no-cache` → `auditor-s4-c3` (sha256:b1f40d67…)
- Product delta since e00f6bb: WI-30 (R-75) — localtime.AddMinutes, fixture.go, integrity.go, amend.go (plus Go tests); WI-29 (R-76) test only
- Upgrade sources: `auditor-s1-c5` (c0f2b7b), `auditor-s2-c5` (aa63cd2), `auditor-s3-c2` (c4a828e)
- Battery code: seat/auditor (stage-4/verify/audit; identical to the clone's merged copy at the start of the run)
- Contract: C1–C4; rulings R-1…R-76. R-77 was ruled during this run, on the Oracle's F4.
- The Foreman superseded this candidate with candidate 4 (2621e7a) while the battery was running. Steps 5 and 7 were not run.

## Battery
| step | check | result | exit |
|------|-------|--------|------|
| 1 | build and boot (`--internal`, 2 CPU / 2 GiB) | pass: healthy_after_s=0.04; no PORT → 8080, PORT=18300 → 200; 4 pages, 0 external refs; egress blocked | 0 |
| 2 | Oracle acceptance (S1/S2/S3 sources) | pass: HTTP 246/246 (20 browser-skipped), browser 21/21 | 0 |
| 3 | differential (seed 1 ×50; fresh 492695 ×50; 80 ops) | pass: 8100 ops, no mismatch | 0 |
| 4 | attacks | **fail**: 1232 checks green (31 groups incl. s4rulings 16); s4rulings + exactness probes 18/18; the new **r77** group has 20 checks with 4 failing (F-4) | 0 / 1 |
| 5 | mutation | not run: superseded | — |
| 6 | race proofs | pass: 10/10 red-without, 10/10 green-with (G5, 10 rounds: 30 DATA RACE vs 0) | 0 |
| 7 | user-facing | not run: superseded | — |
| 8 | holdout | not run | — |

## Findings
### F-3 (verdict 2, R-75) — closed
s4rulings 18/18 on b26757c, including two new exactness probes that are red on e00f6bb and green here:
- a seeded booking under a 2^31−1-minute duration ends exactly 2147483647 minutes after its start;
- slot_minutes 2^31−1 leaves only the opening slot.

### F-4 — C1.109 / R-24, ruling R-77 (reported by the Oracle; reproduced independently here)
- Clause (R-77): imported restaurant closures and plan closures must name a known table and have two non-null bounds with from < to;
  anything else → 422 and the destination is unchanged.
- Request: reset fixture4; book r_rep a_3 at 18:00 for 4; preview and apply a closure of a_3 over [18:00, 21:30); export; set the closure's
  `from` to null (or remove it), in the restaurant record alone or in every copy; `POST /_test/import`.
- Expected: 422 validation_failed, destination unchanged. Actual: 204, and the state changes.
- `to` null or missing, from == to, from > to, `from` not a time, unknown table and missing table are all refused with 422 and leave the
  destination unchanged, so the two bounds are validated asymmetrically.
- Reproduction: `run_attacks.sh auditor-s4-c3 C:/countersign/seats/auditor <out> --groups r77 --rounds 1` (4 failures of 20)
- Owner: Builder, WI-31 (on candidate 4).

## Counts
- mutation: not run (superseded); race proofs: 10 guards red/green; holdout: not run; escapes: none

## Result
result=REJECT — F-4 (R-77): an imported closure with a null or missing `from` is accepted. Candidate 4 (WI-31) gets the full battery.
