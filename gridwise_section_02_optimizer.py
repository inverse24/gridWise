from __future__ import annotations

from decimal import Decimal

import numpy as np

from schemas import BatteryAction, HourPlan, ScenarioRequest
from section1 import InterpretationResult

EPS = 1e-9
MAX_STATES = 20000


class InfeasibleScenario(RuntimeError):
    pass


def _decimals(value: float) -> int:
    exponent = Decimal(str(float(value))).as_tuple().exponent
    return abs(exponent) if isinstance(exponent, int) and exponent < 0 else 0


def infer_step(scenario: ScenarioRequest, floors: list[float]) -> float:
    b = scenario.battery
    values = [
        b.capacity_kwh,
        b.initial_energy_kwh,
        b.minimum_energy_kwh,
        b.max_charge_kwh_per_hour,
        b.max_discharge_kwh_per_hour,
        *floors,
        *scenario.demand(),
        *scenario.solar(),
    ]
    places = max((_decimals(v) for v in values), default=0)
    return 1.0 / (10 ** min(places, 3))


def build_plan(
    scenario: ScenarioRequest,
    interpretation: InterpretationResult,
) -> list[HourPlan]:
    b = scenario.battery
    demand = scenario.demand()
    tariff = scenario.tariff()
    eff_solar = interpretation.effective_solar(scenario.solar())

    floors = interpretation.reserve_floor(b.minimum_energy_kwh)
    caps = interpretation.grid_cap()
    no_charge = interpretation.no_charge_hours()
    no_discharge = interpretation.no_discharge_hours()

    step = infer_step(scenario, floors)
    if b.capacity_kwh / step > MAX_STATES:
        step = b.capacity_kwh / MAX_STATES

    initial_idx = int(round(b.initial_energy_kwh / step))
    cap_idx = int(round(b.capacity_kwh / step))
    charge_steps = int(round(b.max_charge_kwh_per_hour / step))
    discharge_steps = int(round(b.max_discharge_kwh_per_hour / step))
    n_states = cap_idx + 1

    cur = np.full(n_states, np.inf)
    cur[initial_idx] = 0.0

    moves: list[list[tuple[int, str, float, float, float]]] = []
    chosen_all: list[np.ndarray] = []

    for h in range(24):
        floor_idx = int(round(floors[h] / step))
        cap_h = caps[h]
        solar_h = eff_solar[h]
        demand_h = demand[h]
        tariff_h = tariff[h]

        # A transition's cost depends only on the hour and the energy delta,
        # never on the state it starts from, so each is evaluated once.
        transitions: list[tuple[int, str, float, float, float]] = []
        lo = 0 if h in no_discharge else -discharge_steps
        hi = 0 if h in no_charge else charge_steps

        for d in range(lo, hi + 1):
            charge = d * step if d > 0 else 0.0
            discharge = -d * step if d < 0 else 0.0

            net_need = demand_h + charge - discharge
            if net_need < -EPS:
                continue

            solar_used = solar_h if solar_h < net_need else net_need
            grid = net_need - solar_used
            if grid < 0.0:
                grid = 0.0
            if cap_h is not None and grid > cap_h + EPS:
                continue

            action = "charge" if d > 0 else "discharge" if d < 0 else "idle"
            magnitude = charge if d > 0 else discharge if d < 0 else 0.0
            transitions.append((d, action, magnitude, grid, solar_used))

        if not transitions:
            raise InfeasibleScenario(
                f"hour {h}: no battery action satisfies the applied directives"
            )

        nxt = np.full(n_states, np.inf)
        chosen = np.full(n_states, -1, dtype=np.int32)

        for t_index, (d, _, _, grid, _) in enumerate(transitions):
            edge_cost = grid * tariff_h
            if d >= 0:
                src = cur[: n_states - d] if d else cur
                dst_lo, dst_hi = d, n_states
            else:
                src = cur[-d:]
                dst_lo, dst_hi = 0, n_states + d

            candidate = src + edge_cost
            window = nxt[dst_lo:dst_hi]
            better = candidate < window - EPS
            window[better] = candidate[better]
            chosen[dst_lo:dst_hi][better] = t_index

        if floor_idx > 0:
            nxt[:floor_idx] = np.inf
            chosen[:floor_idx] = -1

        if not np.isfinite(nxt).any():
            raise InfeasibleScenario(
                f"no feasible battery state at hour {h} under the applied directives"
            )

        cur = nxt
        moves.append(transitions)
        chosen_all.append(chosen)

    if not np.isfinite(cur[initial_idx]):
        raise InfeasibleScenario(
            "no schedule returns the battery to its initial energy by hour 23"
        )

    plan: list[HourPlan] = []
    idx = initial_idx
    for h in range(23, -1, -1):
        t_index = int(chosen_all[h][idx])
        d, action, magnitude, grid, solar_used = moves[h][t_index]
        plan.append(
            HourPlan(
                hour=h,
                grid_kwh=round(grid, 6),
                solar_used_kwh=round(solar_used, 6),
                battery_action=BatteryAction(action),
                battery_kwh=round(magnitude, 6),
                battery_energy_after_kwh=round(idx * step, 6),
            )
        )
        idx -= d

    plan.reverse()
    return plan


def plan_totals(plan: list[HourPlan], scenario: ScenarioRequest) -> tuple[float, float, float]:
    tariff = scenario.tariff()
    total_grid = sum(p.grid_kwh for p in plan)
    total_cost = sum(p.grid_kwh * tariff[p.hour] for p in plan)
    peak_grid = max(p.grid_kwh for p in plan)
    return round(total_grid, 2), round(total_cost, 2), round(peak_grid, 2)