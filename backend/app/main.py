from fastapi import FastAPI

from backend.app.api.climate import router as climate_router
from backend.app.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="ERA5 Climate Data API",
        version="0.1.0",
        description="API for ERA5 climate data analysis",
    )
    app.include_router(router)
    app.include_router(climate_router)
    return app


app = create_app()
