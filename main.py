from __future__ import annotations

import logging
import os
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gridwise_section_02_optimizer import InfeasibleScenario, build_plan, plan_totals
from gridwise_section_03_validator import PlanValidationError, validate_and_build_response
from schemas import ErrorResponse, HealthResponse, ScenarioRequest
from section1 import interpret_scenario

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("gridwise")

app = FastAPI(
    title="GridWise API",
    version="1.0.0",
    description="LLM-assisted energy-optimization service for the GridWise challenge.",
)


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(error=code, detail=detail).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()})
    named = ", ".join(f for f in fields if f)
    detail = (
        f"Invalid or missing fields: {named}"
        if named
        else "Request body did not match the required schema."
    )
    return _error(400, "invalid_request", detail)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error: %s", type(exc).__name__)
    return _error(500, "internal_error", "The service could not complete this request.")


@app.get("/health", response_model=HealthResponse)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_model=HealthResponse)
def root() -> dict[str, str]:
    return {"status": "ok"}


def _plan_summary(interpretation, totals) -> str:
    applied = [e for e in interpretation.entries if e.applies]
    ignored = len(interpretation.entries) - len(applied)
    total_grid, total_cost, peak_grid = totals

    if applied:
        parts = []
        for entry in applied:
            hours = (entry.structured_adjustment or {}).get("hours", [])
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
def optimize_energy(request: ScenarioRequest) -> JSONResponse:
    interpretation = interpret_scenario(request)
    if interpretation.warnings:
        logger.info(
            "scenario=%s source=%s warnings=%s",
            request.scenario_id,
            interpretation.source,
            interpretation.warnings,
        )

    try:
        plan = build_plan(request, interpretation)
    except InfeasibleScenario as exc:
        logger.warning("scenario=%s infeasible: %s", request.scenario_id, exc)
        return _error(422, "infeasible_scenario", str(exc))

    totals = plan_totals(plan, request)
    total_grid_kwh, total_cost_bdt, peak_grid_kwh = totals

    payload: dict[str, Any] = {
        "scenario_id": request.scenario_id,
        "directive_interpretation": [
            entry.model_dump(mode="json") for entry in interpretation.entries
        ],
        "hourly_plan": [hour.model_dump(mode="json") for hour in plan],
        "total_grid_kwh": total_grid_kwh,
        "total_cost_bdt": total_cost_bdt,
        "peak_grid_kwh": peak_grid_kwh,
        "plan_summary": _plan_summary(interpretation, totals),
    }

    try:
        final_response = validate_and_build_response(payload, request.model_dump(mode="json"))
    except PlanValidationError as exc:
        logger.error("scenario=%s failed self-validation: %s", request.scenario_id, exc)
        return _error(500, "internal_error", "The service could not produce a valid schedule.")

    return JSONResponse(status_code=200, content=final_response)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))