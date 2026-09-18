"""
GridWise Section 3 - hidden-like / adversarial local tests.

Put this file beside gridwise_section_03_validator.py and run:
    python test_section_03_hidden_like.py

These are NOT the official hidden tests. They stress the machine-checkable
rules in the Problem Statement and attack common failure modes.
"""

from copy import deepcopy
from math import inf, nan

from gridwise_section_03_validator import (
    PlanValidationError,
    validate_and_build_response,
)

PASS = 0
FAIL = 0


def expect_pass(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"PASS: {name}")
    except Exception as e:
        FAIL += 1
        print(f"FAIL: {name} -> {type(e).__name__}: {e}")


def expect_reject(name, fn):
    global PASS, FAIL
    try:
        fn()
        FAIL += 1
        print(f"FAIL: {name} -> validator ACCEPTED an invalid case")
    except PlanValidationError:
        PASS += 1
        print(f"PASS: {name} rejected")
    except Exception as e:
        FAIL += 1
        print(f"FAIL: {name} -> unexpected {type(e).__name__}: {e}")


def scenario(notes=None):
    if notes is None:
        notes = ["The cafeteria menu changes tomorrow."]

    return {
        "scenario_id": "HIDDEN-LIKE-01",
        "operator_notes": notes,
        "hours": [
            {
                "hour": h,
                "demand_kwh": 10.0,
                "solar_kwh": 0.0,
                "tariff_bdt_per_kwh": 5.0 + (h % 4),
            }
            for h in range(24)
        ],
        "battery": {
            "capacity_kwh": 20.0,
            "initial_energy_kwh": 10.0,
            "minimum_energy_kwh": 2.0,
            "max_charge_kwh_per_hour": 5.0,
            "max_discharge_kwh_per_hour": 5.0,
        },
    }


def noop(note_index=0, explanation="Irrelevant to today's energy schedule."):
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": explanation,
    }


def directive(note_index, dtype, adjustment, explanation="Applicable directive."):
    return {
        "note_index": note_index,
        "applies": True,
        "directive_type": dtype,
        "structured_adjustment": adjustment,
        "explanation": explanation,
    }


def valid_result(sc, directives=None):
    if directives is None:
        directives = [noop(i) for i in range(len(sc["operator_notes"]))]

    plan = []
    for h in range(24):
        plan.append({
            "hour": h,
            "grid_kwh": 10.0,
            "solar_used_kwh": 0.0,
            "battery_action": "idle",
            "battery_kwh": 0.0,
            "battery_energy_after_kwh": 10.0,
        })

    total_grid = 240.0
    total_cost = sum(
        10.0 * hour["tariff_bdt_per_kwh"]
        for hour in sc["hours"]
    )

    return {
        "scenario_id": sc["scenario_id"],
        "directive_interpretation": directives,
        "hourly_plan": plan,
        "total_grid_kwh": total_grid,
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": 10.0,
        "plan_summary": "Valid hidden-like test plan.",
    }


def run(res, sc):
    validate_and_build_response(res, sc)


# ---------------------------------------------------------------------------
# 1) Basic contract
# ---------------------------------------------------------------------------

sc = scenario()
res = valid_result(sc)

expect_pass("baseline valid 24-hour plan", lambda: run(res, sc))

def duplicate_hour():
    x = deepcopy(res)
    x["hourly_plan"][23]["hour"] = 22
    run(x, sc)
expect_reject("duplicate/missing plan hour", duplicate_hour)

def wrong_order():
    x = deepcopy(res)
    x["hourly_plan"][0], x["hourly_plan"][1] = x["hourly_plan"][1], x["hourly_plan"][0]
    run(x, sc)
expect_reject("plan hours not ascending", wrong_order)

def bad_hour_type():
    x = deepcopy(res)
    x["hourly_plan"][4]["hour"] = "4"
    run(x, sc)
expect_reject("hour string instead of integer", bad_hour_type)

def missing_hourly_field():
    x = deepcopy(res)
    del x["hourly_plan"][5]["grid_kwh"]
    run(x, sc)
expect_reject("missing required hourly field", missing_hourly_field)

def extra_hourly_field():
    x = deepcopy(res)
    x["hourly_plan"][5]["debug"] = "bad"
    run(x, sc)
expect_reject("extra hourly field", extra_hourly_field)

# ---------------------------------------------------------------------------
# 2) Numeric safety
# ---------------------------------------------------------------------------

