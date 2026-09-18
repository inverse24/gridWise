from __future__ import annotations

import json
import pathlib

import pytest

from schemas import BatteryProfile, DirectiveType, ScenarioRequest
from section1 import (
    GeminiInterpreter,
    InterpretationResult,
    LLMConfig,
    LLMUnavailable,
    clean_hours,
    extract_json,
    interpret_notes,
)

_SAMPLE_NAMES = (
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json",
    "samples.json",
)


def _find_samples() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve().parent
    for folder in (here, here.parent, here / "tests", here.parent / "tests"):
        for name in _SAMPLE_NAMES:
            candidate = folder / name
            if candidate.exists():
                return candidate
    raise FileNotFoundError(
        "Public sample pack not found. Put "
        "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json next to this file."
    )


CASES = json.loads(_find_samples().read_text())["cases"]

BATTERY = BatteryProfile(
    capacity_kwh=200,
    initial_energy_kwh=100,
    minimum_energy_kwh=40,
    max_charge_kwh_per_hour=50,
    max_discharge_kwh_per_hour=50,
)

PARAPHRASES = [
    ("PV production will drop to about 20% between 13:00 and 15:00.",
     "solar_reduction", {"hours": [13, 14], "factor": 0.2}),
    ("Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.",
     "solar_reduction", {"hours": [13, 14], "factor": 0.2}),
    ("Panel washing from one until three will leave roughly one-fifth of normal solar output.",
     "solar_reduction", {"hours": [13, 14], "factor": 0.2}),
    ("Heavy overcast from 9 AM until noon should cut usable photovoltaic output to 40 percent.",
     "solar_reduction", {"hours": [9, 10, 11], "factor": 0.4}),
    ("Inverter servicing between 10 AM and 1 PM means only a quarter of forecast solar is usable.",
     "solar_reduction", {"hours": [10, 11, 12], "factor": 0.25}),
    ("The charging contactor is out of service from 3 AM until 6 AM.",
     "no_charge_window", {"hours": [3, 4, 5]}),
    ("Please refrain from charging the battery between 9 PM and 11 PM.",
     "no_charge_window", {"hours": [21, 22]}),
    ("Battery charging is suspended from 11 AM until 2 PM for switchgear work.",
     "no_charge_window", {"hours": [11, 12, 13]}),
    ("Discharging is prohibited from 7 PM until 10 PM while protection relays are tested.",
     "no_discharge_window", {"hours": [19, 20, 21]}),
    ("The battery may not supply load between 4 PM and 6 PM.",
     "no_discharge_window", {"hours": [16, 17]}),
    ("Do not draw down the battery from 8 PM until 11 PM.",
     "no_discharge_window", {"hours": [20, 21, 22]}),
    ("A minimum of 75 kWh must remain stored from 5 PM until 8 PM for critical loads.",
     "minimum_battery_reserve", {"hours": [17, 18, 19], "minimum_energy_kwh": 75}),
    ("Hold back no less than 140 kWh in the battery between 6 PM and 11 PM.",
     "minimum_battery_reserve", {"hours": [18, 19, 20, 21, 22], "minimum_energy_kwh": 140}),
    ("Maintain at least 25% of the battery capacity from 9 PM until midnight.",
     "minimum_battery_reserve", {"hours": [21, 22, 23], "minimum_energy_kwh": 50}),
    ("Grid intake must stay at or below 160 kWh from 6 PM until 9 PM.",
     "max_grid_window", {"hours": [18, 19, 20], "max_grid_kwh": 160}),
    ("Because of substation work, do not import more than 120 kWh per hour between 7 PM and 10 PM.",
     "max_grid_window", {"hours": [19, 20, 21], "max_grid_kwh": 120}),
    ("The feeder is limited to 200 kWh of grid import from 8 PM until 11 PM.",
     "max_grid_window", {"hours": [20, 21, 22], "max_grid_kwh": 200}),
    ("The departmental seminar has been rescheduled to next Thursday.", "no_op", None),
    ("Hostel mess timings will change after the semester break.", "no_op", None),
    ("Please note that the transport office will issue new bus passes on Sunday.", "no_op", None),
    ("A fire drill is scheduled for the administrative building tomorrow morning.", "no_op", None),
]


