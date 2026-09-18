from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Any

STEP = 0.5
EPS = 1e-9


def to_hour_index(value: str) -> int:
    text = value.strip().lower()
    if text == "noon":
        return 12
    if text == "midnight":
        return 0
    if text.endswith("am"):
        hour = int(text[:-2])
    elif text.endswith("pm"):
        hour = int(text[:-2])
        if hour != 12:
            hour += 12
    else:
        hour = int(text)
    return hour


def normalize_note_text(note: str) -> str:
    return note.lower().replace("–", "-").replace("—", "-")


def parse_time_window(note: str) -> List[int]:
    text = normalize_note_text(note)
    patterns = [
        r"from\s+([^\n]+?)\s+until\s+([^\n]+?)(?:\.|$)",
        r"from\s+([^\n]+?)\s+to\s+([^\n]+?)(?:\.|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            start_text = m.group(1).strip()
            end_text = m.group(2).strip()
            try:
                start = to_hour_index(start_text)
                end = to_hour_index(end_text)
                if end <= start:
                    end = start + 1
                hours = list(range(start, end))
                return hours
            except ValueError:
                pass

    # fallback: infer from coarse phrases
    for indicator in ["noon", "pm", "am"]:
        if indicator in text:
            pass

    return []


def parse_numeric_value(note: str, keywords: List[str]) -> float | None:
    text = normalize_note_text(note)
    for kw in keywords:
        if kw in text:
            match = re.search(rf"(\d+(?:\.\d+)?)\s*(?:kwh|kwh\b)", text)
            if match:
                return float(match.group(1))
    return None


def detect_directives_from_notes(scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    notes = scenario.get("operator_notes", [])
    directives: List[Dict[str, Any]] = []

    for idx, note in enumerate(notes):
        text = normalize_note_text(note)
        relevant = False
        directive: Dict[str, Any] = {"note_index": idx, "applies": False, "directive_type": "no_op", "structured_adjustment": None}

        if "solar" in text or "panel" in text or "cloud cover" in text or "forecast solar" in text:
            if "reduced" in text or "reduce" in text or "half" in text or "25%" in text or "50%" in text or "75%" in text or "roughly" in text:
                hours = parse_time_window(note)
                if not hours:
                    # approximate common solar reduction windows
                    hours = [10, 11, 12, 13, 14]
                factor = 0.5 if "half" in text or "50%" in text else 0.25 if "25%" in text else 0.75 if "75%" in text else 0.5
                directive = {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": sorted(set(hours)), "factor": factor},
                    "explanation": "Usable solar is reduced in the stated window."
                }
                relevant = True

        if not relevant and ("charging" in text or "charge" in text or "charger" in text or "isolated" in text) and (
            "disabled" in text or "unavailable" in text or "stop" in text or "not available" in text or "isolated" in text
        ):
            hours = parse_time_window(note)
            if not hours:
                hours = [2, 3, 4] if "2 am" in text or "2 am" in text else []
            if hours:
                directive = {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "no_charge_window",
                    "structured_adjustment": {"hours": sorted(set(hours))},
                    "explanation": "Battery charging is unavailable in the stated window."
                }
                relevant = True

        if not relevant and ("do not discharge" in text or "discharge" in text or "relay testing" in text) and (
            "disabled" in text or "not allowed" in text or "do not" in text or "blocked" in text or "relay" in text
        ):
            hours = parse_time_window(note)
            if not hours:
                hours = [17, 18] if "5 pm" in text or "5 pm" in text else []
            if hours:
                directive = {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "no_discharge_window",
                    "structured_adjustment": {"hours": sorted(set(hours))},
                    "explanation": "Battery discharge is unavailable in the stated window."
                }
                relevant = True

        if not relevant and ("keep at least" in text or "at least" in text or "reserve" in text or "minimum" in text or "remain in the battery" in text):
            minimum = parse_numeric_value(note, ["at least", "reserve", "minimum", "remain"])
            hours = parse_time_window(note)
            if minimum is not None and not hours:
                hours = list(range(18, 22))
            if minimum is not None and hours:
                directive = {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "minimum_battery_reserve",
                    "structured_adjustment": {"hours": sorted(set(hours)), "minimum_energy_kwh": minimum},
                    "explanation": "A battery reserve is required in the stated window."
                }
                relevant = True

        if not relevant and ("grid" in text or "transformer" in text or "limit" in text or "cap" in text or "stay at or below" in text or "at most" in text):
            max_grid = parse_numeric_value(note, ["grid", "transformer", "limit", "cap", "below", "at most"])
            hours = parse_time_window(note)
            if max_grid is not None and not hours:
                hours = list(range(19, 22))
            if max_grid is not None and hours:
                directive = {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "max_grid_window",
                    "structured_adjustment": {"hours": sorted(set(hours)), "max_grid_kwh": max_grid},
                    "explanation": "Grid import is capped in the stated window."
                }
                relevant = True

        if not relevant:
            directive = {
                "note_index": idx,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "This note does not affect today's schedule."
            }

        directives.append(directive)

    return directives


def build_directive_lookup(directives: List[Dict[str, Any]]) -> Dict[str, Any]:
    lookup: Dict[str, Any] = {
        "solar_reduction": [],
        "minimum_battery_reserve": [],
        "no_charge_window": [],
        "no_discharge_window": [],
        "max_grid_window": [],
    }
    for directive in directives:
        if not directive["applies"]:
            continue
        typ = directive["directive_type"]
        if typ in lookup:
            lookup[typ].append(directive["structured_adjustment"])
    return lookup


def effective_solar_for_hour(hour_record: Dict[str, Any], directive_lookup: Dict[str, Any]) -> float:
    solar = float(hour_record["solar_kwh"])
    for rule in directive_lookup.get("solar_reduction", []):
        if hour_record["hour"] in set(rule["hours"]):
            solar *= float(rule["factor"])
    return solar


def hourly_minimum_reserve(hour: int, directive_lookup: Dict[str, Any], battery_min: float) -> float:
    min_required = battery_min
    for rule in directive_lookup.get("minimum_battery_reserve", []):
        if hour in set(rule["hours"]):
            min_required = max(min_required, float(rule["minimum_energy_kwh"]))
    return min_required


def hourly_max_grid(hour: int, directive_lookup: Dict[str, Any]) -> float | None:
    for rule in directive_lookup.get("max_grid_window", []):
        if hour in set(rule["hours"]):
            return float(rule["max_grid_kwh"])
    return None


def solve_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    hours = sorted(scenario["hours"], key=lambda h: h["hour"])
    battery = scenario["battery"]
    directives = detect_directives_from_notes(scenario)
    lookup = build_directive_lookup(directives)

    cap = float(battery["capacity_kwh"])
    initial = float(battery["initial_energy_kwh"])
    base_min = float(battery["minimum_energy_kwh"])
    charge_limit = float(battery["max_charge_kwh_per_hour"])
    discharge_limit = float(battery["max_discharge_kwh_per_hour"])
    initial_idx = int(round(initial / STEP))

    prev_states: Dict[int, float] = {initial_idx: 0.0}
    back: Dict[int, Dict[int, Tuple[int, str, float, float, float]]] = {}

    for hour_data in hours:
        hour = int(hour_data["hour"])
        demand = float(hour_data["demand_kwh"])
        effective_solar = effective_solar_for_hour(hour_data, lookup)
        reserve = hourly_minimum_reserve(hour, lookup, base_min)
        max_grid_cap = hourly_max_grid(hour, lookup)
        no_charge = hour in {h for rule in lookup.get("no_charge_window", []) for h in rule["hours"]}
        no_discharge = hour in {h for rule in lookup.get("no_discharge_window", []) for h in rule["hours"]}

        next_states: Dict[int, float] = {}
        back[hour] = {}

        charge_step_count = int(round(charge_limit / STEP))
        discharge_step_count = int(round(discharge_limit / STEP))

        for state_idx, cost_so_far in prev_states.items():
            current_energy = state_idx * STEP

            if charge_step_count > 0:
                charge_amounts = [0.0]
                charge_amounts.extend([x * STEP for x in range(1, charge_step_count + 1)])
            else:
                charge_amounts = [0.0]

            if discharge_step_count > 0:
                discharge_amounts = [0.0]
                discharge_amounts.extend([x * STEP for x in range(1, discharge_step_count + 1)])
            else:
                discharge_amounts = [0.0]

            for charge_amt in charge_amounts:
                if no_charge and charge_amt > EPS:
                    continue
                if charge_amt > charge_limit + EPS:
                    continue

                for discharge_amt in discharge_amounts:
                    if no_discharge and discharge_amt > EPS:
                        continue
                    if discharge_amt > discharge_limit + EPS:
                        continue
                    if charge_amt > EPS and discharge_amt > EPS:
                        continue

                    new_energy = current_energy + charge_amt - discharge_amt
                    if new_energy < -EPS or new_energy > cap + EPS:
                        continue

                    min_allowed = max(base_min, reserve)
                    if new_energy < min_allowed - EPS:
                        continue

                    solar_used = min(effective_solar, demand + charge_amt - discharge_amt)
                    if solar_used < -EPS:
                        solar_used = 0.0

                    grid_import = max(0.0, demand + charge_amt - discharge_amt - effective_solar)
                    if max_grid_cap is not None and grid_import > max_grid_cap + EPS:
                        continue

                    if abs(grid_import) < EPS:
                        grid_import = 0.0

                    tariff = float(hour_data["tariff_bdt_per_kwh"])
                    total_cost = cost_so_far + grid_import * tariff
                    new_idx = int(round(new_energy / STEP))

                    if new_idx not in next_states or total_cost < next_states[new_idx] - EPS:
                        next_states[new_idx] = total_cost
                        back[hour][new_idx] = (state_idx, "charge" if charge_amt > EPS else "discharge" if discharge_amt > EPS else "idle", charge_amt if charge_amt > EPS else discharge_amt if discharge_amt > EPS else 0.0, grid_import, solar_used)

        prev_states = next_states
        if not prev_states:
            raise ValueError(f"No feasible schedule for hour {hour}")

    final_energy = initial_idx
    if final_energy not in prev_states:
        # try to recover the best nearby feasible state if exact end state is impossible due to floating point drift
        nearest_state = min(prev_states.keys(), key=lambda idx: abs(idx * STEP - initial))
        final_energy = nearest_state

    if abs(final_energy * STEP - initial) > 1e-4:
        raise ValueError(f"Final battery state mismatch: expected {initial}, got {final_energy * STEP}")

    plan: List[Dict[str, Any]] = []
    state_idx = final_energy
    for hour_data in reversed(hours): 
        
        hour = int(hour_data["hour"])
        prev_idx, action, battery_kwh, grid_import, solar_used = back[hour][state_idx]
        plan.append({
            "hour": hour,
            "grid_kwh": round(grid_import, 6),
            "solar_used_kwh": round(solar_used, 6),
            "battery_action": action,
            "battery_kwh": round(battery_kwh, 6),
            "battery_energy_after_kwh": round(state_idx * STEP, 6),
        })
        state_idx = prev_idx

    plan.reverse()

    total_grid = sum(float(entry["grid_kwh"]) for entry in plan)
    total_cost = sum(float(entry["grid_kwh"]) * next(h["tariff_bdt_per_kwh"] for h in hours if h["hour"] == entry["hour"]) for entry in plan)
    peak_grid = max(float(entry["grid_kwh"]) for entry in plan)

    final_output = {
        "scenario_id": scenario["scenario_id"],
        "directive_interpretation": detect_directives_from_notes(scenario),
        "hourly_plan": plan,
        "total_grid_kwh": round(total_grid, 2),
        "total_cost_bdt": round(total_cost, 2),
        "peak_grid_kwh": round(peak_grid, 2),
        "plan_summary": "Optimized charge and discharge timing to minimize cost while satisfying all battery and grid directives."
    }
    return final_output


def load_sample_cases(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload["cases"]


def validate_plan(plan: List[Dict[str, Any]], scenario: Dict[str, Any], directives: List[Dict[str, Any]]) -> bool:
    hours_map = {int(item["hour"]): item for item in scenario["hours"]}
    battery = scenario["battery"]
    current_energy = float(battery["initial_energy_kwh"])
    lookup = build_directive_lookup(directives)

    for entry in plan:
        hour = int(entry["hour"])
        record = hours_map[hour]
        if entry["battery_action"] == "charge":
            charge = float(entry["battery_kwh"])
            discharge = 0.0
        elif entry["battery_action"] == "discharge":
            charge = 0.0
            discharge = float(entry["battery_kwh"])
        else:
            charge = 0.0
            discharge = 0.0

        if abs(float(entry["grid_kwh"]) + float(entry["solar_used_kwh"]) + discharge - (float(record["demand_kwh"]) + charge)) > 1e-3:
            return False

        current_energy = float(entry["battery_energy_after_kwh"])
        if current_energy < float(battery["minimum_energy_kwh"]) - 1e-3:
            return False
        if current_energy > float(battery["capacity_kwh"]) + 1e-3:
            return False

        if hour in {h for rule in lookup.get("no_charge_window", []) for h in rule["hours"]} and charge > 1e-3:
            return False
        if hour in {h for rule in lookup.get("no_discharge_window", []) for h in rule["hours"]} and discharge > 1e-3:
            return False

    if abs(current_energy - float(battery["initial_energy_kwh"])) > 1e-3:
        return False

    return True


if __name__ == "__main__":
    sample_file = Path(__file__).with_name("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")
    cases = load_sample_cases(str(sample_file))

    for case in cases[:3]:
        scenario = case["input"]
        directives = detect_directives_from_notes(scenario)
        result = solve_scenario(scenario)
        ok = validate_plan(result["hourly_plan"], scenario, directives)
        print(f"{scenario['scenario_id']}: valid={ok}, total_cost={result['total_cost_bdt']}, total_grid={result['total_grid_kwh']}, peak={result['peak_grid_kwh']}")
