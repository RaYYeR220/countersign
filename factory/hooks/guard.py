"""Role guard: keeps each seat inside its boundary, in code rather than in its instructions.

Claude Code runs it before every tool call (a PreToolUse hook):

    python guard.py --seat <seat> --config <roles.json>

It reads one PreToolUse event (JSON: tool_name, tool_input, cwd) on stdin. A denied call gets a
deny decision on stdout and a line in the log; an allowed call gets no output. The exit code is
always 0, and a failure inside the guard allows the call and is logged, so the guard can never
stall a seat's turn.

Stdlib only: seats run it with whatever Python the machine has.
"""
import argparse
import datetime
import fnmatch
import glob
import json
import os
import re
import sys

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path"}
SEARCH_TOOLS = {"Glob": "path", "Grep": "path", "LS": "path"}  # a folder; defaults to the cwd
COMMAND_TOOLS = {"Bash": "command", "PowerShell": "command"}

WINDOWS = os.name == "nt"
GUARD_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_DEPTH = 3       # how deep a search check looks for carve-outs below a denied folder
SCAN_ENTRIES = 500   # entries per folder before a search check stops looking and denies

_LONG_PREFIX = re.compile(r"^[\\/]{2}[?.][\\/](unc[\\/])?", re.IGNORECASE)  # \\?\  \\.\  \\?\UNC\
_MSYS_DRIVE = re.compile(r"^/([a-z])(?=/|$)", re.IGNORECASE)                # Git Bash /c/...
_SEPARATORS = re.compile(r"[\\/]+")
_WILDCARD = re.compile(r"[*?\[{]")


# --- paths -------------------------------------------------------------------

def _prep(p: str) -> str:
    p = p.strip()
    m = _LONG_PREFIX.match(p)
    if m:
        p = ("\\\\" if m.group(1) else "") + p[m.end():]
    if p.startswith("~"):
        p = os.path.expanduser(p)
    if WINDOWS:
        p = _MSYS_DRIVE.sub(lambda d: d.group(1) + ":", p)
    return p


def _drop_ignored_name_parts(p: str) -> str:
    """Windows opens `verify.`, `verify ` and `verify::$INDEX_ALLOCATION` as `verify`."""
    drive, rest = os.path.splitdrive(p)
    parts = [part if part in (".", "..") else part.split(":", 1)[0].rstrip(" .")
             for part in re.split(r"[\\/]", rest)]
    return drive + "\\".join(parts)


def _absolute(p: str, cwd: str) -> str:
    p = os.path.abspath(os.path.join(_prep(cwd), _prep(p)))
    if WINDOWS:
        p = os.path.normpath(_drop_ignored_name_parts(p))
    return p


def resolve(p: str, cwd: str) -> str:
    """Absolute, real (links and short names resolved), '/'-separated path in its on-disk case."""
    p = _absolute(p, cwd)
    try:
        p = os.path.realpath(p)
    except (OSError, ValueError):
        pass
    return os.path.normpath(_prep(p)).replace("\\", "/")


def norm(p: str, cwd: str) -> str:
    """The form every rule is matched against: resolved, '/'-separated, lowercase."""
    return resolve(p, cwd).lower()


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def _flat(text: str) -> str:
    """Lowercase with every run of slashes or backslashes folded to one '/'."""
    return _SEPARATORS.sub("/", text.lower())


def _spellings(p: str, cwd: str) -> set:
    """How a command line may spell an absolute path, in `_flat` form."""
    forms = {_flat(resolve(p, cwd)), _flat(_absolute(p, cwd))}
    forms |= {"/" + f[0] + f[2:] for f in list(forms) if re.match(r"^[a-z]:/", f)}  # Git Bash /c/...
    return {f for f in forms if f.strip("/")}


def checkout_root(real: str, stop: str = None):
    """Nearest ancestor (inclusive) holding a `.git` entry; the walk ends at `stop` if given."""
    d = real
    for _ in range(256):
        if os.path.lexists(os.path.join(d, ".git")):
            return d
        if stop is not None and d.lower() == stop.lower():
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def _literal_root(pattern: str) -> str:
    """The folder part of a glob pattern before its first wildcard."""
    keep = []
    for part in re.split(r"[\\/]", _prep(pattern)):
        if _WILDCARD.search(part):
            break
        keep.append(part)
    return "/".join(keep)


def _is_absolute_pattern(pattern: str) -> bool:
    return pattern.startswith(("**/", "/")) or bool(re.match(r"^[a-z]:", pattern))


# --- rules ---------------------------------------------------------------------

