"""Stylist's own headless-browser check of the stage-2 UI (not a verification suite).

Drives every named state at 375, 768 and 1280 px, asserts the behaviour the specification names,
checks for horizontal scrolling, and writes screenshots next to this file.

    python ui_check.py --base http://127.0.0.1:18210 [--widths 375,768,1280]
"""
import argparse
import asyncio
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright, expect

expect.set_options(timeout=10000)

OUT = Path(__file__).resolve().parent
TODAY = dt.date.today()
FUTURE = TODAY + dt.timedelta(days=30)
while FUTURE.weekday() == 0:  # Mondays are closed in the fixture
    FUTURE += dt.timedelta(days=1)
CLOSED = FUTURE + dt.timedelta(days=(7 - FUTURE.weekday()) % 7 or 7)  # next Monday
D = FUTURE.isoformat()

ALL_DAYS = ["tue", "wed", "thu", "fri", "sat", "sun"]
FIXTURE = {
    "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada Lovelace"},
        {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"},
    ],
    "restaurants": [
        {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
         "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
         "opening_hours": [{"weekday": d, "opens": "17:00", "closes": "22:00"} for d in ALL_DAYS],
         "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4},
                    {"id": "t_3", "label": "Window", "capacity": 6}],
         "combinable": [["t_1", "t_2"], ["t_2", "t_3"]]},
        {"id": "r_pier", "name": "Pier 9 Oyster Room", "timezone": "America/New_York", "slot_minutes": 15,
         "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 60,
         "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "21:00"} for d in ALL_DAYS],
         "tables": [{"id": "p_1", "label": "Bar 1", "capacity": 2}, {"id": "p_2", "label": "Booth", "capacity": 4}]},
    ],
    "reservations": [
        {"id": "res_past", "reference": "PAST01", "user_id": "u_ada", "restaurant_id": "r_anker", "table_id": "t_2",
         "starts_at_local": "2026-09-24T19:00", "party_size": 2},
        {"id": "res_bob", "reference": "BOBS01", "user_id": "u_bob", "restaurant_id": "r_anker", "table_id": "t_1",
         "starts_at_local": f"{D}T19:00", "party_size": 2},
    ],
}

results = []


def check(cond, what):
    results.append((bool(cond), what))
    if not cond:
        print("FAIL:", what, flush=True)


