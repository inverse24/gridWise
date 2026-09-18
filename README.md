# GridWise — Smart Campus Energy Optimization

LLM-assisted operator-directive interpretation and 24-hour energy scheduling.
BUP CSE Fest 2026 · Hackathon Online Preliminary.

The service reads a 24-hour campus energy scenario plus 1–3 natural-language
operator notes, interprets those notes with a language model into structured
directives, validates them deterministically, and returns a cost-minimal
24-hour schedule that satisfies every applicable directive.

---

## Submission references

| Item | Value |
|---|---|
| Live endpoint | `https://gridwise-1.onrender.com` |
| Docker image | `ghcr.io/inverse24/gridwise:latest` |
| Image digest | `TODO: sha256:...` (from the GitHub Actions run log) |
| Repository | `https://github.com/inverse24/gridWise` |

> **Please wake the service before testing.** It is hosted on a free tier that
> suspends the instance after a period of inactivity. The first request after an
> idle period can take up to 50 seconds while the instance starts; every request
> after that responds in about a second. Send a `GET /health` first and wait for
> it to return before timing anything else.

```bash
curl https://gridwise-1.onrender.com/health
# {"status":"ok"}   <- may take up to 50s on the very first call

curl -X POST https://gridwise-1.onrender.com/optimize-energy \
  -H 'content-type: application/json' \
  -d @sample01.json
```

---

## Quickstart from a clean environment

```bash
git clone https://github.com/inverse24/gridWise.git
cd gridWise

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env               # Windows: copy .env.example .env
# open .env and set GEMINI_API_KEY to your Google AI Studio key

uvicorn main:app --host 0.0.0.0 --port 8000
```

### Check readiness

```bash
curl http://localhost:8000/health
```

```json
{"status":"ok"}
```

### Run a public sample case

`sample01.json` in this repository is the `input` object of SAMPLE-01 from the
public sample pack, ready to POST.

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H 'content-type: application/json' \
  -d @sample01.json
```

Expected: HTTP 200 with `scenario_id`, `directive_interpretation`,
`hourly_plan` (24 entries), `total_grid_kwh`, `total_cost_bdt`,
`peak_grid_kwh` and `plan_summary`.

For SAMPLE-01 the totals are `total_grid_kwh: 2692.5` and
`total_cost_bdt: 38365.0`, matching the reference cost in the public pack.
`peak_grid_kwh` may differ from the reference schedule — equivalent optimal
schedules are accepted, and the judge recalculates totals from the returned
`hourly_plan`.

To run any other case, extract its `input` object:

```bash
python -c "import json; d=json.load(open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json')); json.dump(d['cases'][5]['input'], open('case.json','w'), indent=2)"
```

---

## Environment variables

Names only — no values are committed anywhere in this repository.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | yes | — | Google AI Studio key for operator-note interpretation |
| `GEMINI_MODEL` | no | `gemini-3.5-flash-lite` | Model identifier |
| `LLM_TIMEOUT_SECONDS` | no | `12` | Per-call timeout, kept well under the 30 s request budget |
| `LLM_MAX_ATTEMPTS` | no | `2` | Retries before the safe fallback engages |
| `LOG_LEVEL` | no | `INFO` | Log verbosity |
| `PORT` | no | `8000` | Listen port |

`.env` is loaded automatically at startup when present. In Docker and on a
hosting platform the same variables are supplied as real environment
variables instead; no code change is needed.

---

## Docker fallback

The image is built and published automatically by GitHub Actions on every push
to `main` (`.github/workflows/docker-image.yml`), which also starts the image
and verifies `/health` before the run is marked successful.

```bash
docker pull ghcr.io/inverse24/gridwise:latest

docker run --rm -p 8000:8000 \
  -e GEMINI_API_KEY=<your key> \
  -e GEMINI_MODEL=gemini-3.5-flash-lite \
  ghcr.io/inverse24/gridwise:latest

curl http://localhost:8000/health
```

To build it locally instead:

```bash
docker build -t gridwise:local .
docker run --rm -p 8000:8000 --env-file .env gridwise:local
```

The image binds `0.0.0.0`, exposes port `8000` (override with `-e PORT=...`),
and contains no baked-in credentials — `.env` is excluded by `.dockerignore`.

---

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `200 {"status":"ok"}` |
| POST | `/optimize-energy` | Judged endpoint |
| GET | `/docs` | OpenAPI UI (FastAPI default) |

Error codes: `400` for malformed or structurally invalid requests, `422` for a
well-formed scenario with no feasible schedule, `500` for a controlled internal
error. Error bodies never contain secrets or stack traces.

---

## Architecture

```
POST /optimize-energy
        |
        v
  request validation         schemas.py
        |                    exact 24-hour + operator-note schema
        v
  LLM interpretation         section1.py, LLM CLIENT section   <-- MANDATORY step
        |                    Gemini, temperature 0, structured JSON output
        v
  deterministic guardrails   section1.py, GUARDRAILS section
        |                    allowed types, note mapping, hours 0-23 unique
        |                    ascending, factor in [0,1], reserve <= capacity,
        |                    applies semantics
        v
  validated directives       section1.py, DIRECTIVE MODELS section
        |
        v
  optimizer                  gridwise_section_02_optimizer.py
        |                    dynamic program over battery energy states
        v
  replay + validation        gridwise_section_03_validator.py
        |                    independent re-check of the finished plan
        v
  JSON response              main.py
