from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from backend.app.core.config import Settings, get_settings
from backend.app.domain.recent_weather import historical_date_bounds

from backend.app.domain.schemas import (
    SynchronizationRequest,
    SynchronizationState,
    SynchronizationStatus,
)
from backend.app.services.dependencies import get_synchronization_service
from backend.app.services.synchronization_service import SynchronizationService, LatestAvailableDateProvider

router = APIRouter(prefix="/api/v1/synchronization", tags=["synchronization"])


@router.get("")
def list_synchronizations(
    service: Annotated[SynchronizationService, Depends(get_synchronization_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict[str, list[SynchronizationState]]:
    return {"states": service.list_states(limit)}


@router.get("/config")
def synchronization_config(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    if settings.climate_provider == "open_meteo":
        earliest, latest = historical_date_bounds()
        return {
            "provider": settings.climate_provider,
            "scheduler_enabled": False,
            "earliest_available_date": earliest.isoformat(),
            "latest_available_date": latest.isoformat(),
            "max_query_days": settings.era5_max_query_days,
        }
    return {
        "provider": settings.climate_provider,
        "scheduler_enabled": settings.era5_sync_enabled,
        "interval_hours": settings.era5_sync_interval_hours,
        "latest_available_date": LatestAvailableDateProvider(
            settings.era5_sync_availability_lag_days
        ).latest_available_date().isoformat(),
        "max_days_per_request": settings.era5_max_days_per_request,
        "max_query_days": settings.era5_max_query_days,
        "max_days_per_run": settings.era5_sync_max_days_per_run,
    }


@router.post("/run", response_model=SynchronizationState, status_code=202)
def run_synchronization(
    request: SynchronizationRequest,
    background_tasks: BackgroundTasks,
    service: Annotated[SynchronizationService, Depends(get_synchronization_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SynchronizationState:
    if settings.climate_provider == "open_meteo":
        raise HTTPException(status_code=409, detail="Lịch đồng bộ cũ không áp dụng cho Open-Meteo.")
    state = service.synchronize(request)
    if state.status == SynchronizationStatus.QUEUED:
        background_tasks.add_task(service.run_synchronization, state.sync_id)
    return state


@router.get("/status/{sync_id}", response_model=SynchronizationState)
def get_synchronization_status(
    sync_id: str,
    service: Annotated[SynchronizationService, Depends(get_synchronization_service)],
) -> SynchronizationState:
    return service.get_state(sync_id)
