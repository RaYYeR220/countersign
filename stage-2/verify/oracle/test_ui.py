"""Stage-2 browser suite (Oracle): the UI clauses of evidence/stage-2/ledger-B.md through a headless Chromium.

Runs only with `--ui`. Every check goes through the served screens plus the public HTTP API for cross-checks.
Network conditions (out-of-order searches, lost responses) are produced with Playwright route interception, so
the server under test is never modified.
"""
from __future__ import annotations

import json
import re
import threading
import time

import pytest

from client import Client
from fixtures import FUT_DAY, FUT_FRI, FUT_THU, FUT_WED, PAST_DAY, base_fixture, stage1_fixture

pytestmark = pytest.mark.ui

REF = re.compile(r"^[A-Z0-9]{6,12}$")


@pytest.fixture(scope="session")
def browser(request):
    if not request.config.getoption("--ui"):
        pytest.skip("browser suite runs with --ui")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url, c):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, base_url=base_url)
    pg = ctx.new_page()
    pg.set_default_timeout(8000)
    yield pg
    ctx.close()


def tid(page, name: str):
    return page.locator(f'[data-testid="{name}"]')


def login(page, email="ada@example.com", password="correct horse"):
    page.goto("/login")
    tid(page, "login-email").fill(email)
    tid(page, "login-password").fill(password)
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for()


def wait_ready(page):
    """The search screen may populate its restaurant list asynchronously."""
    page.wait_for_function("(() => { const s = document.querySelector('[data-testid=\"restaurant-select\"]'); return s && s.options.length > 0; })()")


def search(page, rid: str, date: str, party: int):
    page.goto("/")
    wait_ready(page)
    tid(page, "restaurant-select").select_option(rid)
    tid(page, "date-input").fill(date)
    tid(page, "party-size-input").fill(str(party))
    tid(page, "search-button").click()
    page.wait_for_selector('[data-testid="availability-grid"], [data-testid="no-slots"]')


def cells(page) -> dict:
    out = {}
    for h in page.locator('[data-testid^="slot-"]').element_handles():
        out[h.get_attribute("data-testid")] = h.get_attribute("data-available")
    return out


# ============================================================ routes and chrome
def test_B4_B7_routes_return_html(c, base_url):
    for path in ("/", "/signup", "/login", "/lookup"):
        r = c.get(path)
        assert r.status == 200 and r.headers.get("content-type", "").startswith("text/html"), (path, r.status)
    assert c.get("/restaurants").headers.get("content-type", "").startswith("application/json")


def test_B4_B18_B27_screens_expose_testids_and_nav(page):
    page.goto("/signup")
    for n in ("signup-email", "signup-password", "signup-display-name", "signup-submit"):
        assert tid(page, n).count() == 1, n
    page.goto("/login")
    for n in ("login-email", "login-password", "login-submit"):
        assert tid(page, n).count() == 1, n
    page.goto("/lookup")
    for n in ("lookup-reference-input", "lookup-submit"):
        assert tid(page, n).count() == 1, n
    page.goto("/")
    wait_ready(page)
    for n in ("restaurant-select", "date-input", "party-size-input", "search-button"):
        assert tid(page, n).count() == 1, n
    navs = []
    for path in ("/", "/signup", "/login", "/lookup"):
        page.goto(path)
        navs.append(sorted(set(page.locator("nav a").evaluate_all("as => as.map(a => a.getAttribute('href'))"))))
    assert all(n == navs[0] for n in navs) and navs[0], navs


# ============================================================ auth
def test_B29_B31_B32_B33_signup_login_logout(page, c):
    page.goto("/signup")
    assert tid(page, "auth-error").count() == 0
    tid(page, "signup-email").fill("ui@example.com")
    tid(page, "signup-password").fill("ui-password-1")
    tid(page, "signup-display-name").fill("Uma")
    tid(page, "signup-submit").click()
    tid(page, "current-user").wait_for()
    assert "Uma" in tid(page, "current-user").inner_text()
    for path in ("/", "/signup", "/login", "/lookup"):
        page.goto(path)
        assert tid(page, "current-user").is_visible() and "Uma" in tid(page, "current-user").inner_text(), path
    tid(page, "logout-button").click()
    assert tid(page, "current-user").count() == 0
    page.goto("/login")
    tid(page, "login-email").fill("ada@example.com")
    tid(page, "login-password").fill("wrong horse")
    tid(page, "login-submit").click()
    tid(page, "auth-error").wait_for()
    assert tid(page, "current-user").count() == 0
    tid(page, "login-password").fill("correct horse")
    tid(page, "login-submit").click()
    tid(page, "current-user").wait_for()
    assert tid(page, "auth-error").count() == 0 and "Ada" in tid(page, "current-user").inner_text()


