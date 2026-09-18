from __future__ import annotations

import os
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from gridwise_section_02_optimizer import InfeasibleScenario, build_plan, plan_totals
from gridwise_section_03_validator import PlanValidationError, validate_and_build_response
from schemas import ErrorResponse, HealthResponse, OptimizeResponse, ScenarioRequest
from section1 import interpret_scenario

app = FastAPI(
    title="GridWise API",
    version="1.0.0",
    description="Deterministic energy-optimization service for the GridWise challenge.",
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    payload = ErrorResponse(error="request_error", detail=str(exc.detail)).model_dump()
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    payload = ErrorResponse(error="internal_server_error", detail=str(exc)).model_dump()
    return JSONResponse(status_code=500, content=payload)


@app.get("/health", response_model=HealthResponse)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_model=HealthResponse)
def root() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(request: ScenarioRequest) -> dict[str, Any]:
    try:
        interpretation = interpret_scenario(request)
        plan = build_plan(request, interpretation)
        total_grid_kwh, total_cost_bdt, peak_grid_kwh = plan_totals(plan, request)

        payload: dict[str, Any] = {
            "scenario_id": request.scenario_id,
            "directive_interpretation": [
                entry.model_dump(mode="json") for entry in interpretation.entries
            ],
            "hourly_plan": [hour.model_dump(mode="json") for hour in plan],
            "total_grid_kwh": total_grid_kwh,
            "total_cost_bdt": total_cost_bdt,
            "peak_grid_kwh": peak_grid_kwh,
        }

        final_response = validate_and_build_response(payload, request.model_dump(mode="json"))
        return final_response

    except (InfeasibleScenario, PlanValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
