from frontend.api_client import DashboardApiClient
from frontend import dashboard


def test_collection_payload_is_a_point_for_land_and_an_area_for_era5():
    params = {"variable": "temperature", "latitude": 10.8231, "longitude": 106.6297,
              "start": "2025-01-01T00:00:00+07:00", "end": "2025-01-01T23:00:00+07:00"}
    land = dashboard._collection_payload(params, "land")
    era5 = dashboard._collection_payload(params, "era5")
    assert "area" not in land and land["location"] == {"latitude": 10.8231, "longitude": 106.6297}
    assert "location" not in era5 and "area" in era5
    assert land["source"] == "land" and era5["source"] == "era5"
    assert land["start_date"] == "2024-12-31"  # Cover the preceding UTC date.


def test_frontend_cached_results_include_source_and_query_mode(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def get_timeseries(self, params):
            calls.append(self.kwargs)
            return {"source": self.kwargs["source"], "stored_only": self.kwargs["stored_only"]}

    monkeypatch.setattr(dashboard, "DashboardApiClient", Client)
    dashboard._cached_get.clear()
    try:
        for source, stored_only in [("land", True), ("era5", True), ("land", True), ("land", False)]:
            r = dashboard._cached_get("http://example.test", 3, "timeseries", (("variable", "temperature"),),
                                      source=source, stored_only=stored_only)
            assert r == {"source": source, "stored_only": stored_only}
        assert len(calls) == 3
    finally:
        dashboard._cached_get.clear()


def test_build_client_uses_database_only_by_default(monkeypatch):
    monkeypatch.setattr(dashboard.st, "session_state", {"stored_source": "era5"})
    client = dashboard.build_client()
    assert isinstance(client, DashboardApiClient) and client.stored_only and client.source == "era5"
