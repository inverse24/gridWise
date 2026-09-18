# GridWise Problem Breakdown and Implementation Plan

## Full Problem Scenario

GridWise is an energy-optimization challenge in which the system must read a 24-hour power scenario and produce a valid schedule that minimizes electricity cost while respecting operating constraints and operator directives.

Each scenario contains a 24-hour profile for one day. For every hour, the system receives:
- `hour`: the hour index from 0 to 23
- `demand_kwh`: total electricity demand in kWh
- `solar_kwh`: forecast solar generation in kWh
- `tariff_bdt_per_kwh`: grid electricity price in BDT per kWh

The system also receives a battery profile with:
- `capacity_kwh`: maximum energy storage in the battery
- `initial_energy_kwh`: battery level at the start of the day
- `minimum_energy_kwh`: minimum allowed battery level under normal conditions
- `max_charge_kwh_per_hour`: maximum charging rate per hour
- `max_discharge_kwh_per_hour`: maximum discharging rate per hour

The user must read 1 to 3 natural-language operator notes. These notes may contain:
- real operational directives that affect schedule feasibility
- unrelated or distractor statements that should be ignored

The final challenge is not just to generate any valid schedule. The system must interpret the human notes correctly and then optimize the hourly energy flow so that total cost is minimized while all directives and physical battery rules are obeyed.

### What is being optimized?
The system decides, hour by hour, how much electricity to:
- buy from the grid
- use directly from solar
- charge the battery
- discharge the battery

The objective is:
- minimize total cost over the entire day
- maintain a feasible battery state
- satisfy all directive windows
- end the day with the battery returning to its initial level

### Core operational rules
The solution must enforce these rules:
- Each hour must satisfy the energy balance equation:
  - `grid_kwh + solar_used_kwh + battery_discharge_kwh = demand_kwh + battery_charge_kwh`
- `solar_used_kwh` cannot exceed usable solar after any reduction rule is applied.
- Battery energy must remain within feasible bounds.
- The battery cannot charge or discharge faster than the hourly limit.
- For certain windows, charging or discharging may be fully disabled.
- For certain windows, a minimum battery reserve must be preserved.
- For certain windows, grid import is capped.
- At the end of hour 23, battery energy must return to the original starting battery level.

### Allowed directive types
The notes are interpreted into a deterministic set of directive types:
- `solar_reduction`
- `minimum_battery_reserve`
- `no_charge_window`
- `no_discharge_window`
- `max_grid_window`
- `no_op`

### Important challenge detail
The problem includes distractor notes that do not affect the schedule at all. A correct solution must identify the relevant notes and ignore the irrelevant ones. This is a key pattern in the public sample cases.

### Output contract
The final answer must return:
- `scenario_id`
- `directive_interpretation`: one entry per note, in order
- `hourly_plan`: exact 24-hour schedule
- `total_grid_kwh`
- `total_cost_bdt`
- `peak_grid_kwh`
- `plan_summary`

The public sample pack shows the expected structure and semantics. These examples are intended to teach the rule interpretation and the optimization behavior required by the judge.

This challenge has three core parts. Each part is a distinct implementation layer, and the final system should connect them in order.

---

## Section 1: Input Parsing and Directive Interpretation

### Goal
Read the incoming scenario and correctly understand which operator notes matter, which ones are distractors, and how each relevant note becomes a machine-checkable directive.

### What to implement

#### 1.1 Scenario loader
- Accept the JSON payload with:
  - `scenario_id`
  - `operator_notes`
  - `hours`
  - `battery`
- Validate that the input contains all required fields.
- Ensure the 24-hour profile is complete and ordered from hour 0 to 23.

#### 1.2 Note preprocessing
For each note in `operator_notes`:
- normalize text to lowercase for matching
- remove punctuation noise where useful
- detect time-related hints such as:
  - “from 2 AM until 5 AM”
  - “from noon until 2 PM”
  - “from 6 PM until 10 PM”
  - “from 7 PM until 9 PM”