def http(base, method, path, body=None, token=None, key=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if key:
        req.add_header("Idempotency-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


def reset(base):
    status, _ = http(base, "POST", "/_test/reset", FIXTURE)
    assert status == 204, status


def token_for(base, email):
    return http(base, "POST", "/auth/login", {"email": email, "password": "correct horse"})[1]["token"]


async def shot(page, width, name):
    no_hscroll = await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    check(no_hscroll, f"{width}px {name}: no horizontal scroll")
    await page.screenshot(path=str(OUT / f"{width}-{name}.png"), full_page=True, animations="disabled")


async def grid_matches_api(page, base, width, restaurant, date, party, label):
    """Every cell against GET /availability and GET /restaurants/{id} (C2.26, C2.28, R-39)."""
    _, avail = http(base, "GET", f"/availability?restaurant_id={restaurant}&date={date}&party_size={party}")
    _, rest = http(base, "GET", f"/restaurants/{restaurant}")
    caps = {t["id"]: t["capacity"] for t in rest["tables"]}
    want = {}
    for slot in avail["slots"]:
        hhmm = slot["starts_at_local"][11:16]
        for t in rest["tables"]:
            want[f"slot-{t['id']}-{hhmm}"] = "true" if t["id"] in slot["available_table_ids"] else "false"
        options = {frozenset(o["table_ids"]) for o in slot.get("available_options", [])}
        for a, b in rest.get("combinable", []):
            if caps[a] + caps[b] >= int(party):
                want[f"slot-{a}+{b}-{hhmm}"] = "true" if frozenset((a, b)) in options else "false"
    got = await page.eval_on_selector_all('[data-testid^="slot-"]', "els => els.map(e => [e.dataset.testid, e.dataset.available])")
    check(dict(got) == want and len(got) == len(want), f"{width}: grid == API for {label} ({len(got)} cells)")


async def search(page, restaurant="r_anker", date=D, party="2"):
    await page.get_by_test_id("restaurant-select").select_option(restaurant)
    await page.get_by_test_id("date-input").fill(date)
    await page.get_by_test_id("party-size-input").fill(party)
    await page.get_by_test_id("search-button").click()


async def sign_in(page, email="ada@example.com", password="correct horse"):
    await page.get_by_test_id("login-email").fill(email)
    await page.get_by_test_id("login-password").fill(password)
    await page.get_by_test_id("login-submit").click()


async def run_width(pw, base, width):
    browser = await pw.chromium.launch()
    ctx = await browser.new_context(viewport={"width": width, "height": 900}, device_scale_factor=1)
    page = await ctx.new_page()
    page.set_default_timeout(8000)
    reset(base)

    # 1. Before the first search (signed out).
    await page.goto(base + "/")
    await expect(page.get_by_test_id("restaurant-select").locator("option[value=r_anker]")).to_have_count(1)
    check(await page.get_by_test_id("current-user").count() == 0, f"{width}: no current-user while signed out")
    check(await page.get_by_test_id("availability-grid").count() == 0, f"{width}: no grid before searching")
    await shot(page, width, "01-search-start")

    # 2. Loading: hold the availability response.
    gate = asyncio.Event()

    async def held(route):
        await gate.wait()
        await route.continue_()
    await page.route("**/availability?*", held)
    await search(page)
    await expect(page.locator("[aria-busy=true]")).to_have_count(1)
    await shot(page, width, "02-loading")
    gate.set()
    await expect(page.get_by_test_id("availability-grid")).to_be_visible()
    await page.unroute("**/availability?*")

    # 3. Results: available / unavailable / combination cells.
    check(await page.get_by_test_id("slot-t_1-19:00").get_attribute("data-available") == "false", f"{width}: booked t_1 19:00 unavailable")
    check(await page.get_by_test_id("slot-t_2-19:00").get_attribute("data-available") == "true", f"{width}: t_2 19:00 available")
    check(await page.get_by_test_id("slot-t_1-18:00").get_attribute("data-available") == "false", f"{width}: t_1 18:00 overlaps 19:00 booking")
    check(await page.get_by_test_id("slot-t_1-17:30").get_attribute("data-available") == "true", f"{width}: t_1 17:30 ends as 19:00 starts")
    check(await page.get_by_test_id("slot-t_1-20:30").get_attribute("data-available") == "true", f"{width}: t_1 20:30 free (half-open)")
    check(await page.get_by_test_id("no-slots").count() == 0, f"{width}: no-slots absent when slots exist")
    await grid_matches_api(page, base, width, "r_anker", D, "2", "party 2")
    await shot(page, width, "03-results")

    # Clicking an unavailable cell does nothing.
    await page.get_by_test_id("slot-t_1-19:00").click()
    check(page.url.rstrip("/") == base and await page.get_by_test_id("booking-form").count() == 0, f"{width}: unavailable click is a no-op")

    # 4. Closed day.
    await search(page, date=CLOSED.isoformat())
    await expect(page.get_by_test_id("no-slots")).to_be_visible()
    check(await page.get_by_test_id("availability-grid").count() == 0, f"{width}: grid absent on a closed day")
    await shot(page, width, "04-no-slots")

    # 5. Search error (connection fails).
    await page.route("**/availability?*", lambda route: route.abort())
    await search(page)
    await expect(page.get_by_text("Couldn't load availability.")).to_be_visible()
    check(await page.get_by_test_id("availability-grid").count() == 0, f"{width}: no stale grid after a failed search")
    await shot(page, width, "05-search-error")
    await page.unroute("**/availability?*")

    # 6. Out-of-order searches: A (party 6) is slow, B (party 2) is fast; the grid must describe B.
    first = asyncio.Event()

    async def slow_first(route):
        if "party_size=6" in route.request.url:
            await first.wait()
        await route.continue_()
    await page.route("**/availability?*", slow_first)
    await search(page, party="6")
    await search(page, party="2")
    await expect(page.get_by_test_id("availability-grid")).to_be_visible()
    first.set()
    await page.wait_for_timeout(400)
    check(await page.get_by_test_id("slot-t_2-19:00").get_attribute("data-available") == "true", f"{width}: late search A did not replace B")
    await page.unroute("**/availability?*")

    # 7. Signed-out click on an available cell → /login, then the form opens after sign-in.
    await page.get_by_test_id("slot-t_2-19:00").click()
    await page.wait_for_url("**/login")
    await shot(page, width, "06-login-required")
    await sign_in(page, password="wrong password")
    await expect(page.get_by_test_id("auth-error")).to_be_visible()
    await shot(page, width, "07-login-error")
    await sign_in(page)
    await expect(page.get_by_test_id("booking-form")).to_be_visible()
    await expect(page.get_by_test_id("current-user")).to_contain_text("Ada Lovelace")
    check(await page.get_by_test_id("auth-error").count() == 0, f"{width}: auth-error absent after sign-in")
    summary = await page.get_by_test_id("booking-summary").inner_text()
    check("2" in summary and "19:00" in summary and D in summary, f"{width}: summary names table, time and ISO date ({summary!r})")
    check(await page.get_by_test_id("booking-party-size").input_value() == "2", f"{width}: party pre-filled")
    await shot(page, width, "08-selected-form")

    # 8. Refused: party larger than the table.
    await page.get_by_test_id("booking-party-size").fill("9")
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("booking-error")).to_be_visible()
    check(await page.get_by_test_id("confirmation").count() == 0, f"{width}: no confirmation on refusal")
    await shot(page, width, "09-refused")

    # 9. Success, then an unchanged resubmission returns the same reference.
    await page.get_by_test_id("booking-party-size").fill("3")
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("confirmation")).to_be_visible()
    ref = (await page.get_by_test_id("confirmation-reference").inner_text()).strip()
    details = await page.get_by_test_id("confirmation-details").inner_text()
    check(ref.isalnum() and ref.isupper() and 6 <= len(ref) <= 12, f"{width}: reference text exact ({ref!r})")
    check("Zum Anker" in details and "19:00" in details and "2" in details and D in details, f"{width}: confirmation details ({details!r})")
    check(await page.get_by_test_id("booking-error").count() == 0, f"{width}: booking-error cleared on success")
    check(await page.get_by_test_id("booking-form").count() == 1, f"{width}: form stays after success")
    await shot(page, width, "10-success")
    await page.get_by_test_id("booking-submit").click()
    await page.wait_for_timeout(500)
    check((await page.get_by_test_id("confirmation-reference").inner_text()).strip() == ref, f"{width}: resubmit returns same reference")
    ada = token_for(base, "ada@example.com")
    mine = http(base, "GET", "/reservations", token=ada)[1]["reservations"]
    check(sum(1 for r in mine if r["starts_at_local"] == f"{D}T19:00") == 1, f"{width}: exactly one booking after resubmit")

    # 10. Conflict: another client takes t_3 at 20:00 after the form opens.
    await search(page, party="2")
    await page.get_by_test_id("slot-t_3-20:00").click()
    await expect(page.get_by_test_id("booking-form")).to_be_visible()
    await page.get_by_test_id("booking-party-size").fill("5")
    bob = token_for(base, "bob@example.com")
    st, _ = http(base, "POST", "/reservations", {"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": f"{D}T20:00", "party_size": 2}, bob, "bob-k1")
    check(st == 201, f"{width}: competing booking created ({st})")
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("booking-error")).to_be_visible()
    await expect(page.get_by_test_id("slot-t_3-20:00")).to_have_attribute("data-available", "false")
    check(await page.get_by_test_id("booking-party-size").input_value() == "5", f"{width}: inputs preserved after 409")
    check(await page.get_by_test_id("confirmation").count() == 0, f"{width}: no confirmation after 409")
    await shot(page, width, "11-conflict")

    # 11. Uncertain: the booking commits but its response is lost; the retry recovers it.
    await page.get_by_test_id("slot-t_2-17:00").click()
    await expect(page.get_by_test_id("booking-form")).to_be_visible()

    async def lose_response(route):
        if route.request.method == "POST":
            await route.fetch()  # reaches the server and commits
            await route.abort("connectionreset")
        else:
            await route.continue_()
    await page.route("**/reservations", lose_response)
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("booking-uncertain")).to_be_visible()
    check((await page.get_by_test_id("booking-uncertain").inner_text()).strip() != "", f"{width}: uncertain text nonempty")
    check(await page.get_by_test_id("booking-error").count() == 0, f"{width}: no booking-error while uncertain")
    check(await page.get_by_test_id("confirmation").count() == 0, f"{width}: no confirmation while uncertain")
    await shot(page, width, "12-uncertain")
    await page.unroute("**/reservations")

    # 12. Upgrade between requests: export, wipe, import; the browser stays signed in and the retry recovers.
    st, exported = http(base, "GET", "/_test/export")
    reset(base)
    http(base, "POST", "/_test/import", exported)
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("confirmation")).to_be_visible()
    check(await page.get_by_test_id("booking-uncertain").count() == 0, f"{width}: uncertainty cleared after retry")
    ref2 = (await page.get_by_test_id("confirmation-reference").inner_text()).strip()
    mine = http(base, "GET", "/reservations", token=ada)[1]["reservations"]
    at17 = [r for r in mine if r["starts_at_local"] == f"{D}T17:00"]
    check(len(at17) == 1 and at17[0]["reference"] == ref2, f"{width}: retry recovered the original booking ({ref2})")
    await expect(page.get_by_test_id("current-user")).to_contain_text("Ada")
    await shot(page, width, "13-recovered")

    # 13. Keyboard: reach a cell with Tab and open it with Enter.
    await search(page, restaurant="r_pier", party="2")
    await expect(page.get_by_test_id("availability-grid")).to_be_visible()
    await page.get_by_test_id("search-button").focus()
    for _ in range(40):
        await page.keyboard.press("Tab")
        tid = await page.evaluate("document.activeElement && document.activeElement.dataset.testid || ''")
        if tid.startswith("slot-") and await page.evaluate("document.activeElement.dataset.available") == "true":
            break
    check(tid.startswith("slot-p_"), f"{width}: keyboard reaches a cell ({tid})")
    await shot(page, width, "14-keyboard-focus")
    await page.keyboard.press("Enter")
    await expect(page.get_by_test_id("booking-form")).to_be_visible()

    # 14. Lookup: found, cancel, not found, cancel refused.
    await page.get_by_role("link", name="Look up a booking").click()
    await page.wait_for_url("**/lookup")
    await expect(page.get_by_test_id("current-user")).to_be_visible()
    await page.get_by_test_id("lookup-reference-input").fill(ref)
    await page.get_by_test_id("lookup-submit").click()
    await expect(page.get_by_test_id("reservation-detail")).to_be_visible()
    check((await page.get_by_test_id("reservation-status").inner_text()).strip() == "confirmed", f"{width}: status confirmed")
    check("2" in await page.get_by_test_id("reservation-tables").inner_text(), f"{width}: reservation-tables label")
    await shot(page, width, "15-lookup-found")
    await page.get_by_test_id("reservation-cancel-button").click()
    await expect(page.get_by_test_id("reservation-status")).to_have_text("cancelled")
    check(await page.get_by_test_id("reservation-cancel-button").count() == 0, f"{width}: cancel button absent once cancelled")
    check(await page.get_by_test_id("reservation-error").count() == 0, f"{width}: no reservation-error after cancel")
    await shot(page, width, "16-lookup-cancelled")
    await page.get_by_test_id("lookup-reference-input").fill("NOPE99")
    await page.get_by_test_id("lookup-submit").click()
    await expect(page.get_by_test_id("reservation-error")).to_be_visible()
    check(await page.get_by_test_id("reservation-detail").count() == 0, f"{width}: no detail when not found")
    await shot(page, width, "17-lookup-not-found")
    await page.get_by_test_id("lookup-reference-input").fill("PAST01")
    await page.get_by_test_id("lookup-submit").click()
    await expect(page.get_by_test_id("reservation-detail")).to_be_visible()
    await page.get_by_test_id("reservation-cancel-button").click()
    await expect(page.get_by_test_id("reservation-error")).to_be_visible()
    check((await page.get_by_test_id("reservation-status").inner_text()).strip() == "confirmed", f"{width}: refused cancel keeps status")
    await shot(page, width, "18-lookup-cancel-refused")

    # 15. Signup screen and its error; logout.
    await page.get_by_test_id("logout-button").click()
    check(await page.get_by_test_id("current-user").count() == 0, f"{width}: signed out")
    await page.goto(base + "/signup")
    await shot(page, width, "19-signup")
    await page.get_by_test_id("signup-display-name").fill("Ada again")
    await page.get_by_test_id("signup-email").fill("ADA@example.com")
    await page.get_by_test_id("signup-password").fill("longenough")
    await page.get_by_test_id("signup-submit").click()
    await expect(page.get_by_test_id("auth-error")).to_be_visible()
    await shot(page, width, "20-signup-error")
    await page.get_by_test_id("signup-email").fill(f"new{width}@example.com")
    await page.get_by_test_id("signup-submit").click()
    await expect(page.get_by_test_id("current-user")).to_contain_text("Ada again")
    check(page.url.rstrip("/") == base, f"{width}: signup lands on search")

    # 16. Direct visit to /lookup signed out.
    await page.get_by_test_id("logout-button").click()
    await page.goto(base + "/lookup")
    await page.get_by_test_id("lookup-reference-input").fill(ref)
    await page.get_by_test_id("lookup-submit").click()
    await page.wait_for_url("**/login")
    await shot(page, width, "21-lookup-signed-out")
    await sign_in(page)
    await page.wait_for_url("**/lookup")
    await expect(page.get_by_test_id("reservation-detail")).to_be_visible()
    check((await page.get_by_test_id("reservation-status").inner_text()).strip() == "cancelled", f"{width}: lookup resumes after sign-in")
    await page.get_by_test_id("logout-button").click()

    await combo_step(page, base, width)
    await browser.close()


