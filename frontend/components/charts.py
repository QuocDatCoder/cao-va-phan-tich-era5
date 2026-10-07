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

    center = {
        "lat": float(frame["latitude"].mean()) if not frame.empty else 10.8231,
        "lon": float(frame["longitude"].mean()) if not frame.empty else 106.6297,
    }
    unit = payload.get("unit", "")
    colorbar_title = f"value ({unit})" if unit else "value"

    lat_padding = 1.0
    lon_padding = 1.0
    lat_range = [center["lat"] - lat_padding, center["lat"] + lat_padding]
    lon_range = [center["lon"] - lon_padding, center["lon"] + lon_padding]

    fig = px.scatter_geo(
        frame,
        lat="latitude",
        lon="longitude",
        color="value",
        height=520,
        color_continuous_scale="Viridis",
        labels={"value": colorbar_title},
    )
    fig.update_traces(marker={"size": 18})
    fig.update_geos(
        lataxis_range=lat_range,
        lonaxis_range=lon_range,
        showland=True,
        landcolor="#eef2e6",
        showocean=True,
        oceancolor="#d8ecff",
        showlakes=True,
        lakecolor="#d8ecff",
        showcountries=True,
        countrycolor="#6b7280",
        showcoastlines=True,
        coastlinecolor="#6b7280",
        resolution=50,
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
    )
    return fig
