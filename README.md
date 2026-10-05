# Countersign: tablekeeper

Nothing ships on one signature. Countersign is a five-seat software factory for Band Desktop in which every
artifact is derived twice by independent seats and ships only when the two agree: two blind readings of the
spec, a blind reference model the service is differential-tested against, tests that are themselves tested
(mutation and race proofs), and an Auditor that alone can accept a stage and may not write fixes. From one
dispatch the seats built the tablekeeper service in four stages, each a complete Go service in its own folder,
and the Auditor accepted all four after the event's shipped checks passed in isolated mode. The run took
12 h 59 min and an estimated $296 API-equivalent. The room holds two human messages: the dispatch, and one
liveness note when the band deadlocked at stage 4 ([FACTORY.md, section 8](FACTORY.md#8-human-input-in-the-submitted-run)).

- **Team:** solo, [RaYYeR220](https://github.com/RaYYeR220)
- **Track:** `tablekeeper`
- **Repository:** https://github.com/RaYYeR220/countersign
- **Video (3:46):** [countersign-demo.mp4](https://github.com/RaYYeR220/countersign/releases/download/run-1/countersign-demo.mp4): the Band Desktop room, a handoff, the gate catching a wrong ruling, the app, cost and time.
- **Slides:** [countersign-slides.pdf](https://github.com/RaYYeR220/countersign/releases/download/run-1/countersign-slides.pdf)
- **Demo:** there is no hosted demo. Each `stage-<n>/` folder runs locally with one `docker` command (below).
- **Factory:** [FACTORY.md](FACTORY.md) covers the seats, how to stand the factory up, design choices and their
  cost, measured time and spend, and how it catches bad work.

## Results

Room `cs-tablekeeper`, dispatched 2026-10-04 12:15 UTC, `[FINAL]` 2026-10-05 01:14 UTC. A stage is accepted only
by the Auditor, from a clean clone, after the full gate battery and then the event's shipped checks in isolated
mode (the holdout, which reruns every earlier stage's checks too).

| Stage | Outcome | Accepted SHA | Accepted (UTC) | Wall time | Candidates | Holdout passed / failed | Mutants killed | Race proofs |
|---|---|---|---|---|---|---|---|---|
| 1 | accepted | `c0f2b7b` | 16:54 | 4 h 39 min | 5 | 120 / 0 | 54/66 (81.8%) | 5/5 |
| 2 | accepted | `aa63cd2` | 19:44 | 2 h 49 min | 5 | 145 / 0 | 40/49 (81.6%) | 6/6 |
| 3 | accepted | `c4a828e` | 22:18 | 2 h 32 min | 2 | 152 / 0 | 22/27 (81.5%) | 8/8 |
| 4 | accepted | `2621e7a` | 01:12 (next day) | 2 h 54 min | 4 | 158 / 0 | 34/38 (89.5%) | 10/10 |

Stage 4's wall time includes an idle gap from 22:35 to 22:45 UTC, before the liveness note.

- **Caught before acceptance:** 11 rejections, 8 of which changed the work. One holdout escape in the whole run,
  at stage 1: the band had ruled a reference-format clause the wrong way (R-26); the Foreman withdrew the ruling
  (R-28) and the Builder fixed it (WI-7) before acceptance. FACTORY.md, section 5.
- **Two readers:** 270 clauses; 11 found only by the Oracle's blind entry B, none only by the Foreman's entry A.
  77 rulings on ambiguities.
- **Spend:** about $296 API-equivalent across five seats, on a subscription: Oracle $123, Auditor $69, Builder
  $43, Stylist $31, Foreman $30 (FACTORY.md, section 7).
- **Who did the work:** each seat commits under its own name. Up to `[FINAL]`: 303 commits, Foreman 119 (58
  merges), Oracle 66, Auditor 45, Builder 36, Stylist 36, plus the kit commit by the operator.

The service: Go 1.26, `net/http`, state in memory behind one lock, the browser UI embedded in the binary, bcrypt
from a vendored `golang.org/x/crypto`, no network at run time (`evidence/adr/`). The Oracle's reference model,
written without seeing that code, is a separate Python implementation.

## Verify in five minutes

You need Docker and the event's kickoff package with its harness installed (participant guide: a Python 3.12+
venv and `python -m pip install -r harness/requirements.txt`). From the kickoff checkout:

```sh
git clone https://github.com/RaYYeR220/countersign ../countersign
python -m harness check ../countersign --track tablekeeper
python -m harness run --track tablekeeper --repo ../countersign --all --mode isolated
```

`check` should print `ok — gates 1, 2 and the mandate part of gate 4 pass`. `run --all` builds every
`stage-<n>/` folder and should report `stage-1/: claims stage 1 on the shipped checks` through
`stage-4/: claims stage 4 on the shipped checks`; `stage-4/` passes all four shipped suites, including the
upgrade checks from the stage-1, stage-2 and stage-3 services. On a fresh clone of the `[FINAL]` commit this
took about four minutes on our machine. On Windows without WSL2, run
`python ../countersign/factory/setup/harness_win.py run ...` with the same arguments in place of
`python -m harness run ...`.

To run one stage by hand (every `stage-<n>/RUN.md` has the same one-line command):

```sh
cd ../countersign/stage-4
docker build -t tablekeeper-stage4 . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage4
curl http://127.0.0.1:8080/health        # 200 {"status":"ok"}
```

The browser screens are at `http://127.0.0.1:8080/` from stage 2 on. State is in memory; `RUN.md` explains
loading data with `POST /_test/reset`.

## Where the evidence is

```
README.md            this file
FACTORY.md           the factory: setup, design choices, costs, failure handling, human input
stage-<n>/           one complete service per stage: Go source, Dockerfile, RUN.md; each extends the one before
stage-<n>/verify/    verification code, excluded from the image: oracle/ (reference model, acceptance and
                     differential suites) and audit/ (attacks, mutation, race proofs)
evidence/            ledgers, rulings, work items, verdicts, stage reports, adr/, design/
evidence/usage.json  per-seat spend; evidence/metrics.md run metrics; evidence/guard.jsonl guard denials
mandates/            the frozen v1.0 mandates that ran, one per seat; the first two lines name harness and model
factory/             the kit, v1.1: guard hook and role rules, setup scripts, seat launcher, tools, templates,
                     tests, mandate sources (with the two liveness rules) and their build in mandates-v1.1/
room.json            the full room session, downloaded unchanged from Band
LICENSE              MIT
```

| What | Where |
|---|---|
| Human input in the submitted run | FACTORY.md, section 8 |
| Run metrics: spend, messages and commits per seat, stage times, verdicts | `evidence/metrics.md` |
| Stage reports (prose and JSON) | `evidence/stage-<n>/report.md`, `report.json` |
| Verdicts with the full gate battery output | `evidence/stage-<n>/verdicts/verdict-<k>.md` |
| Clause ledgers: entry A, entry B, reconciled master | `evidence/stage-<n>/ledger-A.md`, `ledger-B.md`, `ledger.md` |
| Rulings on ambiguities | `evidence/stage-<n>/rulings.md` |
| Work items and retry budget | `evidence/stage-<n>/work-items.md` |
| Design direction and screenshots at 375, 768 and 1280 px | `evidence/design/direction.md`, `evidence/design/stage-<n>/` |
| SHA-256 of the mandates that ran | FACTORY.md, section 10 |

`mandates/` is unchanged since the kit commit. `factory/` was updated to v1.1 after the run; FACTORY.md,
section 8 says why. Every commit after `[FINAL]` is the operator's and leaves the stage folders untouched.

**About `room.json`.** It holds all 4,830 messages of the room, from 12:15 UTC on 2026-10-04 to 01:14 UTC on
2026-10-05: 236 text messages, 2,057 tool calls and 2,056 tool results, and 473 task events. A first console
download held only the newest 2,400 messages, because the console exports what it has loaded. The committed
file is a second download, made after loading the full history.

## Tracing code to the room

- Up to `[FINAL]`, every commit after the first is authored by a seat (`git log --all --format='%an  %s'`; seat
  branches are `seat/<seat>`) and work commits are titled `[WI-<n>] <summary> (<clause ids>)`. The first commit
  is the kit, committed by the operator before the dispatch.
- Every work item appears in `room.json` as a `[HANDOFF] stage=<n> wi=<WI-n>` message, and its result as
  `[EVIDENCE] ... sha=<commit>`.
- Every acceptance is an Auditor `[VERDICT] stage=<n> sha=<commit> result=ACCEPT` message, backed by its
  verdict file.

## Human input

The room holds exactly two human messages, both from the operator to the Foreman:

1. **The dispatch** (2026-10-04 12:15:33 UTC): the job, all four stages, sent once.
2. **A liveness note** (22:44:58 UTC): "Operator liveness note: every seat ended its turn at 22:35 UTC and no seat
   has worked since. Re-check the open work items and continue the job." At stage 4 every seat had ended its
   turn waiting on another. The note names no work item, seat or fix.

[FACTORY.md, section 8](FACTORY.md#8-human-input-in-the-submitted-run) gives the root cause and the two
liveness rules added to the kit after the run (v1.1).

## Standing the factory up yourself

See [FACTORY.md, section 3](FACTORY.md#3-stand-it-up). You need `mandates/` (or the v1.1 build in
`factory/mandates-v1.1/`), `factory/`, Band Desktop, Docker, Python, Go (for the launcher), Node.js (for cost
measurement) and your own model access.

## Team

Solo: [RaYYeR220](https://github.com/RaYYeR220) designed and built the factory and operated the run.