- detect keywords for directive types:
  - solar reduction: “solar”, “panel cleaning”, “cloud cover”, “reduced output”, “half of forecast”
  - no charge window: “charging disabled”, “charger unavailable”, “no charge”, “isolated charger”
  - no discharge window: “do not discharge”, “discharge disabled”, “relay testing”
  - minimum battery reserve: “keep at least”, “reserve”, “minimum energy”, “emergency services”
  - max grid window: “grid cap”, “limit grid import”, “must stay at or below”, “transformer cap”
  - distractor/no-op: “booked next week”, “moved next month”, “library hours extended”, etc.

#### 1.3 Directive mapping logic
Map each relevant note to one of the allowed directive types:
- `solar_reduction`
- `minimum_battery_reserve`
- `no_charge_window`
- `no_discharge_window`
- `max_grid_window`
- `no_op`

For each accepted directive, build a structured object like:
- `hours`: list of affected hours
- `factor`: for solar reduction
- `minimum_energy_kwh`: for reserve rule
- `max_grid_kwh`: for grid cap

#### 1.4 Deterministic interpretation output
Produce exactly one `directive_interpretation` entry per note, in note order.

Each entry must contain:
- `note_index`
- `applies`
- `directive_type`
- `structured_adjustment`
- `explanation`

Rules:
- If directive is irrelevant, set `applies = false` and `directive_type = "no_op"`.
- For any non-no_op directive, set `applies = true`.
- `structured_adjustment` must be `null` for `no_op`.
- Time windows must be unique integers from 0 to 23 in ascending order.
- For solar reduction, factor is the usable fraction left after reduction.
  - Example: “reduced to 25%” → factor `0.25`
  - Example: “half of forecast” → factor `0.5`

#### 1.5 Example of expected behavior
Example logic flow:
- Note: “Panel cleaning from noon until 2 PM; usable solar should be treated as roughly 25%.”
- Interpretation:
  - directive type = `solar_reduction`
  - hours = [12, 13]
  - factor = 0.25
  - applies = true

- Note: “The library is extending book-return hours next week.”
- Interpretation:
  - directive type = `no_op`
  - applies = false

### Implementation checklist for Section 1
- Parse note text
- Extract time windows correctly
- Classify each note
- Ignore distractor notes
- Generate structured directive objects
- Return deterministic note-order output

---

## Section 2: Energy Optimization Under Constraints

### Goal
Build the actual hourly operating schedule that minimizes cost while satisfying battery, solar, and grid conditions.

### What to implement

#### 2.1 Effective solar calculation
For each hour:
- start with raw `solar_kwh`
- if the hour is in a `solar_reduction` window, apply the factor
- the actual usable solar becomes:
  - `effective_solar = solar_kwh * factor`
- solar usage cannot exceed this effective value

#### 2.2 Battery state model
Track battery state across the day:
- `capacity_kwh`
- `initial_energy_kwh`
- `minimum_energy_kwh`
- `max_charge_kwh_per_hour`
- `max_discharge_kwh_per_hour`

For each hour, maintain:
- current battery energy before action
- battery charge amount
- battery discharge amount
- battery energy after action

Rules:
- battery energy must remain between minimum reserve and capacity
- hourly charge cannot exceed `max_charge_kwh_per_hour`
- hourly discharge cannot exceed `max_discharge_kwh_per_hour`
- at the end of hour 23, battery must equal initial energy

#### 2.3 Constraint application from directives
For every relevant directive, enforce the correct condition:

- `solar_reduction`:
  - restrict solar use in those hours to `solar_kwh * factor`

- `no_charge_window`:
  - battery charge must be zero in those hours

- `no_discharge_window`:
  - battery discharge must be zero in those hours

- `minimum_battery_reserve`:
  - enforce `battery_energy_after_kwh >= minimum_energy_kwh` in the listed hours

- `max_grid_window`:
  - enforce `grid_kwh <= max_grid_kwh` in the listed hours

#### 2.4 Hourly energy balance equation
For each hour, satisfy:

`grid_kwh + solar_used_kwh + battery_discharge_kwh = demand_kwh + battery_charge_kwh`

This is the fundamental feasibility equation.

#### 2.5 Objective function
Minimize total cost:

`sum(hourly_grid_kwh * tariff_bdt_per_kwh)`

Also compute:
- `total_grid_kwh`
- `total_cost_bdt`
- `peak_grid_kwh`

#### 2.6 Optimization approach
Use one of the following robust techniques:

1. Dynamic programming over battery energy states
2. Linear programming / integer programming with a solver
3. A custom greedy strategy if the problem is narrow and fully understood

Best practical choice for a contest solution:
- dynamic programming for a discretized battery state
- or LP solver if allowed in the environment

#### 2.7 Valid schedule generation
For each hour, output:
- `hour`
- `grid_kwh`
- `solar_used_kwh`
- `battery_action` in {`charge`, `discharge`, `idle`}
- `battery_kwh`
- `battery_energy_after_kwh`

`battery_kwh` should be:
- positive for charging/discharging
- `0` for idle

#### 2.8 Example of logic flow
At each hour:
1. compute effective solar
2. determine whether a directive forbids charging or discharging
3. choose battery action that helps reduce cost
4. ensure battery stays within feasible operating range
5. keep end-of-day battery level equal to initial battery level

### Implementation checklist for Section 2
- Apply effective solar reductions
- Enforce windows and min/max battery constraints
- Model hourly power balance
- Optimize for minimum cost
- Track final battery neutrality
- Produce valid 24-hour schedule

---

## Section 3: Output Validation, Aggregation, and Final Response

### Goal
Turn the optimized schedule into the exact API output shape expected by the judge and validate that it is mathematically correct before returning it.

### What to implement

#### 3.1 Hour-level validation
For each of the 24 hours, verify:
- hour values are exactly 0 through 23, once each
- `grid_kwh`, `solar_used_kwh`, and `battery_kwh` are numeric
- `battery_action` is one of:
  - `charge`
  - `discharge`
  - `idle`
- `battery_kwh` is `0` when action is `idle`
- `solar_used_kwh <= effective_solar`
- charge/discharge rate limits are respected
- battery_energy_after_kwh stays within allowed range

#### 3.2 Global validation
Check that:
- final battery energy equals initial energy
- total grid import matches the sum of hourly grid values
- peak grid equals highest hourly grid value
- total cost matches grid usage × tariff schedule
- the schedule is feasible under all directives

#### 3.3 Response structure
Return the final object with:
- `scenario_id`
- `directive_interpretation`
- `hourly_plan`
- `total_grid_kwh`
- `total_cost_bdt`
- `peak_grid_kwh`
- `plan_summary`

#### 3.4 Response summary text
Generate a short human-readable summary that explains:
- what relevant directives were applied
- what irrelevant notes were ignored
- how the battery was used to reduce cost
- how the final schedule restored the battery state

#### 3.5 Deterministic compliance
The output must be deterministic and judge-friendly:
- note interpretation order must follow note order
- hour order must be ascending
- no extra fields or mismatched types
- enums must match allowed values exactly

#### 3.6 Pre-submission verification checklist
Before sending final output, verify:
- exactly one interpretation per note
- no invalid directive combinations
- all hours satisfy the energy equation
- all battery rules are satisfied
- no hidden note was misinterpreted
- final cost and totals tie to the actual plan

### Implementation checklist for Section 3
- Validate every hour
- Validate the full 24-hour plan
- Check totals and maxima
- Build final JSON response
- Write plan summary
- Ensure judge compliance

---

## Recommended project flow

1. Section 1: parse notes and generate directives
2. Section 2: optimize energy schedule using those directives
3. Section 3: validate all constraints and return final output

This is the cleanest decomposition because each section has a separate job and can be tested independently.

## Practical design note
The most important engineering mindset is:
- Section 1 handles semantic interpretation
- Section 2 handles optimization
- Section 3 handles correctness verification

If any one of these is weak, the final answer will fail even if the others work well.