def _labelled(seat: str, pattern: str, verb: str):
    """(area, advice) for a denied path pattern, in words a seat can act on."""
    route = "hand it to the seat that owns it" if seat == "foreman" else "route the need through the Foreman"
    if pattern == "@holdout":
        return "the external acceptance tests", "only the Auditor runs them; " + route
    if "verify" in pattern:
        return "verification suites", route
    if "verdicts" in pattern:
        return "verdicts", "only the Auditor writes them; " + route
    if "ledger" in pattern:
        return "ledgers", "the Foreman and the Oracle write them; " + route
    if pattern.startswith(("factory", "mandates")):
        return "factory files", "they are frozen during a run; record the need in an [ESCALATION] to the Foreman"
    if pattern.startswith("**/"):
        return "credential and seat-profile locations", "they are closed to every seat"
    if pattern.startswith("stage-"):
        if verb == "read":
            return "implementation files", ("the Oracle stays blind to the implementation; work from the "
                                            "specification and test the running candidate")
        return "implementation files", "the Foreman writes no product code; hand the change to its owner as a work item"
    return f"files matching {pattern}", route


_COMMAND_LABELS = (  # (keyword in the rule, what the command does, advice)
    ("@holdout", "reaches the external acceptance tests", "Only the Auditor runs them; route the need through the Foreman"),
    ("harness", "runs the external acceptance command", "Only the Auditor runs it; route the need through the Foreman"),
    ("verify", "reaches verification suites (stage-*/verify/)", "Route the need through the Foreman"),
    ("(band|jam)", "administers the room or asks the operator",
     "Decide from the specification and record a ruling, or escalate to the Foreman"),
    ("push", "rewrites or publishes history", "Commit on your own branch and merge main to pick up changes"),
    ("rebase", "rewrites or publishes history", "Commit on your own branch and merge main to pick up changes"),
    ("amend", "rewrites or publishes history", "Commit on your own branch and merge main to pick up changes"),
    ("filter-", "rewrites or publishes history", "Commit on your own branch and merge main to pick up changes"),
    ("reset", "rewrites or publishes history", "Commit on your own branch and merge main to pick up changes"),
    ("docker", "prunes shared Docker state", "Stop and remove only your own containers"),
    ("guard-self", "touches the guard's own files", "Seat boundaries are fixed for the run"),
)


class _Target:
    """A path in the two forms rules match against: absolute, and relative to its checkout."""

    def __init__(self, real: str, rules: "_Rules"):
        self.abs = real.lower()
        stop = rules.ws_real if rules.ws and _under(self.abs, rules.ws) else None
        root = checkout_root(real, stop)
        if root is not None:
            self.rels = [self.abs[len(root):].lstrip("/")]
        else:  # outside any checkout: a copied stage folder is still a stage folder
            parts = self.abs.split("/")
            self.rels = ["/".join(parts[i:]) for i in range(1, len(parts))]