async def combo_step(page, base, width):
    """Combination cells and a pair booking on the real core (C2.55-C2.57, R-39), then a 401 (R-30)."""
    await page.goto(base + "/")
    await search(page, party="6")
    await expect(page.get_by_test_id("availability-grid")).to_be_visible()
    await grid_matches_api(page, base, width, "r_anker", D, "6", "party 6")
    await search(page, party="7")
    await expect(page.get_by_test_id("availability-grid")).to_be_visible()
    await grid_matches_api(page, base, width, "r_anker", D, "7", "party 7")
    check(await page.locator('[data-testid^="slot-t_1+t_2-"]').count() == 0, f"{width}: pair too small for 7 has no cell")
    await search(page, party="6")
    await expect(page.get_by_test_id("availability-grid")).to_be_visible()
    cell = page.locator('[data-testid^="slot-t_1+t_2-"][data-available="true"]').first
    tid = await cell.get_attribute("data-testid")
    hhmm = tid.rsplit("-", 1)[1]
    await cell.click()
    await page.wait_for_url("**/login")  # signed out at this point: sign in, then the form opens
    await sign_in(page)
    await expect(page.get_by_test_id("booking-form")).to_be_visible()
    summary = await page.get_by_test_id("booking-summary").inner_text()
    check("1" in summary and "2" in summary and hhmm in summary and D in summary, f"{width}: pair summary ({summary!r})")
    await shot(page, width, "22-combination")
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("confirmation")).to_be_visible()
    tables = await page.get_by_test_id("confirmation-tables").inner_text()
    check("1" in tables and "2" in tables, f"{width}: confirmation-tables lists both ({tables!r})")
    pair_ref = (await page.get_by_test_id("confirmation-reference").inner_text()).strip()
    ada = token_for(base, "ada@example.com")
    _, got = http(base, "GET", f"/reservations/{pair_ref}", token=ada)
    check(got.get("table_ids") == ["t_1", "t_2"], f"{width}: server holds the pair ({got.get('table_ids')})")
    await shot(page, width, "23-combination-booked")
    await page.get_by_role("link", name="Look up a booking").click()
    await page.get_by_test_id("lookup-reference-input").fill(pair_ref)
    await page.get_by_test_id("lookup-submit").click()
    await expect(page.get_by_test_id("reservation-detail")).to_be_visible()
    rt = await page.get_by_test_id("reservation-tables").inner_text()
    check("1" in rt and "2" in rt, f"{width}: reservation-tables lists both ({rt!r})")

    # 401: the server forgets the token (reset); the next booking shows booking-error + auth-error.
    await page.get_by_role("link", name="Find a table").click()
    await search(page, party="2")
    await expect(page.get_by_test_id("availability-grid")).to_be_visible()
    await page.get_by_test_id("slot-t_3-17:00").click()
    await expect(page.get_by_test_id("booking-form")).to_be_visible()
    reset(base)
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("auth-error")).to_be_visible()
    await expect(page.get_by_test_id("booking-error")).to_be_visible()
    check(await page.get_by_test_id("current-user").count() == 0, f"{width}: 401 clears the session")
    check(await page.evaluate("localStorage.getItem('tk.session')") is None, f"{width}: 401 forgets the token")
    await shot(page, width, "24-session-ended")


async def stage1_upgrade(pw, base, base1, width):
    """C2.35-C2.37: browser on a stage-1 service, lost booking, export → import into stage-2, retry."""
    reset(base1)
    reset(base)
    browser = await pw.chromium.launch()
    ctx = await browser.new_context(viewport={"width": width, "height": 900})
    page = await ctx.new_page()
    page.set_default_timeout(8000)
    api_prefixes = ("/auth/", "/restaurants", "/availability", "/reservations")
    mode = {"lose": False}

    async def to_stage1(route):
        path = route.request.url[len(base):]
        if not path.startswith(api_prefixes):
            await route.continue_()
            return
        resp = await route.fetch(url=base1 + path)
        if mode["lose"] and route.request.method == "POST" and path == "/reservations":
            await route.abort("connectionreset")  # committed on stage 1, response lost
            return
        await route.fulfill(response=resp)
    await page.route("**/*", to_stage1)

    await page.goto(base + "/login")
    await sign_in(page)
    await expect(page.get_by_test_id("current-user")).to_contain_text("Ada")
    await search(page, party="2")
    await expect(page.get_by_test_id("availability-grid")).to_be_visible()
    await page.get_by_test_id("slot-t_3-17:00").click()
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("confirmation")).to_be_visible()
    kept = (await page.get_by_test_id("confirmation-reference").inner_text()).strip()
    await page.get_by_test_id("slot-t_2-17:00").click()
    mode["lose"] = True
    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("booking-uncertain")).to_be_visible()
    await shot(page, width, "25-stage1-uncertain")

    st, exported = http(base1, "GET", "/_test/export")
    st2, _ = http(base, "POST", "/_test/import", exported)
    check(st == 200 and st2 == 204, f"{width}: stage-1 export {st} → stage-2 import {st2}")
    await page.unroute("**/*")  # from now on the browser talks to the stage-2 service

    await page.get_by_test_id("booking-submit").click()
    await expect(page.get_by_test_id("confirmation")).to_be_visible()
    recovered = (await page.get_by_test_id("confirmation-reference").inner_text()).strip()
    token = json.loads(await page.evaluate("localStorage.getItem('tk.session')"))["token"]
    _, stage1_list = http(base1, "GET", "/reservations", token=token)
    committed = [r["reference"] for r in stage1_list["reservations"]
                 if r["starts_at_local"] == f"{D}T17:00" and r["table_id"] == "t_2"]
    check(committed == [recovered], f"{width}: upgrade retry shows the original reference ({recovered} vs {committed})")
    check(await page.get_by_test_id("booking-uncertain").count() == 0 and await page.get_by_test_id("booking-error").count() == 0,
          f"{width}: no uncertainty after recovery")
    await expect(page.get_by_test_id("current-user")).to_contain_text("Ada")
    await shot(page, width, "26-stage1-recovered")
    await page.get_by_role("link", name="Look up a booking").click()
    await page.get_by_test_id("lookup-reference-input").fill(kept)
    await page.get_by_test_id("lookup-submit").click()
    await expect(page.get_by_test_id("reservation-detail")).to_be_visible()
    check((await page.get_by_test_id("reservation-status").inner_text()).strip() == "confirmed",
          f"{width}: retained reference looks up after upgrade")
    await browser.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--widths", default="375,768,1280")
    ap.add_argument("--stage1", help="base URL of a stage-1 service for the upgrade scenario")
    args = ap.parse_args()
    async with async_playwright() as pw:
        for w in [int(x) for x in args.widths.split(",")]:
            try:
                await run_width(pw, args.base.rstrip("/"), w)
                if args.stage1:
                    await stage1_upgrade(pw, args.base.rstrip("/"), args.stage1.rstrip("/"), w)
            except Exception as e:  # report and continue with the next width
                import traceback
                traceback.print_exc(limit=-3)
                check(False, f"{w}px aborted: {type(e).__name__}: {str(e).splitlines()[0]}")
    failed = [w for ok, w in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for f in failed:
        print("  FAILED:", f)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
