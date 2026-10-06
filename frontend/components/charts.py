from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def timeseries_frame(payload: dict[str, Any]) -> pd.DataFrame:
    data = payload.get("data", [])
    frame = pd.DataFrame(data)
    if frame.empty:
        return pd.DataFrame(columns=["time", "value"])
    frame["time"] = pd.to_datetime(frame["time"])
    return frame.sort_values("time")


def timeseries_figure(payload: dict[str, Any]) -> go.Figure:
    frame = timeseries_frame(payload)
    unit = payload.get("unit", "")
    variable = payload.get("variable", "value")
    fig = px.line(frame, x="time", y="value", markers=True)
    fig.update_layout(
        xaxis_title="Time",
        yaxis_title=f"{variable} ({unit})" if unit else variable,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
    )
    return fig


def anomaly_figure(payload: dict[str, Any]) -> go.Figure:
    frame = pd.DataFrame(payload.get("data", []))
    if frame.empty:
        frame = pd.DataFrame(columns=["time", "anomaly"])
    else:
        frame["time"] = pd.to_datetime(frame["time"])
        frame = frame.sort_values("time")
    unit = payload.get("unit", "")
    fig = px.bar(frame, x="time", y="anomaly")
    fig.update_layout(
        xaxis_title="Time",
        yaxis_title=f"Anomaly ({unit})" if unit else "Anomaly",
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
    )
    return fig


def comparison_figure(payload: dict[str, Any]) -> go.Figure:
    frame = pd.DataFrame(
        [
            {"period": "Period A", "mean": payload.get("period_a", {}).get("mean")},
            {"period": "Period B", "mean": payload.get("period_b", {}).get("mean")},
        ]
    )
    unit = payload.get("unit", "")
    fig = px.bar(frame, x="period", y="mean", text="mean")
    fig.update_layout(
        xaxis_title="Period",
        yaxis_title=f"Mean ({unit})" if unit else "Mean",
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
    )
    return fig


def heatmap_frame(payload: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(payload.get("data", []))
    if frame.empty:
        return pd.DataFrame()
    return frame.pivot(index="year", columns="month", values="value").sort_index()


def heatmap_figure(payload: dict[str, Any]) -> go.Figure:
    pivot = heatmap_frame(payload)
    unit = payload.get("unit", "")
    fig = px.imshow(
        pivot,
        labels={"x": "Month", "y": "Year", "color": unit or "Value"},
        aspect="auto",
    )
    fig.update_layout(margin={"l": 10, "r": 10, "t": 30, "b": 10})
    return fig


def map_figure(payload: dict[str, Any]) -> go.Figure:
    frame = pd.DataFrame(payload.get("data", []))
    if frame.empty:
        frame = pd.DataFrame(columns=["latitude", "longitude", "value"])
    fig = px.scatter_mapbox(
        frame,
        lat="latitude",
        lon="longitude",
        color="value",
        size="value",
        zoom=7,
        height=420,
    )
    fig.update_layout(
        mapbox_style="open-street-map",
        margin={"l": 0, "r": 0, "t": 20, "b": 0},
    )
    return fig
