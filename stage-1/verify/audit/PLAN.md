# Stage 1 — Auditor attack plan (Tablekeeper reservations API)

Written from the stage-1 specification only. Clause ids are given as spec sections (§n) until the
master ledger (`[RECONCILE] stage=1`) assigns `C1.<n>`; the mapping is added to each verdict.

## Files
| file | purpose |
|---|---|
| `audit.py` | the battery: stdlib Python, ~450 checks in 12 groups, JSON report, exit 0 = no hard failures |
| `run_attacks.sh` | orchestration: two candidate containers (A target, B fresh import destination) on an `--internal` network, `--cpus 2 --memory 2g -e PORT=8080`, and a runner container on the same network |
| `Dockerfile.runner` | runner image `auditor-runner-img` (python:3.12-alpine + tzdata) |

```
bash stage-1/verify/audit/run_attacks.sh <candidate-image> <clean-clone> <out-dir> [--rounds 3] [--groups a,b]
```
On this Windows host, run it through bg.py with the Git Bash binary:
`python factory/tools/bg.py run --timeout 900 -- "C:/Program Files/Git/usr/bin/bash.exe" stage-1/verify/audit/run_attacks.sh ...`

Hard checks fail the run. Soft checks cover readings the specification leaves open; they are
reported, raised with the Foreman for a ruling, and promoted to hard (or deleted) once ruled.

## Cross-cutting (applied to every response)
- No 5xx ever (§5); 4xx/5xx carry `{"error":{"code":str,"message":str}}` (§5); JSON content type (§3.4).
- Per-request latency ≤ 5 s, ≤ 10 s for `/_test/*` (§2).

## Groups
| group | what it attacks | spec |
|---|---|---|
| core | health body, reset 204 + repeat, restaurant list/detail in fixture shape (public), 404 unknown/65-char id, seeded bookings + seeded login, unknown query/body fields ignored | §3, §4, §8 |
| auth | signup/login shapes, multiple live tokens, `email_taken` (incl. seeded), password 7/8 chars, email forms, wrong JSON types → 400, missing → 422, unparseable → 400, bad/absent/foreign-scheme bearer → 401 on every protected endpoint, public endpoints need no token | §5, §6 |
| availability | 30-min grid with `slot+duration ≤ closes` (thu 8 slots, fri 9), capacity filter in fixture order, empty lists kept, closed day `[]`, half-open occupancy around a booking, IANA offsets, missing params → 422, invalid date / integer query forms (`1e9`, `4.0`, `+4`, ` 4`, `0x4`) → 422, unknown restaurant 404 | §5, §8 |
| create | full 201 shape and derived times, GET equals create, overlap 409 vs back-to-back 201, off-grid, before/after hours, ends exactly at `closes`, closed day, capacity, `party_size` matrix (0, -1, "2", true, 2.5, null) → 422, `starts_at_local` matrix (seconds, Z, offset, space, invalid date, 24:00, non-padded) → 422, number → 400, wrong-type ids → 400, missing fields → 422, unknown/foreign table 404, Idempotency-Key absent/empty → 400, 256 → 422, 255 → 201, past and inside-cutoff starts allowed, reference format and uniqueness | §4, §5, §7, §8 |
| reads | own list only, confirmed + cancelled, `starts_at` descending, entry shape, 404 for another user's / unknown reference | §8 |
| cancel | 200 cancelled, twice 200, inside cutoff and past → 409 `cutoff_passed` and still confirmed, 404 foreign/unknown, table freed immediately and rebookable | §8 |
| patch | self-overlapping move allowed (release+reserve together), identity kept, `ends_at` recomputed, old slot released, every failure (capacity, party, grid, hours, closed, format, foreign booking overlap, unknown/foreign table, wrong types, nonexistent local time) leaves the booking byte-identical, 404 foreign/unknown, cutoff on current start, cancelled → 409 | §8, §9 |
| dst | Berlin and New York spring/fall slot lists (skipped hour absent, repeated hour once with first-occurrence offset), booking the skipped hour → `invalid_local_time` (POST and PATCH), absolute-duration `ends_at` across both transitions, overlap computed in absolute time (discriminates wall-clock implementations), winter/summer offsets | §9 |
| idem | replay 200 identical (also with reordered keys and whitespace), no duplicates, different body → 409 even when that body is invalid, parse error and auth precede idempotency, key scoped per user and per path, replay after PATCH and after cancel returns the original, failed keys (422, 404, 409) reusable, 255-char key | §7 |
| moves | table swap in one batch, chain into a slot vacated by a later item, replays (also after cancellation), key reuse 409, unlisted-booking collision → 409 and nothing changes (records and occupancy), failed key reusable, overlap among results, no-op items, shape matrix (0, 9, duplicates, missing reference), 8 allowed, 404 unknown/foreign, cross-restaurant 422, cutoff 409 and its precedence, input-order precedence, cancelled 409, 401, missing/long key | §7, §11 |
| burst | per round (default 3, fresh reset each): B1 50 identical-slot creates → one 201 + 49 `table_unavailable`; B2 45 overlapping starts → only 201/409; B3 30 identical keyed creates → one 201 + 29 identical 200, effect once; B4 same key two bodies → one creation; B5 20 identical keyed batches → one 201 + 19 identical 200; B6 8 PATCHes onto one slot → one 200 + 7 409; B7 10 competing two-item batches → one 201, every batch all-or-nothing; B8 20 same-email signups → one 201; B9 50 mixed requests → no 5xx; B10 50 in-flight reads < 5 s; then a global no-overlap / unique-reference invariant | §1, §2, §6, §7, §11 |
| export | export shape, no plaintext passwords in state, invalid imports (missing fields, wrong track/version, bad state, unparseable) → 422/400 with destination unchanged, import into the same server and into a fresh container: old destination credentials gone, imported tokens and hashed-password logins work, reservations and config identical, create and batch replays return original bodies, reused key with new body 409, failed key reusable, references don't collide, occupancy enforced, repeated import = replacement, reset clears imported state; exports taken under concurrent writes import cleanly and satisfy the invariant | §6, §10 |

