"""Run telemetry: a markdown report built from the room export, git history, the usage
export, the guard log and the per-stage report files.

    python metrics.py --room room.json --repo <result repo> [--usage usage.json]
                      [--guard-log guard.jsonl] [--out evidence/metrics.md]

Every input except the room may be absent or partial; whatever cannot be measured is
shown as "n/a" rather than as zero. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("KICKOFF", "LEDGER", "RECONCILE", "PLAN", "HANDOFF", "EVIDENCE", "CANDIDATE", "VERDICT",
         "FINDINGS", "RULING", "ESCALATION", "CLOSE", "FINAL")
NA = "n/a"

MENTION_RE = re.compile(r"@\[\[([^\]]+)\]\]")
# Mentions, whitespace and light markdown decoration may precede the header.
_LEAD_RE = re.compile(r"^(?:\s|@\[\[[^\]]*\]\]|[*_`>#])*")
_HEADER_RE = re.compile(r"\[(" + "|".join(KINDS) + r")\]\s+stage=(\d+)\b([^\r\n]*)", re.IGNORECASE)
_FIELD_RE = re.compile(r"\b(wi|sha|result|id|part)=([^\s*`]+)", re.IGNORECASE)
_PART_RE = re.compile(r"(\d+)/(\d+)")
WI_RE = re.compile(r"\[WI-\d+\]")
_STAGE_DIR_RE = re.compile(r"stage-(\d+)")

GIT_FORMAT = "%an%x1f%ae%x1f%aI%x1f%s"
_COST_KEYS = ("totalCost", "cost", "costUSD", "costUsd", "total_cost", "estimatedCost")
_TOKEN_KEYS = ("totalTokens", "tokens", "total_tokens")
_WRAPPER_KEYS = ("agents", "byAgent", "perAgent", "data", "items", "rows")
_SKIP_NAMES = {"total", "totals", "summary"}


# --- room ---------------------------------------------------------------------------------

def load_room(path) -> list[dict]:
    """Messages from a room export (`{"messages": [...]}`, or a bare list of messages).

    Raises OSError if unreadable and ValueError if it is not a room export.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    messages = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(messages, list):
        raise ValueError("expected an object whose `messages` is a list of message objects")
    return [msg for msg in messages if isinstance(msg, dict)]


def _lower(value) -> str:
    return str(value or "").strip().lower()


def _is_agent(msg: dict) -> bool:
    return _lower(msg.get("senderType")) == "agent"


def _is_user(msg: dict) -> bool:
    return _lower(msg.get("senderType")) == "user"


def _content(msg: dict) -> str:
    content = msg.get("content")
    return content if isinstance(content, str) else ("" if content is None else str(content))


def seat_ids(msgs: list[dict]) -> dict[str, str]:
    """{participant id: display name} for every agent seat, in order of first appearance.

    A seat is any agent sender (as the event harness counts them) plus any agent that a
    `participant` event shows joining, so a seat that never spoke still gets a row.
    """
    seats: dict[str, str] = {}
    for msg in msgs:
        sender = msg.get("senderId")
        if sender and _is_agent(msg):
            if not seats.get(sender):
                seats[sender] = msg.get("senderName") or sender
        meta = msg.get("metadata")
        if msg.get("messageType") == "participant" and isinstance(meta, dict):
            pid = meta.get("participantId")
            if pid and _lower(meta.get("participantType")) == "agent" and not seats.get(pid):
                seats[pid] = meta.get("participantName") or pid
    return seats


def seat_stats(msgs: list[dict]) -> dict[str, dict]:
    """Per seat name: text messages, tool calls, and seat-to-seat mentions sent / received.

    Mentions are `@[[<participant id>]]` tokens in text messages only (a mention echoed
    inside a tool call is not one seat addressing another); each target counts once per
    message, and mentions of humans or of oneself are left out.
    """
    ids = seat_ids(msgs)
    stats = {name: {"text": 0, "tool_calls": 0, "mentions_out": 0, "mentions_in": 0}
             for name in ids.values()}
    for msg in msgs:
        sender = msg.get("senderId")
        if sender not in ids or not _is_agent(msg):
            continue
        row = stats[ids[sender]]
        kind = msg.get("messageType")
        if kind == "tool_call":
            row["tool_calls"] += 1
        elif kind == "text":
            row["text"] += 1
            targets = {t for t in MENTION_RE.findall(_content(msg)) if t in ids and t != sender}
            for target in targets:
                row["mentions_out"] += 1
                stats[ids[target]]["mentions_in"] += 1
    return stats


