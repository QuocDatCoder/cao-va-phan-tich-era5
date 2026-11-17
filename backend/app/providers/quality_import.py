"""Explicit ERA5 collection: retain finite hourly cells and account for omissions."""
from datetime import datetime, time

import numpy as np
import xarray as xr

from backend.app.domain.quality import current_quality
from backend.app.domain.cancellation import check_cancelled
from backend.app.domain.time_utils import hourly_timestamps


def import_area(provider, path, spec, area, start_date, end_date):
    quality = current_quality.get()
    details = dict(source_file=str(path), variable=spec.cds_name)
    if path.stat().st_size == 0:
        quality.issue("empty_file", "File is empty; original file preserved.", **details)
        return
    try:
        dataset = xr.open_dataset(path)
    except Exception:
        quality.issue("unreadable_file", "Cannot open NetCDF; file preserved.", **details)
        return
    with dataset:
        try:
            tc = provider._find_coordinate(dataset, ("valid_time", "time"))
            lat = provider._find_coordinate(dataset, ("latitude", "lat"))
            lon = provider._find_coordinate(dataset, ("longitude", "lon"))
            variable = provider._find_data_variable(dataset, spec)
        except Exception as exc:
            quality.issue("missing_column", str(exc), **details)
            return
        data = dataset[variable]
        for dimension in tuple(data.dims):
            if dimension not in (tc, lat, lon):
                if data.sizes[dimension] != 1:
                    quality.issue("invalid_dimensions", "Unsupported non-singleton dimension.", **details)
                    return
                data = data.isel({dimension: 0}, drop=True)
        data = data.transpose(tc, lat, lon)
        latitudes = [float(v) for v in data[lat].values]
        longitudes = [float(v) for v in data[lon].values]
        if not latitudes or not longitudes or not all(np.isfinite(v) for v in latitudes + longitudes) or any(abs(v) > 90 for v in latitudes):
            quality.issue("invalid_coordinates", "Invalid grid coordinates; variable skipped.", **details)
            return
        expected = set(hourly_timestamps(datetime.combine(start_date, time.min),
                                       datetime.combine(end_date, time(hour=23))))
        # Keep time indices only; buffer one grid while choosing first valid cells.
        first = {}
        for i, value in enumerate(data[tc].values):
            check_cancelled()
            quality.report.rows_read += len(latitudes) * len(longitudes)
            try:
                stamp = provider._to_datetime(value)
                if stamp is None or stamp.minute or stamp.second or stamp.microsecond:
                    raise ValueError("Non-hourly time")
            except (ValueError, TypeError, OverflowError):
                quality.issue("invalid_time", "Invalid timestamp; time slice skipped.", row_number=i+1, **details)
                continue
            if stamp not in expected:
                quality.issue("outside_request", "Time slice outside requested range skipped.", time_utc=stamp, **details)
                continue
            first.setdefault(stamp, []).append(i)
        if not data.sizes[tc]:
            quality.issue("empty_file", "Variable contains no time records.", **details)
        for stamp in sorted(expected - first.keys()):
            quality.issue("missing_hour", "Requested time slice is absent (all grid cells).", time_utc=stamp, **details)

        def records():
            for stamp, indices in first.items():
                check_cancelled()
                cells = {}
                for i in indices:
                    for (a, b), raw in np.ndenumerate(data.isel({tc: i}).values):
                        value = float(raw) * spec.scale
                        cell_details = dict(time_utc=stamp, row_number=i+1,
                            latitude=latitudes[a], longitude=longitudes[b], **details)
                        if not np.isfinite(value):
                            quality.issue("nonfinite_value", "Empty, NaN or infinite grid cell skipped.", **cell_details)
                            continue
                        key = (a, b)
                        if key in cells:
                            code = "duplicate" if cells[key] == value else "duplicate_conflict"
                            quality.issue(code, "Duplicate grid cell skipped; first valid value retained.", **cell_details)
                            continue
                        cells[key] = value
                        quality.report.valid_values += 1
                for (a, b), value in cells.items():
                    yield latitudes[a], (longitudes[b]+180) % 360-180, stamp, round(value, 6)
        try:
            count = provider._repository.save_area(dataset=provider._settings.era5_dataset,
                variable=spec.cds_name, unit=spec.unit, area=area,
                latitudes=latitudes, longitudes=longitudes, records=records())
        except ValueError as exc:
            if str(exc) != "The area contains no finite observations.":
                raise
            quality.issue("no_valid_values", "Variable has no valid observations to import.", **details)
        else:
            quality.report.imported_values += count
