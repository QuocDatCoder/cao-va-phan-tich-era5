# ERA5 Provider

The backend supports ERA5 and mock providers. ERA5 is the configured default;
set `CLIMATE_PROVIDER=mock` explicitly for offline development.

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

## API units and timezone

The provider keeps temperatures in Kelvin and timestamps in UTC. The service layer
returns temperature/dewpoint in `°C` and observation timestamps with `+07:00`.
Statistics, anomalies, comparisons and heatmaps use this normalized series.
Raw downloads remain in their source units.

API query datetimes accept ISO 8601 offsets. A datetime without an offset means
UTC for compatibility. To query February 1 in Vietnam, use
`start=2025-02-01T00:00:00+07:00` and `end=2025-02-01T23:00:00+07:00`.
The backend requests January 31 17:00 through February 1 16:00 UTC.
The dashboard sends these offsets for all date filters and displays Vietnam
wall times consistently in tables and charts.

## Persistent observation storage

The FastAPI ERA5 provider now uses SQLite at `data/cache/climate.sqlite3`.
Override its path with `CLIMATE_DATA_DB_PATH`. Mock observations are not stored
in this repository. The legacy `data/weather.db` remains a separate database.

`climate_points` stores one observation per dataset, canonical variable, actual
ERA5 grid coordinate and UTC timestamp. Temperature is stored in K; precipitation
is stored in mm (the provider unit). Conversion to °C and UTC+7 occurs once in
the service layer. `climate_locations` maps requested coordinates to the actual
grid selected from NetCDF. Variable aliases share their canonical variable key.
Saving overlapping data updates existing rows without creating duplicates.

A complete hourly range is read directly from SQLite, including smaller ranges
inside previously stored queries, even after restarting the backend. A missing
interior hour prevents a complete cache hit. If coverage is incomplete, the provider
identifies consecutive missing hourly intervals and downloads only those intervals,
then upserts and reads the combined range from SQLite. Missing ranges are split
at UTC midnight so CDS's date/hour combinations cannot download cached hours
on boundary days. Point requests on a single UTC day select only the requested
hours. Existing exact-query NetCDF files are imported before downloading gaps.

For example, querying February 1 00:00–23:00 in Vietnam, then extending the end
to February 2 23:00, downloads only February 1 17:00–23:00 UTC and February 2
00:00–16:00 UTC. Existing SQLite observations are retained if a later download
fails. Incomplete CDS results return a controlled error rather than claiming
that the full requested range is available. The query duration limit still applies.

## Area downloads and synchronization

`POST /api/v1/downloads` and the synchronization service share the ERA5 provider.
When SQLite is enabled, `download_area` imports the downloaded NetCDF before
returning an artifact. A job is marked completed only after the import succeeds;
import failures mark the job failed and do not advance the synchronization date.
Existing area files are also imported when the same download is requested again.

All finite grid observations are upserted in one transaction. `climate_areas`
records the requested bounds and actual grid axes. A point query inside those
bounds can select the nearest imported grid without requiring an earlier point
query at those exact coordinates. Outside points cannot reuse the area's boundary
grid as if it covered them. Existing explicit point mappings take precedence.
Nonfinite observations are skipped, and the usual per-hour completeness check
still applies to subsequent point queries. An area missing requested hourly
timestamps is rejected. Import reads one time slice at a time and rolls back
all imported rows if processing fails.

Download and synchronization `start_date`/`end_date` are UTC calendar dates.
For a full Vietnam day on February 1, download January 31 and February 1 UTC,
then query `2025-02-01T00:00:00+07:00` through `2025-02-01T23:00:00+07:00`.
Dashboard data is visible after a manual refresh; automatic chart refresh is
not included in this step. Mock downloads remain separate from real observations.

Previously downloaded point NetCDF files are imported on their first matching
query. There is no automatic bulk migration of old files or `weather.db`.
The original dataset keeps its existing raw paths; other dataset names have
separate raw directories to prevent cross-dataset file reuse.

## Geographic Selection

The current API accepts a latitude/longitude point. The ERA5 provider requests a small bounding box around that point, then selects the nearest available ERA5 grid point with xarray. ERA5 does not necessarily contain the exact requested coordinate.

## NetCDF And xarray

ERA5 responses are requested as NetCDF and read with `xarray.open_dataset`. The provider selects the variable, time range, nearest latitude, and nearest longitude before converting only the selected subset to API response points.

## Limits

ERA5 requests are limited by `ERA5_MAX_DAYS_PER_REQUEST` (default 7 days).
Area downloads and synchronization imports also respect this per-request limit.
Keep test regions small: an area imports every grid point, not just its centre.

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
