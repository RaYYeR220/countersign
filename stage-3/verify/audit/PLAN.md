# Stage 3 — Auditor attack plan (policies, history, recurring reservations)

Written from `stage-3.md` plus the binding stage-1/2 contracts (C1, C2) and rulings R-1…R-59. Stage-3 clause ids
(C3.n) are attached after reconciliation; until then checks carry section names ("S3 …") and the ruling they enforce.

## Files
`audit.py` (HTTP battery: all stage-1/2 groups as regression + `explain`, `policies`, `history`, `revision`, `series`,
`s3moves`, `s3burst`, `upgrade3`), `run_attacks.sh` (`PREV_IMG` = accepted stage-1 image c0f2b7b, `PREV2_IMG` = accepted
stage-2 image aa63cd2), `ui_audit.py`/`run_ui.sh` (stage-2 browser regression), `run_oracle.sh`, `run_diff.sh`, `mutate.py`,
`race.py`, `race_detect.sh` (all containerised).

## Stage-3 HTTP attacks
| group | attacks | rulings |
|---|---|---|
| explain | stage-2 shape without explain (exact keys); every table once in fixture order, both rules in order, available ⇔ both hold, ids == available_table_ids; both-false table; full explain on a slot with no table; closed day `[]`; 6 invalid values → 422; invalid explain before the 404 and after party_size; repeated explain = first value | R-46 |
| policies | manager_user_ids reset validation (6 refused fixtures, state unchanged; optional); 401/403/404/missing key; R-47 order (key before 403, 403 before validation, 404 before validation); 25 invalid policies → 422 incl. wrong JSON types, null, closes ≤ opens, duplicate weekday, capacities not exact / out of range; no version allocated by failures; exact 201 keys, unknown field not echoed; replay 200 / reuse 409 / failed key reusable; `30.0` integral; GET public, publication order, policy 0 omitted, unknown restaurant 404; detail unchanged (+ manager_user_ids, combinable); existing bookings and histories untouched by publication; selection by local start date (past effective date, same-date tie → greater version, before every policy → policy 0); capacities, grid, hours and duration of the selected policy (incl. pair sum); availability + explain policy_version; real amendment adopts the resulting date's policy (terms, ends_at, revision); failed amendment unchanged; no-op keeps policy-0 terms where the new policy would refuse; cutoff from the accepted terms vs a later cutoff-0 policy | R-47, R-48, R-49, R-59 |
| history | created entry (all three fields from null, revision 1, terms, `at` = created_at in the restaurant offset); seeded booking one created entry; replay records nothing; changed lists only changed fields in order; no-op, same-set and failed PATCH record nothing; cancelled empty and last; repeated cancel nothing; seq and at order; owner-only 404 incl. no token / invalid token / manager; decision shape after cancel; pair creation `table_ids`, reversed pair not an amendment, pair→single full lists; old entries never acquire newer terms | R-51, R-56, R-58 |
| revision | expected_revision current → change; stale → 409 before validation and cutoff; 7 invalid values → 422; R-49 order (400 types → 404 → 422 → stale → cancelled → cutoff); matching expected_revision alone is a no-op; stale on a no-op → 409; cancel +1, repeat +0, ignores expected_revision; replay keeps original revision and terms | R-49, R-50, R-58 |
| series | 201 shape (exact keys, id ≤ 64), anchor = occurrence 0 unchanged (response, history), weekly dates, same table/party, distinct refs, listed, occupying, own histories; GET owner-only 404s; replay 200 / reuse 409 / replay after changes = original; already_in_series for the anchor and a generated occurrence; 9 invalid count/interval → 422; anchor_reference number → 400; missing → 422; invalid count before 404; unknown / foreign anchor 404; cancelled 409; anchor inside cutoff 409; occupancy failure 409 with nothing created and key reusable; spring-forward occurrence → invalid_local_time; fall-back → first occurrence; per-occurrence policy (v4, v3, v2 incl. duration); first failing occurrence decides (capacity at 1 before hours at 2); exception flag + series revision on real PATCH only; cancel +1 without exception; anchor cancel leaves siblings | R-52, R-53 |
| s3moves | invalid per-item expected_revision 422; stale 409; occupancy failure changes nothing (revisions, histories, flags); success: changed occurrences become exceptions, series +1 once, each changed booking +1 and one entry, no-op item untouched; replay changes nothing | R-57 |
| s3burst | per round: R1 10 PATCHes sharing expected_revision → one real change; R2 20 concurrent publications → versions exactly 1..20; R3 10 concurrent adoptions of one anchor → one 201, nine already_in_series; R4 16 concurrent writes on one booking → dense seq, revisions follow, nothing after cancelled; R5 identical keyed publications and adoptions → one 201 + identical 200s, one version; global overlap invariant | R-49, R-54 |
| upgrade3 | exports from the accepted stage-1 and stage-2 images → candidate: old token + lookup, revision 1 under policy 0, created history, original retry body, adoption of an imported booking, no policies | R-55 |

Browser regression: the stage-2 `ui_audit.py` (116 checks) against the stage-3 candidate (grid follows stage-2 rules).

## Self-test
Stylist branch (seat/stylist, WI-19 explain): core + create + combo + explain = 345 checks, 0 failures. Policies,
history, revision, series, moves and upgrade groups are validated on the first build that implements WI-17/18/20.
