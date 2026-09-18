from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DirectiveType(str, Enum):
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"


ACTIVE_DIRECTIVE_TYPES = frozenset(
    {
        DirectiveType.SOLAR_REDUCTION,
        DirectiveType.MINIMUM_BATTERY_RESERVE,
        DirectiveType.NO_CHARGE_WINDOW,
        DirectiveType.NO_DISCHARGE_WINDOW,
        DirectiveType.MAX_GRID_WINDOW,
    }
)

REQUIRED_ADJUSTMENT_KEYS: dict[DirectiveType, frozenset[str]] = {
    DirectiveType.SOLAR_REDUCTION: frozenset({"hours", "factor"}),
    DirectiveType.MINIMUM_BATTERY_RESERVE: frozenset({"hours", "minimum_energy_kwh"}),
    DirectiveType.NO_CHARGE_WINDOW: frozenset({"hours"}),
    DirectiveType.NO_DISCHARGE_WINDOW: frozenset({"hours"}),
    DirectiveType.MAX_GRID_WINDOW: frozenset({"hours", "max_grid_kwh"}),
}

TOLERANCE = 0.01

HOURS_IN_DAY = 24


class HourEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)


class BatteryProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capacity_kwh: float = Field(..., ge=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> "BatteryProfile":
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be below minimum_energy_kwh")
        return self


class ScenarioRequest(BaseModel):

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(..., min_length=1)
    operator_notes: list[str] = Field(..., min_length=1, max_length=3)
    hours: list[HourEntry] = Field(..., min_length=HOURS_IN_DAY, max_length=HOURS_IN_DAY)
    battery: BatteryProfile

    @field_validator("operator_notes")
    @classmethod
    def _notes_non_empty(cls, notes: list[str]) -> list[str]:
        for i, note in enumerate(notes):
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"operator_notes[{i}] must be a non-empty string")
        return notes

    @field_validator("hours")
    @classmethod
    def _hours_complete(cls, hours: list[HourEntry]) -> list[HourEntry]:
        seen = sorted(h.hour for h in hours)
        if seen != list(range(HOURS_IN_DAY)):
            raise ValueError("hours must contain exactly one entry for each hour 0..23")
        return hours


    def hours_sorted(self) -> list[HourEntry]:
        return sorted(self.hours, key=lambda h: h.hour)

    def demand(self) -> list[float]:
        return [h.demand_kwh for h in self.hours_sorted()]

    def solar(self) -> list[float]:
        return [h.solar_kwh for h in self.hours_sorted()]

    def tariff(self) -> list[float]:
        return [h.tariff_bdt_per_kwh for h in self.hours_sorted()]


class SolarReductionAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: list[int]
    factor: float


class MinimumReserveAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: list[int]
    minimum_energy_kwh: float


class WindowOnlyAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: list[int]


class MaxGridAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: list[int]
    max_grid_kwh: float


class DirectiveInterpretation(BaseModel):

    model_config = ConfigDict(extra="forbid")

    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: dict[str, Any] | None
    explanation: str


class HourPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: BatteryAction
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourPlan]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]


class ErrorResponse(BaseModel):

    model_config = ConfigDict(extra="forbid")
    error: str
    detail: str | None = None