def test_B30_B31_signup_errors(page):
    page.goto("/signup")
    tid(page, "signup-email").fill("ada@example.com")
    tid(page, "signup-password").fill("another-password")
    tid(page, "signup-display-name").fill("Dup")
    tid(page, "signup-submit").click()
    tid(page, "auth-error").wait_for()
    assert tid(page, "current-user").count() == 0


# ============================================================ search grid
def test_B34_B40_B41_grid_matches_api(page, c):
    page.goto("/")
    wait_ready(page)
    values = tid(page, "restaurant-select").locator("option").evaluate_all("os => os.map(o => o.value)")
    assert values == [r["id"] for r in c.get("/restaurants").json["restaurants"]]
    texts = tid(page, "restaurant-select").locator("option").evaluate_all("os => os.map(o => o.textContent)")
    assert any("Zum Anker" in t for t in texts)
    search(page, "r_anker", FUT_FRI, 3)
    assert tid(page, "date-input").input_value() == FUT_FRI and tid(page, "party-size-input").input_value() == "3"
    api = c.availability("r_anker", FUT_FRI, 3).json
    got = cells(page)
    tables = [t["id"] for t in c.get("/restaurants/r_anker").json["tables"]]
    for s in api["slots"]:
        hm = s["starts_at_local"][-5:]
        for t in tables:
            assert got.get(f"slot-{t}-{hm}") == ("true" if t in s["available_table_ids"] else "false"), (t, hm)
    assert len([k_ for k_ in got if "+" not in k_]) == len(tables) * len(api["slots"])
    # closed day
    search(page, "r_anker", FUT_WED, 2)
    assert tid(page, "no-slots").is_visible()
    assert page.locator('[data-testid^="slot-"]').count() == 0


def test_B37_search_sends_the_inputs(page):
    seen = []
    page.on("request", lambda r: seen.append(r.url) if "/availability" in r.url else None)
    search(page, "r_all", FUT_DAY, 4)
    assert any(f"restaurant_id=r_all" in u and f"date={FUT_DAY}" in u and "party_size=4" in u for u in seen), seen


def test_B79_B21_pair_cells(page, c):
    search(page, "r_anker", FUT_FRI, 5)
    api = c.availability("r_anker", FUT_FRI, 5).json
    got = cells(page)
    for s in api["slots"]:
        hm = s["starts_at_local"][-5:]
        pairs = {"+".join(o["table_ids"]) for o in s["available_options"] if len(o["table_ids"]) == 2}
        true_pairs = {k_[5:-6] for k_, v in got.items() if k_.endswith("-" + hm) and "+" in k_ and v == "true"}
        assert true_pairs == pairs, (hm, true_pairs, pairs)
        assert all(got[f"slot-{t}-{hm}"] == "false" for t in ("t_1", "t_2")), hm
    cell = tid(page, "slot-t_1+t_2-19:00")
    txt = cell.inner_text()
    assert "1" in txt and "2" in txt and "t_1+t_2" not in txt, txt


def test_B23_states_visually_distinct(page):
    search(page, "r_anker", FUT_FRI, 5)
    avail = tid(page, "slot-t_1+t_2-19:00").evaluate("e => getComputedStyle(e).backgroundColor + '|' + getComputedStyle(e).color")
    unavail = tid(page, "slot-t_1-19:00").evaluate("e => getComputedStyle(e).backgroundColor + '|' + getComputedStyle(e).color")
    assert avail != unavail


