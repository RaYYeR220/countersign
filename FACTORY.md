# Countersign

Nothing ships on one signature.

## 1. What it is

Countersign is a five-seat software factory for Band Desktop. One human message, the dispatch, hands it a
multi-stage specification and a result repository. From there the seats plan, build, verify and accept every
stage on their own, then post `[FINAL]`. Nothing in `mandates/` or `factory/` depends on the problem; the
problem arrives only in the dispatch.

In the submitted run all four tablekeeper stages were accepted, and `[FINAL]` came 12 h 59 min after the
dispatch. The dispatch was meant to be the only human input. At stage 4 the band deadlocked and the operator sent one more message, a
liveness note that names no work item or fix. Section 8 says what happened and what changed in the kit.

**Thesis.** Two independent seats derive every artifact, and the two must agree before it ships:

1. The spec is read twice: two clause ledgers, written blind, then reconciled.
2. Behaviour is derived twice: the implementation, and an executable reference model by a seat that never
   sees the implementation. The two are differential-tested.
3. The tests are tested, with a mutation kill-rate gate and a race proof for every concurrency guard.
4. Acceptance is countersigned by the only seat that may accept, and that seat may not write fixes.

The verifier is itself verified: a green suite counts only after it has been shown it can go red.

## 2. Seats

| Seat | Harness | Model | Owns | May not | Enforced by |
|---|---|---|---|---|---|
| Foreman | Claude Code | `claude-opus-5-5` | intake, ledger A, reconciliation, rulings, work items, merges, retry budget, stage reports | write product code; accept | guard (writes); protocol (only an Auditor ACCEPT closes a stage) |
| Builder | Claude Code | `claude-opus-5-5` | core logic, interfaces, state, concurrency, migrations, ADR, unit tests | read verification suites or the holdout; edit ledgers or verdicts; run the acceptance harness | sparse checkout without `stage-*/verify/`; guard |
| Stylist | Claude Code | `claude-opus-5-5` | user-facing surfaces, design direction, screenshots; other work items when a stage has no UI | same as Builder | same as Builder |
| Oracle | Claude Code | `claude-fable-5-1` | ledger B, reference model, acceptance suites, differential runner, regression tests | read the implementation or the holdout; write outside `stage-*/verify/`, `evidence/` | sparse checkout of only `evidence/`, `factory/`, `mandates/`, `stage-*/verify/`; guard (reads, writes, and `git show`, `cat-file`, `archive`, `log -p`, `diff` against other branches) |
| Auditor | Claude Code | `claude-opus-5-5` | gate battery, verdicts, holdout, escape accounting | write outside `stage-*/verify/`, `evidence/` | guard |

The guard (`factory/hooks/guard.py`, rules in `factory/roles.json`) also stops every seat from asking the
operator (`AskUserQuestion`, plan mode), rewriting or pushing history, administering the room, pruning shared
Docker state, reading credentials or seat profiles, and writing to `mandates/`, `factory/`, the guard itself,
or outside the workspace. Seats run at effort `high` with `bypassPermissions` on a Claude subscription. Each
seat commits under its own git identity on its own branch.

**Why five.** Author, verifier and acceptor are always different seats. Two implementers let core and UI run
in parallel. The Foreman writes no code, so authorship doesn't bias its merges or rulings. Cost: more
messages, and the Foreman is a serial bottleneck.

**Why the Oracle runs a different model.** Two readers on one model tend to misread the same sentence the same
way, and reconciling two identical mistakes finds nothing. A different model decorrelates less than another
model family would (section 6 says why that was dropped); the blind setup carries the rest: the Oracle writes
its ledger without seeing entry A and builds its model without seeing the code.

## 3. Stand it up

You need Band Desktop with its `band` CLI, Git, Docker, Python 3.12+ (all factory code is stdlib-only), Go
(once, for the launcher), the Claude Code CLI with a subscription or API access, Node.js (for `npx ccusage`
when you measure cost), and the job package: specs plus an optional acceptance harness. Commands run from the
repository root.

**1. Clean seat profile (once).** Seats must not load the operator's Claude Code setup: without a pinned
profile a seat came up with 132 tools, including the operator's MCP servers. With the launcher it has 19 plus
Band's own MCP.

```sh
cd factory/setup/launcher
go build -o ~/.claude-countersign/seat-launcher.exe .
```

Put the real `claude` path in `~/.claude-countersign/claude-path.txt`; the launcher sets `CLAUDE_CONFIG_DIR` to
its folder. Log in once: `CLAUDE_CONFIG_DIR=~/.claude-countersign claude`, then `/login`. The kit can also run a
seat on OpenCode (`factory/setup/launcher/README.md`); the submitted run did not.

**2. Mandates.** Each mandate is a role file plus the shared `protocol.md`. Build them with
`python factory/build_mandates.py --src factory/mandates-src --out mandates`. In this repository `mandates/` is
the frozen v1.0 set the submitted run used. `factory/mandates-src/` holds v1.1, which adds the two liveness
rules from section 8, and `factory/mandates-v1.1/` is its build.

**3. Lay out a run.**

```sh
python factory/setup/new_run.py --workspace <WS> --kickoff <job package> --track <track> \
  --holdout <WS>/kickoff/<track>/test --spec <WS>/kickoff/<track>/spec/stage-1.md ... \
  --operator-name <name> --operator-email <email> --python <python command for the hook>
```

```
<WS>/kickoff/       job package, copied once
<WS>/result/        result repo, branch main, the Foreman's checkout; first commit = this kit
<WS>/seats/<seat>/  git worktree per seat on seat/<seat>, own git identity, sparse where the role needs it
<WS>/guard/         frozen roles.json + guard.py, outside every checkout
<WS>/logs/ tmp/     guard log; scratch
<WS>/DISPATCH.md    the one human message, from factory/DISPATCH.template.md
```

The script hooks the guard into every Claude Code seat's checkout as a `PreToolUse` hook in
`.claude/settings.local.json`. It refuses a workspace that already holds a run unless you pass `--archive`.

**4. Create the seats (once).**

```sh
python factory/setup/seats.py --workspace <WS> --launcher ~/.claude-countersign/seat-launcher.exe   # try --dry-run first
```

This runs `band agent create` per seat with the harness and model from each mandate's first two lines,
`bypassPermissions`, effort `high` and a short list of disallowed tools. Each seat reads its mandate live from
`<WS>/result/mandates/<seat>.md`, so new runs never re-point seats.

**5. Band Desktop runtime settings.** Set human approval wait to 60 s, so a stray permission prompt becomes a
denial, not a stall. Set question timeout to 60 s, so a question that slips past the guard resolves to
"proceed with your best judgment". Set room activity feed to `tools`, so tool calls land in `room.json`.

**6. Dispatch.** In a fresh room with all five seats, send `DISPATCH.md` as the only human message:
`band room send <room> "$(cat <WS>/DISPATCH.md)" --mention <foreman id>`. Then send nothing until `[FINAL]`.

**7. Collect.** Download the full session as `room.json` (see the participant guide). The console exports only
the messages it has loaded, so load the whole history first. Then:

```sh
python factory/tools/seat_usage.py --profile ~/.claude-countersign --since <YYYYMMDD> --after <dispatch time, ISO> \
  --seat foreman=<WS>/result --seat builder=<WS>/seats/builder --seat stylist=<WS>/seats/stylist \
  --seat oracle=<WS>/seats/oracle --seat auditor=<WS>/seats/auditor --out <WS>/logs/usage.json
python factory/tools/metrics.py --room room.json --repo . --usage <WS>/logs/usage.json \
  --guard-log <WS>/logs/guard.jsonl --out evidence/metrics.md
python -m harness check <WS>/result --track <track>        # from the job package
```

`band usage agents --json` reads only the operator's default Claude Code profile, so it cannot see seats on
their own `CLAUDE_CONFIG_DIR`; `seat_usage.py` runs `ccusage` on each seat's own session logs instead.

Kit self-checks: `python -m pytest factory/tests`.

## 4. Stage pipeline

| Step | Who | Produces (under `evidence/stage-<n>/` unless noted) |
|---|---|---|
| KICKOFF | Foreman | the complete stage spec, sent to every seat |
| LEDGER A / B | Foreman and Oracle, each blind to the other | `ledger-A.md`, `ledger-B.md`: per normative sentence, a quote, observable behaviour, error precedence and acceptance criterion |
| RECONCILE | Foreman | `ledger.md` (the stage contract), `rulings.md`; both readers re-check single-reader clauses. The contract is not frozen, and no candidate goes to the Auditor, until entry B is reconciled |
| PLAN | Foreman | `work-items.md`: work items mapped to clauses, with full clause text |
| BUILD | Builder, Stylist; Oracle, Auditor in parallel | one commit per work item, an `[EVIDENCE]` packet each |
| CANDIDATE | Foreman | merge to `main`, image builds, SHA + full requirements to Auditor |
| VERDICT | Auditor | `verdicts/verdict-<k>.md`: ACCEPT, or REJECT with findings by clause |
| REWORK | owners | findings become work items under the retry budget |
| CLOSE | Foreman | `report.json`, `report.md`; folder copied to `stage-<n+1>/`; `[FINAL]` at the end |

Every protocol message opens with a header line that `metrics.py` parses:

```
[KIND] stage=<n> wi=<WI-n> sha=<commit> result=<ACCEPT|REJECT> id=<R-n> part=<i>/<k>
KIND: KICKOFF LEDGER RECONCILE PLAN HANDOFF EVIDENCE CANDIDATE VERDICT FINDINGS RULING ESCALATION CLOSE FINAL
```

Commits read `[WI-<n>] <summary> (<clause ids>)`, linking code to a work item, a clause and a room message.
A handoff always carries the full requirement text, never "see above". Mention only the seat that has to act,
and never reply to an acknowledgement. Questions go to the Foreman as `[ESCALATION]`. Servers go through
`factory/tools/bg.py`, and each seat has its own port range. Templates: `factory/templates/`.

## 5. How the factory catches bad work

The Auditor runs this battery on every candidate, from a fresh clone, in order:

1. **Build and boot** per `RUN.md` on an internal network with no egress, 2 CPU, 2 GiB.
2. **Oracle acceptance suites** over HTTP and a headless browser, at least one test per clause.
3. **Differential runner** against the reference model: seeded random sequences, mismatches shrunk to a minimal
   reproduction, every seed recorded.
4. **Attacks** from the spec: synchronised conflicting bursts and replays (`factory/tools/burst.py`),
   malformed input, and a version upgrade from the previous stage's export.
5. **Mutation** of the domain core, 20-minute box, 75% kill target. Survivors become Oracle work items, never
   implementer ones.
6. **Race proofs**: disable each concurrency guard in a scratch copy and the test must fail; restore it and it
   must pass.
7. **UI checks**: every named state, no horizontal scroll at 375 px, keyboard reach, screenshots at
   375/768/1280.
8. **Holdout, last**, only once 1–7 pass: the event's shipped checks in isolated mode. Each failure is an
   escape. It goes to implementers as a clause finding, never as test code, and the Oracle adds a regression
   test.

Around it:
- **Double-entry ledger.** Clauses only one reader found are where hidden requirements live.
- **Either side can be wrong.** A model/implementation mismatch is judged against the quoted spec, and the side
  that contradicts it gets fixed.
- **Retry budget.** Each work item gets two rework rounds, then a clean-context restart with the rejection
  dossier. After that the Foreman re-plans, rules, or records a blocker. A finding repeated twice in a row goes
  straight to the Foreman.
- **Rulings instead of questions.** Take the literal reading. If that leaves options, take the one safest for
  stored state and least surprising to a client. Rejected options are recorded, and the ruling binds every
  seat.
- **Self-calibration.** Oracle and Auditor prove their tools can fail before trusting a pass, using injected
  defects and broken fixtures.

### From the submitted run

The service is a Go 1.26 binary (`net/http`, state in memory behind one `sync.RWMutex`, browser UI embedded with
`embed.FS`, bcrypt from a vendored `golang.org/x/crypto`; `evidence/adr/ADR-001.md` to `ADR-004.md`). The
Oracle's reference model is a separate Python implementation.

- **An escape after the band ruled the wrong way (stage 1).** The spec says a booking reference is 6 to 12
  characters of `A-Z0-9`. The Oracle's tamper test expected import to refuse `bad ref`; the service accepted
  it. On the Auditor's advice the Foreman ruled the format binding only on references the service issues
  (R-26), and the test became a "test defect". The holdout, run last on the same SHA, failed 3 of 120 checks on
  exactly that point. The Foreman withdrew R-26 and ruled the opposite (R-28), the Builder tightened validation
  (WI-7), the Oracle restored its test, and the next candidate passed 120/120. It was the run's only escape.
  `evidence/stage-1/verdicts/verdict-4.md`, `verdict-5.md`, `evidence/stage-1/rulings.md`.
- **A surviving mutant that became a product fix (stage 4).** A mutant survived at the fixture-integer bound,
  so the Oracle, which owns survivors, wrote a test at 2^31−1. It found an overflow: a 2^31−1-minute booking
  duration gave an `ends_at` nine years before the start, and a 2^31−1-minute cancellation cutoff still allowed
  a cancel. The Foreman ruled exact arithmetic (R-75), the Auditor reproduced the fault and rejected the next
  candidate (verdict 2), and the Builder fixed it (WI-30). `evidence/stage-4/oracle-o15b-o16.md`,
  `evidence/stage-4/verdicts/verdict-2.md`.
- **Rejections that changed the work.** 8 of the run's 11 rejections changed the product. At stage 2: ids
  that answered 404 where the contract says 422 (13 cases, WI-13), then two error-precedence faults (WI-15,
  WI-16). At stage 4: import accepted a history that starts with `changed` (R-74, WI-28), and a closure with a
  null `from` while refusing every bad `to` (R-77, WI-31; found by the Oracle, reproduced by the Auditor). Each
  finding quotes the clause and gives the request, expected and actual response, and a reproduction. None
  reached the holdout. `evidence/stage-2/verdicts/`, `evidence/stage-4/verdicts/`.
- **The verifier was wrong too, and the gate said so.** Stage 1's first three verdicts found no product defect:
  they rejected a missing differential runner, a reference model that re-issued a discarded reference, and a
  tamper test that edited the wrong copy of a value. At stage 4 an Oracle tamper test that crashed in its own
  setup was recorded as an Oracle defect, and an Oracle report run against an older image (R-76) did not
  reproduce, so it got a regression test and no code change (WI-29). `evidence/stage-1/verdicts/verdict-1.md`
  to `verdict-4.md`, `evidence/stage-4/verdicts/verdict-2.md`.
- **Race proofs that bite.** Guard G6 makes a booking on a table pair occupy both tables. With that check cut
  to the first table of each set, 7 checks fail, among them 8 concurrent amendments onto pairs sharing a table
  producing more than one success; restored, none fail. On stage 4's second candidate the read-lock proof (G5)
  saw 0 data races with and without the lock over 5 rounds; the Auditor called that inconclusive and ran 10:
  7 races without, 0 with. Every guard is re-proven at every stage: 5, 6, 8 and 10 guards at stages 1–4.
  `evidence/stage-2/verdicts/verdict-4-artifacts/race/G6.json`, `evidence/stage-4/verdicts/verdict-2.md`.
- **Two readers.** At stage 1, entry B found 10 clauses entry A missed, and A none that B missed. Most were
  overview sentences A had read as scope. One, "Each restaurant has its own table capacities, opening hours and
  cancellation policy", became a criterion: two restaurants with different cut-offs in one fixture must answer
  the same request differently. Stages 2–4 had one single-reader clause between them.
  `evidence/stage-<n>/ledger.md`, rows marked "B only".

## 6. Design choices and what they cost

| Choice | Cost |
|---|---|
| Five seats, one acceptor | more messages and tokens; serial Foreman |
| One dispatch for all four stages | no human checkpoint between stages, so a stall stays a stall until someone looks (section 8) |
| Oracle on a different model (`claude-fable-5-1`) | less decorrelation than another family; the most expensive seat (42% of spend) |
| Double-entry ledger before building | a second read and a reconciliation on every stage's critical path |
| Blind reference model + differential runner | a second implementation per stage (a Python model against the Go service) |
| Mutation gate + race proofs | up to 20 min per candidate, a rebuild per guard, equivalent mutants triaged by hand |
| Holdout last, implementers never see tests | escapes show up late (stage 1's surfaced on the fourth candidate), and each must be rephrased as a clause |
| Boundaries in code, not prompts | ~500 lines of guard plus tests; some honest work blocked (in the submitted run the Oracle could not `git show` a verdict file) |
| Worktree and git identity per seat | the Foreman does every merge; conflicts go back to the file owner |
| Clean seat profiles | a separate login and a Go build |

Checking costs more than building. Over the whole run, Oracle and Auditor took 65% of spend, Builder and
Stylist 25%, the Foreman 10%.

### What we tried that failed

- **The Oracle on another model family.** The first design ran the Oracle on OpenCode with `kimi-k3` via Venice,
  chosen by a spike in which each candidate model wrote a spec-derived suite against a known-good service
  (`kimi-k3` 25/25 passing; `gpt-6-sol` 19 passed, 11 skipped; `gemini-3-1-pro-preview` wrote 10 tests). In
  real runs it failed twice. In the toy rehearsal it started a server inside a shell call; the server held the
  call's output pipe, and the seat sat in one turn for over two hours. That produced `factory/tools/bg.py`
  (detached start/stop with a health check, and `run --timeout`, which kills the process tree), now required by
  the protocol. In the first tablekeeper development run it looped for about an hour on its own to-do list and
  never produced entry B, so the Foreman went ahead on entry A alone. We moved the Oracle to Claude Code on
  `claude-fable-5-1` and added the rule that nothing goes to the Auditor until entry B is reconciled. In the
  submitted run the Oracle committed entry B before any of its model or suite code at every stage, and every
  stage's contract was reconciled with it before the first candidate.
- **OpenCode needed two workarounds.** It loads Claude Code instruction files, so it read the operator's
  `CLAUDE.md` (`OPENCODE_DISABLE_CLAUDE_CODE=1` and a private `XDG_CONFIG_HOME` stop it). Band starts a custom
  spawn command with no default arguments (`seats.py` passes `--spawn-arg acp`).
- **`AskUserQuestion` can't be disallowed through Band.** The guard denies it, with the 60 s question timeout as
  backstop.
- **On Windows, the harness failed in isolated mode.** It passes its Linux runner a backslashed suite path. A
  30-line wrapper, `factory/setup/harness_win.py`, rewrites that argument.
- **Docker Desktop won't forward ports from `--internal` networks.** Verification goes through a probe
  container instead, and the dispatch says so.
- **No Docker Sandboxes.** Band couldn't sandbox the first design's OpenCode seat, and the hooks, launcher and
  worktrees were built for seats on the host.

## 7. Measured cost and time

How each figure is measured:

- **Spend:** `seat_usage.py` runs `ccusage` on each seat's own session logs from the dispatch on (one session
  per seat). The seats run on a subscription, so this is an API-equivalent list-price estimate, not money spent
  (`evidence/usage.json`).
- **Wall time:** from the previous stage's close commit (the dispatch, for stage 1) to the Auditor's accepting
  verdict commit. Measured from `room.json` instead (first `[KICKOFF]` to the accepting `[VERDICT]`), each
  stage is within two minutes of this.
- **Everything else:** `room.json`, `git log --all`, `evidence/stage-<n>/report.json` and
  `evidence/guard.jsonl`. `factory/tools/metrics.py` joins them into `evidence/metrics.md`; anything it can't
  measure reads `n/a`, never zero.

**Time.** Room `cs-tablekeeper`, one dispatch for all four stages at 12:15 UTC on 2026-10-04, `[FINAL]` at
01:14 UTC on 2026-10-05: 12 h 59 min in all. Times are UTC.

| Stage | Started | Accepted | Wall time | Candidates | Verdicts | Accepted SHA |
|---|---|---|---|---|---|---|
| 1 | 12:15 (dispatch) | 16:54 | 4 h 39 min | 5 | 6 | `c0f2b7b` |
| 2 | 16:55 | 19:44 | 2 h 49 min | 5 | 4 | `aa63cd2` |
| 3 | 19:46 | 22:18 | 2 h 32 min | 2 | 2 | `c4a828e` |
| 4 | 22:19 | 01:12 (next day) | 2 h 54 min | 4 | 4 | `2621e7a` |

Stage 4's wall time includes the idle gap from 22:35 to 22:45, when every seat had ended its turn and nothing
happened until the liveness note (section 8).

**Per seat, whole run** (commits up to `[FINAL]`):

| Seat | Model | Est. USD | Share | Text messages | Tool calls | Mentions out / in | Commits | Guard denials |
|---|---|---|---|---|---|---|---|---|
| Foreman | `claude-opus-5-5` | 30.27 | 10% | 109 | 299 | 138 / 112 | 119 (58 merges) | 1 |
| Builder | `claude-opus-5-5` | 42.70 | 14% | 32 | 373 | 34 / 40 | 36 | 0 |
| Stylist | `claude-opus-5-5` | 30.59 | 10% | 19 | 261 | 21 / 33 | 36 | 0 |
| Oracle | `claude-fable-5-1` | 123.41 | 42% | 34 | 452 | 36 / 38 | 66 | 7 |
| Auditor | `claude-opus-5-5` | 69.28 | 23% | 40 | 672 | 40 / 46 | 45 | 5 |
| **Total** | | **296.25** | | 234 | 2,057 | 269 / 269 | 302 | 13 |

About 98.5% of all tokens were cache reads. Work items went to two seats: Builder 21, Stylist 10.

**Quality and teamwork**, from `evidence/stage-<n>/report.json`:

| | Stage 1 | Stage 2 | Stage 3 | Stage 4 |
|---|---|---|---|---|
| Clauses (both readers / A only / B only) | 134 (124 / 0 / 10) | 59 (59 / 0 / 0) | 53 (52 / 0 / 1) | 24 (24 / 0 / 0) |
| Rulings | 28 | 17 | 15 | 17 |
| Work items | 7 | 9 | 6 | 9 |
| Rejections (of which changed the work) | 4 (1) | 3 (3) | 1 (1) | 3 (3) |
| First verdict | REJECT (verifier gap) | REJECT (product) | REJECT (product) | REJECT (product) |
| Mutants killed | 54/66 (81.8%) | 40/49 (81.6%; raw 40/54) | 22/27 (81.5%) | 34/38 (89.5%) |
| Race proofs, red without / green with | 5/5 | 6/6 | 8/8 | 10/10 |
| Holdout passed / failed | 120 / 0 | 145 / 0 | 152 / 0 | 158 / 0 |
| Escapes | 1, fixed before acceptance | 0 | 0 | 0 |

The holdout is cumulative: each stage reruns every earlier stage's shipped checks (120 + 25 + 7 + 6 at stage 4).
Whole run: 270 clauses (11 found only by entry B, none only by entry A), 77 rulings, 31 work items, 11
rejections of which 8 changed the work, one escape.

**Commits.** Up to `[FINAL]`, `git log --all` shows 303 commits: 302 by the seats and one by the operator, the
kit itself, before the dispatch. Every later commit is the operator's and leaves the stage folders untouched:
the room export, usage, guard log, metrics, the MIT `LICENSE`, factory v1.1, these documents, and a
re-encoding of `=` as `\u003d` in 22 Auditor JSON artifacts (same JSON values) so the event's credential scan
passes.

**Room.** `room.json` holds 4,830 messages: 236 text messages (234 from seats, 2 from the operator), 2,057 tool
calls and 2,056 results, 473 task events, 5 participant events and 3 errors (background tasks that stopped or
failed).

**Autonomy.** Human messages in the room: 2, the dispatch at 12:15:33 and the liveness note at 22:44:58
(section 8). Guard denials: 13, one of them during stage 4. None reached for product code or the holdout:
three reads of seat settings or the seat profile (at stage 4, the Auditor grepping its own session log), three
attempts to save notes into the profile's memory folder, three scratch files outside a seat's write area, and
four Oracle `git show` or `cat-file` calls to read a verdict on another branch.

## 8. Human input in the submitted run

The dispatch was meant to be the only human input. The room holds one more human message.

**What happened.** At stage 4 the band deadlocked. The Foreman was waiting for the Stylist's work item. The
Stylist was waiting for the Builder's new endpoints. The Builder had delivered them, but only to the Foreman,
who did not pass them on. The Stylist had noted that it was waiting only in its own closing text, which no other
seat reads. By 22:35 UTC every seat had ended its turn. Band wakes a seat only when someone addresses it, so
nothing would have happened again.

At 22:44 UTC the operator sent one message, to the Foreman:

> Operator liveness note: every seat ended its turn at 22:35 UTC and no seat has worked since. Re-check the open
> work items and continue the job.

It names no work item, seat, finding or fix. The Foreman merged the waiting branches at 22:45 and the band went
on; stage 4 was accepted at 01:12. No other human message was sent.

**Root cause.** Protocol v1.0 had no notion of waiting. A seat could end its turn blocked on another seat's
deliverable without telling anyone, and nothing obliged the Foreman to forward a deliverable to the seat that
needed it.

**Fix.** Two generic rules, added to the kit after the run (v1.1). In `protocol.md`, read by every seat:

> Waiting is a state the Foreman must know about. If you end a turn with a work item unfinished because you
> need another seat's deliverable, send the Foreman an `[ESCALATION]` that says `blocked-on: <seat>
> <deliverable>`. Your own closing text is not a message: nobody else ever reads it.

In `foreman.md`:

> Liveness. Keep a blocked-on list in `evidence/stage-<n>/work-items.md`. Whenever a deliverable arrives,
> forward it as a `[HANDOFF]` to every seat blocked on it before you end your turn. Never end a turn while
> the stage is open and no seat holds an open work item: assign the next item, forward a dependency, send a
> candidate, or record a blocker. Every seat wakes only when it is addressed, so a band where everyone is
> waiting stays silent forever.

**Which mandates ran.** `mandates/` is the frozen v1.0 set that ran, unchanged (SHA-256 in section 10). v1.1 is
the fix, in `factory/mandates-src/` and built in `factory/mandates-v1.1/`; it differs from v1.0 only by these
two rules. It has not yet been through a full run.

## 9. Honest limits

- **Guards catch drift in a cooperative band. They don't sandbox a hostile agent.** The hook fails open on
  bad config, unparseable input or its own errors (logged), so it can never stall a seat. Its command rules
  are an incomplete denylist; an interpreter one-liner could get around them.
- **Sparse checkout is not a security boundary.** Worktrees share one object store. The Oracle's git access is
  limited by guard command rules, not by the files being absent.
- **Seats run on the host** with `bypassPermissions`, outside any sandbox.
- **Liveness is a prompt rule.** v1.0 had none, and the submitted run needed one human message (section 8).
  v1.1's rules are mandate text, not code, and nothing watches for a silent band. A seat stuck inside one tool
  call also goes unnoticed, apart from `bg.py` timeouts.
- **Mutation scores are time-boxed samples.** Stages 1–4 evaluated 73 of 473, 60 of 570, 28 of 788 and 41 of
  988 generated mutants. Stage 2 passed the 75% target only after excluding 5 mutants argued equivalent in the
  verdict (raw 74.1%). Two Oracle items from stage-4 survivors (O-19, O-20) came after the final acceptance and
  were not done.
- **No stage passed on its first candidate.** Stage 1's first rejection was a missing verifier deliverable;
  stages 2–4 had real product findings.
- **The shipped checks are a sample.** Zero escapes there proves nothing about the full suite.
- **Windows first.** The dispatch template, harness wrapper and default hook interpreter (`py -3.14`) assume
  Windows with Git Bash.

## 10. Genericity

- No mandate names an endpoint, field, error code, test id or domain noun. The kit's vocabulary test applies
  the event harness's own gate-4 rule (`harness.vocabulary`): identifier shapes checked against both tracks'
  term lists, plus plain domain words the harness doesn't scan. It covers mandates, their sources and every
  factory file. The test itself stays out of this repository, because its word list would trip the scan.
- Track detail lives only in the dispatch (spec paths, plus the track name in the acceptance command) and in
  the spec text the Foreman pastes into handoffs. To re-point the factory, run `new_run.py` with other specs.
- Mandates frozen for the submitted run (v1.0), as `sha256sum mandates/*.md`:

```
eb3947e06609a0b2e175b8b4b6f0d5f8dbc6b2a5e875c550e4a99a3f0de42cca  mandates/auditor.md
a589f85236e055447d2330a240c89ad0ac6acd7fab178ed5365233cc1bea11e5  mandates/builder.md
fb130c9e4599455ed9507c13b340b87a653bf8be273742a38a10bfbb3bd71c41  mandates/foreman.md
d49f40e2cf4d3f93ba63dfc1a2bcb91a6beb2b6ab8a7968813eef3e89aab9187  mandates/oracle.md
ed130a8bd8142b6db39e6fb3fa93edf9df9ffa19f6625678106d42a40e0b1f34  mandates/stylist.md
```

**What is shown and what is not.** The frozen mandates have not run on a second track, so genericity here is
enforced, not demonstrated: by the vocabulary test, by `harness check` (its mandate scan passes) and by keeping
all track detail in the dispatch. An earlier version of the same kit ran the event's toy practice track in
rehearsal; its stage 1 was accepted with the shipped checks at 8/8 before the rehearsal was stopped to fix a
hang (section 6).
