from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from backend.app.domain.schemas import (
    SynchronizationRequest,
    SynchronizationState,
    SynchronizationStatus,
)
from backend.app.services.dependencies import get_synchronization_service
from backend.app.services.synchronization_service import SynchronizationService

router = APIRouter(prefix="/api/v1/synchronization", tags=["synchronization"])


@router.post("/run", response_model=SynchronizationState, status_code=202)
def run_synchronization(
    request: SynchronizationRequest,
    background_tasks: BackgroundTasks,
    service: Annotated[SynchronizationService, Depends(get_synchronization_service)],
) -> SynchronizationState:
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
