"""
GridWise - Section 3: Output Validation and Response Assembly

This module validates an optimizer result before it is returned by the API.
It is intentionally kept separate from the Section 2 optimizer so the
validator can be integrated without rewriting the optimizer implementation.

Public entry point:
    validate_and_build_response(optimizer_result, scenario)

Expected scenario keys follow the project specification:
    scenario_id
    hours
    initial_battery_energy_kwh / initial_battery_kwh
    battery
    directives / directive_interpretation

The validator is deliberately defensive: it accepts common naming variants
used by the existing Section 2 code while producing the exact response shape
required by the project.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional


TOLERANCE = 0.01
ALLOWED_BATTERY_ACTIONS = {"charge", "discharge", "idle"}
REQUIRED_RESPONSE_FIELDS = (
    "scenario_id",
    "directive_interpretation",
    "hourly_plan",
    "total_grid_kwh",
    "total_cost_bdt",
    "peak_grid_kwh",
    "plan_summary",
)


class PlanValidationError(ValueError):
    """Raised when an optimizer result violates the GridWise contract."""


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _require(mapping: Mapping[str, Any], *keys: str) -> Any:
    value = _first(mapping, *keys, default=None)
    if value is None:
        raise PlanValidationError(
            f"Missing required field; expected one of: {', '.join(keys)}"
        )
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanValidationError(f"{field} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise PlanValidationError(f"{field} must be finite")
    return value


def _nonnegative(value: Any, field: str) -> float:
    value = _number(value, field)
    if value < -TOLERANCE:
        raise PlanValidationError(f"{field} must be non-negative")
    return max(0.0, value)


def _close(a: float, b: float, tolerance: float = TOLERANCE) -> bool:
    return abs(a - b) <= tolerance


def _scenario_hours(scenario: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    hours = _first(scenario, "hours", "hourly_data", "hourly_scenario")
    if hours is None:
        raise PlanValidationError("Scenario must contain an hours array")
    if not isinstance(hours, list) or len(hours) != 24:
        raise PlanValidationError("Scenario hours must contain exactly 24 entries")

    normalized = []
    for index, item in enumerate(hours):
        if not isinstance(item, Mapping):
            raise PlanValidationError(f"Scenario hour entry {index} must be an object")
        normalized.append(item)
    return normalized


def _hour_map(scenario: Mapping[str, Any]) -> Dict[int, Mapping[str, Any]]:
    result: Dict[int, Mapping[str, Any]] = {}
    for index, item in enumerate(_scenario_hours(scenario)):
        raw_hour = _first(item, "hour", default=index)
        if isinstance(raw_hour, bool) or not isinstance(raw_hour, int):
            raise PlanValidationError(f"Scenario hour {index} must be an integer")
        if raw_hour in result:
            raise PlanValidationError(f"Duplicate scenario hour: {raw_hour}")
        result[raw_hour] = item
    if set(result) != set(range(24)):
        raise PlanValidationError("Scenario hours must be exactly 0 through 23")
    return result


def _demand_for_hour(data: Mapping[str, Any], hour: int) -> float:
    value = _first(
        data,
        "demand_kwh",
        "demand",
        "load_kwh",
        "load",
        "energy_demand_kwh",
    )
    if value is None:
        raise PlanValidationError(f"Missing demand for hour {hour}")
    return _nonnegative(value, f"demand_kwh[{hour}]")


def _solar_for_hour(data: Mapping[str, Any], hour: int) -> float:
    value = _first(
        data,
        "solar_kwh",
        "solar",
        "available_solar_kwh",
        "solar_generation_kwh",
        "solar_generation",
    )
    if value is None:
        raise PlanValidationError(f"Missing solar generation for hour {hour}")
    return _nonnegative(value, f"solar_kwh[{hour}]")


def _tariff_for_hour(data: Mapping[str, Any], hour: int) -> float:
    value = _first(
        data,
        "tariff_bdt_per_kwh",
        "tariff",
        "price_bdt_per_kwh",
        "grid_tariff_bdt_per_kwh",
        "grid_price_bdt_per_kwh",
    )
    if value is None:
        raise PlanValidationError(f"Missing tariff for hour {hour}")
    return _nonnegative(value, f"tariff_bdt_per_kwh[{hour}]")


def _battery_config(scenario: Mapping[str, Any]) -> Mapping[str, Any]:
    battery = _first(scenario, "battery", default=None)
    if isinstance(battery, Mapping):
        return battery
    return scenario


def _initial_battery(scenario: Mapping[str, Any]) -> float:
    battery = _battery_config(scenario)
    value = _first(
        battery,
        "initial_energy_kwh",
        "initial_battery_energy_kwh",
        "initial_battery_kwh",
        "initial_energy",
    )
    if value is None:
        raise PlanValidationError("Missing initial battery energy")
    return _nonnegative(value, "initial_battery_energy_kwh")


def _capacity(scenario: Mapping[str, Any]) -> float:
    battery = _battery_config(scenario)
    value = _first(
        battery,
        "capacity_kwh",
        "battery_capacity_kwh",
        "capacity",
    )
    if value is None:
        raise PlanValidationError("Missing battery capacity")
    return _nonnegative(value, "battery_capacity_kwh")


def _minimum_energy(scenario: Mapping[str, Any]) -> float:
    battery = _battery_config(scenario)
    value = _first(
        battery,
        "minimum_energy_kwh",
        "minimum_battery_energy_kwh",
        "minimum_energy",
        default=0.0,
    )
    return _nonnegative(value, "minimum_energy_kwh")


def _charge_limit(scenario: Mapping[str, Any]) -> float:
    battery = _battery_config(scenario)
    value = _first(
        battery,
        "max_charge_kwh",
        "max_charge_per_hour_kwh",
        "charge_limit_kwh",
        "max_charge_rate_kwh",
    )
    if value is None:
        return math.inf
    return _nonnegative(value, "max_charge_kwh")


def _discharge_limit(scenario: Mapping[str, Any]) -> float:
    battery = _battery_config(scenario)
    value = _first(
        battery,
        "max_discharge_kwh",
        "max_discharge_per_hour_kwh",
        "discharge_limit_kwh",
        "max_discharge_rate_kwh",
    )
    if value is None:
        return math.inf
    return _nonnegative(value, "max_discharge_kwh")


def _directive_list(scenario: Mapping[str, Any], result: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    for source in (result, scenario):
        value = _first(
            source,
            "directive_interpretation",
            "directives",
            "operator_directives",
            default=None,
        )
        if isinstance(value, list):
            return [x for x in value if isinstance(x, Mapping)]
    return []


def _directive_type(directive: Mapping[str, Any]) -> str:
    value = _first(
        directive,
        "directive_type",
        "type",
        "directive",
        "action",
        default="no_op",
    )
    return str(value).strip().lower()


def _hours_from_directive(directive: Mapping[str, Any]) -> List[int]:
    structured = _first(directive, "structured_adjustment", "adjustment", default=None)
    if not isinstance(structured, Mapping):
        structured = directive

    raw = _first(structured, "hours", "affected_hours", "hour_indices", default=[])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlanValidationError("Directive hours must be an array")

    hours: List[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PlanValidationError("Directive hour values must be integers")
        if value < 0 or value > 23:
            raise PlanValidationError("Directive hour values must be between 0 and 23")
        hours.append(value)

    if hours != sorted(set(hours)):
        raise PlanValidationError("Directive hours must be unique and ascending")
    return hours


def _directive_value(directive: Mapping[str, Any], *keys: str) -> Any:
    structured = _first(directive, "structured_adjustment", "adjustment", default=None)
    if isinstance(structured, Mapping):
        value = _first(structured, *keys, default=None)
        if value is not None:
            return value
    return _first(directive, *keys, default=None)


def _solar_factor_for_hour(
    hour: int,
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
) -> float:
    factor = 1.0

    for directive in _directive_list(scenario, result):
        if _directive_type(directive) != "solar_reduction":
            continue
        applies = _first(directive, "applies", default=True)
        if applies is False:
            continue

        hours = _hours_from_directive(directive)
        if hour not in hours:
            continue

        raw = _directive_value(
            directive,
            "reduction_factor",
            "solar_factor",
            "remaining_factor",
            "factor",
        )
        if raw is None:
            # The problem specification defines the factor as the fraction
            # remaining after reduction.
            percent = _directive_value(
                directive,
                "reduction_percent",
                "reduction_percentage",
                "percentage_reduction",
            )
            if percent is None:
                raise PlanValidationError("solar_reduction is missing its factor")
            factor = 1.0 - (_number(percent, "solar reduction percentage") / 100.0)
        else:
            factor = _number(raw, "solar reduction factor")

        if factor < 0.0 or factor > 1.0:
               raise PlanValidationError(
        "solar reduction factor must be between 0 and 1"
    )

    return factor


def _effective_solar(
    hour: int,
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
) -> float:
    data = _hour_map(scenario)[hour]
    return _solar_for_hour(data, hour) * _solar_factor_for_hour(hour, scenario, result)


def _window_set(
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
    directive_name: str,
) -> set[int]:
    values: set[int] = set()
    for directive in _directive_list(scenario, result):
        if _directive_type(directive) != directive_name:
            continue
        if _first(directive, "applies", default=True) is False:
            continue
        values.update(_hours_from_directive(directive))
    return values


def _minimum_reserve_for_hour(
    hour: int,
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
) -> float:
    reserve = _minimum_energy(scenario)
    for directive in _directive_list(scenario, result):
        if _directive_type(directive) != "minimum_battery_reserve":
            continue
        if _first(directive, "applies", default=True) is False:
            continue
        if hour not in _hours_from_directive(directive):
            continue
        raw = _directive_value(
            directive,
            "minimum_reserve_kwh",
            "reserve_kwh",
            "minimum_energy_kwh",
            "value_kwh",
            "value",
        )
        if raw is None:
            raise PlanValidationError("minimum_battery_reserve is missing its value")
        reserve = max(reserve, _nonnegative(raw, "minimum_battery_reserve"))
    return reserve


def _max_grid_for_hour(
    hour: int,
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
) -> Optional[float]:
    limit: Optional[float] = None
    for directive in _directive_list(scenario, result):
        if _directive_type(directive) != "max_grid_window":
            continue
        if _first(directive, "applies", default=True) is False:
            continue
        if hour not in _hours_from_directive(directive):
            continue
        raw = _directive_value(
            directive,
            "max_grid_kwh",
            "grid_limit_kwh",
            "maximum_grid_kwh",
            "limit_kwh",
            "value_kwh",
            "value",
        )
        if raw is None:
            raise PlanValidationError("max_grid_window is missing its grid limit")
        current = _nonnegative(raw, "max_grid_kwh")
        limit = current if limit is None else min(limit, current)
    return limit


def validate_hour_structure(hourly_plan: Any) -> None:
    if not isinstance(hourly_plan, list):
        raise PlanValidationError("hourly_plan must be an array")
    if len(hourly_plan) != 24:
        raise PlanValidationError("hourly_plan must contain exactly 24 entries")

    actual_hours: List[int] = []
    for index, entry in enumerate(hourly_plan):
        if not isinstance(entry, Mapping):
            raise PlanValidationError(f"hourly_plan[{index}] must be an object")
        hour = _first(entry, "hour", default=None)
        if isinstance(hour, bool) or not isinstance(hour, int):
            raise PlanValidationError(f"hourly_plan[{index}].hour must be an integer")
        actual_hours.append(hour)

    if actual_hours != list(range(24)):
        raise PlanValidationError("hourly_plan hours must be exactly 0 through 23 in ascending order")


def validate_directive_interpretation(
    directives: Any,
    expected_note_count: Optional[int] = None,
) -> None:
    if not isinstance(directives, list):
        raise PlanValidationError("directive_interpretation must be an array")

    if expected_note_count is not None and len(directives) != expected_note_count:
        raise PlanValidationError(
            "directive_interpretation must contain exactly one entry per operator note"
        )

    for index, directive in enumerate(directives):
        if not isinstance(directive, Mapping):
            raise PlanValidationError(f"directive_interpretation[{index}] must be an object")

        dtype = _directive_type(directive)
        if dtype not in {
            "solar_reduction",
            "minimum_battery_reserve",
            "no_charge_window",
            "no_discharge_window",
            "max_grid_window",
            "no_op",
        }:
            raise PlanValidationError(
                f"Unsupported directive_type: {dtype}"
            )

        applies = _first(directive, "applies", default=None)
        if not isinstance(applies, bool):
            raise PlanValidationError(
                f"directive_interpretation[{index}].applies must be boolean"
            )

        # The canonical interpretation object has exactly these fields.
        required_directive_fields = {
            "note_index",
            "applies",
            "directive_type",
            "structured_adjustment",
            "explanation",
        }
        if set(directive.keys()) != required_directive_fields:
            raise PlanValidationError(
                f"directive_interpretation[{index}] must contain exactly "
                f"{sorted(required_directive_fields)}"
            )

        note_index = directive.get("note_index")
        if isinstance(note_index, bool) or not isinstance(note_index, int):
            raise PlanValidationError(
                f"directive_interpretation[{index}].note_index must be an integer"
            )
        if note_index != index:
            raise PlanValidationError(
                "directive_interpretation entries must be in note_index order"
            )

        explanation = directive.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            raise PlanValidationError(
                f"directive_interpretation[{index}].explanation must be a non-empty string"
            )

        structured = directive.get("structured_adjustment")
        if dtype == "no_op":
            if applies is not False or structured is not None:
                raise PlanValidationError(
                    "no_op must have applies=false and structured_adjustment=null"
                )
        else:
            if applies is not True:
                raise PlanValidationError(
                    f"{dtype} must have applies=true"
                )
            if not isinstance(structured, Mapping):
                raise PlanValidationError(
                    f"{dtype} must contain structured_adjustment"
                )

            # Enforce the exact machine-checkable shape from Section 04.
            expected_shapes = {
                "solar_reduction": {"hours", "factor"},
                "minimum_battery_reserve": {"hours", "minimum_energy_kwh"},
                "no_charge_window": {"hours"},
                "no_discharge_window": {"hours"},
                "max_grid_window": {"hours", "max_grid_kwh"},
            }
            expected = expected_shapes[dtype]
            if set(structured.keys()) != expected:
                raise PlanValidationError(
                    f"{dtype}.structured_adjustment must contain exactly "
                    f"{sorted(expected)}"
                )

            hours = _hours_from_directive(directive)
            if dtype != "no_op" and not isinstance(hours, list):
                raise PlanValidationError("Directive hours must be an array")

            if dtype == "solar_reduction":
                factor = _number(structured["factor"], "solar reduction factor")
                if factor < -TOLERANCE or factor > 1.0 + TOLERANCE:
                    raise PlanValidationError(
                        "solar reduction factor must be between 0 and 1"
                    )

            elif dtype == "minimum_battery_reserve":
                reserve = _nonnegative(
                    structured["minimum_energy_kwh"],
                    "minimum_battery_reserve",
                )
                if reserve > _capacity(scenario) + TOLERANCE:
                    raise PlanValidationError(
                        "minimum_battery_reserve cannot exceed battery capacity"
                    )

            elif dtype == "max_grid_window":
                _nonnegative(structured["max_grid_kwh"], "max_grid_kwh")


def validate_hourly_plan(
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    hourly_plan = _require(result, "hourly_plan")
    validate_hour_structure(hourly_plan)

    hours = _hour_map(scenario)
    no_charge = _window_set(scenario, result, "no_charge_window")
    no_discharge = _window_set(scenario, result, "no_discharge_window")

    capacity = _capacity(scenario)
    base_minimum = _minimum_energy(scenario)
    charge_limit = _charge_limit(scenario)
    discharge_limit = _discharge_limit(scenario)

    previous_energy = _initial_battery(scenario)

    required_hour_fields = {
        "hour",
        "grid_kwh",
        "solar_used_kwh",
        "battery_action",
        "battery_kwh",
        "battery_energy_after_kwh",
    }

    for entry in hourly_plan:
        if set(entry.keys()) != required_hour_fields:
            raise PlanValidationError(
                "Each hourly_plan entry must contain exactly "
                f"{sorted(required_hour_fields)}"
            )

        hour = entry["hour"]
        scenario_hour = hours[hour]

        grid = _nonnegative(_require(entry, "grid_kwh"), f"hour {hour} grid_kwh")
        solar_used = _nonnegative(
            _require(entry, "solar_used_kwh"),
            f"hour {hour} solar_used_kwh",
        )
        battery_action = _first(entry, "battery_action", default=None)
        if battery_action not in ALLOWED_BATTERY_ACTIONS:
            raise PlanValidationError(
                f"hour {hour} battery_action must be charge, discharge, or idle"
            )

        battery_kwh = _nonnegative(
            _require(entry, "battery_kwh"),
            f"hour {hour} battery_kwh",
        )
        after = _nonnegative(
            _require(entry, "battery_energy_after_kwh"),
            f"hour {hour} battery_energy_after_kwh",
        )

        if battery_action == "idle" and not _close(battery_kwh, 0.0):
            raise PlanValidationError(
                f"hour {hour}: idle battery_action requires battery_kwh=0"
            )

        if battery_action == "charge":
            if hour in no_charge and battery_kwh > TOLERANCE:
                raise PlanValidationError(f"hour {hour}: charging is forbidden by directive")
            if battery_kwh > charge_limit + TOLERANCE:
                raise PlanValidationError(f"hour {hour}: charge rate limit exceeded")
            expected_after = previous_energy + battery_kwh
        elif battery_action == "discharge":
            if hour in no_discharge and battery_kwh > TOLERANCE:
                raise PlanValidationError(f"hour {hour}: discharging is forbidden by directive")
            if battery_kwh > discharge_limit + TOLERANCE:
                raise PlanValidationError(f"hour {hour}: discharge rate limit exceeded")
            expected_after = previous_energy - battery_kwh
        else:
            expected_after = previous_energy

        if not _close(after, expected_after):
            raise PlanValidationError(
                f"hour {hour}: battery transition is inconsistent "
                f"(expected {expected_after}, got {after})"
            )

        reserve = max(
            base_minimum,
            _minimum_reserve_for_hour(hour, scenario, result),
        )
        if after < reserve - TOLERANCE:
            raise PlanValidationError(
                f"hour {hour}: battery energy {after} is below minimum reserve {reserve}"
            )

        if after > capacity + TOLERANCE:
            raise PlanValidationError(
                f"hour {hour}: battery energy {after} exceeds capacity {capacity}"
            )

        effective_solar = _effective_solar(hour, scenario, result)
        if solar_used > effective_solar + TOLERANCE:
            raise PlanValidationError(
                f"hour {hour}: solar_used_kwh exceeds effective solar availability"
            )

        max_grid = _max_grid_for_hour(hour, scenario, result)
        if max_grid is not None and grid > max_grid + TOLERANCE:
            raise PlanValidationError(
                f"hour {hour}: grid import exceeds max_grid_window limit"
            )

        demand = _demand_for_hour(scenario_hour, hour)
        discharge = battery_kwh if battery_action == "discharge" else 0.0
        charge = battery_kwh if battery_action == "charge" else 0.0

        lhs = grid + solar_used + discharge
        rhs = demand + charge
        if not _close(lhs, rhs):
            raise PlanValidationError(
                f"hour {hour}: energy balance violation "
                f"(supply={lhs}, demand={rhs})"
            )

        previous_energy = after


def calculate_totals(
    scenario: Mapping[str, Any],
    hourly_plan: List[Mapping[str, Any]],
) -> Dict[str, float]:
    hours = _hour_map(scenario)

    total_grid = sum(
        _nonnegative(entry["grid_kwh"], f"hour {entry['hour']} grid_kwh")
        for entry in hourly_plan
    )

    total_cost = sum(
        _nonnegative(entry["grid_kwh"], f"hour {entry['hour']} grid_kwh")
        * _tariff_for_hour(hours[entry["hour"]], entry["hour"])
        for entry in hourly_plan
    )

    peak_grid = max(
        _nonnegative(entry["grid_kwh"], f"hour {entry['hour']} grid_kwh")
        for entry in hourly_plan
    )

    return {
        "total_grid_kwh": total_grid,
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": peak_grid,
    }


def validate_global_constraints(
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
) -> Dict[str, float]:
    hourly_plan = _require(result, "hourly_plan")
    initial = _initial_battery(scenario)
    final = _number(
        hourly_plan[-1]["battery_energy_after_kwh"],
        "final battery_energy_after_kwh",
    )

    if not _close(final, initial):
        raise PlanValidationError(
            f"Final battery energy {final} does not equal initial energy {initial}"
        )

    totals = calculate_totals(scenario, hourly_plan)

    for field in ("total_grid_kwh", "total_cost_bdt", "peak_grid_kwh"):
        reported = _number(_require(result, field), field)
        if not _close(reported, totals[field]):
            raise PlanValidationError(
                f"{field} does not match recalculated value "
                f"({reported} vs {totals[field]})"
            )

    return totals


def _expected_note_count(scenario: Mapping[str, Any]) -> Optional[int]:
    notes = _first(
        scenario,
        "operator_notes",
        "notes",
        "operator_directives",
        default=None,
    )
    if notes is None:
        return None
    if not isinstance(notes, list):
        raise PlanValidationError("operator_notes must be an array")
    return len(notes)


def _build_summary(
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
    totals: Mapping[str, float],
) -> str:
    directives = _directive_list(scenario, result)

    applied = []
    ignored = []
    for directive in directives:
        dtype = _directive_type(directive)
        applies = _first(directive, "applies", default=False)
        if applies:
            applied.append(dtype)
        elif dtype == "no_op":
            ignored.append(dtype)

    applied_text = ", ".join(applied) if applied else "no applicable directives"
    ignored_text = (
        f"; ignored {len(ignored)} irrelevant note(s)"
        if ignored
        else ""
    )

    return (
        f"Applied {applied_text}{ignored_text}. "
        f"The battery was scheduled to reduce grid cost while respecting "
        f"all constraints, and the final battery energy was restored to "
        f"its initial level. Total grid import was "
        f"{totals['total_grid_kwh']:.2f} kWh with total cost "
        f"{totals['total_cost_bdt']:.2f} BDT."
    )


def build_final_response(
    scenario: Mapping[str, Any],
    result: Mapping[str, Any],
) -> Dict[str, Any]:
    """Validate and return only the exact public response fields."""
    totals = validate_global_constraints(scenario, result)

    scenario_id = _require(result, "scenario_id")
    expected_scenario_id = _require(scenario, "scenario_id")
    if scenario_id != expected_scenario_id:
        raise PlanValidationError(
            "scenario_id in the optimizer result must match the request scenario_id"
        )

    directives = _require(result, "directive_interpretation")
    hourly_plan = _require(result, "hourly_plan")

    summary = result.get("plan_summary")
    if summary is None:
        summary = _build_summary(scenario, result, totals)
    if not isinstance(summary, str):
        raise PlanValidationError("plan_summary must be a string")

    response = {
        "scenario_id": scenario_id,
        "directive_interpretation": directives,
        "hourly_plan": hourly_plan,
        "total_grid_kwh": round(totals["total_grid_kwh"], 2),
        "total_cost_bdt": round(totals["total_cost_bdt"], 2),
        "peak_grid_kwh": round(totals["peak_grid_kwh"], 2),
        "plan_summary": summary,
    }

    if set(response.keys()) != set(REQUIRED_RESPONSE_FIELDS):
        raise PlanValidationError("Final response contains an invalid field set")

    return response


def validate_and_build_response(
    optimizer_result: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Main Section 3 entry point.

    The optimizer result is checked first at directive/hour level, then at
    global level, and finally normalized into the exact public response shape.
    """
    if not isinstance(optimizer_result, Mapping):
        raise PlanValidationError("optimizer_result must be an object")

    if not isinstance(scenario, Mapping):
        raise PlanValidationError("scenario must be an object")

    # Reject unexpected fields at the top level.
    allowed_top_level_fields = {
        "scenario_id",
        "directive_interpretation",
        "hourly_plan",
        "total_grid_kwh",
        "total_cost_bdt",
        "peak_grid_kwh",
        "plan_summary",
    }

    extra_fields = set(optimizer_result.keys()) - allowed_top_level_fields

    if extra_fields:
        raise PlanValidationError(
            f"unexpected top-level response fields: {sorted(extra_fields)}"
        )

    directives = _require(
        optimizer_result,
        "directive_interpretation",
    )

    validate_directive_interpretation(
        directives,
        expected_note_count=_expected_note_count(scenario),
    )

    validate_hourly_plan(
        scenario,
        optimizer_result,
    )

    return build_final_response(
        scenario,
        optimizer_result,
    )

__all__ = [
    "TOLERANCE",
    "ALLOWED_BATTERY_ACTIONS",
    "REQUIRED_RESPONSE_FIELDS",
    "PlanValidationError",
    "validate_directive_interpretation",
    "validate_hour_structure",
    "validate_hourly_plan",
    "validate_global_constraints",
    "calculate_totals",
    "build_final_response",
    "validate_and_build_response",
]