class _Rules:
    def __init__(self, seat: str, cfg: dict, cwd: str, protect=()):
        self.seat = seat.strip().lower()
        self.name = self.seat.capitalize() or "This seat"
        self.cwd = cwd
        common = cfg.get("common") or {}
        role = (cfg.get("roles") or {}).get(self.seat) or {}

        def both(key):
            return [str(x) for x in (common.get(key) or []) + (role.get(key) or [])]

        self.deny_tools = both("deny_tools")
        self.deny_command = both("deny_command")
        self.deny_read = [p.replace("\\", "/").lower() for p in both("deny_read")]
        self.allow_read = [p.replace("\\", "/").lower() for p in (role.get("allow_read") or [])]
        self.deny_write = [p.replace("\\", "/").lower() for p in both("deny_write")]
        only = role.get("allow_write_only")
        self.allow_write_only = None if only is None else [p.replace("\\", "/").lower() for p in only]
        self.inside_only = bool(common.get("write_inside_workspace_only"))

        ws = cfg.get("workspace")
        self.ws_real = resolve(ws, cwd) if ws else None
        self.ws = self.ws_real.lower() if ws else None
        base = self.ws_real or cwd
        self.holdout_given = [str(h) for h in (cfg.get("holdout") or [])]
        self.holdouts = [norm(h, base) for h in self.holdout_given]
        guarded = [GUARD_DIR] + [str(p) for p in protect if p]
        if cfg.get("log"):
            guarded.append(str(cfg["log"]))
        self.guarded = [norm(p, base) for p in guarded]
        self.guarded_spellings = set().union(*(_spellings(p, base) for p in guarded))

    # -- matching

    def target(self, path: str) -> _Target:
        return _Target(resolve(path, self.cwd), self)

    def match(self, t: _Target, pattern: str, as_dir: bool) -> bool:
        if pattern == "@holdout":
            return any(_under(t.abs, h) for h in self.holdouts)
        names = [t.abs] if _is_absolute_pattern(pattern) else t.rels
        return any(fnmatch.fnmatchcase(n, pattern) or (as_dir and fnmatch.fnmatchcase(n + "/", pattern))
                   for n in names)

    def read_denial(self, t: _Target, as_dir: bool = False):
        for pattern in self.deny_read:
            if self.match(t, pattern, as_dir) and not any(self.match(t, a, as_dir) for a in self.allow_read):
                return pattern
        return None

    # -- commands

    def command_denial(self, command: str):
        flat = _flat(command)
        for pattern in self.deny_command:
            if pattern == "@holdout":
                if any(s in flat for s in self._holdout_spellings()):
                    return pattern
                continue
            try:
                rx = re.compile(pattern, re.IGNORECASE)
            except re.error:
                continue  # a broken rule must not break the seat's turn
            if rx.search(command) or rx.search(flat):
                return pattern
        if any(s in flat for s in self.guarded_spellings):
            return "guard-self"
        return None

    def _holdout_spellings(self) -> set:
        base = self.ws_real or self.cwd
        forms = set().union(*(_spellings(h, base) for h in self.holdout_given)) if self.holdout_given else set()
        for h in self.holdouts:  # also as written relative to the workspace, e.g. ../../kickoff/x/tests
            if self.ws and _under(h, self.ws) and h != self.ws:
                rel = h[len(self.ws):].strip("/")
                if rel.count("/") >= 1:
                    forms.add(rel)
        return forms

    # -- searches

    def search_denial(self, real: str):
        """A search shows everything below its folder, so it is denied if a file the seat may
        not read lies at or below that folder."""
        is_dir = os.path.isdir(real) or not os.path.exists(real)
        pattern = self._exposes(real, is_dir, SCAN_DEPTH)
        if pattern or not is_dir:
            return pattern
        d = real.lower()
        if "@holdout" in self.deny_read and any(_under(h, d) for h in self.holdouts):
            return "@holdout"
        relative = [p for p in self.deny_read if p != "@holdout" and not _is_absolute_pattern(p)]
        if not relative:
            return None
        for root in self._roots_at_or_below(real):
            for pattern in relative:
                base = pattern[:-3] if pattern.endswith("/**") else pattern
                for hit in glob.glob(os.path.join(root, base)):
                    hit = hit.replace("\\", "/")
                    if _under(hit.lower(), d):
                        found = self._exposes(hit, os.path.isdir(hit), SCAN_DEPTH)
                        if found:
                            return found
        return None

    def _exposes(self, real: str, is_dir: bool, depth: int):
        pattern = self.read_denial(_Target(real, self), as_dir=is_dir)
        if pattern is None or not (is_dir and self.allow_read and depth > 0 and os.path.isdir(real)):
            return pattern
        try:  # denied folder with carve-outs (e.g. stage-*/** except stage-*/verify/**): look inside
            with os.scandir(real) as it:
                entries = [e for _, e in zip(range(SCAN_ENTRIES + 1), it)]
        except OSError:
            return pattern
        if len(entries) > SCAN_ENTRIES:
            return pattern
        for e in entries:
            found = self._exposes(e.path.replace("\\", "/"), e.is_dir(), depth - 1)
            if found:
                return found
        return None

    def _roots_at_or_below(self, real: str) -> list:
        d = real.lower()
        roots = []
        stop = self.ws_real if self.ws and _under(d, self.ws) else None
        own = checkout_root(real, stop)
        if own:
            roots.append(own)
        if self.ws and (_under(d, self.ws) or _under(self.ws, d)):
            for pattern in ("*/.git", "*/*/.git", "*/*/*/.git"):
                for g in glob.glob(os.path.join(self.ws_real, pattern)):
                    root = os.path.dirname(g).replace("\\", "/")
                    if _under(root.lower(), d) and root not in roots:
                        roots.append(root)
        return roots

    # -- writes

    def write_denial(self, t: _Target):
        if self.inside_only and self.ws and not _under(t.abs, self.ws):
            return "write_inside_workspace_only", None
        if any(_under(t.abs, g) for g in self.guarded):
            return "guard-self", None
        for pattern in self.deny_write:
            if self.match(t, pattern, False):
                return "deny_write", pattern
        pattern = self.read_denial(t)
        if pattern:  # what a seat may not read, it may not write either
            return "deny_read", pattern
        if self.allow_write_only is not None and not any(self.match(t, p, False) for p in self.allow_write_only):
            return "allow_write_only", None
        return None


# --- decision ------------------------------------------------------------------

def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _tool_denied(tool: str, deny: list):
    for name in deny:
        if tool == name or ("__" not in name and tool.endswith("__" + name)):
            return name
    return None


