from frontend.components.charts import heatmap_frame, timeseries_frame


def test_timeseries_frame_sorts_observations_chronologically() -> None:
    payload = {
        "data": [
            {"time": "2025-01-01T02:00:00", "value": 3.0},
            {"time": "2025-01-01T00:00:00", "value": 1.0},
        ]
    }

    frame = timeseries_frame(payload)

    assert list(frame["value"]) == [1.0, 3.0]


def test_heatmap_frame_pivots_backend_year_month_values() -> None:
    payload = {
        "data": [
            {"year": 2025, "month": 1, "value": 2.0, "count": 2},
            {"year": 2025, "month": 2, "value": 5.0, "count": 1},
        ]
    }

    frame = heatmap_frame(payload)

    assert frame.loc[2025, 1] == 2.0
    assert frame.loc[2025, 2] == 5.0