def human_messages(msgs: list[dict]) -> int:
    """Text messages sent by a human participant."""
    return sum(1 for msg in msgs if _is_user(msg) and msg.get("messageType") == "text")


def parse_header(content) -> dict | None:
    """The protocol header on the first line of a message (after any mentions), or None.

    `[KIND] stage=<n> [wi=<WI-n>] [sha=<hex>] [result=ACCEPT|REJECT] [id=<R-n>] [part=<i>/<k>]`
    """
    if not isinstance(content, str):
        return None
    rest = content[_LEAD_RE.match(content).end():]
    head = _HEADER_RE.match(rest)
    if not head:
        return None
    fields = {key.lower(): value for key, value in _FIELD_RE.findall(head.group(3))}
    part = _PART_RE.fullmatch(fields.get("part", ""))
    result = fields.get("result")
    return {"kind": head.group(1).upper(), "stage": int(head.group(2)), "wi": fields.get("wi"),
            "sha": fields.get("sha"), "result": result.upper() if result else None,
            "id": fields.get("id"), "part": (int(part.group(1)), int(part.group(2))) if part else None}


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _headers(msgs: list[dict]) -> list[tuple[dict, dict, datetime | None]]:
    """(header, message, time) for every text message carrying a header, oldest first.

    Continuation parts of a split message (part 2/k and later) are dropped so a long
    message counts once.
    """
    found = []
    for index, msg in enumerate(msgs):
        if msg.get("messageType") != "text":
            continue
        head = parse_header(msg.get("content"))
        if head is None or (head["part"] and head["part"][0] > 1):
            continue
        stamp = _parse_ts(msg.get("insertedAt"))
        found.append((stamp or _EPOCH, index, head, msg, stamp))
    found.sort(key=lambda item: (item[0], item[1]))
    return [(head, msg, stamp) for _key, _index, head, msg, stamp in found]


def stage_timeline(msgs: list[dict]) -> dict[int, dict]:
    """{stage: {start, end, minutes}} from the first `[KICKOFF] stage=n` to the last
    `[CLOSE] stage=n`; an unclosed stage has end and minutes None."""
    starts: dict[int, tuple[datetime | None, str]] = {}
    ends: dict[int, tuple[datetime | None, str]] = {}
    for head, msg, stamp in _headers(msgs):
        raw = msg.get("insertedAt")
        if head["kind"] == "KICKOFF" and head["stage"] not in starts:
            starts[head["stage"]] = (stamp, raw)
        elif head["kind"] == "CLOSE":
            ends[head["stage"]] = (stamp, raw)
    timeline = {}
    for stage in sorted(starts):
        start_ts, start_raw = starts[stage]
        end_ts, end_raw = ends.get(stage, (None, None))
        minutes = None
        if start_ts and end_ts:
            minutes = round((end_ts - start_ts).total_seconds() / 60, 1)
        timeline[stage] = {"start": start_raw, "end": end_raw, "minutes": minutes}
    return timeline


def verdicts(msgs: list[dict]) -> dict[int, dict]:
    """{stage: {"accept": n, "reject": n}} from `[VERDICT] stage=n ... result=...` headers."""
    counts: dict[int, dict] = {}
    for head, _msg, _stamp in _headers(msgs):
        if head["kind"] == "VERDICT" and head["result"] in ("ACCEPT", "REJECT"):
            row = counts.setdefault(head["stage"], {"accept": 0, "reject": 0})
            row[head["result"].lower()] += 1
    return dict(sorted(counts.items()))


def first_verdicts(msgs: list[dict]) -> dict[int, str]:
    """{stage: "ACCEPT"|"REJECT"} for the earliest verdict of each stage."""
    first: dict[int, str] = {}
    for head, _msg, _stamp in _headers(msgs):
        if head["kind"] == "VERDICT" and head["result"] in ("ACCEPT", "REJECT"):
            first.setdefault(head["stage"], head["result"])
    return first


# --- other inputs -------------------------------------------------------------------------

