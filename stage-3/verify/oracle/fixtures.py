"""Shared fixtures and dates for the Oracle suites (stage 2: restaurants carry `combinable` pairs).

Dates: FUT_* are far enough ahead that no cutoff can pass during a run; PAST_* are past, so every
cancel/amend hits 409 cutoff_passed. DST dates are the four transitions named in §9.
"""
from __future__ import annotations

import copy

ADA = {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}
BOB = {"id": "u_bob", "email": "bob@example.com", "password": "bob secret 1", "display_name": "Bob"}

ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Thursdays
FUT_THU = "2027-09-23"
FUT_FRI = "2027-09-24"
FUT_WED = "2027-09-22"
PAST_THU = "2020-09-24"
FUT_DAY = "2027-06-15"      # a Tuesday; r_all is open every day
FUT_DAY2 = "2027-06-16"
PAST_DAY = "2020-06-15"

BERLIN_SPRING = "2026-03-29"   # 02:00 -> 03:00 (Sunday)
BERLIN_FALL = "2026-10-25"     # 03:00 -> 02:00 (Sunday)
NY_SPRING = "2026-03-08"       # 02:00 -> 03:00 (Sunday)
NY_FALL = "2026-11-01"         # 02:00 -> 01:00 (Sunday)


def anker() -> dict:
    return {
        "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
        "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
        "opening_hours": [
            {"weekday": "thu", "opens": "18:00", "closes": "23:00"},
            {"weekday": "fri", "opens": "18:00", "closes": "23:30"},
            {"weekday": "sun", "opens": "00:00", "closes": "06:00"},
        ],
        "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4}],
        "combinable": [["t_1", "t_2"]],
    }


def trio() -> dict:
    """Three tables, two declared pairs sharing q_2 (non-transitive); open every day 18:00-23:00."""
    return {
        "id": "r_trio", "name": "Trio", "timezone": "Europe/Berlin",
        "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
        "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in ALL_DAYS],
        "tables": [{"id": "q_1", "label": "Window", "capacity": 2}, {"id": "q_2", "label": "Centre", "capacity": 4},
                   {"id": "q_3", "label": "Garden", "capacity": 4}],
        "combinable": [["q_1", "q_2"], ["q_2", "q_3"]],
    }


def r_all() -> dict:
    return {
        "id": "r_all", "name": "All Week", "timezone": "Europe/Berlin",
        "slot_minutes": 15, "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 60,
        "opening_hours": [{"weekday": d, "opens": "10:00", "closes": "22:00"} for d in ALL_DAYS],
        "tables": [{"id": "a_1", "label": "A1", "capacity": 2}, {"id": "a_2", "label": "A2", "capacity": 4},
                   {"id": "a_3", "label": "A3", "capacity": 6}],
        "combinable": [["a_1", "a_2"]],
    }


def ny() -> dict:
    return {
        "id": "r_ny", "name": "Hudson", "timezone": "America/New_York",
        "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 60,
        "opening_hours": [{"weekday": "sun", "opens": "00:00", "closes": "06:00"}]
        + [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in ALL_DAYS if d != "sun"],
        "tables": [{"id": "n_1", "label": "N1", "capacity": 4}],
        "combinable": [],
    }


def base_fixture() -> dict:
    return {
        "users": [copy.deepcopy(ADA), copy.deepcopy(BOB)],
        "restaurants": [anker(), r_all(), ny(), trio()],
        "reservations": [
            {"id": "res_seed", "reference": "SEED01", "user_id": "u_bob", "restaurant_id": "r_anker",
             "table_id": "t_2", "starts_at_local": f"{FUT_THU}T18:00", "party_size": 4},
        ],
    }


def other_fixture() -> dict:
    """A different fixture, used to prove that reset/import replace rather than merge."""
    return {
        "users": [{"id": "u_zed", "email": "zed@example.com", "password": "zed password", "display_name": "Zed"}],
        "restaurants": [{
            "id": "r_other", "name": "Other", "timezone": "Europe/Berlin",
            "slot_minutes": 30, "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 30,
            "opening_hours": [{"weekday": d, "opens": "12:00", "closes": "20:00"} for d in ALL_DAYS],
            "tables": [{"id": "o_1", "label": "O1", "capacity": 8}],
        }],
        "reservations": [],
    }


def stage1_fixture() -> dict:
    """The stage-1 shape (no `combinable`, single `table_id` seeds): reset must still accept it."""
    fx = base_fixture()
    for r in fx["restaurants"]:
        r.pop("combinable", None)
    return fx
