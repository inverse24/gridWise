from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

from schemas import (
    ACTIVE_DIRECTIVE_TYPES,
    BatteryProfile,
    DirectiveInterpretation,
    DirectiveType,
    ScenarioRequest,
)

load_dotenv()


WORD_NUMBERS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

FRACTION_WORDS: dict[str, float] = {
    "half": 0.5,
    "one-half": 0.5,
    "a half": 0.5,
    "one half": 0.5,
    "third": 1 / 3,
    "one-third": 1 / 3,
    "one third": 1 / 3,
    "quarter": 0.25,
    "one-quarter": 0.25,
    "one quarter": 0.25,
    "a quarter": 0.25,
    "fourth": 0.25,
    "fifth": 0.2,
    "one-fifth": 0.2,
    "one fifth": 0.2,
    "tenth": 0.1,
    "one-tenth": 0.1,
    "one tenth": 0.1,
    "three-quarters": 0.75,
    "three quarters": 0.75,
    "two-thirds": 2 / 3,
    "two thirds": 2 / 3,
}

_MERIDIEM = r"(a\.?m\.?|p\.?m\.?)"
_CLOCK = rf"(?:(\d{{1,2}})(?::(\d{{2}}))?\s*{_MERIDIEM}?|noon|midday|midnight|{'|'.join(WORD_NUMBERS)})"

_RANGE_PATTERNS = [
    rf"from\s+{_CLOCK}\s+(?:until|till|til|to|through|up\s+to)\s+{_CLOCK}",
    rf"between\s+{_CLOCK}\s+and\s+{_CLOCK}",
    rf"{_CLOCK}\s*(?:-|–|—|\bto\b)\s*{_CLOCK}",
    rf"starting\s+(?:at\s+)?{_CLOCK}\s+(?:until|till|to|through)\s+{_CLOCK}",
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _clock_to_hour(raw: str) -> int | None:
    token = raw.strip().lower()
    if token in {"noon", "midday"}:
        return 12
    if token == "midnight":
        return 0

    word_match = re.fullmatch(r"([a-z-]+)", token)
    if word_match and word_match.group(1) in WORD_NUMBERS:
        return WORD_NUMBERS[word_match.group(1)] % 12

    m = re.fullmatch(rf"(\d{{1,2}})(?::(\d{{2}}))?\s*{_MERIDIEM}?", token)
    if not m:
        return None

    hour = int(m.group(1))
    meridiem = (m.group(3) or "").replace(".", "")

    if meridiem.startswith("p"):
        hour = 12 if hour == 12 else hour + 12
    elif meridiem.startswith("a"):
        hour = 0 if hour == 12 else hour

    return hour if 0 <= hour <= 23 else None


def _has_meridiem(raw: str) -> bool:
    token = raw.strip().lower()
    if token in {"noon", "midday", "midnight"}:
        return True
    return bool(re.search(_MERIDIEM, token))


def _is_24h_clock(raw: str) -> bool:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    return bool(m)


def parse_window(text: str) -> list[int] | None:
    t = normalize(text)

    for pattern in _RANGE_PATTERNS:
        m = re.search(pattern, t)
        if not m:
            continue

        groups = [g for g in m.groups() if g is not None]
        raw_parts = _split_endpoints(m.group(0), t)
        if raw_parts is None:
            continue
        start_raw, end_raw = raw_parts

        start = _clock_to_hour(start_raw)
        end = _clock_to_hour(end_raw)
        if start is None or end is None:
            continue

        start, end = _resolve_meridiem(start_raw, end_raw, start, end)

        hours = _expand(start, end)
        if hours:
            return hours
        del groups

    return None


def _split_endpoints(matched: str, _full: str) -> tuple[str, str] | None:
    separators = [
        r"\s+(?:until|till|til|through|up\s+to)\s+",
        r"\s+and\s+",
        r"\s+to\s+",
        r"\s*(?:-|–|—)\s*",
    ]
    cleaned = re.sub(r"^(?:from|between|starting\s+(?:at\s+)?)\s*", "", matched).strip()
    for sep in separators:
        parts = re.split(sep, cleaned, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()
    return None


def _resolve_meridiem(
    start_raw: str, end_raw: str, start: int, end: int
) -> tuple[int, int]:
    start_known = _has_meridiem(start_raw) or _is_24h_clock(start_raw) or start > 12
    end_known = _has_meridiem(end_raw) or _is_24h_clock(end_raw) or end > 12

    if start_known and not end_known:
        if end < start and end + 12 <= 23:
            end += 12
    elif end_known and not start_known:
        if start > end and start + 12 <= 23:
            pass
        elif start < end - 12:
            start += 12
    elif not start_known and not end_known:
        if start <= 7:
            start += 12
        if end <= 7:
            end += 12

    return start % 24, end % 24


def _expand(start: int, end: int) -> list[int]:
    if start == end:
        return []
    hours: list[int] = []
    h = start
    while h != end:
        hours.append(h)
        h = (h + 1) % 24
        if len(hours) > 24:
            return []
    return sorted(set(hours))


_REDUCTION_CUE = re.compile(
    r"(reduc\w*|drop\w*|down|loss|lower\w*|cut)", re.IGNORECASE
)
_REMAINING_CUE = re.compile(
    r"(to about|to roughly|treated as|remain\w*|usable|available|leav\w*|only|at)",
    re.IGNORECASE,
)


def parse_solar_factor(text: str) -> float | None:
    t = normalize(text)

    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", t):
        value = float(m.group(1)) / 100.0
        if not 0.0 <= value <= 1.0:
            continue
        window = t[max(0, m.start() - 45) : m.end() + 25]
        reduction = bool(_REDUCTION_CUE.search(window))
        remaining = bool(_REMAINING_CUE.search(window))
        if reduction and not re.search(r"(to|as|at)\s*(about|roughly|around)?\s*$", t[: m.start()]):
            if not remaining or re.search(r"reduction|reduce\w*\s+by", window):
                return round(1.0 - value, 6)
        return round(value, 6)

    for phrase, frac in sorted(FRACTION_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            window_start = max(0, t.find(phrase) - 45)
            window = t[window_start : t.find(phrase) + len(phrase) + 25]
            if re.search(r"reduc\w*\s+by|reduction of", window):
                return round(1.0 - frac, 6)
            return round(frac, 6)

    return None


def parse_kwh_value(text: str) -> float | None:
    t = normalize(text)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kwh|kw-h|kilowatt[- ]hours?)", t)
    if m:
        return float(m.group(1))
    return None


def parse_percentage_of_capacity(text: str, capacity_kwh: float) -> float | None:
    t = normalize(text)
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:of\s+(?:the\s+)?)?"
        r"(?:battery\s+)?(?:total\s+)?(?:rated\s+)?(?:capacity|battery|storage|full)",
        t,
    )
    if m:
        return round(float(m.group(1)) / 100.0 * capacity_kwh, 6)

    for phrase, frac in FRACTION_WORDS.items():
        if re.search(
            rf"\b{re.escape(phrase)}\b\s+(?:of\s+)?(?:the\s+)?(?:battery\s+)?(?:capacity|battery|storage)",
            t,
        ):
            return round(frac * capacity_kwh, 6)
    return None