class _Stub(GeminiInterpreter):
    def __init__(self, entries, available=True):
        super().__init__(LLMConfig(
            api_key="stub" if available else None,
            model="stub", timeout_seconds=1, max_attempts=1,
        ))
        self._entries = entries

    @property
    def available(self):
        return self.config.api_key is not None

    def interpret(self, notes, battery):
        if self._entries is None:
            raise LLMUnavailable("stub failure")
        return self._entries


NO_LLM = _Stub(None, available=False)


def _request(case):
    return ScenarioRequest.model_validate(case["input"])


def _expected(case):
    return case["expected_output"]["directive_interpretation"]


def _pairs(entries):
    return [(e.directive_type.value, e.structured_adjustment) for e in entries]


def _expected_pairs(case):
    return [(e["directive_type"], e["structured_adjustment"]) for e in _expected(case)]


def _assert_contract(result: InterpretationResult, note_count: int) -> None:
    assert len(result.entries) == note_count
    assert [e.note_index for e in result.entries] == list(range(note_count))
    for entry in result.entries:
        if entry.directive_type is DirectiveType.NO_OP:
            assert entry.applies is False
            assert entry.structured_adjustment is None
        else:
            assert entry.applies is True
            assert isinstance(entry.structured_adjustment, dict)
            hours = entry.structured_adjustment["hours"]
            assert hours == sorted(set(hours))
            assert all(isinstance(h, int) and 0 <= h <= 23 for h in hours)
        assert entry.explanation.strip()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_sample_request_parses(case):
    request = _request(case)
    assert [h.hour for h in request.hours_sorted()] == list(range(24))
    assert 1 <= len(request.operator_notes) <= 3


def test_missing_hour_rejected():
    bad = json.loads(json.dumps(CASES[0]["input"]))
    bad["hours"] = bad["hours"][:23]
    with pytest.raises(Exception):
        ScenarioRequest.model_validate(bad)


def test_duplicate_hour_rejected():
    bad = json.loads(json.dumps(CASES[0]["input"]))
    bad["hours"][5]["hour"] = 4
    with pytest.raises(Exception):
        ScenarioRequest.model_validate(bad)


def test_blank_note_rejected():
    bad = json.loads(json.dumps(CASES[0]["input"]))
    bad["operator_notes"] = ["   "]
    with pytest.raises(Exception):
        ScenarioRequest.model_validate(bad)


def test_four_notes_rejected():
    bad = json.loads(json.dumps(CASES[0]["input"]))
    bad["operator_notes"] = ["a", "b", "c", "d"]
    with pytest.raises(Exception):
        ScenarioRequest.model_validate(bad)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_contract_holds_without_llm(case):
    request = _request(case)
    result = interpret_notes(request.operator_notes, request.battery, NO_LLM)
    _assert_contract(result, len(request.operator_notes))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_reference_interpretation_survives_guardrails(case):
    request = _request(case)
    entries = [dict(e) for e in _expected(case)]
    result = interpret_notes(request.operator_notes, request.battery, _Stub(entries))
    _assert_contract(result, len(request.operator_notes))
    assert [e.model_dump(mode="json") for e in result.entries] == [dict(e) for e in _expected(case)]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_fallback_matches_reference(case):
    request = _request(case)
    result = interpret_notes(request.operator_notes, request.battery, NO_LLM)
    assert _pairs(result.entries) == _expected_pairs(case)