def evaluate(seat: str, event: dict, cfg: dict, protect=()):
    """(allowed, reason, rule, target) for one PreToolUse event."""
    if not isinstance(event, dict):
        return True, "", "", ""
    tool = _text(event.get("tool_name"))
    inp = event.get("tool_input")
    inp = inp if isinstance(inp, dict) else {}
    cwd = _text(event.get("cwd")) or os.getcwd()
    rules = _Rules(seat, cfg, cwd, protect)
    who = rules.name
    tail = "" if rules.seat == "foreman" else ", or escalate to the Foreman"

    hit = _tool_denied(tool, rules.deny_tools)
    if hit:
        return (False, f"{who} may not use {tool}: no seat asks or waits on the operator. Decide from the "
                       f"specification and record a ruling{tail}.", "deny_tools:" + hit, tool)

    if tool in COMMAND_TOOLS:
        command = _text(inp.get(COMMAND_TOOLS[tool]))
        hit = rules.command_denial(command) if command else None
        if hit:
            low = hit.lower()
            does, advice = next(((d, a) for key, d, a in _COMMAND_LABELS if key in low),
                                ("touches credential or seat-profile locations", "They are closed to every seat"))
            return False, f"{who} may not run this command: it {does}. {advice}.", "deny_command:" + hit, command[:500]
        return True, "", "", ""

    if tool in READ_TOOLS:
        path = _text(inp.get(READ_TOOLS[tool]))
        if path:
            hit = rules.read_denial(rules.target(path))
            if hit:
                area, advice = _labelled(rules.seat, hit, "read")
                return False, f"{who} may not read {area} ({hit}): {advice}.", "deny_read:" + hit, path
        return True, "", "", ""

    if tool in SEARCH_TOOLS:
        folder = _text(inp.get(SEARCH_TOOLS[tool])) or cwd
        folders = [resolve(folder, cwd)]
        root = _literal_root(_text(inp.get("pattern"))) if tool == "Glob" else ""
        if root:  # a Glob pattern can climb out of its folder: ../../x/*.py or C:/x/*.py
            folders.append(resolve(root, folders[0]))
        for f in folders:
            hit = rules.search_denial(f)
            if hit:
                area, _advice = _labelled(rules.seat, hit, "read")
                return (False, f"{who} may not search a folder that holds {area} ({hit}): search a narrower "
                               f"folder in your own checkout.", "search:" + hit, f)
        return True, "", "", ""

    if tool in WRITE_TOOLS:
        path = _text(inp.get(WRITE_TOOLS[tool]))
        if not path:
            return True, "", "", ""
        found = rules.write_denial(rules.target(path))
        if found is None:
            return True, "", "", ""
        kind, hit = found
        if kind == "write_inside_workspace_only":
            reason = f"{who} may write only inside the workspace ({rules.ws_real}): use its tmp folder for scratch files."
        elif kind == "guard-self":
            reason = f"{who} may not change the guard's own files: seat boundaries are fixed for the run."
        elif kind == "allow_write_only":
            reason = (f"{who} may write only {' and '.join(rules.allow_write_only)}: product changes go to the "
                      f"Foreman as findings.")
        else:
            area, advice = _labelled(rules.seat, hit, "edit")
            reason = f"{who} may not edit {area} ({hit}): {advice}."
        return False, reason, kind + (":" + hit if hit else ""), path

    return True, "", "", ""


def decide(seat: str, event: dict, cfg: dict, protect=()) -> tuple:
    """(allowed, reason) for one PreToolUse event under `cfg` (a parsed roles.json)."""
    allowed, reason, _rule, _target = evaluate(seat, event, cfg, protect)
    return allowed, reason


# --- hook entry point ------------------------------------------------------------

def _log(cfg, entry: dict) -> None:
    path = cfg.get("log") if isinstance(cfg, dict) else None
    if not path:
        return
    entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), **entry}
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def main(argv=None) -> int:
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--seat", default="")
        parser.add_argument("--config", default="")
        args, _unknown = parser.parse_known_args(argv)
    except (SystemExit, Exception):
        return 0
    try:
        with open(args.config, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config is not a JSON object")
    except Exception as e:  # no rules to apply: allow, and say why where the operator can see it
        print(f"guard: config unreadable ({e!r}); allowing", file=sys.stderr)
        return 0
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if not isinstance(event, dict):
            raise ValueError("event is not a JSON object")
    except Exception as e:
        _log(cfg, {"seat": args.seat, "tool": "", "target": "", "rule": "parse-error", "error": repr(e)[:300]})
        return 0
    try:
        allowed, reason, rule, target = evaluate(args.seat, event, cfg, protect=[args.config])
    except Exception as e:
        _log(cfg, {"seat": args.seat, "tool": _text(event.get("tool_name")), "target": "",
                   "rule": "guard-error", "error": repr(e)[:300]})
        return 0
    if allowed:
        return 0
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                  "permissionDecisionReason": reason}}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
    _log(cfg, {"seat": args.seat, "tool": _text(event.get("tool_name")), "target": target, "rule": rule})
    return 0


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