def mentions_percentage(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", normalize(text))
    return float(m.group(1)) if m else None


class Directive(BaseModel):

    model_config = ConfigDict(extra="forbid")

    note_index: int
    directive_type: DirectiveType
    hours: list[int]

    factor: float | None = None
    minimum_energy_kwh: float | None = None
    max_grid_kwh: float | None = None

    def as_adjustment(self) -> dict[str, Any]:
        if self.directive_type is DirectiveType.SOLAR_REDUCTION:
            return {"hours": self.hours, "factor": self.factor}
        if self.directive_type is DirectiveType.MINIMUM_BATTERY_RESERVE:
            return {"hours": self.hours, "minimum_energy_kwh": self.minimum_energy_kwh}
        if self.directive_type is DirectiveType.MAX_GRID_WINDOW:
            return {"hours": self.hours, "max_grid_kwh": self.max_grid_kwh}
        return {"hours": self.hours}


class InterpretationResult(BaseModel):

    model_config = ConfigDict(extra="forbid")

    entries: list[DirectiveInterpretation]

    directives: list[Directive]

    source: str

    warnings: list[str] = []


    def effective_solar(self, raw_solar: list[float]) -> list[float]:
        eff = list(raw_solar)
        for d in self.directives:
            if d.directive_type is DirectiveType.SOLAR_REDUCTION:
                for h in d.hours:
                    eff[h] = min(eff[h], raw_solar[h] * float(d.factor or 0.0))
        return eff

    def no_charge_hours(self) -> set[int]:
        return {
            h
            for d in self.directives
            if d.directive_type is DirectiveType.NO_CHARGE_WINDOW
            for h in d.hours
        }

    def no_discharge_hours(self) -> set[int]:
        return {
            h
            for d in self.directives
            if d.directive_type is DirectiveType.NO_DISCHARGE_WINDOW
            for h in d.hours
        }

    def reserve_floor(self, base_minimum_kwh: float) -> list[float]:
        floor = [float(base_minimum_kwh)] * 24
        for d in self.directives:
            if d.directive_type is DirectiveType.MINIMUM_BATTERY_RESERVE:
                for h in d.hours:
                    floor[h] = max(floor[h], float(d.minimum_energy_kwh or 0.0))
        return floor

    def grid_cap(self) -> list[float | None]:
        caps: list[float | None] = [None] * 24
        for d in self.directives:
            if d.directive_type is DirectiveType.MAX_GRID_WINDOW:
                for h in d.hours:
                    value = float(d.max_grid_kwh or 0.0)
                    caps[h] = value if caps[h] is None else min(caps[h], value)
        return caps


SYSTEM_INSTRUCTION = """\
You are a strict information-extraction component inside a campus energy \
scheduling service. You convert short natural-language notes written by \
campus electricity operators into machine-checkable directives.

You never schedule energy, never invent numbers, and never explain your \
reasoning outside the JSON you return.

## Directive types — use exactly one per note

1. solar_reduction — usable rooftop solar is reduced during specific hours.
   structured_adjustment: {"hours": [int], "factor": number}
2. minimum_battery_reserve — battery energy must stay at or above a level.
   structured_adjustment: {"hours": [int], "minimum_energy_kwh": number}
3. no_charge_window — battery charging is unavailable during specific hours.
   structured_adjustment: {"hours": [int]}
4. no_discharge_window — battery discharging is unavailable during hours.
   structured_adjustment: {"hours": [int]}
5. max_grid_window — grid import must not exceed an amount during hours.
   structured_adjustment: {"hours": [int], "max_grid_kwh": number}
6. no_op — the note does not affect today's 24-hour energy schedule.
   structured_adjustment: null

## Hard rules

- Return exactly one entry per note, in note_index order 0, 1, ... N-1.
- "applies" is true for every directive type except no_op. For no_op,
  "applies" is false and "structured_adjustment" is null.
- TIME WINDOWS ARE START-INCLUSIVE AND END-EXCLUSIVE. The end hour itself is
  NOT included.
    "1 PM to 3 PM"        -> [13, 14]
    "from 2 AM until 5 AM"-> [2, 3, 4]
    "6 PM until 10 PM"    -> [18, 19, 20, 21]
    "between 11 AM and 2 PM" -> [11, 12, 13]
    "noon until 2 PM"     -> [12, 13]
- "hours" must be unique integers from 0 to 23 in ascending order.
- For solar_reduction, "factor" is the FRACTION THAT REMAINS USABLE, not the
  amount removed.
    "output will drop to about 20%"  -> factor 0.2
    "an 80% reduction"               -> factor 0.2
    "roughly one-fifth of normal"    -> factor 0.2
    "about half of the forecast"     -> factor 0.5
    "treated as roughly 25%"         -> factor 0.25
  factor must be between 0 and 1 inclusive.
- For minimum_battery_reserve, "minimum_energy_kwh" must be an ABSOLUTE kWh
  value. If the note gives a percentage, multiply it by the battery capacity
  supplied in the scenario context. "50% of battery capacity" with a 200 kWh
  battery is 100.
- Notes about schedules, bookings, menus, deadlines, notices, office hours,
  staffing, events, or anything that does not change electricity demand,
  solar, battery, or grid limits are no_op. Do not invent an energy rule for
  them.
- Never change demand, tariff, or battery parameters. Never invent a directive
  type outside the six above.
- The same rule may be phrased many different ways; interpret the meaning, not
  the wording.

## Output format

Return ONLY a JSON object of this shape, with no prose and no code fences.
Report the adjustment as FLAT fields; downstream code assembles the final
nested object.

{"directive_interpretation": [
  {"note_index": 0, "applies": true, "directive_type": "solar_reduction",
   "hours": [13, 14], "factor": 0.2,
   "explanation": "one short sentence"}
]}

Include only the fields the directive type needs:
- solar_reduction          -> hours, factor
- minimum_battery_reserve  -> hours, minimum_energy_kwh
- no_charge_window         -> hours
- no_discharge_window      -> hours
- max_grid_window          -> hours, max_grid_kwh
- no_op                    -> no hours and no numeric fields

"explanation" is one short sentence describing the interpretation. It is not
matched word-for-word, but it must be accurate.
"""

FEW_SHOT: list[tuple[str, dict]] = [
    (
        "Solar output will drop to about 20% from 1 PM to 3 PM.",
        {
            "applies": True,
            "directive_type": "solar_reduction",
            "hours": [13, 14],
            "factor": 0.2,
            "explanation": "Usable solar falls to 20% of forecast for hours 13-14.",
        },
    ),
    (
        "Do not charge the battery between 2 PM and 4 PM.",
        {
            "applies": True,
            "directive_type": "no_charge_window",
            "hours": [14, 15],
            "explanation": "Battery charging is prohibited for hours 14-15.",
        },
    ),
    (
        "Keep at least 120 kWh in reserve from 6 PM until 9 PM.",
        {
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "hours": [18, 19, 20],
            "minimum_energy_kwh": 120,
            "explanation": "Battery must hold at least 120 kWh during hours 18-20.",
        },
    ),
    (
        "Grid import must stay at or below 150 kWh from 7 PM until 9 PM.",
        {
            "applies": True,
            "directive_type": "max_grid_window",
            "hours": [19, 20],
            "max_grid_kwh": 150,
            "explanation": "Grid import is capped at 150 kWh for hours 19-20.",
        },
    ),
    (
        "Panel washing from one until three will leave roughly one-fifth of "
        "normal solar output.",
        {
            "applies": True,
            "directive_type": "solar_reduction",
            "hours": [13, 14],
            "factor": 0.2,
            "explanation": "Only one-fifth of forecast solar is usable for hours 13-14.",
        },
    ),
    (
        "The cafeteria menu changes tomorrow.",
        {
            "applies": False,
            "directive_type": "no_op",
            "explanation": "This note does not affect today's energy schedule.",
        },
    ),
]


def build_examples_block() -> str:
    lines = ["## Worked examples", ""]
    for note, expected in FEW_SHOT:
        lines.append(f"Note: {note}")
        lines.append(f"Interpretation: {json.dumps(expected)}")
        lines.append("")
    return "\n".join(lines)


def build_user_prompt(notes: list[str], battery: BatteryProfile) -> str:
    numbered = "\n".join(f"{i}. {note}" for i, note in enumerate(notes))
    return (
        f"{build_examples_block()}\n"
        "## Scenario context\n"
        "Use these only to resolve relative quantities such as "
        "'50% of capacity'. Do not otherwise reference them.\n"
        f"- battery capacity_kwh: {battery.capacity_kwh}\n"
        f"- battery minimum_energy_kwh (base reserve): {battery.minimum_energy_kwh}\n"
        f"- planning horizon: hours 0 through 23\n\n"
        f"## Operator notes to interpret ({len(notes)} total)\n"
        f"{numbered}\n\n"
        f"Return one entry for each of the {len(notes)} notes above, in "
        "note_index order, as the JSON object described in your instructions."
    )


RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "directive_interpretation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "note_index": {"type": "integer"},
                    "applies": {"type": "boolean"},
                    "directive_type": {
                        "type": "string",
                        "enum": [
                            "solar_reduction",
                            "minimum_battery_reserve",
                            "no_charge_window",
                            "no_discharge_window",
                            "max_grid_window",
                            "no_op",
                        ],
                    },
                    "hours": {"type": "array", "items": {"type": "integer"}},
                    "factor": {"type": "number"},
                    "minimum_energy_kwh": {"type": "number"},
                    "max_grid_kwh": {"type": "number"},
                    "explanation": {"type": "string"},
                },
                "required": [
                    "note_index",
                    "applies",
                    "directive_type",
                    "explanation",
                ],
            },
        }
    },
    "required": ["directive_interpretation"],
}