Version upgrade (export from the previous stage's image, import into the candidate) does not apply to
stage 1; from stage 2 on, `run_attacks.sh` gains an upgrade step that exports from the accepted
`stage-<n-1>` image.

## Other battery steps prepared for the verdict
- Step 1: build per `RUN.md` literally from a clean clone; boot on an internal network, 2 CPU / 2 GiB;
  `audit.py` records seconds to first healthy response (limit 60 s).
- Step 5 (mutation): tool chosen once the Builder's ADR fixes the stack, 20-minute box, target 75%
  kill rate on the domain core; survivors go to the Oracle.
- Step 6 (race proofs): for every concurrency/atomicity guard found in the candidate (lock around
  the state machine, idempotency claim, batch commit, signup uniqueness), build a scratch image with
  the guard disabled, show B1/B3/B5/B7/B8 (as relevant) red, restore and show green.
- Step 7: no user-facing surface in stage 1 (skip with reason).
- Step 8: holdout `harness_win.py run --track tablekeeper --stage 1 --mode isolated` only when 1–7 are green.

## Ambiguities raised for rulings (currently soft checks)
1. `moves` item field errors: is `party_size: 0` an "invalid shape" (422 up front for the whole
   batch) or an ordinary amendment error subject to cutoff-first and input-order precedence?
2. `moves` item with a wrong-type `table_id` (number): 400 `malformed_request` (§5) or 422 (§11 "invalid shape")?
3. `moves` with `moves` not an array / items not objects: 400 or 422 (both accepted for now).
4. `PATCH {}` (empty subset): 200 unchanged expected.
5. `PATCH` moving a far booking to a start inside the cutoff: allowed (cutoff measured on the current start).
6. Body that is valid JSON but not an object (`[]`): 400 `malformed_request` expected.
7. `signup` without `display_name`, `login` without `password`: 422 expected.
8. Import of `state: {}` or an unrecognised state object: 422 expected.
