"""Compare the Hanoi report sample against a local ERA5-Land archive."""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path.cwd()))
from backend.app.domain.schemas import Location
from backend.app.providers.era5_land_timeseries_provider import (
    BASE_VARIABLES,
    ERA5LandTimeseriesProvider,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    request = Location(latitude=21.0245, longitude=105.8412)
    # Parse the local file without constructing a CDS client or creating a repository.
    provider = object.__new__(ERA5LandTimeseriesProvider)
    grid, series, _ = provider._parse_download(args.archive, request)
    if (grid.latitude, grid.longitude) != (21.0, 105.8):
        parser.error("Unexpected archive grid, expected (21.0, 105.8).")
    start = datetime(2024, 12, 31)
    stop = datetime(2025, 1, 6)
    expected = {(variable, start + timedelta(hours=hour))
                for variable in BASE_VARIABLES for hour in range(144)}
    source = {(variable, point.time.replace(tzinfo=None)): point.value
              for variable, points in series.items() for point in points}
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute(
            """SELECT variable,time_utc,value FROM climate_points
               WHERE dataset=? AND grid_latitude=? AND grid_longitude=?
                 AND time_utc>=? AND time_utc<?""",
            ("reanalysis-era5-land-timeseries", grid.latitude, grid.longitude,
             start.isoformat(), stop.isoformat()),
        ).fetchall()
    stored = {(variable, datetime.fromisoformat(time)): value
              for variable, time, value in rows}
    matched = expected & source.keys() & stored.keys()
    errors = [abs(source[key] - stored[key]) for key in matched]
    result = {
        "archive": args.archive.name,
        "grid": grid.model_dump(),
        "start_utc": start.isoformat() + "Z",
        "stop_exclusive_utc": stop.isoformat() + "Z",
        "expected_rows": len(expected),
        "sqlite_rows": len(rows),
        "rows_per_variable": dict(sorted(Counter(row[0] for row in rows).items())),
        "matched": len(matched),
        "equal": sum(error == 0 for error in errors),
        "max_absolute_error": max(errors, default=None),
    }
    print(json.dumps(result, indent=2))
    return int(len(rows) != len(expected) or len(matched) != len(expected) or any(errors))


if __name__ == "__main__":
    sys.exit(main())