NO_OP_EXPLANATION = "This note does not affect today's 24-hour energy schedule."
REJECTED_EXPLANATION = (
    "No supported energy directive could be validated from this note, so it "
    "does not change the schedule."
)


class GuardrailReport:

    def __init__(self) -> None:
        self.entries: list[DirectiveInterpretation] = []
        self.directives: list[Directive] = []
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def clean_hours(raw: Any) -> list[int] | None:
    if not isinstance(raw, (list, tuple)):
        return None
    out: set[int] = set()
    for item in raw:
        if isinstance(item, bool):
            return None
        if isinstance(item, int):
            value = item
        elif isinstance(item, float) and float(item).is_integer():
            value = int(item)
        elif isinstance(item, str) and item.strip().lstrip("-").isdigit():
            value = int(item.strip())
        else:
            return None
        if not 0 <= value <= 23:
            return None
        out.add(value)
    if not out:
        return None
    return sorted(out)


def clean_factor(raw: Any, note: str | None = None) -> float | None:
    if not _finite(raw):
        return None
    value = float(raw)
    if value > 1.0:
        if note is None or value > 100.0:
            return None
        pct = mentions_percentage(note)
        if pct is None or abs(pct - value) > 0.01:
            return None
        value = value / 100.0
    if not 0.0 <= value <= 1.0:
        return None
    return round(value, 6)


