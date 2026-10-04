import os
import sys
from pathlib import Path

import pytest


def _root() -> Path:
    """The kit root, or the result repository root when the tests ship under factory/tests."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "factory" / "hooks" / "guard.py").exists():
            return parent
    return here.parents[1]


KIT = _root()
sys.path.insert(0, str(KIT))


@pytest.fixture(scope="session")
def kickoff_dir() -> Path:
    candidates = [os.environ.get("CS_KICKOFF"), str(KIT.parent / "kickoff"), "C:/countersign/kickoff"]
    for c in candidates:
        if c and (Path(c) / "harness" / "vocabulary.py").exists():
            return Path(c)
    pytest.skip("event kickoff package not found; set CS_KICKOFF")


@pytest.fixture(scope="session")
def kit_dir() -> Path:
    return KIT
