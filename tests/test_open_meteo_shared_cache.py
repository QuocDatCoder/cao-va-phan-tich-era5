"""Cross-account Open-Meteo reuse with real collections and fake HTTP responses."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
from threading import Barrier
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.app.core.config import get_settings
from backend.app.core.user_settings import get_settings_for_user
from backend.app.domain import recent_weather
from backend.app.domain.recent_weather import VIETNAM_TIME, historical_date_bounds
from backend.app.domain.schemas import DownloadRequest, Location
from backend.app.domain.time_utils import hourly_timestamps, to_utc_naive
from backend.app.providers import open_meteo
from backend.app.providers.dependencies import get_provider_for_source
from backend.app.services import download_job_service, live_refresh_service
from backend.app.services.collection_service import CollectionService
from backend.app.services.dependencies import get_download_job_service_for_user
from backend.app.services.stored_climate_service import StoredClimateService
from backend.app.storage import shared_open_meteo_cache
from backend.app.storage.auth_repository import AuthRepository
from backend.app.storage.climate_repository import ClimateRepository


HANOI = Location(latitude=21.02776, longitude=105.83416)
GRID = Location(latitude=21.0, longitude=105.75)
VARIABLE = "2m_temperature"


@pytest.fixture
def base_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("CLIMATE_PROVIDER", "open_meteo")
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def http_calls(monkeypatch):
    calls = []

    def urlopen(url, *, timeout):
        assert timeout == 30
        target = urlsplit(url)
        params = parse_qs(target.query)
        start, end = (date.fromisoformat(params[key][0]) for key in ("start_date", "end_date"))
        calls.append((target.netloc, start, end))
        hours = hourly_timestamps(datetime.combine(start, datetime.min.time()),
                                  datetime.combine(end, datetime.max.time()))
        hourly = {"time": [hour.isoformat(timespec="minutes") for hour in hours]}
        units = {"time": "iso8601"}
        for i, (field, unit, _) in enumerate(open_meteo.FIELDS.values()):
            hourly[field] = [hour.day + hour.hour / 24 + i for hour in hours]
            units[field] = unit
        payload = dict(**GRID.model_dump(), hourly=hourly, hourly_units=units)
        return BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(open_meteo, "urlopen", urlopen)
    return calls


@pytest.fixture
def month_start():
    _, latest = historical_date_bounds()
    return (latest.replace(day=1) - timedelta(days=1)).replace(day=1)


def collect(user_id, start_date, end_date, **kwargs):
    service = get_download_job_service_for_user(user_id)
    request = DownloadRequest(source="open_meteo", variable=VARIABLE, location=HANOI,
                              place_name="Hà Nội", start_date=start_date, end_date=end_date, **kwargs)
    job = service.create_job(request)
    service.run_job(job.job_id)
    finished = service.get_job(job.job_id)
    assert finished.status == "completed", finished
    assert finished.quality.status == "passed", finished.quality
    return service.collections.get(job.collection_id)


def collection_data(user_id, entry, **kwargs):
    settings = get_settings_for_user(user_id)
    repository = ClimateRepository(settings.climate_data_db_path, read_only=True)
    return CollectionService(StoredClimateService(repository, settings)).query(entry["id"], **kwargs)["data"]


def test_full_cache_hit_across_users(base_settings, http_calls, month_start):
    day4, day10 = (month_start.replace(day=day) for day in (4, 10))
    first = collect(1, month_start, day10)
    assert len(http_calls) == 1
    second = collect(2, day4, day10)
    assert len(http_calls) == 1
    data = collection_data(2, second)
    assert len(data) == 7 * 24
    assert data == collection_data(1, first)[3 * 24:]
    assert collection_data(2, second, variable="10m_u_component_of_wind") == (
        collection_data(1, first, variable="10m_u_component_of_wind")[3 * 24:]
    )
    artifact = Path(second["artifacts"][0]["path"])
    assert artifact.is_relative_to(get_settings_for_user(2).data_dir)
    assert json.loads(artifact.read_text(encoding="utf-8"))["cached"] is True
    assert (base_settings.data_dir / "cache" / "shared_open_meteo.sqlite3").is_file()


@pytest.mark.parametrize("request_start, seeded_start, missing", [
    (4, 1, [(11, 12)]),
    (1, 4, [(1, 3), (11, 12)]),
])
def test_gap_filling_across_users(base_settings, http_calls, month_start, request_start, seeded_start, missing):
    first = collect(1, month_start.replace(day=seeded_start), month_start.replace(day=10))
    http_calls.clear()
    second = collect(2, month_start.replace(day=request_start), month_start.replace(day=12))
    archive_host = urlsplit(open_meteo.FORECAST_URL).netloc
    assert http_calls == [(archive_host, month_start.replace(day=a), month_start.replace(day=b)) for a, b in missing]
    data = collection_data(2, second)
    assert len(data) == (13 - request_start) * 24
    assert data[(4 - request_start) * 24:(11 - request_start) * 24] == collection_data(1, first)[(4 - seeded_start) * 24:]


def test_seven_day_collection_after_vietnam_0700_merges_archive_and_live(base_settings, http_calls, monkeypatch):
    end = datetime(2026, 11, 28, 10, tzinfo=VIETNAM_TIME)
    start = (end - timedelta(days=6)).replace(hour=0)
    lower, cutoff = map(to_utc_naive, (start, end))
    archive_host = urlsplit(open_meteo.FORECAST_URL).netloc
    live_host = urlsplit(open_meteo.LIVE_FORECAST_URL).netloc

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return end.astimezone(tz)

    monkeypatch.setattr(open_meteo, "datetime", FrozenDateTime)
    monkeypatch.setattr(recent_weather, "datetime", FrozenDateTime)
    seeded_day = lower.date() + timedelta(days=2)
    collect(1, seeded_day, seeded_day)
    http_calls.clear()

    def collect_preset(user_id):
        return collect(user_id, lower.date(), cutoff.date(), requested_start=start, requested_end=end,
                       request_timezone="UTC+7")

    entry = collect_preset(2)
    assert http_calls == [
        (archive_host, lower.date(), seeded_day - timedelta(days=1)),
        (archive_host, seeded_day + timedelta(days=1), cutoff.date() - timedelta(days=1)),
        (live_host, cutoff.date(), cutoff.date()),
    ]
    hours = hourly_timestamps(lower, cutoff)
    payload = json.loads(Path(entry["artifacts"][0]["path"]).read_text(encoding="utf-8"))
    assert payload["hourly"]["time"] == [hour.isoformat(timespec="minutes") for hour in hours]
    data = collection_data(2, entry)
    assert [point["value"] for point in data] == pytest.approx(
        [hour.day + hour.hour / 24 for hour in hours],
    )

    http_calls.clear()
    assert collection_data(3, collect_preset(3)) == data
    assert http_calls == []


def test_today_cache_expires_after_thirty_minutes(base_settings, http_calls, monkeypatch):
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end.replace(hour=0)
    now = int(end.timestamp())
    monkeypatch.setattr(shared_open_meteo_cache, "time", lambda: now)
    providers = [get_provider_for_source("open_meteo", user_id=user_id) for user_id in (1, 2)]

    def fetch(provider):
        return provider._fetch(HANOI, start.date(), end.date(), live_interval=(start, end))

    first = fetch(providers[0])
    assert len(http_calls) == 1
    now += 1800
    hit = fetch(providers[1])
    assert len(http_calls) == 1
    assert hit[0]["cached"] is True
    assert hit[1:] == first[1:]
    now += 1
    refreshed = fetch(providers[1])
    assert len(http_calls) == 2
    assert refreshed[1:] == first[1:]


def test_concurrent_requests_share_one_http_call(base_settings, http_calls, month_start):
    providers = [get_provider_for_source("open_meteo", user_id=user_id) for user_id in (1, 2, 3, 4)]
    ready = Barrier(len(providers), timeout=10)
    end = month_start.replace(day=3)

    def download(provider):
        ready.wait()
        provider.download_point(VARIABLE, HANOI, month_start, end, "hourly")
        return provider.get_timeseries(VARIABLE, HANOI.latitude, HANOI.longitude,
                                       datetime.combine(month_start, datetime.min.time()),
                                       datetime.combine(end, datetime.max.time())).data

    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        results = list(executor.map(download, providers, timeout=30))
    assert len(http_calls) == 1
    assert len(results[0]) == 3 * 24
    assert results == [results[0]] * len(providers)


def test_live_schedules_share_fresh_observations(base_settings, http_calls, monkeypatch):
    latest = datetime.now(VIETNAM_TIME).replace(hour=10, minute=0, second=0, microsecond=0)
    previous = latest - timedelta(hours=1)
    monkeypatch.setattr(download_job_service, "latest_weather_hour", lambda: latest)
    monkeypatch.setattr(live_refresh_service, "latest_weather_hour", lambda: latest)
    day = to_utc_naive(previous).date()
    now = int(latest.timestamp())
    schedules = AuthRepository(base_settings.data_dir / "cache" / "auth.sqlite3")
    users = [schedules.create_user(f"fixture_{index}", "unused-hash", now) for index in (1, 2)]
    entries = [collect(user, day, day, requested_start=previous, requested_end=previous,
                       request_timezone="UTC+7") for user in users]
    for user, entry in zip(users, entries):
        schedules.enable_live_schedule(user, entry["id"], "Hà Nội", 1, now)
    http_calls.clear()

    for user, entry in zip(users, entries):
        result = live_refresh_service.refresh_collection(schedules, user, entry["id"], automatic=True, now=now)
        assert result["status"] == "updated"
        assert result["added_hours"] == 1
    assert http_calls == [(urlsplit(open_meteo.LIVE_FORECAST_URL).netloc, day, day)]
    first, second = [collection_data(user, entry) for user, entry in zip(users, entries)]
    assert first == second
    assert len(second) == 2