def clean_reserve(raw: Any, capacity_kwh: float) -> float | None:
    if not _finite(raw):
        return None
    value = float(raw)
    if value < 0:
        return None
    if value > capacity_kwh:
        return None
    return round(value, 6)


def clean_grid_cap(raw: Any) -> float | None:
    if not _finite(raw):
        return None
    value = float(raw)
    if value < 0:
        return None
    return round(value, 6)


def _coerce_type(raw: Any) -> DirectiveType | None:
    if not isinstance(raw, str):
        return None
    try:
        return DirectiveType(raw.strip().lower())
    except ValueError:
        return None


def repair_percentage_reserve(
    note: str, value: float, capacity_kwh: float, report: GuardrailReport, note_index: int
) -> float:
    resolved = parse_percentage_of_capacity(note, capacity_kwh)
    if resolved is None:
        return value
    pct = mentions_percentage(note)
    if pct is not None and abs(value - pct) < 0.01 and abs(resolved - value) > 0.01:
        report.warn(
            f"note {note_index}: reserve {value} looked like a raw percentage; "
            f"resolved against capacity {capacity_kwh} to {resolved}"
        )
        return resolved
    return value


def repair_hours_from_text(
    note: str, hours: list[int] | None, report: GuardrailReport, note_index: int
) -> list[int] | None:
    if hours:
        return hours
    parsed = parse_window(note)
    if parsed:
        report.warn(f"note {note_index}: hours recovered from note text -> {parsed}")
    return parsed


