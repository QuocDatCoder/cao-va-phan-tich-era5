import plotly.graph_objects as go

from frontend.components.charts import heatmap_frame, map_figure, timeseries_frame


def test_timeseries_frame_sorts_observations_chronologically() -> None:
    payload = {
        "data": [
            {"time": "2025-01-01T02:00:00", "value": 3.0},
            {"time": "2025-01-01T00:00:00", "value": 1.0},
        ]
    }

    frame = timeseries_frame(payload)

    assert list(frame["value"]) == [1.0, 3.0]


def test_chart_preserves_local_midnight_without_browser_timezone_conversion():
    frame = timeseries_frame({"data": [
        {"time": "2025-02-01T00:00:00+07:00", "value": 26.0},
        {"time": "2025-01-31T18:00:00Z", "value": 27.0},
    ]})
    assert list(frame["time"].dt.hour) == [0, 1]
    assert list(frame["time"].dt.day) == [1, 1]
    assert frame["time"].dt.tz is None


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


def test_map_figure_renders_backend_point_payload() -> None:
    payload = {
        "unit": "K",
        "data": [
            {
                "latitude": 10.8231,
                "longitude": 106.6297,
                "value": 299.15,
            }
        ],
    }

    figure = map_figure(payload)

    assert isinstance(figure, go.Figure)
    assert figure.data[0].type == "scattergeo"
    assert list(figure.data[0].lat) == [10.8231]
    assert list(figure.data[0].lon) == [106.6297]
    assert list(figure.data[0].marker.color) == [299.15]
    assert figure.data[0].marker.size == 18
    assert figure.layout.geo.lataxis.range == (9.8231, 11.8231)
    assert figure.layout.geo.lonaxis.range == (105.6297, 107.6297)
    assert figure.layout.geo.showland is True
    assert figure.layout.geo.showocean is True
