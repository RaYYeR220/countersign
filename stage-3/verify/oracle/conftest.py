"""pytest configuration: the service under test is reached only through --base-url (or ORACLE_BASE_URL).

    python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18401
    python -m pytest stage-1/verify/oracle -q --base-url http://127.0.0.1:18401 --second-base-url http://127.0.0.1:18402

--second-base-url is a second, independent instance (fresh container) for the cross-process import test.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client import Client  # noqa: E402
from fixtures import base_fixture  # noqa: E402


def pytest_addoption(parser):
    parser.addoption("--base-url", default=os.environ.get("ORACLE_BASE_URL", "http://127.0.0.1:18400"))
    parser.addoption("--second-base-url", default=os.environ.get("ORACLE_SECOND_BASE_URL", ""))
    parser.addoption("--stage1-base-url", default=os.environ.get("ORACLE_STAGE1_BASE_URL", ""),
                     help="a running accepted stage-1 service, used to produce a real stage-1 export for the upgrade test")
    parser.addoption("--stage1-export", default=os.environ.get("ORACLE_STAGE1_EXPORT", ""),
                     help="path to a saved stage-1 export (used when no --stage1-base-url)")
    parser.addoption("--ui", action="store_true", default=False, help="run the headless-browser suite too")


@pytest.fixture(scope="session")
def base_url(request) -> str:
    return request.config.getoption("--base-url").rstrip("/")


@pytest.fixture(scope="session")
def second_base_url(request) -> str:
    return request.config.getoption("--second-base-url").rstrip("/")


@pytest.fixture
def c(base_url) -> Client:
    """A client against a freshly reset service (base fixture)."""
    cl = Client(base_url)
    r = cl.reset(base_fixture())
    assert r.status == 204, r
    return cl


@pytest.fixture
def ada(c) -> str:
    return c.login("ada@example.com", "correct horse")


@pytest.fixture
def bob(c) -> str:
    return c.login("bob@example.com", "bob secret 1")