def _no_op_entry(note_index: int, explanation: str) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type=DirectiveType.NO_OP,
        structured_adjustment=None,
        explanation=explanation,
    )


def _explanation_of(raw: dict, fallback: str) -> str:
    text = raw.get("explanation")
    if isinstance(text, str) and text.strip():
        return text.strip()[:400]
    return fallback


def validate_entry(
    raw: dict,
    note_index: int,
    note: str,
    battery: BatteryProfile,
    report: GuardrailReport,
) -> tuple[DirectiveInterpretation, Directive | None]:
    directive_type = _coerce_type(raw.get("directive_type"))

    if directive_type is None:
        report.warn(
            f"note {note_index}: unsupported directive_type "
            f"{raw.get('directive_type')!r}; downgraded to no_op"
        )
        return _no_op_entry(note_index, REJECTED_EXPLANATION), None

    if directive_type is DirectiveType.NO_OP:
        return _no_op_entry(note_index, _explanation_of(raw, NO_OP_EXPLANATION)), None

    adjustment = raw.get("structured_adjustment")
    source: dict[str, Any] = adjustment if isinstance(adjustment, dict) else {}
    get = lambda key: source.get(key, raw.get(key))

    hours = clean_hours(get("hours"))
    hours = repair_hours_from_text(note, hours, report, note_index)
    if not hours:
        report.warn(f"note {note_index}: no valid hours for {directive_type.value}; downgraded to no_op")
        return _no_op_entry(note_index, REJECTED_EXPLANATION), None

    directive = Directive(note_index=note_index, directive_type=directive_type, hours=hours)

    if directive_type is DirectiveType.SOLAR_REDUCTION:
        factor = clean_factor(get("factor"), note)
        if factor is None:
            factor = parse_solar_factor(note)
            if factor is not None:
                report.warn(f"note {note_index}: factor recovered from note text -> {factor}")
        if factor is None:
            report.warn(f"note {note_index}: invalid solar factor; downgraded to no_op")
            return _no_op_entry(note_index, REJECTED_EXPLANATION), None
        directive.factor = factor

    elif directive_type is DirectiveType.MINIMUM_BATTERY_RESERVE:
        reserve = clean_reserve(get("minimum_energy_kwh"), battery.capacity_kwh)
        if reserve is None:
            recovered = parse_percentage_of_capacity(note, battery.capacity_kwh)
            if recovered is None:
                recovered = parse_kwh_value(note)
            reserve = clean_reserve(recovered, battery.capacity_kwh) if recovered is not None else None
            if reserve is not None:
                report.warn(f"note {note_index}: reserve recovered from note text -> {reserve}")
        if reserve is None:
            report.warn(f"note {note_index}: invalid battery reserve; downgraded to no_op")
            return _no_op_entry(note_index, REJECTED_EXPLANATION), None
        reserve = repair_percentage_reserve(note, reserve, battery.capacity_kwh, report, note_index)
        directive.minimum_energy_kwh = reserve

    elif directive_type is DirectiveType.MAX_GRID_WINDOW:
        cap = clean_grid_cap(get("max_grid_kwh"))
        if cap is None:
            recovered = parse_kwh_value(note)
            cap = clean_grid_cap(recovered) if recovered is not None else None
            if cap is not None:
                report.warn(f"note {note_index}: grid cap recovered from note text -> {cap}")
        if cap is None:
            report.warn(f"note {note_index}: invalid grid cap; downgraded to no_op")
            return _no_op_entry(note_index, REJECTED_EXPLANATION), None
        directive.max_grid_kwh = cap

    entry = DirectiveInterpretation(
        note_index=note_index,
        applies=True,
        directive_type=directive_type,
        structured_adjustment=directive.as_adjustment(),
        explanation=_explanation_of(raw, f"Applied {directive_type.value} for hours {hours}."),
    )
    return entry, directive


