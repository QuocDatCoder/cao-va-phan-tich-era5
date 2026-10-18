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
- `wind_u`, `u10`, `10m_u_component_of_wind`
- `wind_v`, `v10`, `10m_v_component_of_wind`
- `wind_speed`, `wind_direction`
- `solar_radiation`, `ssrd`, `surface_solar_radiation_downwards`

Precipitation is converted from ERA5 metres to millimetres.
Solar radiation is converted from J/m² to MJ/m² once at the provider boundary;
SQLite stores MJ/m². For the configured ERA5 single-levels hourly reanalysis,
these are hourly accumulated energies, not W/m² fluxes.

Point wind speed/direction are derived from time-aligned u/v source series in m/s.
SQLite retains source components, which are reused for both derived variables.
Area jobs for either derived variable request both CDS components in one file
and import each before reporting completion. Each source import is atomic;
if the second source fails, the job fails without advancing synchronization,
while the first source's successfully imported data remains available.
The mock provider supports the same variable names and units, using synthetic data.

Direction is meteorological (where wind comes from); calm hours have no direction
and are omitted. Direction statistics use an unweighted circular mean/std,
with null min/max. Opposing directions can yield null mean/std. Linear
trend/anomaly/compare reject direction with 422. The dashboard shows circular
statistics and markers at 0–360° without joining points across north.
See [ECMWF wind convention](https://confluence.ecmwf.int/pages/viewpage.action?pageId=690456538)
and [ERA5 accumulation units](https://confluence.ecmwf.int/pages/viewpage.action?pageId=414588701).

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
Mock downloads remain separate from real observations.

## Dashboard updates

The sidebar polls synchronization states and the dashboard's current download job
every 15 seconds using a Streamlit fragment (Streamlit >=1.37). A newly observed
successful completion changes the cache revision and reruns the charts/statistics.
History mode keeps the selected date range. Polling failures show an error while
retaining the last known data, and failed/running syncs do not invalidate completed
data. `GET /api/v1/synchronization` lists up to 100 recent states;
`GET /api/v1/synchronization/config` exposes public runtime limits and the estimated
latest available date, without credentials.

Enable **Theo dõi dữ liệu mới nhất** for a sliding window of complete Vietnam
calendar days ending at the latest synced UTC date. Enable **Tự đồng bộ khi theo dõi**
to start a recent-window synchronization and repeat it at the configured interval
(default 6 hours) while the dashboard session is open. This checkbox is off by
default. **Đồng bộ khoảng đang chọn** starts a manual sync. History mode limits
manual synchronization to the selected dates, rather than backfilling to today.
The follow window is capped to leave room for the preceding UTC day within the
backend's per-run/per-request limits. If no matching completed data exists, follow
mode waits instead of starting chart queries before the sync finishes.

The backend scheduler is independent and continues only when `ERA5_SYNC_ENABLED`
is enabled. It synchronizes the variable/region configured in its environment,
not arbitrary dashboard selections. Polling detects matching scheduler/API syncs
as well as syncs started from the dashboard. The UI's automatic sync runs only
while the page is open. Raw download/sync dates remain UTC dates.

Previously downloaded point NetCDF files are imported on their first matching
query. There is no automatic bulk migration of old files or `weather.db`.
The original dataset keeps its existing raw paths; other dataset names have
separate raw directories to prevent cross-dataset file reuse.

## Geographic Selection

The current API accepts a latitude/longitude point. The ERA5 provider requests a small bounding box around that point, then selects the nearest available ERA5 grid point with xarray. ERA5 does not necessarily contain the exact requested coordinate.

## NetCDF And xarray

ERA5 responses are requested as NetCDF and read with `xarray.open_dataset`. The provider selects the variable, time range, nearest latitude, and nearest longitude before converting only the selected subset to API response points.

## Limits

Total queries are bounded by `ERA5_MAX_QUERY_DAYS` (default 366 complete days,
configurable up to 3660). Individual CDS downloads remain bounded by
`ERA5_MAX_DAYS_PER_REQUEST` (default 7). Missing full UTC days can be batched;
partial boundary days stay separate to avoid extra date/hour combinations.
Point queries without SQLite are also split. Existing SQLite observations and
successful download chunks survive failures and are reused on retry.

Long area downloads create multiple NetCDF chunks, import each, and return a
`.manifest.json` artifact listing their relative storage paths. Short jobs still
return a single NetCDF artifact. The job completes only after all chunks succeed.
Synchronization continues to advance one configured batch per run.
Keep test regions small: an area imports every grid point, not just its centre.

## Calendar aggregation

`GET /api/v1/climate/aggregate` adds `frequency=hourly|daily|monthly|yearly` and
`aggregation=auto|mean|min|max|sum`. Auto uses sums for precipitation/radiation,
means for scalar instantaneous variables, and unweighted circular means for
wind direction. Temperature and wind-speed results include hourly min/max.
Sum is rejected for other variables; direction rejects min/max/sum.

Calendar buckets use Vietnam time, with leap-year/month lengths handled by
calendar boundaries. Each result has an exclusive period_end, observation count,
requested-hour expected_count, coverage, partial_period and complete flags.
Partial months/years are not advertised as full-period totals. Undefined circular
means return null. Calm hours omitted from wind direction reduce its count.

ERA5 accumulated variables represent the hour ending at validity time. The
aggregate service queries sources +1 hour and relabels to the hour start before
calendar grouping (`time_basis=hour_start`). The legacy timeseries endpoint keeps
original validity timestamps. This alignment applies to the configured ERA5
single-levels product, not indiscriminately to all ERA5-Land products.

The dashboard's period selector affects the timeseries chart/table; overview
statistics, trend, comparison and anomaly remain hourly analyses. Monthly heatmap
uses sums for precipitation/radiation and means for other variables; it covers
only the requested dates, which may be a partial month.

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
