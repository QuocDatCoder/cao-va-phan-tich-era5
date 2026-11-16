from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.app.api.climate import router as climate_router
from backend.app.api.downloads import router as downloads_router
from backend.app.api.routes import router
from backend.app.api.synchronization import router as synchronization_router
from backend.app.api.stored_climate import router as stored_climate_router
from backend.app.core.config import get_settings
from backend.app.providers.errors import (
    ClimateConfigurationError,
    ClimateDataError,
    ClimateProviderError,
    UnsupportedVariableError,
)
from backend.app.services.climate_analysis_service import ClimateAnalysisError
from backend.app.services.download_job_service import JobNotFoundError
from backend.app.services.dependencies import get_synchronization_scheduler
from backend.app.services.synchronization_service import (
    SynchronizationError,
    SynchronizationNotFoundError,
)


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message})


async def unsupported_variable_handler(_, exc: UnsupportedVariableError) -> JSONResponse:
    return _error_response(status_code=400, message=str(exc))


async def climate_configuration_handler(_, exc: ClimateConfigurationError) -> JSONResponse:
    return _error_response(status_code=503, message=str(exc))


async def climate_data_handler(_, exc: ClimateDataError) -> JSONResponse:
    return _error_response(status_code=502, message=str(exc))


async def climate_provider_handler(_, exc: ClimateProviderError) -> JSONResponse:
    return _error_response(status_code=502, message=str(exc))


async def climate_analysis_handler(_, exc: ClimateAnalysisError) -> JSONResponse:
    return _error_response(status_code=422, message=str(exc))


async def job_not_found_handler(_, exc: JobNotFoundError) -> JSONResponse:
    return _error_response(status_code=404, message=str(exc))


async def synchronization_error_handler(_, exc: SynchronizationError) -> JSONResponse:
    return _error_response(status_code=422, message=str(exc))


async def synchronization_not_found_handler(_, exc: SynchronizationNotFoundError) -> JSONResponse:
    return _error_response(status_code=404, message=str(exc))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    scheduler = None
    settings = get_settings()
    if settings.era5_sync_enabled:
        scheduler = get_synchronization_scheduler()
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ERA5 Climate Data API",
        version="0.1.0",
        description="API for ERA5 climate data analysis",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(climate_router)
    app.include_router(stored_climate_router)
    app.include_router(downloads_router)
    app.include_router(synchronization_router)
    app.add_exception_handler(UnsupportedVariableError, unsupported_variable_handler)
    app.add_exception_handler(ClimateConfigurationError, climate_configuration_handler)
    app.add_exception_handler(ClimateDataError, climate_data_handler)
    app.add_exception_handler(ClimateProviderError, climate_provider_handler)
    app.add_exception_handler(ClimateAnalysisError, climate_analysis_handler)
    app.add_exception_handler(JobNotFoundError, job_not_found_handler)
    app.add_exception_handler(SynchronizationError, synchronization_error_handler)
    app.add_exception_handler(SynchronizationNotFoundError, synchronization_not_found_handler)
    return app


app = create_app()