# ============================================================ booking form and confirmation
def test_B42_B43_B45_B46_B47_B48_B50_booking_flow(page, c, ada):
    login(page)
    search(page, "r_anker", FUT_FRI, 2)
    tid(page, "slot-t_2-19:00").click()
    tid(page, "booking-form").wait_for()
    summary = tid(page, "booking-summary").inner_text()
    assert "2" in summary and "19:00" in summary, summary
    assert tid(page, "booking-party-size").input_value() == "2"
    tid(page, "booking-submit").click()
    tid(page, "confirmation").wait_for()
    ref = tid(page, "confirmation-reference").inner_text().strip()
    assert REF.match(ref), ref
    details = tid(page, "confirmation-details").inner_text()
    assert "Zum Anker" in details and "2" in details and "19:00" in details, details
    assert tid(page, "booking-form").is_visible() and tid(page, "booking-error").count() == 0
    mine = c.get("/reservations", token=ada).json["reservations"]
    assert [x["reference"] for x in mine] == [ref] and mine[0]["table_ids"] == ["t_2"]
    # resubmitting unchanged: same reference, no new booking
    tid(page, "booking-submit").click()
    page.wait_for_timeout(300)
    assert tid(page, "confirmation-reference").inner_text().strip() == ref
    assert tid(page, "booking-error").count() == 0
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 1
    # changing a field: a new booking request (party 1 on the same slot -> 409 now, because t_3 is ours) -> use another cell
    search(page, "r_anker", FUT_FRI, 2)
    assert cells(page)["slot-t_2-19:00"] == "false"
    tid(page, "slot-t_2-19:00").click()
    assert tid(page, "booking-form").count() == 0
    tid(page, "slot-t_1-21:00").click()
    tid(page, "booking-form").wait_for()
    tid(page, "booking-party-size").fill("1")
    tid(page, "booking-submit").click()
    tid(page, "confirmation").wait_for()
    ref2 = tid(page, "confirmation-reference").inner_text().strip()
    assert ref2 != ref
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 2


def test_B44_booking_requires_sign_in(page):
    search(page, "r_anker", FUT_FRI, 2)
    tid(page, "slot-t_1-19:00").click()
    page.wait_for_timeout(300)
    assert tid(page, "auth-error").count() == 1 or page.url.endswith("/login"), page.url


def test_B80_B81_B82_pair_booking_labels(page, c, ada):
    login(page)
    search(page, "r_anker", FUT_FRI, 6)
    tid(page, "slot-t_1+t_2-19:00").click()
    tid(page, "booking-form").wait_for()
    summary = tid(page, "booking-summary").inner_text()
    assert "1" in summary and "2" in summary, summary
    tid(page, "booking-submit").click()
    tid(page, "confirmation").wait_for()
    ref = tid(page, "confirmation-reference").inner_text().strip()
    tables = tid(page, "confirmation-tables").inner_text()
    assert "1" in tables and "2" in tables, tables
    o = c.get(f"/reservations/{ref}", token=ada).json
    assert o["table_ids"] == ["t_1", "t_2"]
    page.goto("/lookup")
    tid(page, "lookup-reference-input").fill(ref)
    tid(page, "lookup-submit").click()
    tid(page, "reservation-detail").wait_for()
    t = tid(page, "reservation-tables").inner_text()
    assert "1" in t and "2" in t, t


# ============================================================ lookup
def test_B51_lookup_and_cancel(page, c, ada):
    o = c.book(ada, "k-ui-lookup", "r_all", "a_1", f"{FUT_DAY}T12:00", 2).json
    past = c.book(ada, "k-ui-past", "r_all", "a_2", f"{PAST_DAY}T12:00", 2).json
    login(page)
    page.goto("/lookup")
    tid(page, "lookup-reference-input").fill("NOPE01")
    tid(page, "lookup-submit").click()
    tid(page, "reservation-error").wait_for()
    assert tid(page, "reservation-detail").count() == 0
    tid(page, "lookup-reference-input").fill(o["reference"])
    tid(page, "lookup-submit").click()
    tid(page, "reservation-detail").wait_for()
    assert tid(page, "reservation-status").inner_text().strip() == "confirmed"
    tid(page, "reservation-cancel-button").click()
    page.wait_for_function("document.querySelector('[data-testid=\"reservation-status\"]').textContent.trim() === 'cancelled'")
    assert tid(page, "reservation-cancel-button").count() == 0
    assert c.get(f"/reservations/{o['reference']}", token=ada).json["status"] == "cancelled"
    tid(page, "lookup-reference-input").fill(past["reference"])
    tid(page, "lookup-submit").click()
    tid(page, "reservation-detail").wait_for()
    tid(page, "reservation-cancel-button").click()
    tid(page, "reservation-error").wait_for()
    assert c.get(f"/reservations/{past['reference']}", token=ada).json["status"] == "confirmed"