@pytest.mark.parametrize("raw", [
    {"note_index": 0, "directive_type": "make_it_cheaper", "hours": [1]},
    {"note_index": 0, "directive_type": "solar_reduction", "hours": [99], "factor": 0.2},
    {"note_index": 0, "directive_type": "solar_reduction", "hours": [1], "factor": 4},
    {"note_index": 0, "directive_type": "max_grid_window", "hours": [1], "max_grid_kwh": -5},
    {"note_index": 0, "directive_type": "minimum_battery_reserve", "hours": [1], "minimum_energy_kwh": 9999},
    {"note_index": 0, "directive_type": "solar_reduction", "hours": "13,14", "factor": 0.2},
    {"note_index": 0, "directive_type": "solar_reduction", "hours": [1], "factor": None},
])
def test_bad_model_output_never_reaches_optimizer(raw):
    result = interpret_notes(["The seminar room booking moved to next week."], BATTERY, _Stub([raw]))
    _assert_contract(result, 1)
    assert result.directives == []
    assert result.entries[0].directive_type is DirectiveType.NO_OP


def test_missing_entry_filled_with_no_op():
    notes = ["Do not charge the battery from 2 PM until 4 PM.", "Menu changes tomorrow."]
    result = interpret_notes(notes, BATTERY, _Stub([
        {"note_index": 0, "directive_type": "no_charge_window", "hours": [14, 15]},
    ]))
    _assert_contract(result, 2)
    assert result.entries[1].directive_type is DirectiveType.NO_OP


def test_duplicate_note_index_remapped():
    notes = ["Do not charge from 2 PM until 4 PM.", "Do not discharge from 6 PM until 8 PM."]
    result = interpret_notes(notes, BATTERY, _Stub([
        {"note_index": 0, "directive_type": "no_charge_window", "hours": [14, 15]},
        {"note_index": 0, "directive_type": "no_discharge_window", "hours": [18, 19]},
    ]))
    _assert_contract(result, 2)
    assert result.entries[1].directive_type is DirectiveType.NO_DISCHARGE_WINDOW


def test_percentage_reserve_resolved_against_capacity():
    result = interpret_notes(
        ["Keep at least 50% of the battery capacity from 6 PM until 9 PM."],
        BATTERY,
        _Stub([{"note_index": 0, "directive_type": "minimum_battery_reserve",
                "hours": [18, 19, 20], "minimum_energy_kwh": 50}]),
    )
    assert result.entries[0].structured_adjustment["minimum_energy_kwh"] == 100.0


def test_factor_as_percentage_normalized():
    result = interpret_notes(
        ["Solar drops to 25% from noon until 2 PM."],
        BATTERY,
        _Stub([{"note_index": 0, "directive_type": "solar_reduction",
                "hours": [12, 13], "factor": 25}]),
    )
    assert result.entries[0].structured_adjustment["factor"] == 0.25


def test_clean_hours():
    assert clean_hours([14, 13, 13]) == [13, 14]
    assert clean_hours([]) is None
    assert clean_hours([24]) is None
    assert clean_hours([True]) is None
    assert clean_hours("13,14") is None


def test_optimizer_handoff_helpers():
    notes = [
        "Solar drops to 20% from 1 PM until 3 PM.",
        "Do not charge from 2 PM until 4 PM.",
        "Keep at least 80 kWh from 6 PM until 8 PM.",
    ]
    result = interpret_notes(notes, BATTERY, _Stub([
        {"note_index": 0, "directive_type": "solar_reduction", "hours": [13, 14], "factor": 0.2},
        {"note_index": 1, "directive_type": "no_charge_window", "hours": [14, 15]},
        {"note_index": 2, "directive_type": "minimum_battery_reserve", "hours": [18, 19], "minimum_energy_kwh": 80},
    ]))
    eff = result.effective_solar([100.0] * 24)
    assert eff[13] == pytest.approx(20.0)
    assert eff[12] == pytest.approx(100.0)
    assert result.no_charge_hours() == {14, 15}
    assert result.no_discharge_hours() == set()
    floor = result.reserve_floor(BATTERY.minimum_energy_kwh)
    assert floor[18] == 80
    assert floor[0] == 40
    assert result.grid_cap()[18] is None