```

**Model and provider:** Google Gemini (`gemini-3.5-flash-lite` by default) via
the official `google-genai` SDK.

**LLM role:** the model reads each `operator_notes` string and emits the
structured directive — type, affected hours and numeric value — that becomes an
optimizer constraint. It is not used for cosmetic text; `plan_summary` is
generated deterministically from the finished plan.

**Guardrails:** model output is treated as untrusted structured data. Each entry
is validated field by field; anything unusable is either repaired from the note
text where the specification makes that machine-checkable, or downgraded to
`no_op`. An unsupported directive type is never invented, and malformed model
output never reaches the optimizer.

**Safe fallback:** if the provider is unreachable, times out, or returns
unparseable output, a conservative rule-based reading passes through the same
guardrails so the service returns a controlled result instead of a 5xx. This is
a crash guard only — the language model is the interpretation path.

**Optimizer:** a dynamic program over discretized battery energy states. Each
hour's transition cost depends only on the hour and the energy delta, so costs
are computed once per hour and applied with vectorized NumPy operations; a full
24-hour scenario solves in roughly 200 ms. The step size is inferred from the
scenario so integer and fractional inputs are both handled exactly.

**Validation:** the finished plan is replayed independently before it is
returned — energy balance, effective solar, battery transitions and bounds,
rate limits, reserve floors, grid caps, and end-of-day neutrality. Totals are
recomputed from `hourly_plan` rather than carried from the solver.

---

## Directive conventions

| Convention | Rule |
|---|---|
| Time windows | Start inclusive, end exclusive. "1 PM to 3 PM" → `[13, 14]` |
| Solar factor | The fraction **remaining usable**. "80% reduction" → `0.2` |
| Relative reserve | Converted to absolute kWh against `capacity_kwh`. "50% of capacity" on a 200 kWh battery → `100` |
| Distractor notes | `applies: false`, `directive_type: "no_op"`, `structured_adjustment: null` |
| Entry order | Exactly one entry per note, in `note_index` order `0..N-1` |
| Overlapping directives | The tighter constraint wins — lowest solar factor, highest reserve, lowest grid cap |

---

## Testing

```bash
pytest -q                      # 114 tests, no API key required
pytest -q -s                   # also prints fallback paraphrase coverage
pytest -m llm -q               # 33 real Gemini calls: 10 public samples + 21 paraphrases
pytest -m llm -q -k SAMPLE-01  # a single live call, to check configuration cheaply
```

The suite covers request validation, the response contract on every code path,
guardrail behaviour against hostile model output (unknown directive types,
out-of-range hours, negative caps, percentages in the wrong unit, duplicate and
missing `note_index`), time-window and quantity parsing, the optimizer
hand-off helpers, determinism across repeated calls, and a 21-note paraphrase
corpus using wording that does not appear in the public sample pack.

All 10 public sample cases return the reference `total_cost_bdt` exactly, and
every returned plan passes an independent replay of the challenge constraints.

---

## Repository layout

```
gridWise/
├── main.py                            FastAPI service, endpoints, error handlers
├── schemas.py                         request/response contract, shared by all sections
├── section1.py                        Section 1 — note interpretation:
│                                        text helpers, directive models, prompt,
│                                        guardrails, Gemini client, safe fallback, pipeline
├── gridwise_section_02_optimizer.py   Section 2 — cost-minimal 24-hour schedule
├── gridwise_section_03_validator.py   Section 3 — independent replay and response building
├── test_section1.py                   interpretation, guardrail and parsing tests
├── test_section_03_local.py           Section 3 constraint checks
├── sample01.json                      ready-to-POST public sample request
├── BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
├── Dockerfile / .dockerignore
├── requirements.txt / pytest.ini / .env.example
└── .github/workflows/docker-image.yml build, publish and smoke-test the image
```

---

## Known limitations

- The service is hosted on a free tier that suspends idle instances. A cold
  start costs up to 50 seconds on the first request; subsequent requests respond
  in about a second. A scheduled health ping keeps the instance warm during the
  evaluation window.
- Interpretation quality depends on Gemini availability. With no reachable
  provider the service still responds, but from the conservative fallback
  reading, which is weaker on unusual paraphrases.
- The Gemini free tier has per-model daily request limits. A sustained burst of
  hidden cases can exhaust the quota, after which the service degrades to the
  fallback interpreter rather than failing.
- Each note is mapped to exactly one directive type, per the Problem Statement.
  A note combining two directives resolves to the dominant one.
- Ambiguous bare-hour ranges without AM/PM (for example "from one until three")
  are read as afternoon in the fallback path.
- The optimizer discretizes battery energy. The step size is inferred from the
  scenario, but a scenario mixing very large capacity with very fine decimals
  would be coarsened to keep the state space bounded.

---

## Dependencies

- FastAPI, Uvicorn, Pydantic — service layer and schema validation
- `google-genai` — official Gemini SDK
- NumPy — vectorized dynamic program in the optimizer
- pytest — test suite

AI coding assistants were used during development. The architecture, the
LLM-to-guardrails-to-optimizer pipeline, and the constraint logic are the
team's own work.

---

## Security

- No API keys, tokens or `.env` files are committed. `.env` is gitignored and
  excluded from the Docker image; `.env.example` lists variable names only.
- Provider errors are logged by exception type. Keys, raw provider bodies and
  stack traces are never logged or returned in a response.
- Only the synthetic challenge data supplied by the harness is used.