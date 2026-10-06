import math
import os
from datetime import datetime

import pytest
import xarray as xr

from backend.app.core.config import Settings
from backend.app.domain.schemas import Location, TimeSeriesRequest
from backend.app.providers.era5_provider import ERA5ClimateProvider
from backend.app.services.climate_service import ClimateService


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_CDS_INTEGRATION") != "1",
        reason="Live CDS integration test requires RUN_CDS_INTEGRATION=1.",
    ),
]


def test_live_era5_provider_downloads_netcdf_and_returns_timeseries(tmp_path):
    settings = Settings(data_dir=tmp_path, era5_max_days_per_request=1)
    if not settings.cds_url or not settings.cds_api_key:
        pytest.skip("Live ERA5 provider test requires CDS_URL and CDS_API_KEY.")

    provider = ERA5ClimateProvider(settings=settings)
    start = datetime(2024, 1, 1, 0)
    end = datetime(2024, 1, 1, 1)

    response = provider.get_timeseries(
        variable="temperature",
        latitude=10.8231,
        longitude=106.6297,
        start=start,
        end=end,
    )

    assert response.variable == "temperature"
    assert response.unit == "K"
    assert response.data
    assert all(start <= point.time <= end for point in response.data)
    assert all(math.isfinite(point.value) for point in response.data)

    downloaded_files = list((tmp_path / "raw" / "era5").rglob("*.nc"))
    assert len(downloaded_files) == 1
    with xr.open_dataset(downloaded_files[0]) as dataset:
        assert "t2m" in dataset.data_vars or "2m_temperature" in dataset.data_vars

    service = ClimateService(provider=provider)
    service_response = service.get_timeseries(
        TimeSeriesRequest(
            variable="temperature",
            location=Location(latitude=10.8231, longitude=106.6297),
            start=start,
            end=end,
        )
    )
    assert service_response.variable == response.variable
    assert service_response.unit == response.unit
    assert service_response.data