def negative_grid():
    x = deepcopy(res)
    x["hourly_plan"][2]["grid_kwh"] = -1
    run(x, sc)
expect_reject("negative grid import", negative_grid)

def nan_grid():
    x = deepcopy(res)
    x["hourly_plan"][2]["grid_kwh"] = nan
    run(x, sc)
expect_reject("NaN grid import", nan_grid)

def inf_grid():
    x = deepcopy(res)
    x["hourly_plan"][2]["grid_kwh"] = inf
    run(x, sc)
expect_reject("infinite grid import", inf_grid)

def negative_solar():
    x = deepcopy(res)
    x["hourly_plan"][2]["solar_used_kwh"] = -0.5
    run(x, sc)
expect_reject("negative solar usage", negative_solar)

# ---------------------------------------------------------------------------
# 3) Battery action/state consistency
# ---------------------------------------------------------------------------

def idle_nonzero():
    x = deepcopy(res)
    x["hourly_plan"][3]["battery_kwh"] = 1
    run(x, sc)
expect_reject("idle with non-zero battery_kwh", idle_nonzero)

def charge_transition_wrong():
    x = deepcopy(res)
    x["hourly_plan"][3].update({
        "battery_action": "charge",
        "battery_kwh": 2,
        "battery_energy_after_kwh": 10,  # should be 12
        "grid_kwh": 12,
    })
    run(x, sc)
expect_reject("wrong charge state transition", charge_transition_wrong)

def discharge_transition_wrong():
    x = deepcopy(res)
    x["hourly_plan"][3].update({
        "battery_action": "discharge",
        "battery_kwh": 2,
        "battery_energy_after_kwh": 10,  # should be 8
        "grid_kwh": 8,
    })
    run(x, sc)
expect_reject("wrong discharge state transition", discharge_transition_wrong)

def charge_rate():
    x = deepcopy(res)
    x["hourly_plan"][3].update({
        "battery_action": "charge",
        "battery_kwh": 5.01,
        "battery_energy_after_kwh": 15.01,
        "grid_kwh": 15.01,
    })
    run(x, sc)
expect_reject("charge-rate limit exceeded", charge_rate)

def discharge_rate():
    x = deepcopy(res)
    x["hourly_plan"][3].update({
        "battery_action": "discharge",
        "battery_kwh": 5.01,
        "battery_energy_after_kwh": 4.99,
        "grid_kwh": 4.99,
    })
    run(x, sc)
expect_reject("discharge-rate limit exceeded", discharge_rate)

def below_base_reserve():
    x = deepcopy(res)
    x["hourly_plan"][3].update({
        "battery_action": "discharge",
        "battery_kwh": 8.01,
        "battery_energy_after_kwh": 1.99,
        "grid_kwh": 1.99,
    })
    run(x, sc)
expect_reject("below base minimum battery", below_base_reserve)

def above_capacity():
    x = deepcopy(res)
    x["hourly_plan"][3].update({
        "battery_action": "charge",
        "battery_kwh": 5,
        "battery_energy_after_kwh": 21,
        "grid_kwh": 15,
    })
    run(x, sc)
expect_reject("battery above capacity", above_capacity)

def wrong_action():
    x = deepcopy(res)
    x["hourly_plan"][3]["battery_action"] = "charging"
    run(x, sc)
expect_reject("unsupported battery action enum", wrong_action)

# ---------------------------------------------------------------------------
# 4) Energy balance / solar
# ---------------------------------------------------------------------------

def energy_balance():
    x = deepcopy(res)
    x["hourly_plan"][7]["grid_kwh"] = 9
    run(x, sc)
expect_reject("energy-balance violation", energy_balance)

def solar_overuse():
    x = deepcopy(res)
    sc2 = deepcopy(sc)
    sc2["hours"][8]["solar_kwh"] = 4
    x["hourly_plan"][8]["solar_used_kwh"] = 4.01
    x["hourly_plan"][8]["grid_kwh"] = 5.99
    run(x, sc2)
expect_reject("solar usage above available solar", solar_overuse)

def solar_reduction_overuse():
    sc2 = deepcopy(sc)
    sc2["hours"][8]["solar_kwh"] = 10
    d = directive(0, "solar_reduction", {"hours": [8], "factor": 0.2})
    x = valid_result(sc2, [d])
    x["hourly_plan"][8]["solar_used_kwh"] = 2.01
    x["hourly_plan"][8]["grid_kwh"] = 7.99
    run(x, sc2)