def test_tighter_grid_cap_wins():
    notes = ["Cap grid at 200 kWh from 7 PM until 9 PM.", "Cap grid at 150 kWh from 7 PM until 8 PM."]
    result = interpret_notes(notes, BATTERY, _Stub([
        {"note_index": 0, "directive_type": "max_grid_window", "hours": [19, 20], "max_grid_kwh": 200},
        {"note_index": 1, "directive_type": "max_grid_window", "hours": [19], "max_grid_kwh": 150},
    ]))
    caps = result.grid_cap()
    assert caps[19] == 150
    assert caps[20] == 200


@pytest.mark.parametrize("text", [
    '{"directive_interpretation": []}',
    '```json\n{"directive_interpretation": []}\n```',
    'Here you go:\n{"directive_interpretation": []}\nHope that helps.',
])
def test_extract_json_tolerates_wrapping(text):
    assert extract_json(text) == {"directive_interpretation": []}


def test_fallback_paraphrase_coverage(capsys):
    misses = []
    for note, want_type, want_adj in PARAPHRASES:
        result = interpret_notes([note], BATTERY, NO_LLM)
        got_type, got_adj = _pairs(result.entries)[0]
        if got_type != want_type or got_adj != want_adj:
            misses.append(f"  {note}\n    want {want_type} {want_adj}\n    got  {got_type} {got_adj}")
    hit = len(PARAPHRASES) - len(misses)
    with capsys.disabled():
        print(f"\nfallback paraphrase coverage: {hit}/{len(PARAPHRASES)}")
        if misses:
            print("\n".join(misses))
    assert hit >= len(PARAPHRASES) // 2


@pytest.mark.llm
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_live_llm_matches_reference(case):
    if not GeminiInterpreter().available:
        pytest.skip("GEMINI_API_KEY not set")
    request = _request(case)
    result = interpret_notes(request.operator_notes, request.battery)
    _assert_contract(result, len(request.operator_notes))
    assert _pairs(result.entries) == _expected_pairs(case), f"{case['id']} warnings={result.warnings}"


@pytest.mark.llm
@pytest.mark.parametrize("item", PARAPHRASES, ids=[p[1] + ":" + p[0][:30] for p in PARAPHRASES])
def test_live_llm_handles_paraphrase(item):
    if not GeminiInterpreter().available:
        pytest.skip("GEMINI_API_KEY not set")
    note, want_type, want_adj = item
    result = interpret_notes([note], BATTERY)
    got_type, got_adj = _pairs(result.entries)[0]
    assert got_type == want_type, note
    assert got_adj == want_adj, note


def test_overlapping_solar_reductions_take_the_tightest():
    result = interpret_notes(["a", "b"], BATTERY, _Stub([
        {"note_index": 0, "directive_type": "solar_reduction", "hours": [12, 13], "factor": 0.5},
        {"note_index": 1, "directive_type": "solar_reduction", "hours": [13, 14], "factor": 0.2},
    ]))
    eff = result.effective_solar([100.0] * 24)
    assert eff[11] == pytest.approx(100.0)
    assert eff[12] == pytest.approx(50.0)
    assert eff[13] == pytest.approx(20.0)
    assert eff[14] == pytest.approx(20.0)


def test_overlapping_solar_reductions_order_independent():
    forward = [
        {"note_index": 0, "directive_type": "solar_reduction", "hours": [13], "factor": 0.5},
        {"note_index": 1, "directive_type": "solar_reduction", "hours": [13], "factor": 0.2},
    ]
    backward = [
        {"note_index": 0, "directive_type": "solar_reduction", "hours": [13], "factor": 0.2},
        {"note_index": 1, "directive_type": "solar_reduction", "hours": [13], "factor": 0.5},
    ]
    a = interpret_notes(["a", "b"], BATTERY, _Stub(forward)).effective_solar([100.0] * 24)
    b = interpret_notes(["a", "b"], BATTERY, _Stub(backward)).effective_solar([100.0] * 24)
    assert a[13] == b[13] == pytest.approx(20.0)


