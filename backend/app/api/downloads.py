from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.app.domain.schemas import DownloadCreateResponse, DownloadJob, DownloadRequest
from backend.app.services.dependencies import get_download_job_service
from backend.app.services.download_job_service import DownloadJobService

router = APIRouter(tags=["downloads"])


@router.get("/api/v1/jobs/{job_id}/quality")
def get_quality(job_id: str, service: Annotated[DownloadJobService, Depends(get_download_job_service)],
                offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)):
    return service.get_quality(job_id, offset, limit)


@router.post("/api/v1/downloads", response_model=DownloadCreateResponse, status_code=202)
def create_download(
    request: DownloadRequest,
    background_tasks: BackgroundTasks,
    service: Annotated[DownloadJobService, Depends(get_download_job_service)],
) -> DownloadCreateResponse:
    try:
        response = service.create_job(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(service.run_job, response.job_id)
    return response


@router.get("/api/v1/jobs/{job_id}", response_model=DownloadJob)
def get_job(
    job_id: str,
    service: Annotated[DownloadJobService, Depends(get_download_job_service)],
) -> DownloadJob:
    return service.get_job(job_id)