# ============================================================ competing clients
def test_B9_out_of_order_searches(page, c):
    """Search A (r_anker, Friday) is held until search B (r_all) has rendered; A's late response must not win."""
    gate = threading.Event()
    held = []

    def handler(route, request):
        if "restaurant_id=r_anker" in request.url and not held:
            held.append(route)          # park A
        else:
            route.continue_()

    page.route("**/availability*", handler)
    page.goto("/")
    tid(page, "restaurant-select").select_option("r_anker")
    tid(page, "date-input").fill(FUT_FRI)
    tid(page, "party-size-input").fill("2")
    tid(page, "search-button").click()                       # A, parked
    page.wait_for_timeout(200)
    tid(page, "restaurant-select").select_option("r_all")
    tid(page, "date-input").fill(FUT_DAY)
    tid(page, "search-button").click()                       # B, answered
    page.wait_for_selector('[data-testid="slot-a_1-10:00"]')
    assert held, "search A was not intercepted"
    held[0].continue_()                                       # A's late response arrives now
    page.wait_for_timeout(500)
    got = cells(page)
    assert "slot-a_1-10:00" in got and not any(k_.startswith("slot-t_") for k_ in got), list(got)[:5]
    page.unroute("**/availability*")


def test_B10_B17_conflict_after_form_opens(page, c, ada, bob):
    login(page)
    search(page, "r_anker", FUT_FRI, 2)
    tid(page, "slot-t_1-19:00").click()
    tid(page, "booking-form").wait_for()
    assert c.book(bob, "k-ui-steal", "r_anker", "t_1", f"{FUT_FRI}T19:00", 2).status == 201
    tid(page, "booking-submit").click()
    tid(page, "booking-error").wait_for()
    assert tid(page, "confirmation").count() == 0
    assert tid(page, "booking-form").is_visible() and tid(page, "booking-party-size").input_value() == "2"
    page.wait_for_function("document.querySelector('[data-testid=\"slot-t_1-19:00\"]').getAttribute('data-available') === 'false'")
    assert c.get("/reservations", token=ada).json["reservations"] == []


def _lost_response_once(page):
    state = {"dropped": False, "keys": [], "bodies": []}

    def handler(route, request):
        state["keys"].append(request.headers.get("idempotency-key"))
        state["bodies"].append(json.loads(request.post_data or "null"))
        if not state["dropped"]:
            state["dropped"] = True
            # let the server process it, then drop the response on the floor
            resp = route.fetch()
            _ = resp.status
            route.abort("failed")
        else:
            route.continue_()

    page.route("**/reservations", handler)
    return state


def test_B11_B12_B13_lost_response_then_retry(page, c, ada):
    login(page)
    search(page, "r_anker", FUT_FRI, 2)
    tid(page, "slot-t_2-19:00").click()
    tid(page, "booking-form").wait_for()
    state = _lost_response_once(page)
    tid(page, "booking-submit").click()
    tid(page, "booking-uncertain").wait_for()
    assert tid(page, "booking-uncertain").inner_text().strip() != ""
    assert tid(page, "booking-error").count() == 0 and tid(page, "confirmation").count() == 0
    mine = c.get("/reservations", token=ada).json["reservations"]
    assert len(mine) == 1 and mine[0]["table_ids"] == ["t_2"]           # the booking committed server-side
    tid(page, "booking-submit").click()                                   # unchanged form: retry
    tid(page, "confirmation").wait_for()
    assert state["keys"][0] == state["keys"][1] and state["bodies"][0] == state["bodies"][1], state
    assert tid(page, "booking-uncertain").count() == 0 and tid(page, "booking-error").count() == 0
    assert tid(page, "confirmation-reference").inner_text().strip() == mine[0]["reference"]
    assert len(c.get("/reservations", token=ada).json["reservations"]) == 1
    page.unroute("**/reservations")


