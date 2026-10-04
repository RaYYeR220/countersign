"""Compose each seat mandate from its role file plus the shared protocol.

mandates/<seat>.md = mandates-src/<seat>.md + "\\n\\n---\\n\\n" + mandates-src/protocol.md
With --check, write nothing and exit 1 if any generated file is missing or out of date.
"""
import argparse
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
PROTOCOL = "protocol.md"
SEPARATOR = "\n\n---\n\n"


def compose(role: str, protocol: str) -> str:
    return role.rstrip() + SEPARATOR + protocol.strip() + "\n"


def build(src: Path, out: Path) -> dict[Path, bytes]:
    """Expected bytes for every output file, keyed by its path."""
    protocol = (src / PROTOCOL).read_text(encoding="utf-8")
    return {out / role.name: compose(role.read_text(encoding="utf-8"), protocol).encode("utf-8")
            for role in sorted(src.glob("*.md")) if role.name != PROTOCOL}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=KIT / "mandates-src")
    ap.add_argument("--out", type=Path, default=KIT / "mandates")
    ap.add_argument("--check", action="store_true", help="exit 1 if generated files are out of date")
    args = ap.parse_args(argv)
    expected = build(args.src, args.out)
    if args.check:
        stale = [p for p, data in expected.items() if not p.is_file() or p.read_bytes() != data]
        for p in stale:
            print(f"out of date: {p}", file=sys.stderr)
        return 1 if stale else 0
    args.out.mkdir(parents=True, exist_ok=True)
    for p, data in expected.items():
        p.write_bytes(data)
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
