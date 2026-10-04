[EVIDENCE] stage=1 wi=WI-5 sha=3c5e8358d16457d5fa01e4f4b7af8a01a689ad76

# Evidence — WI-5 (export / import with versioned state)

- Revision: 3c5e8358d16457d5fa01e4f4b7af8a01a689ad76 (code); this file is committed on top of it
- Branch: seat/stylist (main 564f21e and seat/builder 276f729 merged in)
- Clauses claimed: C1.105, C1.106, C1.107, C1.108, C1.109, C1.110, C1.112. C1.111 and C1.124 are claimed for
  accounts, hashes, tokens, fixture configuration, reservations, references, ids, statuses and timestamps;
  their receipt part is pending (see Known gaps).

## What was built
- `stage-1/internal/snapshot/snapshot.go` writes the envelope
  `{"track":"tablekeeper","format_version":1,"state":{"schema":1, …}}`.
  - The persisted state is every exported, JSON-tagged field of `state.State`, found by reflection. A
    collection the Builder adds (receipts, counters) is therefore exported and imported with no change to
    this package.
  - `Import` checks the envelope: `track` must be exactly the string "tablekeeper", `format_version` must
    be the number 1, `state` must be an object, and its `schema` must be one this service knows.
  - It then applies `migrations[n]` up to `Current`, the hook that later stages append to.
  - It requires every persisted field to be present with the right JSON kind, decodes with
    `DisallowUnknownFields`, and calls `state.Rebuild`.
  - Every failure is 422 `validation_failed`; an unparseable body or a non-object is 400 through `readObject`.
- `stage-1/internal/state/integrity.go`: `state.Rebuild(st)` checks referential integrity, then rebuilds the
  indexes. It checks:
  - users: unique ids and emails, and a hash is present
  - restaurants: a valid timezone, unique days in the opening hours, and valid tables
  - reservations: ids and references unique; user, restaurant and table exist; status is confirmed or
    cancelled; the times are set
  - tokens: each one names an existing user
- `GET /_test/export` serialises the state under the read lock into bytes, so it is an atomic snapshot that
  later writes cannot change. `POST /_test/import` decodes and validates without holding the lock, then calls
  `store.Replace` and returns 204. A rejected import never touches the store. Neither endpoint needs
  authentication.

## Commands

### `cd stage-1 && go vet ./... && go test ./...`
Exit code: 0
```
ok  	tablekeeper/internal/api
ok  	tablekeeper/internal/jsonin
ok  	tablekeeper/internal/localtime
ok  	tablekeeper/internal/password
ok  	tablekeeper/internal/state
--- PASS: TestExportShape
--- PASS: TestExportImportRoundTrip
--- PASS: TestImportRejectsWithoutChange
```
- The round-trip test checks:
  - a signup after the export does not change the exported bytes
  - importing twice re-exports byte-identical bytes
  - the destination's earlier token and account are gone
  - the source's tokens and logins work after import, and an imported email is taken (409)
  - the seeded reservation keeps its id, owner, status and created_at
  - imported bookings block availability
  - a reset afterwards clears the imported tokens
- The rejection test covers 21 bad bodies; after each one the export must be byte-identical to before:
  - unparseable or array body → 400
  - `{}`, a missing state, the wrong track, `format_version` 2 or "1", state "x" or `{}`, schema 99 → 422
  - a missing `tokens` key, null or wrong-typed `users`, an unknown `user_id`, a bad status, a duplicate
    reference, an unknown timezone, a token for an unknown user, an empty hash, a null user entry → 422

### `python factory/tools/bg.py run --timeout 600 -- docker build -t stylist-tk1:wi5 stage-1`
Exit code: 0

### Two containers, X on 127.0.0.1:18201 and Y on 127.0.0.1:18202 (bg.py start, `docker run --rm --cpus 2 --memory 2g -e PORT=8080`)
Both healthy. The steps ran in the order below:
```
reset X 204; signup bob on X; export X → tablekeeper 1 [reservations restaurants schema tokens users] reservations 2
signup late on X after export → earlier export contains it 0 times
signup carol on Y; import X-export into Y 204; again 204
Y re-export byte-identical to X export
Y import format_version 2 → 422 validation_failed; state {} → 422 validation_failed; '{oops' → 400 malformed_request
Y unchanged after rejected imports (byte-identical export)
X token valid on Y: True | Y pre-import token present: False
login bob on Y 200; login carol on Y 401
reset Y 204; login bob on Y after reset 401
```
### `bg.py stop --name stylist-tk1x`, `bg.py stop --name stylist-tk1y`, `docker stop …`
Exit code: 0. `docker ps -a --filter name=stylist` is empty.

## Known gaps
- C1.111 (completed idempotent requests replay after import; failed keys stay reusable) and C1.124 (batch
  receipts) are not yet covered. The receipt store belongs to WI-4/WI-6 (Builder) and does not exist on any
  branch yet. Once `State` has a JSON-tagged receipts field, it is exported and imported automatically.
  Remaining for the Builder:
  - store the original response as `json.RawMessage` or `[]byte`, so it round-trips byte for byte
  - extend `checkIntegrity` in `state/integrity.go` to cover the new collection
  - rebuild any receipt index in `reindex`
  I will add round-trip replay tests once WI-4 merges.
