from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import gridwise_section_02_optimizer as optimizer
import gridwise_section_03_validator as validator
from schemas import ScenarioRequest
from section1 import interpret_scenario

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("gridwise")

app = FastAPI(
    title="GridWise",
    description="LLM-assisted smart campus energy optimization",
    version="1.0.0",
)


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": detail})


@app.exception_handler(RequestValidationError)
async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()})
    named = ", ".join(f for f in fields if f)
    return _error(400, "invalid_request", f"Invalid or missing fields: {named}" if named else "Request body did not match the required schema.")


@app.exception_handler(Exception)
async def on_unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error: %s", type(exc).__name__)
    return _error(500, "internal_error", "The service could not complete this request.")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(status_code=200, content={"status": "ok"})


def _plan_summary(scenario: ScenarioRequest, interpretation, totals) -> str:
    applied = [e for e in interpretation.entries if e.applies]
    ignored = len(interpretation.entries) - len(applied)
    total_grid, total_cost, peak_grid = totals

    if applied:
        parts = []
        for entry in applied:
            hours = entry.structured_adjustment.get("hours", [])
            span = f"hours {hours[0]}-{hours[-1]}" if hours else "the stated window"
            parts.append(f"{entry.directive_type.value} over {span}")
        directive_text = "Applied " + "; ".join(parts) + "."
    else:
        directive_text = "No operator note changed the schedule."

    ignored_text = f" {ignored} note(s) were unrelated and ignored." if ignored else ""

    return (
        f"{directive_text}{ignored_text} The battery was charged during cheaper hours "
        f"and discharged during expensive ones, ending the day back at its initial "
        f"energy level. Total grid import {total_grid} kWh at {total_cost} BDT, "
        f"peaking at {peak_grid} kWh in a single hour."
    )


@app.post("/optimize-energy")
async def optimize_energy(scenario: ScenarioRequest) -> JSONResponse:
    scenario_dict = scenario.model_dump(mode="json")

    interpretation = interpret_scenario(scenario)
    if interpretation.warnings:
        logger.info(
            "scenario=%s source=%s warnings=%s",
            scenario.scenario_id,
            interpretation.source,
            interpretation.warnings,
        )

    try:
        plan = optimizer.build_plan(scenario, interpretation)
    except optimizer.InfeasibleScenario as exc:
        logger.warning("scenario=%s infeasible: %s", scenario.scenario_id, exc)
        return _error(422, "infeasible_scenario", str(exc))

    totals = optimizer.plan_totals(plan, scenario)
    total_grid, total_cost, peak_grid = totals

    optimizer_result = {
        "scenario_id": scenario.scenario_id,
        "directive_interpretation": [e.model_dump(mode="json") for e in interpretation.entries],
        "hourly_plan": [p.model_dump(mode="json") for p in plan],
        "total_grid_kwh": total_grid,
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": peak_grid,
        "plan_summary": _plan_summary(scenario, interpretation, totals),
    }

    try:
        response = validator.validate_and_build_response(optimizer_result, scenario_dict)
    except validator.PlanValidationError as exc:
        logger.error("scenario=%s failed self-validation: %s", scenario.scenario_id, exc)
        return _error(500, "internal_error", "The service could not produce a valid schedule.")

    return JSONResponse(status_code=200, content=response)