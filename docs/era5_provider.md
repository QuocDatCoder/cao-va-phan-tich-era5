# ERA5 Provider

Phase 4 adds an optional ERA5-backed climate provider while keeping the mock provider as the default.

## Provider Selection

Use environment variables:

```powershell
$env:CLIMATE_PROVIDER = "mock"
$env:CLIMATE_PROVIDER = "era5"
```

`mock` is deterministic and safe for offline development. `era5` requires CDS credentials.

## CDS Configuration

Set these variables when `CLIMATE_PROVIDER=era5`:

```powershell
$env:CDS_URL = "https://cds.climate.copernicus.eu/api"
$env:CDS_API_KEY = "<personal-access-token>"
```

Do not commit real credentials. `.env` is ignored; `.env.example` contains placeholders.

## Supported Variables

- `temperature`
- `2m_temperature`
- `dewpoint`
- `2m_dewpoint_temperature`
- `precipitation`
- `total_precipitation`

Precipitation is converted from ERA5 metres to millimetres.

## Geographic Selection

The current API accepts a latitude/longitude point. The ERA5 provider requests a small bounding box around that point, then selects the nearest available ERA5 grid point with xarray. ERA5 does not necessarily contain the exact requested coordinate.

## NetCDF And xarray

ERA5 responses are requested as NetCDF and read with `xarray.open_dataset`. The provider selects the variable, time range, nearest latitude, and nearest longitude before converting only the selected subset to API response points.

## Limits

Phase 4 direct ERA5 requests are limited by `ERA5_MAX_DAYS_PER_REQUEST` to reduce accidental large downloads. Background download jobs and synchronization belong to later phases.

## Tests

Offline tests:

```powershell
pytest
```

Optional live integration tests should be marked with `integration` and run only with credentials/network access:

```powershell
$env:RUN_CDS_INTEGRATION = "1"
pytest -m integration
```