def validate_all(
    raw_entries: Any,
    notes: list[str],
    battery: BatteryProfile,
) -> GuardrailReport:
    report = GuardrailReport()

    by_index: dict[int, dict] = {}
    unclaimed: list[dict] = []

    if isinstance(raw_entries, dict):
        raw_entries = raw_entries.get("directive_interpretation")

    if isinstance(raw_entries, list):
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            idx = item.get("note_index")
            if isinstance(idx, bool):
                idx = None
            if isinstance(idx, float) and float(idx).is_integer():
                idx = int(idx)
            if isinstance(idx, int) and 0 <= idx < len(notes) and idx not in by_index:
                by_index[idx] = item
            else:
                unclaimed.append(item)
    else:
        report.warn("model output was not a list of entries")

    for i in range(len(notes)):
        if i not in by_index and unclaimed:
            by_index[i] = unclaimed.pop(0)
            report.warn(f"note {i}: entry had a bad note_index and was mapped positionally")

    if unclaimed:
        report.warn(f"{len(unclaimed)} extra model entries discarded")

    for i, note in enumerate(notes):
        raw = by_index.get(i)
        if raw is None:
            report.warn(f"note {i}: missing from model output; emitted no_op")
            report.entries.append(_no_op_entry(i, REJECTED_EXPLANATION))
            continue
        entry, directive = validate_entry(raw, i, note, battery, report)
        report.entries.append(entry)
        if directive is not None:
            report.directives.append(directive)

    _assert_invariants(report, len(notes))
    return report


def _assert_invariants(report: GuardrailReport, note_count: int) -> None:
    assert len(report.entries) == note_count, "one entry per note is required"
    assert [e.note_index for e in report.entries] == list(range(note_count)), (
        "entries must be in note_index order"
    )
    for entry in report.entries:
        if entry.directive_type is DirectiveType.NO_OP:
            assert entry.applies is False
            assert entry.structured_adjustment is None
        else:
            assert entry.applies is True
            assert entry.directive_type in ACTIVE_DIRECTIVE_TYPES
            adjustment = entry.structured_adjustment
            assert isinstance(adjustment, dict)
            hours = adjustment.get("hours")
            assert isinstance(hours, list) and hours == sorted(set(hours))
            assert all(isinstance(h, int) and 0 <= h <= 23 for h in hours)


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_MAX_ATTEMPTS = 2


class LLMUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str | None
    model: str
    timeout_seconds: float
    max_attempts: int

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            api_key=os.getenv("GEMINI_API_KEY") or None,
            model=os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
            timeout_seconds=float(
                os.getenv("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            ),
            max_attempts=max(1, int(os.getenv("LLM_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS))),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def extract_json(text: str) -> Any:
    if not text:
        raise LLMUnavailable("empty model response")

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise LLMUnavailable("model response was not parseable JSON")


class GeminiInterpreter:

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        self._client = None


    @property
    def available(self) -> bool:
        return self.config.configured

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.config.configured:
            raise LLMUnavailable("GEMINI_API_KEY is not set")
        try:
            from google import genai
        except ImportError as exc:
            raise LLMUnavailable("google-genai is not installed") from exc
        self._client = genai.Client(api_key=self.config.api_key)
        return self._client


    def interpret(self, notes: list[str], battery: BatteryProfile) -> list[dict]:
        client = self._get_client()
        user_prompt = build_user_prompt(notes, battery)

        last_error: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                text = self._call(client, user_prompt)
                payload = extract_json(text)
                entries = self._entries_of(payload)
                if entries:
                    return entries
                last_error = LLMUnavailable("model returned no entries")
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "gemini attempt %d/%d failed: %s",
                    attempt,
                    self.config.max_attempts,
                    type(exc).__name__,
                )

        raise LLMUnavailable(
            f"gemini interpretation failed after {self.config.max_attempts} attempts"
        ) from last_error


    def _call(self, client, user_prompt: str) -> str:
        from google.genai import types

        response = client.models.generate_content(
            model=self.config.model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                temperature=0.0,
                http_options=types.HttpOptions(
                    timeout=int(self.config.timeout_seconds * 1000)
                ),
            ),
        )
        return getattr(response, "text", "") or ""

    @staticmethod
    def _entries_of(payload: Any) -> list[dict]:
        if isinstance(payload, dict):
            candidate = payload.get("directive_interpretation")
            if isinstance(candidate, list):
                return [x for x in candidate if isinstance(x, dict)]
            if "directive_type" in payload:
                return [payload]
            return []
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        return []


CLASSIFIERS: list[tuple[DirectiveType, re.Pattern[str]]] = [
    (
        DirectiveType.NO_DISCHARGE_WINDOW,
        re.compile(
            r"(not\s+discharg\w*|no\s+discharg\w*|don'?t\s+discharg\w*|"
            r"avoid\s+discharg\w*|withhold\s+discharg\w*|refrain\s+from\s+discharg\w*|"
            r"(?:not|don'?t|never)\s+draw\s+(?:down|from)\s+the\s+battery|"
            r"battery\s+(?:may|must|can)\s+not\s+(?:supply|serve|feed|support)|"
            r"discharg\w*\b.{0,30}?\b(?:disabled|prohibited|unavailable|blocked|"
            r"not\s+allowed|suspended|isolated|offline|off)\b)"
        ),
    ),
    (
        DirectiveType.NO_CHARGE_WINDOW,
        re.compile(
            r"(not\s+charg\w*|no\s+charg\w*|don'?t\s+charg\w*|avoid\s+charg\w*|"
            r"refrain\s+from\s+charg\w*|hold\s+off\s+(?:on\s+)?charg\w*|"
            r"charg\w*\b.{0,30}?\b(?:disabled|prohibited|unavailable|blocked|"
            r"not\s+allowed|suspended|isolated|offline|off|"
            r"out\s+of\s+service)\b)"
        ),
    ),
    (
        DirectiveType.MINIMUM_BATTERY_RESERVE,
        re.compile(
            r"(keep\s+at\s+least|at\s+least\s+[\d.]+\s*(?:kwh|%|percent)|reserve|"
            r"minimum\s+(?:of\s+)?[\d.]+\s*(?:kwh|%|percent)|"
            r"minimum\s+(?:battery|energy|charge|level)|no\s+less\s+than|"
            r"(?:must|should)\s+(?:remain|stay|be\s+kept)\s+"
            r"(?:stored|in\s+the\s+battery|at\s+or\s+above|available)|"
            r"hold\s+back\s+(?:no\s+less\s+than|at\s+least)|"
            r"requires?\s+at\s+least|maintain\s+(?:at\s+least|a\s+minimum))"
        ),
    ),
    (
        DirectiveType.MAX_GRID_WINDOW,
        re.compile(
            r"(grid\s+(?:import|intake|draw|usage|supply)|import\s+from\s+the\s+grid)"
            r".{0,80}?(?:not\s+exceed|must\s+not\s+go\s+above|at\s+or\s+below|"
            r"cap\w*|limit\w*|maximum|no\s+more\s+than|below)"
            r"|(?:not\s+exceed|at\s+or\s+below|cap\w*\s+at|limited\s+to|"
            r"no\s+more\s+than).{0,80}?(?:grid|import|intake|feeder|transformer|"
            r"substation)"
            r"|(?:not|don'?t)\s+(?:import|draw|purchase|buy)\s+more\s+than"
            r"|(?:transformer|feeder|substation)\s+limit"
        ),
    ),
    (
        DirectiveType.SOLAR_REDUCTION,
        re.compile(
            r"(solar|pv|photovoltaic|panel|rooftop|inverter|cloud\s+cover|"
            r"overcast|shading)"
        ),
    ),
]

DISTRACTOR = re.compile(
    r"(menu|cafeteria|canteen|library|book[- ]return|registration|deadline|"
    r"seminar|booking|booked|notice|club|sports\s+office|student\s+affairs|"
    r"exam\s+schedule|holiday|newsletter|orientation|convocation|"
    r"next\s+(?:week|month|semester|term)|tomorrow)"
)

ENERGY_CUE = re.compile(
    r"(kwh|solar|pv|battery|charg\w*|discharg\w*|grid|tariff|feeder|"
    r"transformer|substation|reserve|import|panel|inverter)"
)


def classify_note(note: str) -> DirectiveType:
    text = normalize(note)

    if DISTRACTOR.search(text) and not ENERGY_CUE.search(text):
        return DirectiveType.NO_OP

    for directive_type, pattern in CLASSIFIERS:
        if pattern.search(text):
            return directive_type

    return DirectiveType.NO_OP


def fallback_interpret(notes: list[str], battery: BatteryProfile) -> list[dict]:
    entries: list[dict] = []

    for index, note in enumerate(notes):
        directive_type = classify_note(note)
        entry: dict = {"note_index": index, "directive_type": directive_type.value}

        if directive_type is DirectiveType.NO_OP:
            entry["applies"] = False
            entry["explanation"] = (
                "This note does not affect today's 24-hour energy schedule."
            )
            entries.append(entry)
            continue

        hours = parse_window(note)
        entry["applies"] = True
        entry["hours"] = hours or []

        if directive_type is DirectiveType.SOLAR_REDUCTION:
            entry["factor"] = parse_solar_factor(note)
            entry["explanation"] = (
                f"Usable solar is reduced to {entry['factor']} of forecast "
                f"during hours {entry['hours']}."
            )
        elif directive_type is DirectiveType.MINIMUM_BATTERY_RESERVE:
            reserve = parse_percentage_of_capacity(note, battery.capacity_kwh)
            if reserve is None:
                reserve = parse_kwh_value(note)
            entry["minimum_energy_kwh"] = reserve
            entry["explanation"] = (
                f"Battery must hold at least {reserve} kWh during hours "
                f"{entry['hours']}."
            )
        elif directive_type is DirectiveType.MAX_GRID_WINDOW:
            cap = parse_kwh_value(note)
            entry["max_grid_kwh"] = cap
            entry["explanation"] = (
                f"Grid import is capped at {cap} kWh during hours {entry['hours']}."
            )
        else:
            verb = (
                "charging"
                if directive_type is DirectiveType.NO_CHARGE_WINDOW
                else "discharging"
            )
            entry["explanation"] = (
                f"Battery {verb} is unavailable during hours {entry['hours']}."
            )

        entries.append(entry)

    return entries


logger = logging.getLogger(__name__)

_interpreter: GeminiInterpreter | None = None


def get_interpreter() -> GeminiInterpreter:
    global _interpreter
    if _interpreter is None:
        _interpreter = GeminiInterpreter()
    return _interpreter


def interpret_notes(
    notes: list[str],
    battery: BatteryProfile,
    interpreter: GeminiInterpreter | None = None,
) -> InterpretationResult:
    client = interpreter or get_interpreter()
    source = "llm"
    warnings: list[str] = []
    raw_entries: list[dict] | None = None

    if client.available:
        try:
            raw_entries = client.interpret(notes, battery)
        except LLMUnavailable as exc:
            warnings.append(f"llm unavailable, using fallback interpreter: {exc}")
            logger.warning("llm interpretation failed, falling back: %s", exc)
    else:
        warnings.append("GEMINI_API_KEY not configured, using fallback interpreter")

    if raw_entries is None:
        raw_entries = fallback_interpret(notes, battery)
        source = "fallback"

    report = validate_all(raw_entries, notes, battery)

    if source == "llm" and not report.directives:
        candidate = fallback_interpret(notes, battery)
        candidate_report = validate_all(candidate, notes, battery)
        if candidate_report.directives:
            warnings.append(
                "llm produced no valid directives; fallback reading used instead"
            )
            report = candidate_report
            source = "llm+fallback"

    return InterpretationResult(
        entries=report.entries,
        directives=report.directives,
        source=source,
        warnings=warnings + report.warnings,
    )


def interpret_scenario(
    request: ScenarioRequest, interpreter: GeminiInterpreter | None = None
) -> InterpretationResult:
    return interpret_notes(request.operator_notes, request.battery, interpreter)