def test_overlapping_reserves_take_the_highest():
    result = interpret_notes(["a", "b"], BATTERY, _Stub([
        {"note_index": 0, "directive_type": "minimum_battery_reserve", "hours": [18, 19], "minimum_energy_kwh": 60},
        {"note_index": 1, "directive_type": "minimum_battery_reserve", "hours": [19, 20], "minimum_energy_kwh": 90},
    ]))
    floor = result.reserve_floor(BATTERY.minimum_energy_kwh)
    assert floor[18] == 60
    assert floor[19] == 90
    assert floor[20] == 90
    assert floor[21] == BATTERY.minimum_energy_kwh


def test_overlapping_windows_union():
    result = interpret_notes(["a", "b"], BATTERY, _Stub([
        {"note_index": 0, "directive_type": "no_charge_window", "hours": [10, 11]},
        {"note_index": 1, "directive_type": "no_charge_window", "hours": [11, 12]},
    ]))
    assert result.no_charge_hours() == {10, 11, 12}


@pytest.mark.parametrize("text,expected", [
    ("Solar drops from 1 PM to 3 PM.", [13, 14]),
    ("Solar drops from 1 pm to 3 pm.", [13, 14]),
    ("Solar drops from 1 P.M. until 3 P.M.", [13, 14]),
    ("Work runs from 2 AM until 5 AM.", [2, 3, 4]),
    ("Outage from 6 PM until 10 PM.", [18, 19, 20, 21]),
    ("Cloud cover between 11 AM and 2 PM.", [11, 12, 13]),
    ("Cleaning from noon until 2 PM.", [12, 13]),
    ("Servicing from 10 PM to midnight.", [22, 23]),
    ("Maintenance from 11 PM until 2 AM.", [0, 1, 23]),
    ("Reduced output between 13:00 and 15:00.", [13, 14]),
    ("Window from 09:00 until 12:00.", [9, 10, 11]),
    ("Panel wash from one until three.", [13, 14]),
    ("Testing from 6 PM through 8 PM.", [18, 19]),
    ("Limit applies 18:00-21:00.", [18, 19, 20]),
    ("Inspection from noon until 1 PM.", [12]),
    ("Overnight work from 10 PM until 1 AM.", [0, 22, 23]),
    ("Grid cap starting at 7 PM until 9 PM.", [19, 20]),
])
def test_parse_window(text, expected):
    from section1 import parse_window
    assert parse_window(text) == expected


@pytest.mark.parametrize("text", [
    "The schedule is unchanged today.",
    "Solar will be low all day.",
    "Maintenance from 6 PM until 6 PM.",
])
def test_parse_window_returns_none_when_no_usable_range(text):
    from section1 import parse_window
    assert parse_window(text) is None


@pytest.mark.parametrize("text,expected", [
    ("Solar will drop to about 20% from 1 PM to 3 PM.", 0.2),
    ("Expect an 80% reduction in solar.", 0.2),
    ("Usable solar should be treated as roughly 25%.", 0.25),
    ("Only half of the forecast will be available.", 0.5),
    ("Roughly one-fifth of normal output.", 0.2),
    ("Output falls to 40 percent.", 0.4),
    ("A quarter of forecast solar is usable.", 0.25),
])
def test_parse_solar_factor(text, expected):
    from section1 import parse_solar_factor
    assert parse_solar_factor(text) == pytest.approx(expected)


def test_parse_percentage_of_capacity():
    from section1 import parse_percentage_of_capacity
    assert parse_percentage_of_capacity("Keep 50% of the battery capacity.", 200) == 100
    assert parse_percentage_of_capacity("Keep a quarter of the battery capacity.", 200) == 50
    assert parse_percentage_of_capacity("Keep 90 kWh in reserve.", 200) is None


@pytest.mark.parametrize("payload", [
    {"directive_interpretation": []},
    [],
    {"unexpected": "shape"},
    "not json at all",
    None,
])
def test_unusable_model_payload_falls_back(payload):
    class _Weird(_Stub):
        def interpret(self, notes, battery):
            entries = GeminiInterpreter._entries_of(payload)
            if not entries:
                raise LLMUnavailable("no entries")
            return entries

    notes = ["Do not charge the battery from 2 PM until 4 PM."]
    result = interpret_notes(notes, BATTERY, _Weird([]))
    _assert_contract(result, 1)
    assert result.entries[0].directive_type is DirectiveType.NO_CHARGE_WINDOW
    assert result.source == "fallback"


