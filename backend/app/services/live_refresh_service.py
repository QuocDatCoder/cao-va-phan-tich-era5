from datetime import UTC, datetime
from itertools import groupby
import logging
from threading import Lock
import time

from apscheduler.schedulers.background import BackgroundScheduler

from backend.app.core.user_settings import get_settings_for_user
from backend.app.domain.live_refresh import refresh_due
from backend.app.domain.recent_weather import latest_weather_hour
from backend.app.domain.schemas import Location
from backend.app.domain.time_utils import to_utc_naive
from backend.app.providers.dependencies import get_provider_for_source
from backend.app.providers.errors import ClimateDataError
from backend.app.providers.open_meteo import OUTPUT_UNITS
from backend.app.storage.collection_repository import CollectionRepository, stamp


logger = logging.getLogger(__name__)
_LOCKS = tuple(Lock() for _ in range(64))


def collection_lock(user_id, collection_id):
    return _LOCKS[hash((user_id, collection_id)) % len(_LOCKS)]


def require_open_meteo(repository, collection_id):
    entry = repository.get(collection_id)
    if entry is None:
        raise LookupError("Không tìm thấy bộ dữ liệu.")
    if entry["metadata"]["source"] != "open_meteo":
        raise ValueError("Tự động cập nhật chỉ hỗ trợ bộ dữ liệu Open-Meteo.")
    return entry


def refresh_collection(schedules, user_id, collection_id, *, automatic=False, now=None):
    settings = get_settings_for_user(user_id)
    repository = CollectionRepository(settings.climate_data_db_path, read_only=True)
    with collection_lock(user_id, collection_id):
        checked_at = int(time.time()) if now is None else now
        entry = require_open_meteo(repository, collection_id)
        metadata = entry["metadata"]
        end = metadata["requested_end"]
        if automatic and not refresh_due(schedules.get_live_schedule(user_id, collection_id, checked_at), checked_at):
            return dict(status="not_due", added_hours=0, requested_end=end)
        if entry["status"] in {"queued", "running", "cancelling"}:
            raise ValueError("Hãy đợi tác vụ thu thập kết thúc trước khi cập nhật.")

        latest = latest_weather_hour()
        if entry["status"] == "complete" and stamp(latest) <= stamp(datetime.fromisoformat(end)):
            result = dict(status="up_to_date", added_hours=0, requested_end=end)
        else:
            hours = repository.missing_refresh_hours(entry, latest)
            if not hours and stamp(latest) < metadata["requested_start_utc"]:
                raise ValueError("Bộ dữ liệu chưa đến giờ bắt đầu.")
            observations = _read_missing_hours(user_id, entry, hours, latest)
            writer = CollectionRepository(settings.climate_data_db_path)
            end, added = writer.apply_live_refresh(collection_id, latest, observations)
            result = dict(status="updated", added_hours=added, requested_end=end)

        if automatic or result["status"] == "updated":
            refreshed_at = time.time() if now is None else now
            schedules.record_live_refresh(
                user_id, collection_id, metadata["place_name"], refreshed_at, automatic=automatic,
            )
            result["last_refreshed_at"] = datetime.fromtimestamp(refreshed_at, UTC).isoformat()
        return result


def _read_missing_hours(user_id, entry, hours, latest):
    if not hours:
        return []
    metadata = entry["metadata"]
    location = Location(**metadata["location"])
    grids = {(item["latitude"], item["longitude"]) for item in entry["grids"]}
    variables = {item["variable"] for item in entry["variables"]}
    provider = get_provider_for_source("open_meteo", user_id=user_id)
    missing = set(hours)
    observations = []

    # Group consecutive missing dates so archived gaps never cause a read of intervening complete days.
    by_date = {day: list(points) for day, points in groupby(hours, key=lambda hour: hour.date())}
    partitions = groupby(enumerate(by_date), key=lambda item: item[1].toordinal() - item[0])
    for _, partition in partitions:
        run = [day for _, day in partition]
        for grid, series in provider.fetch_refresh(
            location, by_date[run[0]][0], by_date[run[-1]][-1], latest, dataset=metadata["dataset"],
        ):
            lat, lon = round(grid.latitude, 10), round(grid.longitude, 10)
            if grids and (lat, lon) not in grids:
                raise ClimateDataError("Tọa độ lưới Open-Meteo đã thay đổi so với bộ dữ liệu.")
            for variable in variables.intersection(series):
                observations.extend(
                    (entry["id"], metadata["dataset"], variable, lat, lon, stamp(point.time),
                     OUTPUT_UNITS[variable], point.value)
                    for point in series[variable] if to_utc_naive(point.time) in missing
                )
    if not observations:
        raise ClimateDataError("Open-Meteo chưa có dữ liệu mới cho bộ này.")
    return observations


def run_live_schedules(schedules, *, now=None):
    checked_at = int(time.time()) if now is None else now
    for row in schedules.list_live_schedules(checked_at, active_only=True):
        if not refresh_due(row, checked_at):
            continue
        user_id, collection_id = row["user_id"], row["collection_id"]
        try:
            refresh_collection(schedules, user_id, collection_id, automatic=True, now=now)
        except LookupError:
            # A collection may have been removed independently of the schedule.
            schedules.delete_live_schedule(user_id, collection_id)
        except Exception:
            logger.exception("Live refresh failed for account %s, collection %s", user_id, collection_id)


def start_live_scheduler(schedules):
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run_live_schedules, "interval", minutes=1, args=[schedules],
                      id="open-meteo-live-refresh", max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler
