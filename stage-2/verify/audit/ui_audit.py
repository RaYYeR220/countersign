#!/usr/bin/env python3
"""Battery step 7 for stage 2: browser checks of the diner UI (Playwright, Chromium, headless).

Runs in the auditor-ui-runner container on the candidate's internal network:

    python ui_audit.py --base http://auditor-s2-a:8080 [--prev http://auditor-s2-p:8080] --out /out/ui.json --shots /out/shots

Covers: routes return HTML; every data-testid contract; grid cells mirror GET /availability (single and combination
cells); clicking rules; booking, combination booking, confirmation; resubmit returns the same reference; a changed field
is a new request; 409 recovery keeps the form; a lost response shows booking-uncertain and the unchanged retry reuses
key and body and recovers the original reference; out-of-order searches; lookup and cancel; named states visually
distinct; no horizontal scroll at 375/768/1280 px; keyboard reachability and visible labels; signed-in state and a
pending retry surviving an export/import between requests (and, with --prev, an upgrade from the previous stage).
Each check records clause section, expectation and actual value; screenshots of every named state go to --shots.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import (Checker, Client, Ctx, S, body2, booking_body, fixture, fixture2)  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

REF_RE = re.compile(r"^[A-Z0-9]{6,12}$")
TIMEOUT = 6000


def ui_fixture(ctx: Ctx) -> dict:
    fx = fixture2(ctx)
    nt = ctx.now_tz
    fx["restaurants"].append({"id": "r_near", "name": "Corner Bistro", "timezone": nt, "slot_minutes": 15,
                              "reservation_duration_minutes": 30, "cancellation_cutoff_minutes": 120,
                              "opening_hours": [{"weekday": w, "opens": "00:00", "closes": "23:45"} for w in
                                                ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
                              "tables": [{"id": "n_1", "label": "Corner", "capacity": 4}]})
    fx["restaurants"].append({"id": "r_closed", "name": "Weekend Only", "timezone": "Europe/Berlin", "slot_minutes": 30,
                              "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
                              "opening_hours": [{"weekday": "sat", "opens": "18:00", "closes": "22:00"}],
                              "tables": [{"id": "w_1", "label": "Weekend", "capacity": 4}]})
    return fx


class UI:
    def __init__(self, a):
        self.a = a
        self.chk = Checker()
        self.ctx = Ctx()
        self.api = Client(a.base, self.chk, "A")
        self.shots = Path(a.shots)
        self.shots.mkdir(parents=True, exist_ok=True)
        self.D = self.ctx.thu
        self.D2 = self.ctx.thu + timedelta(days=7)

    # ---------------------------------------------------------------- helpers
    def check(self, name, ok, expected=None, actual=None, section="S2 UI", soft=False):
        return self.chk.check(name, ok, expected, actual, None, section, soft)

    def shot(self, page, name):
        try:
            page.screenshot(path=str(self.shots / f"{name}-{page.viewport_size['width']}.png"), full_page=True)
        except Exception:
            pass

    @staticmethod
    def t(page, tid):
        return page.get_by_test_id(tid)

    def visible(self, page, tid, timeout=TIMEOUT):
        try:
            self.t(page, tid).first.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False

    def absent(self, page, tid, timeout=1500):
        """R-29: conditional elements are absent from the DOM, not merely hidden."""
        end = time.monotonic() + timeout / 1000
        while True:
            if self.t(page, tid).count() == 0:
                return True
            if time.monotonic() > end:
                return False
            time.sleep(0.1)

    def text(self, page, tid):
        try:
            return self.t(page, tid).first.inner_text(timeout=2000).strip()
        except Exception:
            return None

    def no_hscroll(self, page):
        return page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")

    def reset(self):
        r = self.api.req("POST", "/_test/reset", ui_fixture(self.ctx), timeout=12)
        if r.status != 204:
            raise RuntimeError(f"UI fixture reset failed: {r.status} {r.text(200)}")

    def api_token(self, email, password):
        r = self.api.req("POST", "/auth/login", {"email": email, "password": password})
        return (r.json or {}).get("token")

    def new_user(self):
        email = f"ui{uuid.uuid4().hex[:8]}@example.com"
        pw = "browser pass 1"
        r = self.api.req("POST", "/auth/signup", {"email": email, "password": pw, "display_name": "Mira Ui"})
        return email, pw, (r.json or {}).get("token")

    def ui_login(self, page, email, pw):
        page.goto(self.a.base + "/login")
        self.t(page, "login-email").fill(email)
        self.t(page, "login-password").fill(pw)
        self.t(page, "login-submit").click()
        return self.visible(page, "current-user")

    def search(self, page, rid, d, party):
        if page.evaluate("() => location.pathname") != "/":
            page.goto(self.a.base + "/")
        self.t(page, "restaurant-select").select_option(rid)
        self.t(page, "date-input").fill(str(d))
        self.t(page, "party-size-input").fill(str(party))
        self.t(page, "search-button").click()

    def api_slots(self, rid, d, party):
        r = self.api.req("GET", "/availability", query={"restaurant_id": rid, "date": str(d), "party_size": party})
        return (r.json or {}).get("slots", []) if r.status == 200 else []

    def grid_matches(self, page, rid, d, party, label):
        """Every single cell exists and mirrors available_table_ids; every available declared pair has a true cell."""
        slots = self.api_slots(rid, d, party)
        rest = next(x for x in ui_fixture(self.ctx)["restaurants"] if x["id"] == rid)
        tables = [t["id"] for t in rest["tables"]]
        caps = {t["id"]: t["capacity"] for t in rest["tables"]}
        cells = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('[data-testid^="slot-"]')]
                                  .map(e => [e.getAttribute('data-testid'), e.getAttribute('data-available')]))""")
        bad = []
        for sl in slots:
            hhmm = sl["starts_at_local"][-5:]
            for t in tables:
                want = "true" if t in sl.get("available_table_ids", []) else "false"
                got = cells.get(f"slot-{t}-{hhmm}")
                if got != want:
                    bad.append((f"slot-{t}-{hhmm}", want, got))
            free_pairs = {tuple(o["table_ids"]) for o in sl.get("available_options", []) if len(o["table_ids"]) == 2}
            for pair in rest.get("combinable", []):
                tid = f"slot-{pair[0]}+{pair[1]}-{hhmm}"
                if caps[pair[0]] + caps[pair[1]] >= party:          # R-39: capable pair -> cell in every slot
                    want = "true" if tuple(pair) in free_pairs else "false"
                    if cells.get(tid) != want:
                        bad.append((tid, want, cells.get(tid)))
                elif tid in cells:                                   # below party size -> no cell
                    bad.append((tid, "no cell", cells.get(tid)))
            for tid in cells:
                if "+" in tid and tid.endswith(hhmm) and not any(tid == f"slot-{a}+{b}-{hhmm}" for a, b in rest.get("combinable", [])):
                    bad.append((tid, "not a declared pair in combinable order", cells.get(tid)))
        self.check(f"{label}: grid cells mirror GET /availability ({len(slots)} slots)", bool(slots) and not bad, "all cells match", bad[:6],
                   "C2.26,C2.28,C2.55 (R-39)")
        return slots

    # ---------------------------------------------------------------- checks
    def u_routes(self, page):
        for route in ("/", "/signup", "/login", "/lookup"):
            r = page.goto(self.a.base + route)
            ct = (r.headers.get("content-type") or "") if r else ""
            self.check(f"route {route} returns 200 HTML", r is not None and r.status == 200 and "text/html" in ct, "200 text/html",
                       f"{r.status if r else None} {ct}", "C2.3,C2.4")
        page.goto(self.a.base + "/")
        for tid in ("restaurant-select", "date-input", "party-size-input", "search-button"):
            self.check(f"/ exposes {tid}", self.visible(page, tid), "visible", None, "C2.21,C2.25,C2.26,C2.28,C2.29")
        values = page.evaluate("() => [...document.querySelectorAll('[data-testid=\"restaurant-select\"] option')].map(o => o.value).filter(v => v)")
        want = [r["id"] for r in ui_fixture(self.ctx)["restaurants"]]
        self.check("restaurant-select option values are restaurant ids", sorted(values) == sorted(want), want, values, "C2.21,C2.25,C2.26,C2.28,C2.29")
        self.shot(page, "search-empty")

    def u_auth(self, page):
        page.goto(self.a.base + "/signup")
        self.check("auth-error absent before any error", self.absent(page, "auth-error"), "absent", None, "C2.16,C2.17,C2.18,C2.19,C2.20 (R-29)")
        email = f"su{uuid.uuid4().hex[:8]}@example.com"
        self.t(page, "signup-email").fill(email)
        self.t(page, "signup-password").fill("signup pass 1")
        self.t(page, "signup-display-name").fill("Noor Example")
        self.t(page, "signup-submit").click()
        ok = self.visible(page, "current-user")
        self.check("signup signs in: current-user shows the display name", ok and "Noor Example" in (self.text(page, "current-user") or ""),
                   "Noor Example", self.text(page, "current-user"), "C2.16,C2.17,C2.18,C2.19,C2.20 (R-29)")
        for route in ("/", "/lookup", "/login", "/signup"):
            page.goto(self.a.base + route)
            self.check(f"current-user visible on {route} when signed in", self.visible(page, "current-user", 3000)
                       and "Noor Example" in (self.text(page, "current-user") or ""), "visible", None, "C2.16,C2.17,C2.18,C2.19,C2.20 (R-29)")
        self.check("logout-button present when signed in", self.visible(page, "logout-button", 3000), "visible", None, "C2.16,C2.17,C2.18,C2.19,C2.20 (R-29)")
        self.t(page, "logout-button").first.click()
        self.check("logout removes current-user", self.absent(page, "current-user", 4000), "absent", None, "C2.16,C2.17,C2.18,C2.19,C2.20 (R-29)")
        page.goto(self.a.base + "/signup")
        self.t(page, "signup-email").fill(email)
        self.t(page, "signup-password").fill("signup pass 1")
        self.t(page, "signup-display-name").fill("Dup")
        self.t(page, "signup-submit").click()
        self.check("signup with a taken email shows auth-error", self.visible(page, "auth-error") and bool(self.text(page, "auth-error")),
                   "auth-error", None, "C2.16,C2.17,C2.18,C2.19,C2.20 (R-29)")
        self.shot(page, "auth-error")
        page.goto(self.a.base + "/login")
        self.t(page, "login-email").fill(email)
        self.t(page, "login-password").fill("wrong password")
        self.t(page, "login-submit").click()
        self.check("wrong password shows auth-error", self.visible(page, "auth-error"), "auth-error", None, "C2.16,C2.17,C2.18,C2.19,C2.20 (R-29)")
        self.check("login works", self.ui_login(page, email, "signup pass 1"), "current-user", None, "C2.16,C2.17,C2.18,C2.19,C2.20 (R-29)")
        self.t(page, "logout-button").first.click()
        self.absent(page, "current-user", 4000)

    def u_grid(self, page):
        page.goto(self.a.base + "/")
        self.search(page, "r_combo", self.D, 2)
        self.check("availability-grid shown after search", self.visible(page, "availability-grid"), "visible", None, "C2.21,C2.25,C2.26,C2.28,C2.29")
        self.grid_matches(page, "r_combo", self.D, 2, "party 2")
        self.shot(page, "grid")
        self.search(page, "r_combo", self.D, 7)
        time.sleep(0.5)
        self.grid_matches(page, "r_combo", self.D, 7, "party 7 (pairs only)")
        cells = page.evaluate("() => [...document.querySelectorAll('[data-testid^=\"slot-\"]')].map(e => e.getAttribute('data-testid'))")
        self.check("party 7: no cell for the pair whose summed capacity is below the party (R-39)", not any(x.startswith("slot-c_2+c_1-") for x in cells)
                   and any(x.startswith("slot-c_2+c_3-") for x in cells), "c_2+c_3 cells, no c_2+c_1", [x for x in cells if "+" in x][:4], "C2.55 (R-39)")
        self.search(page, "r_closed", self.D, 2)          # Thursday: closed
        self.check("closed day shows no-slots; availability-grid absent (R-29)", self.visible(page, "no-slots") and self.absent(page, "availability-grid")
                   and page.evaluate("() => !document.querySelector('[data-testid^=\"slot-\"]')"), "no-slots only", None, "C2.27 (R-29)")
        self.search(page, "r_combo", self.D, 2)
        self.check("day with slots: no-slots absent (R-29)", self.visible(page, "availability-grid") and self.absent(page, "no-slots"), "grid only", None,
                   "C2.25,C2.27 (R-29)")
        self.shot(page, "no-slots")

    def u_click_rules(self, page):
        page.goto(self.a.base + "/")
        self.check("signed out: current-user and logout-button absent (R-29)", self.absent(page, "current-user") and self.absent(page, "logout-button"),
                   "absent", None, "C2.19,C2.20 (R-29)")
        self.search(page, "r_combo", self.D, 2)
        self.visible(page, "availability-grid")
        for sel, what in (('[data-available="false"]:not([data-testid*="+"])', "single"), ('[data-available="false"][data-testid*="+"]', "combination")):
            busy = page.evaluate(f"() => {{ const e = document.querySelector('{sel}'); return e ? e.getAttribute('data-testid') : null }}")
            if busy:
                page.get_by_test_id(busy).click(force=True)
                self.check(f"clicking an unavailable {what} cell does nothing", self.absent(page, "booking-form", 1500) and "/login" not in page.url,
                           "no form, no navigation", page.url, "C2.29 (R-39)")
            else:
                self.check(f"an unavailable {what} cell exists for the click test", False, "seeded pair makes 12:00-13:30 unavailable", None, "harness")
        page.get_by_test_id("slot-c_4-19:00").click()
        try:
            page.wait_for_url(re.compile(r".*/login.*"), timeout=TIMEOUT)
            nav = True
        except Exception:
            nav = False
        self.check("signed out: clicking an available cell navigates to /login (R-40)", nav, "/login", page.url, "C2.30 (R-40)")
        self.shot(page, "signed-out-click")
        email, pw, _ = self.new_user()
        if nav:
            self.t(page, "login-email").fill(email)
            self.t(page, "login-password").fill(pw)
            self.t(page, "login-submit").click()
            back = self.visible(page, "booking-form")
            summary = self.text(page, "booking-summary") or ""
            self.check("after sign-in: back on / with the search restored and the form open for that cell (R-40)",
                       back and page.evaluate("() => location.pathname") == "/" and "Garden" in summary and "19:00" in summary,
                       "/ + form for Garden 19:00", [page.url, summary], "C2.30 (R-40)")
            self.check("after sign-in: the grid shows the same search (R-40)", self.t(page, "date-input").input_value() == str(self.D)
                       and self.t(page, "restaurant-select").input_value() == "r_combo", [str(self.D), "r_combo"],
                       [self.t(page, "date-input").input_value(), self.t(page, "restaurant-select").input_value()], "C2.30 (R-40)")
            self.t(page, "logout-button").first.click()
            self.absent(page, "current-user", 4000)
        page.goto(self.a.base + "/lookup")
        moved = False
        try:
            self.t(page, "lookup-reference-input").fill("ABCDEF", timeout=2000)
            self.t(page, "lookup-submit").click(timeout=2000)
        except Exception:
            pass
        try:
            page.wait_for_url(re.compile(r".*/login.*"), timeout=TIMEOUT)
            moved = True
        except Exception:
            pass
        self.check("signed out: using the lookup screen navigates to /login (R-40)", moved, "/login", page.url, "C2.34 (R-40)")
        if moved:
            self.t(page, "login-email").fill(email)
            self.t(page, "login-password").fill(pw)
            self.t(page, "login-submit").click()
            try:
                page.wait_for_url(re.compile(r".*/lookup.*"), timeout=TIMEOUT)
                ok = True
            except Exception:
                ok = False
            self.check("after sign-in: returns to /lookup (R-40)", ok, "/lookup", page.url, "C2.34 (R-40)")
            self.t(page, "logout-button").first.click()

    def book_flow(self, page, user, rid, d, party, cell, labels, hhmm, label):
        email, pw, tok = user
        self.ui_login(page, email, pw)
        page.goto(self.a.base + "/")
        self.search(page, rid, d, party)
        self.visible(page, "availability-grid")
        keys = []
        page.on("request", lambda rq: keys.append((rq.headers.get("idempotency-key"), rq.post_data)) if rq.method == "POST" and rq.url.endswith("/reservations") else None)
        page.get_by_test_id(cell).click()
        ok = self.visible(page, "booking-form")
        summary = self.text(page, "booking-summary") or ""
        self.check(f"{label}: booking-form opens; booking-summary names every table, HH:MM and the ISO date (R-32)",
                   ok and all(lb in summary for lb in labels) and hhmm in summary and str(d) in summary,
                   labels + [hhmm, str(d)], summary, "C2.31,C2.57 (R-32)")
        pre = page.get_by_test_id("booking-party-size").input_value() if ok else None
        self.check(f"{label}: booking-party-size pre-filled from the search", str(pre) == str(party), party, pre, "C2.31,C2.32 (R-38)")
        self.shot(page, f"form-{label}")
        self.t(page, "booking-submit").click()
        ok = self.visible(page, "confirmation")
        ref = self.text(page, "confirmation-reference")
        details = self.text(page, "confirmation-details") or ""
        tables = self.text(page, "confirmation-tables") or ""
        self.check(f"{label}: confirmation-reference is exactly a reference", ok and bool(ref and REF_RE.match(ref)), "^[A-Z0-9]{6,12}$", ref, "C2.33")
        name = next(x for x in ui_fixture(self.ctx)["restaurants"] if x["id"] == rid)["name"]
        self.check(f"{label}: confirmation-details has restaurant, table label, HH:MM and ISO date (R-32)",
                   name in details and labels[0] in details and hhmm in details and str(d) in details,
                   [name, labels[0], hhmm, str(d)], details, "C2.33 (R-32)")
        self.check(f"{label}: confirmation-tables lists every table label", all(lb in tables for lb in labels), labels, tables, "C2.55,C2.56,C2.57")
        self.check(f"{label}: no booking-error on success", self.absent(page, "booking-error", 800), "absent", None, "C2.31,C2.32 (R-38)")
        g = self.api.req("GET", f"/reservations/{ref}", token=tok) if ref else None
        self.check(f"{label}: reservation exists server-side for the shown reference", g is not None and g.status == 200, 200, g.status if g else None, "C2.33")
        self.shot(page, f"confirmation-{label}")
        self.check(f"{label}: booking-form stays on screen after success", self.visible(page, "booking-form", 2000), "visible", None, "C2.31,C2.32 (R-38)")
        n_before = len([x for x in (self.api.req('GET', '/reservations', token=tok).json or {}).get("reservations", [])])
        self.t(page, "booking-submit").click()
        time.sleep(1.0)
        ref2 = self.text(page, "confirmation-reference")
        n_after = len([x for x in (self.api.req('GET', '/reservations', token=tok).json or {}).get("reservations", [])])
        self.check(f"{label}: unchanged resubmit -> same reference, no booking-error, no new booking", ref2 == ref and n_after == n_before
                   and self.absent(page, "booking-error", 800), {"ref": ref, "count": n_before}, {"ref": ref2, "count": n_after}, "C2.31,C2.32 (R-38)")
        same_key = len(keys) >= 2 and keys[0] == keys[1]
        self.check(f"{label}: resubmit reused the idempotency key and body", same_key, "same key+body", keys[:2], "C2.32,C1.62 (R-38)")
        self.t(page, "booking-party-size").fill(str(party + 1 if party < 4 else party - 1))
        self.t(page, "booking-submit").click()
        time.sleep(1.0)
        self.check(f"{label}: changing a field makes a new request (new key)", len(keys) >= 3 and keys[2][0] != keys[0][0], "new key", keys[2:3], "C2.31,C2.32 (R-38)")
        return ref, tok

    def u_booking(self, page):
        user = self.new_user()
        self.book_flow(page, user, "r_combo", self.D, 2, "slot-c_4-19:00", ["Garden"], "19:00", "single")
        user2 = self.new_user()
        ref, tok = self.book_flow(page, user2, "r_combo", self.D, 5, "slot-c_2+c_1-20:00", ["Booth", "Window"], "20:00", "pair")
        g = self.api.req("GET", f"/reservations/{ref}", token=tok)
        self.check("pair booked from the UI holds both tables", sorted((g.json or {}).get("table_ids") or []) == ["c_1", "c_2"], ["c_1", "c_2"],
                   (g.json or {}).get("table_ids"), "C2.55,C2.56,C2.57")
        self.t(page, "logout-button").first.click()

    def u_conflict(self, page):
        email, pw, tok = self.new_user()
        self.ui_login(page, email, pw)
        page.goto(self.a.base + "/")
        self.search(page, "r_combo", self.D2, 3)
        self.visible(page, "availability-grid")
        page.get_by_test_id("slot-c_3-18:00").click()
        self.visible(page, "booking-form")
        self.t(page, "booking-party-size").fill("3")
        _, _, other = self.new_user()
        r = self.api.req("POST", "/reservations", booking_body("r_combo", "c_3", f"{self.D2}T18:00", 2), token=other, key=uuid.uuid4().hex)
        self.check("conflict setup: another client took the table", r.status == 201, 201, r.status, "harness")
        self.t(page, "booking-submit").click()
        self.check("409 shows booking-error", self.visible(page, "booking-error"), "booking-error", None, "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        self.check("409: no confirmation for that attempt", self.absent(page, "confirmation", 800), "absent", None, "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        self.check("409: form and inputs preserved", self.visible(page, "booking-form", 1000) and page.get_by_test_id("booking-party-size").input_value() == "3",
                   "form, party 3", None, "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        time.sleep(0.8)
        cell = page.get_by_test_id("slot-c_3-18:00").get_attribute("data-available") if page.get_by_test_id("slot-c_3-18:00").count() else None
        self.check("409 refreshes availability (cell now false)", cell == "false", "false", cell, "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        self.shot(page, "refused-409")
        self.t(page, "logout-button").first.click()

    def u_lost(self, page, commit_first: bool, label: str):
        email, pw, tok = self.new_user()
        self.ui_login(page, email, pw)
        page.goto(self.a.base + "/")
        d = self.D2 + timedelta(days=0 if commit_first else 7)
        self.search(page, "r_combo", d, 2)
        self.visible(page, "availability-grid")
        page.get_by_test_id("slot-c_2-15:00").click()
        self.visible(page, "booking-form")
        seen, committed = [], {}

        def handler(route):
            rq = route.request
            seen.append((rq.headers.get("idempotency-key"), rq.post_data))
            if commit_first:
                resp = route.fetch()
                committed["status"] = resp.status
                try:
                    committed["body"] = resp.json()
                except Exception:
                    committed["body"] = None
            route.abort("connectionreset")

        page.route(re.compile(r".*/reservations$"), handler)
        self.t(page, "booking-submit").click()
        unc = self.visible(page, "booking-uncertain")
        self.check(f"{label}: lost response shows non-empty booking-uncertain", unc and bool(self.text(page, "booking-uncertain")), "booking-uncertain",
                   self.text(page, "booking-uncertain"), "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        self.check(f"{label}: no booking-error and no confirmation while uncertain", self.absent(page, "booking-error", 800) and self.absent(page, "confirmation", 800),
                   "absent", None, "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        self.shot(page, f"uncertain-{label}")
        page.unroute(re.compile(r".*/reservations$"))
        keys = []
        page.on("request", lambda rq: keys.append((rq.headers.get("idempotency-key"), rq.post_data)) if rq.method == "POST" and rq.url.endswith("/reservations") else None)
        self.t(page, "booking-submit").click()
        ok = self.visible(page, "confirmation")
        ref = self.text(page, "confirmation-reference")
        self.check(f"{label}: retry uses the same idempotency key and body", bool(seen and keys) and keys[0] == seen[0], seen[:1], keys[:1], "C2.8,C1.62 (R-38)")
        want = (committed.get("body") or {}).get("reference") if commit_first else None
        self.check(f"{label}: retry shows the original reference", ok and bool(ref) and (want is None or ref == want), want or "a reference", ref,
                   "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        self.check(f"{label}: retry removes booking-uncertain and booking-error", self.absent(page, "booking-uncertain", 1500) and self.absent(page, "booking-error", 800),
                   "absent", None, "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        mine = [x for x in (self.api.req("GET", "/reservations", token=tok).json or {}).get("reservations", []) if x.get("starts_at_local") == f"{d}T15:00"]
        self.check(f"{label}: exactly one booking exists", len(mine) == 1, 1, len(mine), "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        self.t(page, "logout-button").first.click()

    def u_out_of_order(self, page):
        email, pw, tok = self.new_user()
        self.api.req("POST", "/reservations", booking_body("r_combo", "c_4", f"{self.D}T22:00", 2), token=tok, key=uuid.uuid4().hex)  # makes A differ from B
        self.api.req("POST", "/reservations", booking_body("r_combo", "c_4", f"{self.D}T21:30", 2), token=tok, key=uuid.uuid4().hex)
        self.ui_login(page, email, pw)
        page.goto(self.a.base + "/")
        held = []

        def handler(route):
            if f"date={self.D}" in route.request.url:
                held.append(route)          # A: answered after B
            else:
                route.continue_()

        page.route(re.compile(r".*/availability\?.*"), handler)
        self.search(page, "r_combo", self.D, 2)              # A (held)
        time.sleep(0.3)
        loading = page.evaluate("() => !!document.querySelector('[aria-busy=\"true\"], [data-loading], .loading, [data-testid*=\"loading\"]')")
        self.check("a loading state is shown while a search is in flight", loading, "aria-busy/loading marker", loading, "C2.11,C2.12,C2.14,C2.15", soft=True)
        self.shot(page, "loading")
        self.search(page, "r_combo", self.D2, 2)             # B
        self.visible(page, "availability-grid")
        time.sleep(0.5)
        for rt in held:
            rt.continue_()
        time.sleep(1.5)
        page.unroute(re.compile(r".*/availability\?.*"))
        slots_b = self.api_slots("r_combo", self.D2, 2)
        b_val = "true" if any(s["starts_at_local"].endswith("22:00") and "c_4" in s.get("available_table_ids", []) for s in slots_b) else "false"
        cell = page.get_by_test_id("slot-c_4-22:00").get_attribute("data-available") if page.get_by_test_id("slot-c_4-22:00").count() else None
        self.check("late response of search A does not replace search B's grid", held and cell == b_val, b_val, cell, "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)")
        self.grid_matches(page, "r_combo", self.D2, 2, "after out-of-order responses (B)")
        page.get_by_test_id("slot-c_4-22:00").click()
        summary = self.text(page, "booking-summary") if self.visible(page, "booking-form", 3000) else ""
        self.check("booking form after out-of-order search describes B", str(self.D2) in (summary or "") or "22:00" in (summary or ""), str(self.D2), summary,
                   "C2.5,C2.6,C2.7,C2.8,C2.9 (R-38)", soft=True)
        self.t(page, "logout-button").first.click()

    def u_lookup(self, page):
        email, pw, tok = self.new_user()
        r = self.api.req("POST", "/reservations", body2("r_combo", ["c_3", "c_4"], f"{self.D2}T20:00", 6), token=tok, key=uuid.uuid4().hex)
        ref = (r.json or {}).get("reference")
        near = self.api.req("POST", "/reservations", booking_body("r_near", "n_1", self.ctx.near, 2), token=tok, key=uuid.uuid4().hex)
        nref = (near.json or {}).get("reference")
        self.ui_login(page, email, pw)
        page.goto(self.a.base + "/lookup")
        self.t(page, "lookup-reference-input").fill(ref or "")
        self.t(page, "lookup-submit").click()
        ok = self.visible(page, "reservation-detail")
        self.check("lookup shows reservation-detail with status exactly 'confirmed'", ok and self.text(page, "reservation-status") == "confirmed", "confirmed",
                   self.text(page, "reservation-status"), "C2.34 (R-29)")
        rt = self.text(page, "reservation-tables") or ""
        self.check("reservation-tables lists every table label", "Terrace" in rt and "Garden" in rt, ["Terrace", "Garden"], rt, "C2.55,C2.56,C2.57")
        self.shot(page, "lookup-confirmed")
        self.t(page, "reservation-cancel-button").click()
        time.sleep(1.0)
        self.check("cancel -> status exactly 'cancelled', cancel button gone", self.text(page, "reservation-status") == "cancelled"
                   and self.absent(page, "reservation-cancel-button", 2000), "cancelled", self.text(page, "reservation-status"), "C2.34 (R-29)")
        g = self.api.req("GET", f"/reservations/{ref}", token=tok)
        self.check("cancel really happened server-side", (g.json or {}).get("status") == "cancelled", "cancelled", (g.json or {}).get("status"), "C2.34 (R-29)")
        self.shot(page, "lookup-cancelled")
        page.goto(self.a.base + "/lookup")
        self.t(page, "lookup-reference-input").fill("ZZZZZZ")
        self.t(page, "lookup-submit").click()
        self.check("unknown reference -> reservation-error", self.visible(page, "reservation-error"), "reservation-error", None, "C2.34 (R-29)")
        self.shot(page, "lookup-error")
        if nref:
            page.goto(self.a.base + "/lookup")
            self.t(page, "lookup-reference-input").fill(nref)
            self.t(page, "lookup-submit").click()
            self.visible(page, "reservation-detail")
            self.t(page, "reservation-cancel-button").click()
            self.check("refused cancel (inside cutoff) -> reservation-error, still confirmed", self.visible(page, "reservation-error")
                       and self.text(page, "reservation-status") == "confirmed", "reservation-error", self.text(page, "reservation-status"), "C2.34 (R-29)")
        self.t(page, "logout-button").first.click()

    def u_layout(self, browser):
        email, pw, _ = self.new_user()
        for w, h in ((375, 812), (768, 1024), (1280, 800)):
            page = browser.new_page(viewport={"width": w, "height": h})
            self.ui_login(page, email, pw)
            for route in ("/", "/signup", "/login", "/lookup"):
                page.goto(self.a.base + route)
                time.sleep(0.3)
                self.check(f"no horizontal scroll at {w}px on {route}", self.no_hscroll(page), "scrollWidth <= clientWidth", None, "C2.11,C2.12,C2.14,C2.15")
            page.goto(self.a.base + "/")
            self.search(page, "r_combo", self.D, 2)
            self.visible(page, "availability-grid")
            self.check(f"no horizontal page scroll at {w}px with the grid", self.no_hscroll(page), "no page h-scroll", None, "C2.11,C2.12,C2.14,C2.15")
            page.get_by_test_id("slot-c_4-21:00").click()
            self.visible(page, "booking-form", 3000)
            self.check(f"no horizontal scroll at {w}px with the booking form", self.no_hscroll(page), "no page h-scroll", None, "C2.11,C2.12,C2.14,C2.15")
            self.shot(page, "grid-form")
            page.close()

    def u_states_distinct(self, page):
        email, pw, _ = self.new_user()
        self.ui_login(page, email, pw)
        page.goto(self.a.base + "/")
        self.search(page, "r_combo", self.D, 2)
        self.visible(page, "availability-grid")
        style = "e => { const s = getComputedStyle(e); return [s.backgroundColor, s.color, s.borderColor, s.opacity, s.textDecorationLine, s.cursor].join('|') }"
        av = page.locator('[data-available="true"]').first.evaluate(style)
        un = page.locator('[data-available="false"]').first.evaluate(style)
        self.check("available and unavailable cells look different", av != un, "different computed styles", [av, un], "C2.11,C2.12,C2.14,C2.15")
        cell = page.get_by_test_id("slot-c_3-21:00")
        before = cell.evaluate(style)
        cell.click()
        self.visible(page, "booking-form", 3000)
        after = cell.evaluate(style + "") if cell.count() else None
        sel = page.evaluate("() => !!document.querySelector('[aria-pressed=\"true\"], [aria-selected=\"true\"], [aria-current], [data-selected=\"true\"]')")
        self.check("selected cell is visually marked", after != before or sel, "changed style or aria state", [before, after, sel], "C2.11,C2.12,C2.14,C2.15")
        self.shot(page, "selected")
        self.t(page, "logout-button").first.click()

    def u_keyboard(self, page):
        email, pw, _ = self.new_user()
        self.ui_login(page, email, pw)
        page.goto(self.a.base + "/")
        reached = set()
        for _ in range(40):
            page.keyboard.press("Tab")
            tid = page.evaluate("() => document.activeElement && document.activeElement.getAttribute('data-testid')")
            if tid:
                reached.add(tid)
        need = {"restaurant-select", "date-input", "party-size-input", "search-button"}
        self.check("search controls reachable by Tab", need <= reached, sorted(need), sorted(reached), "C2.11,C2.12,C2.14,C2.15")
        outline = page.get_by_test_id("search-button").evaluate("e => { e.focus(); const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.boxShadow].join('|') }")
        self.check("keyboard focus is visible on controls", not outline.startswith("none|0px|none"), "outline or ring", outline, "C2.11,C2.12,C2.14,C2.15")
        self.search(page, "r_combo", self.D, 2)
        self.visible(page, "availability-grid")
        page.get_by_test_id("slot-c_3-22:00").focus()
        page.keyboard.press("Enter")
        self.check("an available cell opens the form from the keyboard (Enter)", self.visible(page, "booking-form", 3000), "booking-form", None,
                   "C2.11,C2.12,C2.14,C2.15")
        got = False
        for _ in range(25):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement && document.activeElement.getAttribute('data-testid')") == "booking-submit":
                got = True
                break
        self.check("booking-submit reachable by Tab", got, "focus", None, "C2.11,C2.12,C2.14,C2.15")
        labels = page.evaluate("""() => [...document.querySelectorAll('input, select')].filter(e => e.offsetParent !== null).map(e => {
            const id = e.id; const lab = id && document.querySelector(`label[for="${id}"]`);
            return [e.getAttribute('data-testid'), !!(lab || e.closest('label') || e.getAttribute('aria-label') || e.getAttribute('aria-labelledby'))]; })""")
        unlabeled = [t for t, ok in labels if not ok]
        self.check("every visible input has a label", not unlabeled, [], unlabeled, "C2.11,C2.12,C2.14,C2.15")
        self.t(page, "logout-button").first.click()

    def u_import_between_requests(self, page, prev: Client | None):
        """Signed-in browser and a pending retry survive an export/import between requests (and an upgrade from --prev)."""
        legacy = None
        if prev is not None:
            sp = S(prev, self.chk, self.ctx)
            sp.reset()
            ltok = sp.signup(email="legacy-ui@example.com", password="legacy pass 1", name="Legacy Diner")
            lr = prev.req("POST", "/reservations", booking_body("r_anker", "t_3", self.ctx.D(self.ctx.fri, "18:00"), 2), token=ltok, key=uuid.uuid4().hex)
            E1 = prev.req("GET", "/_test/export").json
            r = self.api.req("POST", "/_test/import", E1, timeout=12)
            self.check("UI upgrade: candidate imports the previous stage's export", r.status == 204, 204, r.status, "C2.35,C2.36,C2.37 (R-30,R-37)")
            legacy = (lr.json or {}).get("reference")
            email, pw = "legacy-ui@example.com", "legacy pass 1"
            rid, d, cell = "r_anker", self.ctx.thu, "slot-t_2-20:00"
        else:
            email, pw, _ = self.new_user()
            rid, d, cell = "r_combo", self.D2 + timedelta(days=14), "slot-c_2-18:00"
        self.check("UI upgrade: sign in", self.ui_login(page, email, pw), "current-user", None, "C2.35,C2.36,C2.37 (R-30,R-37)")
        if legacy:
            page.goto(self.a.base + "/lookup")
            self.t(page, "lookup-reference-input").fill(legacy)
            self.t(page, "lookup-submit").click()
            self.check("UI upgrade: a pre-upgrade reference works on the lookup screen", self.visible(page, "reservation-detail")
                       and self.text(page, "reservation-status") == "confirmed", "confirmed", self.text(page, "reservation-status"), "C2.35,C2.36,C2.37 (R-30,R-37)")
        page.goto(self.a.base + "/")
        self.search(page, rid, d, 2)
        self.visible(page, "availability-grid")
        page.get_by_test_id(cell).click()
        self.visible(page, "booking-form")
        seen, committed = [], {}

        def handler(route):
            seen.append((route.request.headers.get("idempotency-key"), route.request.post_data))
            resp = route.fetch()
            try:
                committed["body"] = resp.json()
            except Exception:
                committed["body"] = None
            route.abort("connectionreset")

        page.route(re.compile(r".*/reservations$"), handler)
        self.t(page, "booking-submit").click()
        self.visible(page, "booking-uncertain")
        page.unroute(re.compile(r".*/reservations$"))
        E = self.api.req("GET", "/_test/export").json          # export/import between browser requests
        self.api.req("POST", "/_test/reset", fixture(self.ctx), timeout=12)
        r = self.api.req("POST", "/_test/import", E, timeout=12)
        self.check("UI upgrade: re-import between requests -> 204", r.status == 204, 204, r.status, "C2.35,C2.36,C2.37 (R-30,R-37)")
        # no reload: the page stays as it was
        self.check("UI upgrade: still signed in without a reload", self.visible(page, "current-user", 2000), "current-user", None, "C2.35,C2.36,C2.37 (R-30,R-37)")
        keys = []
        page.on("request", lambda rq: keys.append((rq.headers.get("idempotency-key"), rq.post_data)) if rq.method == "POST" and rq.url.endswith("/reservations") else None)
        self.t(page, "booking-submit").click()
        ok = self.visible(page, "confirmation")
        ref = self.text(page, "confirmation-reference")
        want = (committed.get("body") or {}).get("reference")
        self.check("UI upgrade: pending retry keeps key and body across the import", bool(seen and keys) and keys[0] == seen[0], seen[:1], keys[:1], "C2.35,C2.36,C2.37 (R-30,R-37)")
        self.check("UI upgrade: retry after import recovers the original confirmation", ok and ref == want, want, ref, "C2.35,C2.36,C2.37 (R-30,R-37)")
        self.t(page, "logout-button").first.click()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--prev")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shots", required=True)
    ap.add_argument("--only", help="comma-separated u_* names")
    a = ap.parse_args(argv)
    ui = UI(a)
    prev = Client(a.prev, ui.chk, "P") if a.prev else None
    order = ["u_routes", "u_auth", "u_grid", "u_click_rules", "u_booking", "u_conflict", "u_lost_after", "u_lost_before",
             "u_out_of_order", "u_lookup", "u_states_distinct", "u_keyboard", "u_layout", "u_import"]
    if a.only:
        order = [x for x in order if x in a.only.split(",")]
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name in order:
            ui.chk.group = name
            print(f"== {name}", flush=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(TIMEOUT)
            try:
                ui.reset()
                if name == "u_lost_after":
                    ui.u_lost(page, True, "lost after commit")
                elif name == "u_lost_before":
                    ui.u_lost(page, False, "lost before commit")
                elif name == "u_layout":
                    ui.u_layout(browser)
                elif name == "u_import":
                    ui.u_import_between_requests(page, prev)
                else:
                    getattr(ui, name)(page)
            except Exception as e:
                errors.append({"group": name, "error": repr(e)[:300], "trace": traceback.format_exc()[-1200:]})
                ui.check("group ran to completion", False, "no exception", repr(e)[:300], "harness")
                ui.shot(page, f"error-{name}")
            finally:
                page.close()
        browser.close()
    res = ui.chk.results
    hard = [r for r in res if not r["ok"] and not r["soft"]]
    soft = [r for r in res if not r["ok"] and r["soft"]]
    per = {}
    for r in res:
        g = per.setdefault(r["group"], {"pass": 0, "fail": 0, "soft": 0})
        g["pass" if r["ok"] else ("soft" if r["soft"] else "fail")] += 1
    summary = {"checks": len(res), "hard_failures": len(hard), "soft_failures": len(soft), "per_group": per, "errors": errors}
    Path(a.out).write_text(json.dumps({**summary, "failures": hard, "soft": soft, "results": res}, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
