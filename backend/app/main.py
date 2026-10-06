from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.app.api.climate import router as climate_router
from backend.app.api.downloads import router as downloads_router
from backend.app.api.routes import router
from backend.app.providers.errors import (
    ClimateConfigurationError,
    ClimateDataError,
    ClimateProviderError,
    UnsupportedVariableError,
)
from backend.app.services.download_job_service import JobNotFoundError


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


async def job_not_found_handler(_, exc: JobNotFoundError) -> JSONResponse:
    return _error_response(status_code=404, message=str(exc))


def create_app() -> FastAPI:
    app = FastAPI(
        title="ERA5 Climate Data API",
        version="0.1.0",
        description="API for ERA5 climate data analysis",
    )
    app.include_router(router)
    app.include_router(climate_router)
    app.include_router(downloads_router)
    app.add_exception_handler(UnsupportedVariableError, unsupported_variable_handler)
    app.add_exception_handler(ClimateConfigurationError, climate_configuration_handler)
    app.add_exception_handler(ClimateDataError, climate_data_handler)
    app.add_exception_handler(ClimateProviderError, climate_provider_handler)
    app.add_exception_handler(JobNotFoundError, job_not_found_handler)
    return app


app = create_app()