def test_llm_retries_then_succeeds():
    calls = {"n": 0}

    class _Flaky(GeminiInterpreter):
        def __init__(self):
            super().__init__(LLMConfig(api_key="x", model="stub", timeout_seconds=1, max_attempts=2))

        @property
        def available(self):
            return True

        def _get_client(self):
            return object()

        def _call(self, client, user_prompt):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient provider error")
            return '{"directive_interpretation": [{"note_index": 0, "applies": true, ' \
                   '"directive_type": "no_charge_window", "hours": [14, 15], ' \
                   '"explanation": "ok"}]}'

    result = interpret_notes(["Do not charge from 2 PM until 4 PM."], BATTERY, _Flaky())
    assert calls["n"] == 2
    assert result.source == "llm"
    assert result.entries[0].directive_type is DirectiveType.NO_CHARGE_WINDOW


def test_llm_exhausts_retries_then_falls_back():
    calls = {"n": 0}

    class _Dead(GeminiInterpreter):
        def __init__(self):
            super().__init__(LLMConfig(api_key="x", model="stub", timeout_seconds=1, max_attempts=2))

        @property
        def available(self):
            return True

        def _get_client(self):
            return object()

        def _call(self, client, user_prompt):
            calls["n"] += 1
            raise RuntimeError("provider down")

    result = interpret_notes(["Do not charge from 2 PM until 4 PM."], BATTERY, _Dead())
    assert calls["n"] == 2
    assert result.source == "fallback"
    _assert_contract(result, 1)


def test_all_distractor_notes_produce_no_directives():
    notes = [
        "The seminar has been rescheduled.",
        "Hostel mess timings change next week.",
        "New bus passes will be issued on Sunday.",
    ]
    result = interpret_notes(notes, BATTERY, _Stub([
        {"note_index": i, "applies": False, "directive_type": "no_op",
         "structured_adjustment": None, "explanation": "unrelated"}
        for i in range(3)
    ]))
    _assert_contract(result, 3)
    assert result.directives == []
    assert result.effective_solar([100.0] * 24) == [100.0] * 24
    assert result.grid_cap() == [None] * 24


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_repeated_calls_are_deterministic(case):
    request = _request(case)
    first = interpret_notes(request.operator_notes, request.battery, NO_LLM)
    second = interpret_notes(request.operator_notes, request.battery, NO_LLM)
    assert [e.model_dump(mode="json") for e in first.entries] == \
           [e.model_dump(mode="json") for e in second.entries]


def test_interpretation_without_llm_is_fast():
    import time
    request = _request(CASES[-1])
    start = time.perf_counter()
    for _ in range(50):
        interpret_notes(request.operator_notes, request.battery, NO_LLM)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"50 interpretations took {elapsed:.2f}s"


def test_long_and_messy_note_does_not_crash():
    notes = ["Please note:\n\n" + ("context " * 400) + "\nDo not charge from 2 PM until 4 PM."]
    result = interpret_notes(notes, BATTERY, _Stub(None, available=False))
    _assert_contract(result, 1)


@pytest.mark.llm
def test_live_llm_latency_is_within_budget():
    import time
    if not GeminiInterpreter().available:
        pytest.skip("GEMINI_API_KEY not set")
    request = _request(CASES[-1])
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        interpret_notes(request.operator_notes, request.battery)
        timings.append(time.perf_counter() - start)
    worst = max(timings)
    print(f"\nlive interpretation timings: {[f'{t:.2f}s' for t in timings]}")
    assert worst < 15.0, f"slowest call {worst:.2f}s — p95 latency points at risk"


@pytest.mark.llm
def test_live_llm_is_deterministic():
    if not GeminiInterpreter().available:
        pytest.skip("GEMINI_API_KEY not set")
    request = _request(CASES[5])
    runs = [
        _pairs(interpret_notes(request.operator_notes, request.battery).entries)
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2], f"non-deterministic across runs: {runs}"