expect_reject("solar usage above reduced effective solar", solar_reduction_overuse)

# ---------------------------------------------------------------------------
# 5) Directive guardrails
# ---------------------------------------------------------------------------

def unsupported_directive():
    x = deepcopy(res)
    x["directive_interpretation"] = [
        directive(0, "battery_magic", {"hours": [1]})
    ]
    run(x, sc)
expect_reject("unsupported directive type", unsupported_directive)

def bad_applies_noop():
    x = deepcopy(res)
    x["directive_interpretation"] = [{
        "note_index": 0,
        "applies": True,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "bad",
    }]
    run(x, sc)
expect_reject("no_op with applies=true", bad_applies_noop)

def bad_applies_relevant():
    x = deepcopy(res)
    x["directive_interpretation"] = [{
        "note_index": 0,
        "applies": False,
        "directive_type": "no_charge_window",
        "structured_adjustment": {"hours": [1]},
        "explanation": "bad",
    }]
    run(x, sc)
expect_reject("relevant directive with applies=false", bad_applies_relevant)

def no_op_with_adjustment():
    x = deepcopy(res)
    x["directive_interpretation"] = [{
        "note_index": 0,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": {"hours": [1]},
        "explanation": "bad",
    }]
    run(x, sc)
expect_reject("no_op with non-null adjustment", no_op_with_adjustment)

def wrong_note_order():
    sc2 = scenario([
        "irrelevant one",
        "irrelevant two",
    ])
    x = valid_result(sc2, [noop(1), noop(0)])
    run(x, sc2)
expect_reject("directive note_index out of order", wrong_note_order)

def duplicate_note_index():
    sc2 = scenario([
        "irrelevant one",
        "irrelevant two",
    ])
    x = valid_result(sc2, [noop(0), noop(0)])
    run(x, sc2)
expect_reject("duplicate note_index", duplicate_note_index)

def missing_note_interpretation():
    sc2 = scenario([
        "irrelevant one",
        "irrelevant two",
    ])
    x = valid_result(sc2, [noop(0)])
    run(x, sc2)
expect_reject("missing one interpretation entry", missing_note_interpretation)

def unsorted_directive_hours():
    sc2 = scenario(["no charge"])
    d = directive(0, "no_charge_window", {"hours": [5, 3]})
    x = valid_result(sc2, [d])
    run(x, sc2)
expect_reject("directive hours not ascending", unsorted_directive_hours)

def duplicate_directive_hours():
    sc2 = scenario(["no charge"])
    d = directive(0, "no_charge_window", {"hours": [3, 3]})
    x = valid_result(sc2, [d])
    run(x, sc2)
expect_reject("duplicate directive hour", duplicate_directive_hours)

def out_of_range_directive_hour():
    sc2 = scenario(["no charge"])
    d = directive(0, "no_charge_window", {"hours": [24]})
    x = valid_result(sc2, [d])
    run(x, sc2)
expect_reject("directive hour 24", out_of_range_directive_hour)

def bad_solar_factor():
    sc2 = scenario(["solar reduced"])
    d = directive(0, "solar_reduction", {"hours": [5], "factor": 1.01})
    x = valid_result(sc2, [d])
    run(x, sc2)
expect_reject("solar factor above 1", bad_solar_factor)

def negative_solar_factor():
    sc2 = scenario(["solar reduced"])
    d = directive(0, "solar_reduction", {"hours": [5], "factor": -0.1})
    x = valid_result(sc2, [d])
    run(x, sc2)
expect_reject("negative solar factor", negative_solar_factor)

def reserve_above_capacity():
    sc2 = scenario(["reserve"])
    d = directive(0, "minimum_battery_reserve", {
        "hours": [18],
        "minimum_energy_kwh": 20.01,
    })
    x = valid_result(sc2, [d])
    run(x, sc2)
expect_reject("reserve above battery capacity", reserve_above_capacity)

def max_grid_negative():
    sc2 = scenario(["grid cap"])
    d = directive(0, "max_grid_window", {
        "hours": [18],
        "max_grid_kwh": -1,
    })
    x = valid_result(sc2, [d])
    run(x, sc2)
expect_reject("negative max grid cap", max_grid_negative)

# ---------------------------------------------------------------------------
# 6) Directive application replay
# ---------------------------------------------------------------------------

