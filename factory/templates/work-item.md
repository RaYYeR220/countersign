[HANDOFF] stage=<n> wi=<WI-n> sha=<base commit> part=<i>/<k>

# WI-<n> — <short title>

- Owner: <Builder | Stylist>
- Stage: <n>
- Base revision: <sha> on `main`
- Clauses: C<stage>.<n>, C<stage>.<n>

## Requirement text
<The complete text of every clause above, quoted from the master ledger. No pointers, no "see above".>

## Acceptance criteria
- C<stage>.<n>: <observable check that proves the clause>
- C<stage>.<n>: <observable check>

## Files likely touched
- `stage-<n>/<path>`

## Definition of done
- [ ] The image builds.
- [ ] Your own tests pass.
- [ ] The container starts on your port range and answers a smoke request; it is stopped afterwards.
- [ ] Committed as `[WI-<n>] <summary> (<clause ids>)`.
- [ ] Evidence packet posted with `[EVIDENCE] stage=<n> wi=<WI-n> sha=<sha>`.

## Budget
Rework rounds used: <0|1|2> of 2. Clean restart: <not yet | done, dossier: <path>>.