def test_B14_B15_lost_response_then_rejection_pair(page, c, ada, bob):
    login(page)
    search(page, "r_anker", FUT_FRI, 6)
    tid(page, "slot-t_1+t_2-19:00").click()
    tid(page, "booking-form").wait_for()
    # the request itself never reaches the server this time; then another client takes t_2
    state = {"n": 0}

    def handler(route, request):
        state["n"] += 1
        if state["n"] == 1:
            route.abort("failed")
        else:
            route.continue_()

    page.route("**/reservations", handler)
    tid(page, "booking-submit").click()
    tid(page, "booking-uncertain").wait_for()
    assert c.book(bob, "k-ui-steal2", "r_anker", "t_2", f"{FUT_FRI}T19:00", 2).status == 201
    tid(page, "booking-submit").click()
    tid(page, "booking-error").wait_for()
    assert tid(page, "booking-uncertain").count() == 0 and tid(page, "confirmation").count() == 0
    assert c.get("/reservations", token=ada).json["reservations"] == []
    page.unroute("**/reservations")


# ============================================================ upgrade (B53, B55, B57)
def test_B53_B54_B55_B57_browser_survives_import(page, c, ada):
    """Sign in, start a booking whose response is lost, then replace the state through export/import
    (between browser requests); the session, the lookup and the pending retry must survive."""
    login(page)
    search(page, "r_anker", FUT_FRI, 2)
    tid(page, "slot-t_2-19:00").click()
    tid(page, "booking-form").wait_for()
    state = _lost_response_once(page)
    tid(page, "booking-submit").click()
    tid(page, "booking-uncertain").wait_for()
    committed = c.get("/reservations", token=ada).json["reservations"]
    assert len(committed) == 1
    exp = c.export().json
    assert c.reset(stage1_fixture()).status == 204                   # the destination is wiped ...
    assert c.import_(exp).status == 204                              # ... and the exported state restored
    assert tid(page, "current-user").is_visible()
    tid(page, "booking-submit").click()                              # same key and body, no reload
    tid(page, "confirmation").wait_for()
    assert state["keys"][0] == state["keys"][1] and state["bodies"][0] == state["bodies"][1]
    assert tid(page, "confirmation-reference").inner_text().strip() == committed[0]["reference"]
    page.unroute("**/reservations")
    page.goto("/lookup")
    assert tid(page, "current-user").is_visible()
    tid(page, "lookup-reference-input").fill(committed[0]["reference"])
    tid(page, "lookup-submit").click()
    tid(page, "reservation-detail").wait_for()
    assert tid(page, "reservation-status").inner_text().strip() == "confirmed"


# ============================================================ layout and accessibility
@pytest.mark.parametrize("width", [375, 768, 1280])
def test_B25_B26_no_horizontal_scroll_labels_focus(browser, base_url, c, width):
    ctx = browser.new_context(viewport={"width": width, "height": 812}, base_url=base_url)
    page = ctx.new_page()
    page.set_default_timeout(8000)
    try:
        for path in ("/", "/signup", "/login", "/lookup"):
            page.goto(path)
            assert page.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth + 1"), (path, width)
            for inp in page.locator("input, select").element_handles():
                ok = inp.evaluate("""e => {
                    const id = e.id; const byFor = id && document.querySelector(`label[for="${id}"]`);
                    const wrapped = e.closest('label'); const aria = e.getAttribute('aria-label') || e.getAttribute('aria-labelledby');
                    return !!(byFor || wrapped || aria);
                }""")
                assert ok, (path, inp.get_attribute("data-testid"))
        login(page)
        search(page, "r_anker", FUT_FRI, 5)
        assert page.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth + 1"), ("grid", width)
        tid(page, "slot-t_1+t_2-19:00").click()
        tid(page, "booking-form").wait_for()
        assert page.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth + 1"), ("form", width)
        # keyboard: the search button is reachable by Tab and shows a visible focus style
        page.goto("/")
        wait_ready(page)
        for _ in range(16):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement && document.activeElement.getAttribute('data-testid')") == "search-button":
                break
        assert page.evaluate("document.activeElement.getAttribute('data-testid')") == "search-button"
        style = page.evaluate("(() => { const s = getComputedStyle(document.activeElement); return s.outlineStyle + '|' + s.outlineWidth + '|' + s.boxShadow; })()")
        assert not style.startswith("none|0px|none"), style
    finally:
        ctx.close()