def no_charge_violation():
    sc2 = scenario(["charging disabled"])
    d = directive(0, "no_charge_window", {"hours": [6]})
    x = valid_result(sc2, [d])
    x["hourly_plan"][6].update({
        "battery_action": "charge",
        "battery_kwh": 1,
        "battery_energy_after_kwh": 11,
        "grid_kwh": 11,
    })
    run(x, sc2)
expect_reject("no_charge_window actually enforced", no_charge_violation)

def no_discharge_violation():
    sc2 = scenario(["do not discharge"])
    d = directive(0, "no_discharge_window", {"hours": [6]})
    x = valid_result(sc2, [d])
    x["hourly_plan"][6].update({
        "battery_action": "discharge",
        "battery_kwh": 1,
        "battery_energy_after_kwh": 9,
        "grid_kwh": 9,
    })
    run(x, sc2)
expect_reject("no_discharge_window actually enforced", no_discharge_violation)

def reserve_violation():
    sc2 = scenario(["keep reserve"])
    d = directive(0, "minimum_battery_reserve", {
        "hours": [6],
        "minimum_energy_kwh": 12,
    })
    x = valid_result(sc2, [d])
    # Make scenario feasible for this validator test by using 20 kWh initial.
    sc2["battery"]["initial_energy_kwh"] = 15
    for e in x["hourly_plan"]:
        e["battery_energy_after_kwh"] = 15
    x["hourly_plan"][6].update({
        "battery_action": "discharge",
        "battery_kwh": 4,
        "battery_energy_after_kwh": 11,
        "grid_kwh": 6,
    })
    # Fix totals after changing grid at hour 6.
    x["total_grid_kwh"] = 236
    x["total_cost_bdt"] = sum(
        e["grid_kwh"] * sc2["hours"][e["hour"]]["tariff_bdt_per_kwh"]
        for e in x["hourly_plan"]
    )
    run(x, sc2)
expect_reject("minimum_battery_reserve actually enforced", reserve_violation)

def grid_cap_violation():
    sc2 = scenario(["transformer cap"])
    d = directive(0, "max_grid_window", {
        "hours": [6],
        "max_grid_kwh": 8,
    })
    x = valid_result(sc2, [d])
    x["hourly_plan"][6]["grid_kwh"] = 8.01
    # Keep energy balance by adding battery discharge.
    x["hourly_plan"][6].update({
        "battery_action": "discharge",
        "battery_kwh": 1.99,
        "battery_energy_after_kwh": 8.01,
    })
    x["total_grid_kwh"] = 238.01
    x["total_cost_bdt"] = sum(
        e["grid_kwh"] * sc2["hours"][e["hour"]]["tariff_bdt_per_kwh"]
        for e in x["hourly_plan"]
    )
    x["peak_grid_kwh"] = 10
    run(x, sc2)
expect_reject("max_grid_window actually enforced", grid_cap_violation)

# ---------------------------------------------------------------------------
# 7) Global totals / identity
# ---------------------------------------------------------------------------

def wrong_total_grid():
    x = deepcopy(res)
    x["total_grid_kwh"] = 239
    run(x, sc)
expect_reject("reported total_grid_kwh mismatch", wrong_total_grid)

def wrong_total_cost():
    x = deepcopy(res)
    x["total_cost_bdt"] += 1
    run(x, sc)
expect_reject("reported total_cost_bdt mismatch", wrong_total_cost)

def wrong_peak():
    x = deepcopy(res)
    x["peak_grid_kwh"] = 9
    run(x, sc)
expect_reject("reported peak_grid_kwh mismatch", wrong_peak)

def wrong_scenario_id():
    x = deepcopy(res)
    x["scenario_id"] = "OTHER"
    run(x, sc)
expect_reject("scenario_id mismatch", wrong_scenario_id)

def final_battery_wrong():
    x = deepcopy(res)
    x["hourly_plan"][-1]["battery_energy_after_kwh"] = 9.98
    run(x, sc)
expect_reject("final battery != initial battery", final_battery_wrong)

# ---------------------------------------------------------------------------
# 8) Exact response field guard
# ---------------------------------------------------------------------------

def extra_top_level_field():
    x = deepcopy(res)
    x["debug"] = True
    run(x, sc)
expect_reject("extra top-level response field", extra_top_level_field)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print()
print(f"RESULT: {PASS} passed, {FAIL} failed")
if FAIL:
    raise SystemExit(1)
print("ALL HIDDEN-LIKE SECTION 3 TESTS PASSED")