def git_stats(repo) -> dict[str, dict] | None:
    """{author name: {commits, work_items, emails, first, last}} from `git log` on HEAD.

    Work items are the `[WI-n]` tags in commit subjects. Returns {} for a repository with
    no commits and None when there is no readable repository (or no git).
    """
    if repo is None:
        return None
    env = dict(os.environ, LC_ALL="C", LANG="C", LANGUAGE="C")
    try:
        proc = subprocess.run(["git", "-C", str(repo), "log", f"--format={GIT_FORMAT}"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env)
    except OSError:
        return None
    if proc.returncode != 0:
        return {} if "does not have any commits" in proc.stderr else None
    stats: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f", 3)
        if len(parts) != 4:
            continue
        name, email, date, subject = parts
        author = name or email or "unknown"
        row = stats.setdefault(author, {"commits": 0, "work_items": set(), "emails": set(),
                                        "first": None, "last": None})
        row["commits"] += 1
        row["work_items"].update(tag[1:-1] for tag in WI_RE.findall(subject))
        if email:
            row["emails"].add(email)
        stamp = _parse_ts(date)
        if stamp:
            if row["first"] is None or stamp < _parse_ts(row["first"]):
                row["first"] = date
            if row["last"] is None or stamp > _parse_ts(row["last"]):
                row["last"] = date
    return stats


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _usage_records(data):
    """(name, record) pairs from the shapes a usage export may take."""
    if isinstance(data, dict):
        for key in _WRAPPER_KEYS:
            if isinstance(data.get(key), (list, dict)):
                yield from _usage_records(data[key])
                return
        for name, record in data.items():
            if isinstance(record, dict) and _lower(name) not in _SKIP_NAMES:
                yield name, record
    elif isinstance(data, list):
        for record in data:
            if not isinstance(record, dict):
                continue
            name = next((record[k] for k in ("agentName", "agent", "name", "displayName",
                                             "participantName", "seat")
                         if isinstance(record.get(k), str) and record[k].strip()), None)
            if name:
                yield name, record


def usage(path) -> dict[str, dict]:
    """{seat name: {"cost": float, "tokens": int}} from a per-agent usage export
    (e.g. `band usage agents --json`). A missing or unreadable file gives {}."""
    if path is None:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for name, record in _usage_records(data):
        cost = next((v for v in (_number(record.get(k)) for k in _COST_KEYS) if v is not None), None)
        tokens = next((v for v in (_number(record.get(k)) for k in _TOKEN_KEYS) if v is not None), None)
        if tokens is None:
            counted = [_number(v) for k, v in record.items() if k.lower().endswith("tokens")]
            counted = [v for v in counted if v is not None]
            tokens = sum(counted) if counted else None
        if cost is None and tokens is None:
            continue
        row = out.setdefault(name, {"cost": 0.0, "tokens": 0})
        row["cost"] += cost or 0.0
        row["tokens"] += int(tokens or 0)
    return out


def guard_denials(path) -> dict[str, int]:
    """{seat: denied tool calls} from the guard's JSON-lines log; parse-error lines (which
    the guard allows) and unreadable lines are skipped. A missing log gives {}."""
    if path is None:
        return {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return {}
    counts: dict[str, int] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("rule") == "parse-error" or not entry.get("seat"):
            continue
        seat = str(entry["seat"])
        counts[seat] = counts.get(seat, 0) + 1
    return counts


def stage_reports(repo) -> list[dict]:
    """Every `evidence/stage-<n>/report.json`, ordered by stage. An unreadable file yields
    `{"stage": n, "error": "..."}` so the gap stays visible."""
    if repo is None:
        return []
    base = Path(repo) / "evidence"
    if not base.is_dir():
        return []
    reports = []
    for path in base.glob("stage-*/report.json"):
        match = _STAGE_DIR_RE.fullmatch(path.parent.name)
        number = int(match.group(1)) if match else None
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("report is not a JSON object")
        except (OSError, ValueError) as exc:
            data = {"stage": number, "error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(data.get("stage"), int) or isinstance(data.get("stage"), bool):
            data["stage"] = number
        reports.append(data)
    reports.sort(key=lambda r: (r["stage"] is None, r["stage"] or 0))
    return reports


# --- rendering ----------------------------------------------------------------------------

def _slug(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _match(name: str, table: dict | None, emails: bool = False):
    """The entry of `table` that belongs to seat `name` (matched on a normalised name, or
    on an email local part for git authors), plus the keys it consumed."""
    if not table:
        return [], set()
    want = _slug(name)
    keys = set()
    for key, value in table.items():
        names = {_slug(key)}
        if emails and isinstance(value, dict):
            names |= {_slug(str(e).split("@")[0]) for e in value.get("emails", ())}
        if want in names:
            keys.add(key)
    return [table[k] for k in sorted(keys, key=str)], keys


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _cell(value) -> str:
    if value is None:
        return NA
    if isinstance(value, float):
        return str(round(value, 2))
    return str(value).replace("|", "\\|").replace("\n", " ")


def _usd(value) -> str | None:
    return None if value is None else f"{value:.2f}"


def _pct(part, whole) -> str:
    return f"{100 * part / whole:.1f}%" if whole else NA


def _table(header: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return lines


def _seat_section(msgs, git, use, denials) -> list[str]:
    stats = seat_stats(msgs)
    lines = ["## Seats", ""]
    if not stats:
        return lines + [f"No agent seats in the room: {NA}", ""]
    used_git, used_use, used_den, all_items = set(), set(), set(), set()
    rows, totals = [], {"commits": None, "cost": None, "tokens": None, "denials": None}

    def add(key, value):
        if value is not None:
            totals[key] = (totals[key] or 0) + value

    for name, row in stats.items():
        commits = items = cost = tokens = denied = None
        if git is not None:
            found, keys = _match(name, git, emails=True)
            used_git |= keys
            commits = sum(e.get("commits", 0) for e in found)
            seat_items = set().union(*(e.get("work_items", ()) for e in found))
            all_items |= seat_items
            items = len(seat_items)
        if use:
            found, keys = _match(name, use)
            used_use |= keys
            if found:
                cost = sum(e.get("cost", 0.0) for e in found)
                tokens = sum(e.get("tokens", 0) for e in found)
        if denials is not None:
            found, keys = _match(name, denials)
            used_den |= keys
            denied = sum(found)
        add("commits", commits)
        add("cost", cost)
        add("tokens", tokens)
        add("denials", denied)
        rows.append([name, row["text"], row["tool_calls"], row["mentions_out"], row["mentions_in"],
                     commits, items, _usd(cost), tokens, denied])
    rows.append(["total", sum(r["text"] for r in stats.values()),
                 sum(r["tool_calls"] for r in stats.values()),
                 sum(r["mentions_out"] for r in stats.values()),
                 sum(r["mentions_in"] for r in stats.values()),
                 totals["commits"], len(all_items) if git is not None else None, _usd(totals["cost"]),
                 totals["tokens"], totals["denials"]])
    lines += _table(["seat", "text messages", "tool calls", "mentions out", "mentions in", "commits",
                     "work items", "cost (USD)", "tokens", "guard denials"], rows)
    lines.append("")
    extra = []
    if git:
        other = [f"{k} ({git[k].get('commits', 0)})" for k in git if k not in used_git]
        if other:
            extra.append("Commits by authors outside the room: " + ", ".join(other))
    if use:
        other = [f"{k} ({use[k].get('cost', 0.0):.2f} USD)" for k in use if k not in used_use]
        if other:
            extra.append("Usage entries outside the room: " + ", ".join(other))
    if denials:
        other = [f"{k} ({denials[k]})" for k in denials if k not in used_den]
        if other:
            extra.append("Guard denials for seats outside the room: " + ", ".join(other))
    return lines + [f"- {e}" for e in extra] + ([""] if extra else [])


def _timeline_section(msgs) -> list[str]:
    timeline = stage_timeline(msgs)
    lines = ["## Stage timeline (minutes)", ""]
    if not timeline:
        return lines + [f"No `[KICKOFF] stage=n` header in the room: {NA}", ""]
    rows = [[stage, t["start"], t["end"], t["minutes"]] for stage, t in timeline.items()]
    lines += _table(["stage", "start", "end", "minutes"], rows)
    known = [t["minutes"] for t in timeline.values() if t["minutes"] is not None]
    lines += ["", f"Closed stages: {len(known)}/{len(timeline)}; total minutes: "
                  f"{round(sum(known), 1) if known else NA}", ""]
    return lines


def _verdict_section(msgs) -> list[str]:
    counts, first = verdicts(msgs), first_verdicts(msgs)
    lines = ["## Verdicts", ""]
    if not counts:
        return lines + [f"No `[VERDICT]` header in the room: {NA}", ""]
    rows = [[stage, c["accept"], c["reject"], first.get(stage)] for stage, c in counts.items()]
    lines += _table(["stage", "accept", "reject", "first verdict"], rows)
    passed = sum(1 for result in first.values() if result == "ACCEPT")
    lines += ["", f"First-pass yield: {passed}/{len(first)} stages ({_pct(passed, len(first))})", ""]
    return lines


def _report_row(rep: dict) -> list:
    stage = rep.get("stage")
    if "error" in rep:
        return [stage, "unreadable"] + [None] * 14
    clauses = rep.get("clauses") if isinstance(rep.get("clauses"), dict) else {}
    rejections = rep.get("rejections")
    rej = None
    if isinstance(rejections, list):
        changed = sum(1 for r in rejections if isinstance(r, dict) and r.get("changed_work") is True)
        rej = f"{len(rejections)} ({changed} changed work)"
    mutation = rep.get("mutation") if isinstance(rep.get("mutation"), dict) else {}
    killed, total = _int(mutation.get("killed")), _int(mutation.get("total"))
    mut = f"{killed}/{total} ({_pct(killed, total)})" if killed is not None and total is not None else None
    proofs = rep.get("race_proofs")
    red = green = None
    if isinstance(proofs, list):
        entries = [p for p in proofs if isinstance(p, dict)]
        red = f"{sum(1 for p in entries if p.get('red_without') is True)}/{len(entries)}"
        green = f"{sum(1 for p in entries if p.get('green_with') is True)}/{len(entries)}"
    holdout = rep.get("holdout") if isinstance(rep.get("holdout"), dict) else None
    if holdout is None:
        h_pass = h_fail = escapes = None
    elif holdout.get("ran") is False:
        h_pass = h_fail = escapes = "not run"
    else:
        h_pass, h_fail = _int(holdout.get("passed")), _int(holdout.get("failed"))
        esc = holdout.get("escapes")
        escapes = len(esc) if isinstance(esc, list) else None
    return [stage, rep.get("outcome"), _int(clauses.get("total")), _int(clauses.get("both")),
            _int(clauses.get("a_only")), _int(clauses.get("b_only")), _int(clauses.get("rulings")),
            _int(rep.get("work_items")), _int(rep.get("candidates")), rej, mut, red, green,
            h_pass, h_fail, escapes]


def _reports_section(reports) -> list[str]:
    lines = ["## Stage reports", ""]
    if not reports:
        return lines + [f"No `evidence/stage-<n>/report.json` found: {NA}", ""]
    lines += _table(["stage", "outcome", "clauses", "both", "A-only", "B-only", "rulings", "work items",
                     "candidates", "rejections", "mutation killed/total", "race proofs red-without",
                     "race proofs green-with", "holdout passed", "holdout failed", "escapes"],
                    [_report_row(rep) for rep in reports])
    notes = []
    for rep in reports:
        stage = rep.get("stage")
        if "error" in rep:
            notes.append(f"Stage {stage} report unreadable: {rep['error']}")
            continue
        holdout = rep.get("holdout") if isinstance(rep.get("holdout"), dict) else {}
        if isinstance(holdout.get("escapes"), list) and holdout["escapes"]:
            notes.append(f"Stage {stage} escapes: " + ", ".join(map(str, holdout["escapes"])))
        if isinstance(rep.get("blockers"), list) and rep["blockers"]:
            notes.append(f"Stage {stage} blockers: " + "; ".join(map(str, rep["blockers"])))
    lines.append("")
    return lines + [f"- {n}" for n in notes] + ([""] if notes else [])


def render(msgs: list[dict], git: dict | None = None, usage: dict | None = None,
           denials: dict | None = None, reports: list[dict] | None = None) -> str:
    """The metrics report as markdown. `git=None` / `denials=None` mean the input was not
    supplied; an empty `usage` means no usage data. Either way the cells read "n/a"."""
    lines = ["# Run metrics", ""]
    lines += _seat_section(msgs, git, usage, denials)
    lines += ["## Human input", "", f"Human text messages: {human_messages(msgs)}", ""]
    lines += _timeline_section(msgs)
    lines += _verdict_section(msgs)
    lines += _reports_section(reports or [])
    return "\n".join(lines).rstrip("\n") + "\n"


# --- CLI ----------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the run metrics report.")
    parser.add_argument("--room", required=True, help="room export (room.json)")
    parser.add_argument("--repo", required=True, help="result repository (git log, evidence/)")
    parser.add_argument("--usage", help="per-agent usage export (JSON)")
    parser.add_argument("--guard-log", help="guard log (JSON lines)")
    parser.add_argument("--out", help="write the report here instead of stdout")
    args = parser.parse_args(argv)
    try:
        msgs = load_room(args.room)
    except (OSError, ValueError) as exc:
        print(f"metrics: cannot read room export {args.room}: {exc}", file=sys.stderr)
        return 2
    text = render(msgs, git=git_stats(args.repo),
                  usage=usage(args.usage) if args.usage else None,
                  denials=guard_denials(args.guard_log) if args.guard_log else None,
                  reports=stage_reports(args.repo))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
