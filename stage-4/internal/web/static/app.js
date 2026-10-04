// Tablekeeper browser client. One page; screens are rendered by route (/, /signup, /login, /lookup).
// The server is authoritative: nothing here invents a result. Booking retries reuse the exact
// idempotency key and body (§7), so a lost response can be recovered without a second booking.
"use strict";

(function () {
  const SESSION_KEY = "tk.session";
  const BOOKING_TIMEOUT_MS = 15000;
  const READ_TIMEOUT_MS = 20000;

  // ---------- state ----------
  const S = {
    session: loadSession(),         // {token, user_id, display_name} | null
    restaurants: null,              // [{id,name,timezone}] | null
    restaurantsError: false,
    inputs: { restaurantId: "", date: todayISO(), party: "2" },
    searchSeq: 0,                   // newest search wins; older responses are discarded
    results: null,                  // {status:'loading'|'ok'|'error', params, restaurant, slots}
    selection: null,                // {params, restaurant, tableIds, hhmm, startsAtLocal}
    form: null,                     // {party, key, bodyText, inFlight, error, uncertain}
    confirmation: null,             // {key, reservation, restaurant}
    pendingAfterLogin: null,        // selection to reopen after sign-in
    pendingLookup: null,            // reference to look up after sign-in
    authError: null,
    authNote: null,
    lookup: { reference: "", status: "idle", reservation: null, restaurant: null, error: null, busy: false },
  };

  // ---------- utilities ----------
  function loadSession() {
    try {
      const s = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
      return s && typeof s.token === "string" && s.token ? s : null;
    } catch (_) { return null; }
  }
  function saveSession(s) {
    S.session = s;
    try { s ? localStorage.setItem(SESSION_KEY, JSON.stringify(s)) : localStorage.removeItem(SESSION_KEY); } catch (_) {}
  }
  function todayISO() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function newKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  }
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "testid") el.setAttribute("data-testid", v);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  function authErrorLine(text) {
    return h("p", { testid: "auth-error", role: "alert" }, text + " ",
      h("a", { href: "/login", "data-link": true }, "Sign in"));
  }
  function notice(kind, testid, glyph, lines, actions) {
    const role = kind === "error" ? "alert" : "status";
    return h("div", { class: "notice notice-" + kind, role, testid },
      h("span", { class: "glyph", "aria-hidden": "true" }, glyph),
      h("div", null, ...lines.map((l) => h("p", null, l)), actions ? h("div", { class: "actions" }, actions) : null));
  }

  // Dates: starts_at_local is "YYYY-MM-DDTHH:MM" wall clock at the restaurant; format it without
  // re-interpreting it in the browser's own zone.
  function fmtDate(isoDate, long) {
    const [y, m, d] = isoDate.split("-").map(Number);
    if (!y || !m || !d) return isoDate;
    const opts = long ? { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }
                      : { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" };
    return new Intl.DateTimeFormat("en-GB", opts).format(new Date(Date.UTC(y, m - 1, d)));
  }
  function fmtLocal(startsAtLocal) {
    const date = startsAtLocal.slice(0, 10);
    return fmtDate(date, false) + " · " + startsAtLocal.slice(11, 16) + " (" + date + ")";
  }
  function tableOf(restaurant, id) {
    return (restaurant && (restaurant.tables || []).find((t) => t.id === id)) || null;
  }
  function tableLabel(restaurant, id) {
    const t = tableOf(restaurant, id);
    return t && t.label ? t.label : id;
  }
  // Hosts say "Table 4" for numbered tables and use a name as-is ("Window", "Booth").
  const numbered = (label) => /^\d+[A-Za-z]?$/.test(label);
  function tablesText(restaurant, ids) {
    const labels = ids.map((id) => tableLabel(restaurant, id));
    if (labels.every(numbered)) return (labels.length > 1 ? "Tables " : "Table ") + labels.join(" + ");
    return labels.map((l) => (numbered(l) ? "Table " + l : l)).join(" + ");
  }
  function seats(restaurant, ids) {
    return ids.reduce((n, id) => n + ((tableOf(restaurant, id) || {}).capacity || 0), 0);
  }
  function reservationTableIds(res) {
    if (Array.isArray(res.table_ids) && res.table_ids.length) return res.table_ids;
    return res.table_id ? [res.table_id] : [];
  }

  // ---------- HTTP ----------
  class NetworkError extends Error {}
  async function api(method, path, opts) {
    opts = opts || {};
    const headers = { Accept: "application/json" };
    if (opts.auth && S.session) headers.Authorization = "Bearer " + S.session.token;
    if (opts.key) headers["Idempotency-Key"] = opts.key;
    if (opts.bodyText !== undefined) headers["Content-Type"] = "application/json; charset=utf-8";
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), opts.timeout || READ_TIMEOUT_MS);
    let res, text;
    try {
      res = await fetch(path, { method, headers, body: opts.bodyText, signal: ctrl.signal, cache: "no-store", credentials: "omit" });
      text = await res.text();
    } catch (e) {
      throw new NetworkError(String(e && e.message || e));
    } finally {
      clearTimeout(timer);
    }
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    return { status: res.status, data };
  }
  function errorCode(r) { return r && r.data && r.data.error && r.data.error.code || ""; }

  // ---------- routing ----------
  const routes = { "/": renderSearch, "/signup": renderSignup, "/login": renderLogin, "/lookup": renderLookup };
  function navigate(path) {
    if (location.pathname !== path) history.pushState(null, "", path);
    S.authError = null;
    render();
    const main = document.getElementById("main");
    main && main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
  window.addEventListener("popstate", () => { S.authError = null; render(); });
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[data-link]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    S.authNote = null;
    navigate(a.getAttribute("href"));
  });

  function render() {
    renderAccount();
    const path = routes[location.pathname] ? location.pathname : "/";
    for (const a of document.querySelectorAll("[data-nav]")) {
      if (a.getAttribute("data-nav") === path) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    const main = document.getElementById("main");
    main.replaceChildren();
    routes[path](main);
  }

  function renderAccount() {
    const box = document.getElementById("account");
    box.replaceChildren();
    if (S.session) {
      box.append(
        h("span", { class: "who" }, "Signed in as ", h("strong", { testid: "current-user" }, S.session.display_name || "Guest")),
        h("button", { class: "btn btn-secondary btn-sm", type: "button", testid: "logout-button", onclick: logout }, "Sign out"));
    } else {
      box.append(
        h("a", { class: "btn btn-quiet btn-sm", href: "/login", "data-link": true }, "Sign in"),
        h("a", { class: "btn btn-secondary btn-sm", href: "/signup", "data-link": true }, "Create account"));
    }
  }

  function logout() {
    saveSession(null);
    S.selection = null; S.form = null; S.confirmation = null; S.pendingAfterLogin = null; S.pendingLookup = null;
    S.lookup = { reference: S.lookup.reference, status: "idle", reservation: null, restaurant: null, error: null, busy: false };
    render();
  }

  // A 401 from an authenticated call means the stored session is no longer valid.
  function sessionEnded() {
    saveSession(null);
    renderAccount();
  }

  // ---------- auth screens ----------
  function authCard(kind) {
    const isSignup = kind === "signup";
    const fields = [];
    const email = h("input", { class: "input", id: kind + "-email", type: "email", autocomplete: "email", required: true, testid: kind + "-email" });
    const password = h("input", { class: "input", id: kind + "-password", type: "password", autocomplete: isSignup ? "new-password" : "current-password", required: true, testid: kind + "-password", "aria-describedby": isSignup ? "pw-hint" : null });
    let name = null;
    if (isSignup) {
      name = h("input", { class: "input", id: "signup-display-name", type: "text", autocomplete: "name", required: true, testid: "signup-display-name" });
      fields.push(h("div", { class: "field" }, h("label", { for: "signup-display-name" }, "Your name"), name));
    }
    fields.push(h("div", { class: "field" }, h("label", { for: kind + "-email" }, "Email"), email));
    fields.push(h("div", { class: "field" }, h("label", { for: kind + "-password" }, "Password"), password,
      isSignup ? h("span", { class: "hint", id: "pw-hint" }, "At least 8 characters.") : null));

    const submit = h("button", { class: "btn btn-primary", type: "submit", testid: kind + "-submit" }, isSignup ? "Create account" : "Sign in");
    const errorSlot = h("div");
    const form = h("form", { class: "stack", novalidate: true }, ...fields, errorSlot, h("div", { class: "form-actions" }, submit));

    function showError(msg) {
      S.authError = msg;
      errorSlot.replaceChildren(msg ? notice("error", "auth-error", "!", [msg]) : "");
    }
    showError(S.authError);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (submit.getAttribute("aria-busy") === "true") return;
      submit.setAttribute("aria-busy", "true");
      submit.textContent = isSignup ? "Creating account…" : "Signing in…";
      const body = isSignup
        ? { email: email.value.trim(), password: password.value, display_name: name.value.trim() }
        : { email: email.value.trim(), password: password.value };
      let r;
      try {
        r = await api("POST", isSignup ? "/auth/signup" : "/auth/login", { bodyText: JSON.stringify(body) });
      } catch (_) {
        r = null;
      }
      submit.removeAttribute("aria-busy");
      submit.textContent = isSignup ? "Create account" : "Sign in";
      if (r && (r.status === 200 || r.status === 201) && r.data && r.data.token) {
        saveSession({ token: r.data.token, user_id: r.data.user_id, display_name: r.data.display_name });
        S.authError = null;
        S.authNote = null;
        afterSignIn();
        return;
      }
      showError(authMessage(r, isSignup));
    });

    const other = isSignup
      ? h("p", { class: "lede" }, "Already have an account? ", h("a", { href: "/login", "data-link": true }, "Sign in"))
      : h("p", { class: "lede" }, "New here? ", h("a", { href: "/signup", "data-link": true }, "Create an account"));
    return { form, other };
  }

  function authMessage(r, isSignup) {
    if (!r) return "We couldn't reach Tablekeeper. Check your connection and try again.";
    const code = errorCode(r);
    if (code === "email_taken") return "An account with this email already exists. Sign in instead.";
    if (code === "unauthenticated") return "That email and password don't match an account.";
    if (code === "validation_failed" || code === "malformed_request") {
      return isSignup ? "Please enter your name, a valid email address and a password of at least 8 characters."
                      : "Please enter your email address and password.";
    }
    return "Something went wrong (" + (code || r.status) + "). Please try again.";
  }

  function afterSignIn() {
    const pending = S.pendingAfterLogin;
    const lookupRef = S.pendingLookup;
    S.pendingAfterLogin = null;
    S.pendingLookup = null;
    if (pending) {
      openSelection(pending);
      navigate("/");
      scrollToPanel();
      return;
    }
    if (lookupRef !== null) {
      navigate("/lookup");
      runLookup();
      return;
    }
    navigate("/");
  }

  function renderSignup(main) {
    document.title = "Create account · Tablekeeper";
    const { form, other } = authCard("signup");
    main.append(h("section", { class: "card narrow stack", "aria-labelledby": "signup-title" },
      h("div", null, h("p", { class: "eyebrow" }, "Welcome"), h("h1", { id: "signup-title" }, "Create your account")),
      S.authNote ? notice("info", null, "i", [S.authNote]) : null,
      form, other));
  }

  function renderLogin(main) {
    document.title = "Sign in · Tablekeeper";
    const { form, other } = authCard("login");
    main.append(h("section", { class: "card narrow stack", "aria-labelledby": "login-title" },
      h("div", null, h("p", { class: "eyebrow" }, "Welcome back"), h("h1", { id: "login-title" }, "Sign in")),
      S.authNote ? notice("info", null, "i", [S.authNote]) : null,
      form, other));
  }

  // ---------- search ----------
  function loadRestaurants() {
    S.restaurantsError = false;
    api("GET", "/restaurants").then((r) => {
      if (r.status === 200 && r.data && Array.isArray(r.data.restaurants)) {
        S.restaurants = r.data.restaurants;
        if (!S.inputs.restaurantId && S.restaurants.length) S.inputs.restaurantId = S.restaurants[0].id;
      } else {
        S.restaurantsError = true;
      }
    }, () => { S.restaurantsError = true; }).then(() => {
      if (location.pathname === "/" || !routes[location.pathname]) refreshSearchCard();
    });
  }

  let searchRefs = null; // live elements of the search screen

  function renderSearch(main) {
    document.title = "Find a table · Tablekeeper";
    const select = h("select", { class: "select", id: "restaurant", testid: "restaurant-select" });
    const date = h("input", { class: "input", id: "date", type: "date", testid: "date-input", value: S.inputs.date });
    const party = h("input", { class: "input", id: "party", type: "number", min: "1", step: "1", inputmode: "numeric", testid: "party-size-input", value: S.inputs.party });
    const button = h("button", { class: "btn btn-primary", type: "submit", testid: "search-button" }, "Search");
    select.addEventListener("change", () => { S.inputs.restaurantId = select.value; });
    date.addEventListener("input", () => { S.inputs.date = date.value; });
    date.addEventListener("change", () => { S.inputs.date = date.value; });
    party.addEventListener("input", () => { S.inputs.party = party.value; });

    const form = h("form", { class: "card search-card", role: "search", "aria-label": "Find a table" },
      h("div", { class: "field" }, h("label", { for: "restaurant" }, "Restaurant"), select),
      h("div", { class: "field" }, h("label", { for: "date" }, "Date"), date),
      h("div", { class: "field" }, h("label", { for: "party" }, "Party size"), party),
      button);
    form.addEventListener("submit", (e) => { e.preventDefault(); startSearch(false); });

    const results = h("section", { class: "results", "aria-live": "polite", "aria-label": "Availability" });
    const side = h("aside", { class: "side", "aria-label": "Your booking" });
    const layout = h("div", { class: "layout" }, results, side);

    main.append(
      h("div", { class: "page-head" }, h("p", { class: "eyebrow" }, "Reserve"), h("h1", null, "Find a table"),
        h("p", { class: "lede" }, "Pick a restaurant, a date and how many of you are coming.")),
      form, layout);
    searchRefs = { select, date, party, button, results, side, layout };
    refreshSearchCard();
    renderResults();
    renderPanel();
  }

  function onSearchScreen() { return searchRefs && document.body.contains(searchRefs.results); }

  function refreshSearchCard() {
    if (!onSearchScreen()) return;
    const { select } = searchRefs;
    select.replaceChildren();
    if (S.restaurants === null && !S.restaurantsError) {
      select.append(h("option", { value: "" }, "Loading restaurants…"));
      return;
    }
    if (S.restaurantsError) {
      select.append(h("option", { value: "" }, "Couldn't load restaurants"));
      return;
    }
    if (!S.restaurants.length) select.append(h("option", { value: "" }, "No restaurants yet"));
    for (const r of S.restaurants) {
      const opt = h("option", { value: r.id }, r.name || r.id);
      if (r.id === S.inputs.restaurantId) opt.selected = true;
      select.append(opt);
    }
  }

  // startSearch runs a search. Every search gets a sequence number; only the newest may render.
  // refresh=true re-runs the current results' parameters and keeps the open booking form (409 recovery).
  async function startSearch(refresh) {
    let params;
    if (refresh && S.results) {
      params = S.results.params;
    } else {
      if (onSearchScreen()) {
        S.inputs.restaurantId = searchRefs.select.value;
        S.inputs.date = searchRefs.date.value;
        S.inputs.party = searchRefs.party.value;
      }
      params = { restaurantId: S.inputs.restaurantId, date: S.inputs.date, party: String(S.inputs.party).trim() };
      // A new search describes a new choice: the old selection and its form no longer apply.
      S.selection = null; S.form = null; S.confirmation = null;
    }
    const seq = ++S.searchSeq;
    S.results = { status: "loading", params, restaurant: refresh && S.results ? S.results.restaurant : null, slots: [] };
    renderResults();
    if (!refresh) renderPanel(); // a refresh keeps the open form exactly as the diner left it

    let outcome;
    try {
      const q = "restaurant_id=" + encodeURIComponent(params.restaurantId) + "&date=" + encodeURIComponent(params.date) +
        "&party_size=" + encodeURIComponent(params.party);
      const [detail, avail] = await Promise.all([
        api("GET", "/restaurants/" + encodeURIComponent(params.restaurantId)),
        api("GET", "/availability?" + q),
      ]);
      if (avail.status === 200 && detail.status === 200 && avail.data && Array.isArray(avail.data.slots)) {
        outcome = { status: "ok", params, restaurant: detail.data, slots: avail.data.slots };
      } else {
        outcome = { status: "error", params, restaurant: null, slots: [], message: searchMessage(avail.status === 200 ? detail : avail) };
      }
    } catch (_) {
      outcome = { status: "error", params, restaurant: null, slots: [], message: "We couldn't reach Tablekeeper. Check your connection and try again." };
    }
    if (seq !== S.searchSeq) return; // a newer search started: this response is stale
    S.results = outcome;
    if (S.selection && outcome.status === "ok") S.selection.restaurant = outcome.restaurant;
    renderResults();
    if (!refresh) renderPanel();
  }

  function searchMessage(r) {
    const code = errorCode(r);
    if (code === "not_found") return "We couldn't find that restaurant.";
    if (code === "validation_failed") return "Choose a restaurant, a valid date and a party size of at least 1.";
    return "We couldn't load availability (" + (code || r.status) + ").";
  }

  function renderResults() {
    if (!onSearchScreen()) return;
    const box = searchRefs.results;
    box.replaceChildren();
    box.removeAttribute("aria-busy");
    const R = S.results;
    if (!R) {
      box.append(h("div", { class: "card prompt" }, h("span", { class: "glyph", "aria-hidden": "true" }, "T"),
        h("p", null, "Choose a restaurant, date and party size to see open tables.")));
      return;
    }
    if (R.status === "loading") {
      box.setAttribute("aria-busy", "true");
      const rows = [0, 1, 2].map(() => h("div", { class: "slot-row" }, h("div", { class: "bar short" }),
        h("div", { class: "bars" }, h("div", { class: "bar" }), h("div", { class: "bar" }), h("div", { class: "bar" }))));
      box.append(h("div", { class: "card skeleton", "aria-label": "Loading availability" }, h("p", { class: "sr-only" }, "Loading availability…"), h("div", null, ...rows)));
      return;
    }
    if (R.status === "error") {
      box.append(h("div", { class: "card" }, notice("error", null, "!", ["Couldn't load availability.", R.message],
        h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => startSearch(true) }, "Try again"))));
      return;
    }
    const rest = R.restaurant;
    const head = h("div", { class: "results-head" },
      h("div", null, h("h2", null, rest.name || rest.id),
        h("p", { class: "lede" }, fmtDate(R.params.date, true) + " · party of " + R.params.party)),
      h("ul", { class: "legend", "aria-label": "Legend" },
        h("li", null, h("span", { class: "swatch ok", "aria-hidden": "true" }), "Available"),
        h("li", null, h("span", { class: "swatch no", "aria-hidden": "true" }), "Taken or too small"),
        h("li", null, h("span", { class: "swatch sel", "aria-hidden": "true" }), "Your choice")));
    if (!R.slots.length) {
      box.append(h("div", { class: "card" }, head, h("div", { class: "empty", testid: "no-slots" },
        h("div", { class: "moon", "aria-hidden": "true" }),
        h("h3", null, "No tables on this day"),
        h("p", null, "The restaurant is closed or has no bookable times — try another date."))));
      return;
    }
    const partyNum = Number(R.params.party) || 0;
    const pairs = declaredPairs(rest, R.slots).filter((p) => seats(rest, p) >= partyNum);
    const list = h("ol", { class: "slots" });
    for (const slot of R.slots) {
      const hhmm = slot.starts_at_local.slice(11, 16);
      const free = new Set(slot.available_table_ids || []);
      const singles = h("div", { class: "cells" });
      for (const t of rest.tables || []) {
        singles.append(cell(R, slot, hhmm, [t.id], free.has(t.id), false));
      }
      const groups = [h("div", { class: "cell-group" }, singles)];
      if (pairs.length) {
        const combos = h("div", { class: "cells combo-cells" });
        for (const p of pairs) combos.append(cell(R, slot, hhmm, p, optionAvailable(slot, p), true));
        groups.push(h("div", { class: "cell-group" }, h("span", { class: "group-label" }, "Combined tables"), combos));
      }
      list.append(h("li", { class: "slot-row" },
        h("div", { class: "slot-time" }, h("time", { datetime: slot.starts_at }, hhmm)),
        h("div", null, ...groups)));
    }
    box.append(h("div", { class: "card" }, head, h("div", { testid: "availability-grid" }, list)));
  }

  // The restaurant's declared pairs, in combinable order; falls back to the pairs availability reports.
  function declaredPairs(rest, slots) {
    if (Array.isArray(rest.combinable)) return rest.combinable.filter((p) => Array.isArray(p) && p.length === 2);
    const seen = new Map();
    for (const s of slots) for (const o of s.available_options || []) {
      if (o.table_ids && o.table_ids.length === 2) seen.set(o.table_ids.join("+"), o.table_ids);
    }
    return [...seen.values()];
  }
  function optionAvailable(slot, ids) {
    const want = [...ids].sort().join("+");
    return (slot.available_options || []).some((o) => Array.isArray(o.table_ids) && [...o.table_ids].sort().join("+") === want);
  }

  function cell(R, slot, hhmm, ids, available, combo) {
    const rest = R.restaurant;
    const cap = seats(rest, ids);
    const party = Number(R.params.party) || 0;
    const selected = S.selection && S.selection.startsAtLocal === slot.starts_at_local &&
      S.selection.tableIds.join("+") === ids.join("+") && S.selection.params.restaurantId === R.params.restaurantId;
    // "Taken" covers a booking and a closure alike: the API does not say which.
    const sub = available ? cap + " seats" : (cap < party ? "Seats " + cap : "Taken");
    const name = tablesText(rest, ids);
    const btn = h("button", {
      type: "button",
      class: "cell" + (combo ? " combo" : ""),
      testid: "slot-" + ids.join("+") + "-" + hhmm,
      "data-available": available ? "true" : "false",
      "aria-pressed": available ? (selected ? "true" : "false") : null,
      "aria-label": hhmm + ", " + name + ", " + cap + " seats, " + (available ? (selected ? "selected" : "available") : "not available"),
    }, h("span", { class: "cell-name" }, name), h("span", { class: "cell-sub" }, sub));
    if (available) {
      btn.addEventListener("click", () => chooseCell({ params: R.params, restaurant: rest, tableIds: ids.slice(), hhmm, startsAtLocal: slot.starts_at_local }));
    }
    return btn;
  }

  function chooseCell(sel) {
    if (!S.session) {
      S.pendingAfterLogin = sel;
      S.authNote = "Sign in to book " + tablesText(sel.restaurant, sel.tableIds) + " at " + sel.hhmm + ".";
      navigate("/login");
      return;
    }
    openSelection(sel);
    renderResults();
    renderPanel();
    scrollToPanel();
  }

  function openSelection(sel) {
    S.selection = sel;
    S.form = { party: String(sel.params.party), key: null, bodyText: null, inFlight: false, error: null, uncertain: null };
    S.confirmation = null;
  }

  function scrollToPanel() {
    requestAnimationFrame(() => {
      const form = document.querySelector('[data-testid="booking-form"]');
      if (form && window.innerWidth < 1024) form.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }

  // ---------- booking panel ----------
  let panelRefs = null;

  function renderPanel() {
    if (!onSearchScreen()) return;
    const side = searchRefs.side;
    searchRefs.layout.classList.toggle("has-panel", !!S.selection);
    side.replaceChildren();
    panelRefs = null;
    if (!S.selection) return;
    const sel = S.selection;
    const rest = sel.restaurant;
    const party = h("input", { class: "input", id: "booking-party", type: "number", min: "1", step: "1", inputmode: "numeric", testid: "booking-party-size", value: S.form.party });
    party.addEventListener("input", () => { S.form.party = party.value; formChanged(); });
    const submit = h("button", { class: "btn btn-primary", type: "submit", testid: "booking-submit" }, "Book");
    const status = h("div", { class: "booking-status" });
    const form = h("form", { class: "card panel-card stack", testid: "booking-form", "aria-labelledby": "booking-title", novalidate: true },
      h("div", null, h("p", { class: "eyebrow" }, rest.name || rest.id), h("h2", { id: "booking-title" }, "Complete your booking")),
      h("div", { class: "summary", testid: "booking-summary" },
        h("div", { class: "what" }, tablesText(rest, sel.tableIds)),
        h("div", { class: "when" }, fmtLocal(sel.startsAtLocal) + " · " + (rest.name || rest.id))),
      h("div", { class: "field" }, h("label", { for: "booking-party" }, "Party size"), party,
        h("span", { class: "hint" }, "Seats up to " + seats(rest, sel.tableIds) + ".")),
      status,
      h("div", { class: "form-actions" }, submit,
        h("button", { class: "btn btn-quiet", type: "button", onclick: closeForm }, "Close")));
    form.addEventListener("submit", (e) => { e.preventDefault(); submitBooking(); });
    const confirmSlot = h("div");
    side.append(form, confirmSlot);
    panelRefs = { form, party, submit, status, confirmSlot };
    renderBookingStatus();
  }

  function closeForm() {
    S.selection = null; S.form = null; S.confirmation = null;
    renderResults();
    renderPanel();
  }

  // Any edit makes the next submission a new booking request with a new key.
  function formChanged() {
    if (!S.form) return;
    S.form.key = null;
    S.form.bodyText = null;
  }

  function renderBookingStatus() {
    if (!panelRefs) return;
    const f = S.form;
    const { status, submit, confirmSlot } = panelRefs;
    status.replaceChildren();
    if (f.error) {
      const panel = notice("error", "booking-error", "!", f.error);
      if (f.authError) panel.querySelector("div").append(authErrorLine(f.authError));
      status.append(panel);
    }
    if (f.uncertain) {
      status.append(notice("warn", "booking-uncertain", "?", [
        "We couldn't confirm your booking — the connection dropped before Tablekeeper answered.",
        "Press “Try again” to check. You won't be booked twice.",
      ]));
    }
    if (f.inFlight) {
      submit.setAttribute("aria-busy", "true");
      submit.textContent = "Booking…";
    } else {
      submit.removeAttribute("aria-busy");
      submit.textContent = f.uncertain ? "Try again" : "Book";
    }
    confirmSlot.replaceChildren();
    if (S.confirmation) confirmSlot.append(confirmationCard(S.confirmation));
  }

  function bookingBody() {
    const sel = S.selection;
    const raw = String(S.form.party).trim();
    const body = { restaurant_id: sel.params.restaurantId };
    if (sel.tableIds.length === 1) body.table_id = sel.tableIds[0];
    else body.table_ids = sel.tableIds.slice();
    body.starts_at_local = sel.startsAtLocal;
    body.party_size = /^\d+$/.test(raw) ? Number(raw) : raw;
    return body;
  }

  async function submitBooking() {
    const f = S.form;
    if (!f || f.inFlight) return;
    if (!S.session) {
      S.pendingAfterLogin = S.selection;
      S.authNote = "Sign in to finish your booking.";
      navigate("/login");
      return;
    }
    // Unchanged form: same key and byte-identical body (a replay). Changed form: a new request.
    if (!f.key) {
      f.key = newKey();
      f.bodyText = JSON.stringify(bookingBody());
    }
    const key = f.key, bodyText = f.bodyText;
    if (S.confirmation && S.confirmation.key !== key) S.confirmation = null;
    f.inFlight = true;
    renderBookingStatus();

    let r = null;
    try {
      r = await api("POST", "/reservations", { auth: true, key, bodyText, timeout: BOOKING_TIMEOUT_MS });
    } catch (_) {
      r = null;
    }
    if (S.form !== f) return; // the form was closed or replaced meanwhile
    f.inFlight = false;

    if (r && (r.status === 200 || r.status === 201) && r.data && r.data.reference) {
      f.error = null; f.uncertain = null; f.authError = null;
      S.confirmation = { key, reservation: r.data, restaurant: S.selection.restaurant };
      renderBookingStatus();
      const ref = document.querySelector('[data-testid="confirmation"]');
      if (ref && window.innerWidth < 1024) ref.scrollIntoView({ block: "nearest", behavior: "smooth" });
      return;
    }
    if (!r || r.status >= 500) {
      // Outcome unknown: the booking may have committed. Keep key and body for a safe retry.
      f.error = null; f.authError = null;
      f.uncertain = true;
      if (S.confirmation && S.confirmation.key !== key) S.confirmation = null;
      renderBookingStatus();
      return;
    }
    // A definite refusal.
    f.uncertain = null;
    const code = errorCode(r);
    f.authError = null;
    if (code === "unauthenticated" || r.status === 401) {
      sessionEnded();
      S.pendingAfterLogin = S.selection;
      f.error = ["Your booking wasn't made."];
      f.authError = "Your session has ended. Sign in again to book this table.";
    } else {
      f.error = bookingMessage(code, r);
    }
    if (S.confirmation && S.confirmation.key !== key) S.confirmation = null;
    renderBookingStatus();
    if (code === "table_unavailable") startSearch(true);
  }

  function bookingMessage(code, r) {
    switch (code) {
      case "table_unavailable": return ["Someone just took that table.", "Availability has been refreshed — pick another time or table, then book again."];
      case "party_exceeds_capacity": return ["That party is too large for this table. Choose a smaller party or a bigger table."];
      case "validation_failed": return ["Enter a party size of at least 1."];
      case "combination_not_allowed": return ["These tables can't be combined. Choose another option."];
      case "outside_opening_hours": return ["The restaurant isn't open for a booking at that time."];
      case "not_on_slot_grid":
      case "invalid_local_time": return ["That start time isn't bookable. Choose a time from the grid."];
      case "not_found": return ["That restaurant or table no longer exists. Search again."];
      case "idempotency_key_reuse": return ["This request clashed with an earlier one. Change a detail and book again."];
      default: return ["We couldn't book that table (" + (code || r.status) + ")."];
    }
  }

  function confirmationCard(c) {
    const res = c.reservation, rest = c.restaurant;
    const ids = reservationTableIds(res);
    const tables = tablesText(rest, ids);
    return h("section", { class: "card confirm", testid: "confirmation", role: "status", "aria-labelledby": "confirm-title" },
      h("div", { class: "confirm-head" }, h("span", { class: "tick", "aria-hidden": "true" }, "✓"),
        h("div", null, h("h2", { id: "confirm-title" }, "You're booked"),
          h("p", { class: "lede" }, "Keep this reference to look up or cancel your booking."))),
      h("div", { class: "reference-box" }, h("span", { class: "eyebrow" }, "Reference"),
        h("span", { class: "reference", testid: "confirmation-reference" }, res.reference)),
      h("dl", { class: "details", testid: "confirmation-details" },
        h("dt", null, "Restaurant"), h("dd", null, rest.name || res.restaurant_id),
        h("dt", null, "When"), h("dd", null, fmtLocal(res.starts_at_local)),
        h("dt", null, "Tables"), h("dd", { testid: "confirmation-tables" }, tables),
        h("dt", null, "Party"), h("dd", null, String(res.party_size))));
  }

  // ---------- lookup ----------
  let lookupRefs = null;

  function renderLookup(main) {
    document.title = "Look up a booking · Tablekeeper";
    const input = h("input", { class: "input", id: "lookup-ref", type: "text", autocomplete: "off", spellcheck: "false", testid: "lookup-reference-input", value: S.lookup.reference });
    input.addEventListener("input", () => { S.lookup.reference = input.value; });
    const submit = h("button", { class: "btn btn-primary", type: "submit", testid: "lookup-submit" }, "Look up");
    const form = h("form", { class: "card stack", "aria-label": "Look up a booking" },
      h("div", { class: "lookup-row" },
        h("div", { class: "field" }, h("label", { for: "lookup-ref" }, "Booking reference"), input,
          h("span", { class: "hint" }, "The code on your confirmation, e.g. K3P7QW.")),
        submit));
    form.addEventListener("submit", (e) => { e.preventDefault(); runLookup(); });
    const out = h("div", { class: "lookup-out" });
    main.append(h("div", { class: "narrow", style: "max-width: 640px" },
      h("div", { class: "page-head" }, h("p", { class: "eyebrow" }, "Manage"), h("h1", null, "Look up a booking"),
        h("p", { class: "lede" }, "Check the details of a booking or cancel it.")),
      form, out));
    lookupRefs = { input, submit, out };
    renderLookupResult();
  }

  async function runLookup() {
    const L = S.lookup;
    if (L.busy) return;
    const ref = String(lookupRefs ? lookupRefs.input.value : L.reference).trim();
    L.reference = ref;
    L.error = null; L.reservation = null; L.restaurant = null;
    if (!ref) { L.error = "Enter the reference from your confirmation."; renderLookupResult(); return; }
    if (!S.session) {
      S.pendingLookup = ref;
      S.authNote = "Sign in to look up booking " + ref + ".";
      navigate("/login");
      return;
    }
    L.needsLogin = false;
    L.busy = true; L.status = "loading";
    renderLookupResult();
    let r = null, detail = null;
    try {
      r = await api("GET", "/reservations/" + encodeURIComponent(ref), { auth: true });
      if (r.status === 200 && r.data) detail = await api("GET", "/restaurants/" + encodeURIComponent(r.data.restaurant_id));
    } catch (_) { r = null; }
    L.busy = false;
    if (S.lookup !== L) return;
    if (r && r.status === 200 && r.data) {
      L.status = "found"; L.reservation = r.data;
      L.restaurant = detail && detail.status === 200 ? detail.data : { id: r.data.restaurant_id, name: r.data.restaurant_id, tables: [] };
    } else if (r && r.status === 401) {
      sessionEnded(); L.status = "idle"; L.error = "We couldn't look up that booking."; L.needsLogin = true;
    } else if (r && r.status === 404) {
      L.status = "idle"; L.error = "We couldn't find a booking with reference “" + ref + "” on your account.";
    } else {
      L.status = "idle"; L.error = r ? "We couldn't look up that booking (" + (errorCode(r) || r.status) + ")." : "We couldn't reach Tablekeeper. Check your connection and try again.";
    }
    renderLookupResult();
  }

  async function cancelBooking() {
    const L = S.lookup;
    if (L.busy || !L.reservation) return;
    L.busy = true; L.error = null; L.needsLogin = false;
    renderLookupResult();
    let r = null;
    try {
      r = await api("POST", "/reservations/" + encodeURIComponent(L.reservation.reference) + "/cancel", { auth: true, bodyText: "{}" });
    } catch (_) { r = null; }
    L.busy = false;
    if (S.lookup !== L) return;
    if (r && r.status === 200 && r.data) {
      L.reservation = Object.assign({}, L.reservation, r.data);
    } else if (r && errorCode(r) === "cutoff_passed") {
      L.error = "This booking is inside the restaurant's cancellation window, so it can't be cancelled online. Please call the restaurant.";
    } else if (r && r.status === 401) {
      sessionEnded(); L.error = "Your booking wasn't cancelled."; L.needsLogin = true;
    } else if (r && r.status === 404) {
      L.error = "We couldn't find that booking on your account any more.";
    } else {
      // Cancelling is safe to repeat, so an unknown outcome is simply retried by the diner.
      L.error = r ? "We couldn't cancel this booking (" + (errorCode(r) || r.status) + ")." : "We couldn't reach Tablekeeper — your booking may not be cancelled yet. Press Cancel booking again to check.";
    }
    renderLookupResult();
  }

  function renderLookupResult() {
    if (!lookupRefs || !document.body.contains(lookupRefs.out)) return;
    const L = S.lookup;
    const { out, submit } = lookupRefs;
    out.replaceChildren();
    if (L.status === "loading") { submit.setAttribute("aria-busy", "true"); submit.textContent = "Looking up…"; }
    else { submit.removeAttribute("aria-busy"); submit.textContent = "Look up"; }
    if (L.error) {
      const panel = notice("error", "reservation-error", "!", [L.error]);
      if (L.needsLogin) {
        panel.querySelector("div").append(authErrorLine("Your session has ended."));
        S.pendingLookup = L.reference;
      }
      out.append(h("div", { class: "card" }, panel));
    }
    if (L.status === "loading") {
      out.append(h("div", { class: "card skeleton", "aria-busy": "true" }, h("p", { class: "sr-only" }, "Looking up your booking…"),
        h("div", { class: "bar" }), h("div", { class: "bars", style: "margin-top:12px" }, h("div", { class: "bar short" }), h("div", { class: "bar short" }))));
      return;
    }
    if (L.status !== "found" || !L.reservation) return;
    const res = L.reservation, rest = L.restaurant;
    const cancelled = res.status === "cancelled";
    const card = h("section", { class: "card stack", testid: "reservation-detail", "aria-labelledby": "res-title" },
      h("div", { class: "detail-head" },
        h("div", null, h("p", { class: "eyebrow" }, "Booking " + res.reference), h("h2", { id: "res-title" }, rest.name || rest.id)),
        h("span", { class: "status-pill " + (cancelled ? "cancelled" : "confirmed") },
          h("span", { "aria-hidden": "true" }, cancelled ? "○" : "●"),
          h("span", { class: "sr-only" }, "Status: "),
          h("span", { testid: "reservation-status" }, res.status))),
      h("dl", { class: "details" },
        h("dt", null, "When"), h("dd", null, fmtLocal(res.starts_at_local)),
        h("dt", null, "Tables"), h("dd", { testid: "reservation-tables" }, tablesText(rest, reservationTableIds(res))),
        h("dt", null, "Party"), h("dd", null, String(res.party_size))),
      cancelled ? null : h("div", { class: "form-actions" },
        h("button", { class: "btn btn-danger", type: "button", testid: "reservation-cancel-button", "aria-busy": L.busy ? "true" : null, onclick: cancelBooking },
          L.busy ? "Cancelling…" : "Cancel booking")));
    out.append(card);
  }

  // ---------- boot ----------
  function boot() {
    loadRestaurants();
    render();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
