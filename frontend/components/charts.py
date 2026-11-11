from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def apply_scientific_layout(
    fig: go.Figure,
    xaxis_title: str = "",
    yaxis_title: str = "",
    margin: dict[str, int] | None = None,
) -> go.Figure:
    """Apply consistent dark atmospheric scientific styling to a Plotly figure."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(20, 32, 50, 0.45)",
        font={"family": "Inter, -apple-system, sans-serif", "color": "#94A3B8", "size": 12},
        xaxis=dict(
            gridcolor="#1E3147",
            gridwidth=1,
            zerolinecolor="#23354E",
            title_text=xaxis_title,
            title_font={"size": 12, "color": "#94A3B8"},
            tickfont={"family": "JetBrains Mono, monospace", "size": 11, "color": "#94A3B8"},
        ),
        yaxis=dict(
            gridcolor="#1E3147",
            gridwidth=1,
            zerolinecolor="#23354E",
            title_text=yaxis_title,
            title_font={"size": 12, "color": "#94A3B8"},
            tickfont={"family": "JetBrains Mono, monospace", "size": 11, "color": "#94A3B8"},
        ),
        hoverlabel=dict(
            bgcolor="#142032",
            bordercolor="#00D2FF",
            font=dict(family="JetBrains Mono, monospace", size=12, color="#F1F5F9"),
        ),
        margin=margin or {"l": 15, "r": 15, "t": 35, "b": 15},
    )
    return fig


def vietnam_display_times(values: pd.Series) -> pd.Series:
    # Plotly receives local wall times so browser timezone cannot shift the chart.
    return pd.to_datetime(values, utc=True).dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None)


def timeseries_frame(payload: dict[str, Any]) -> pd.DataFrame:
    data = payload.get("data", [])
    frame = pd.DataFrame(data)
    if frame.empty:
        return pd.DataFrame(columns=["time", "value"])
    frame["time"] = vietnam_display_times(frame["time"])
    return frame.sort_values("time")


def timeseries_figure(payload: dict[str, Any]) -> go.Figure:
    frame = timeseries_frame(payload)
    unit = payload.get("unit", "")
    variable = payload.get("variable", "giá trị")

    if payload.get("aggregation") == "sum":
        fig = px.bar(frame, x="time", y="value")
        fig.update_traces(
            marker_color="#00D2FF",
            marker_line_color="#0284C7",
            marker_line_width=1,
            opacity=0.9,
        )
    else:
        fig = px.line(frame, x="time", y="value", markers=True)
        fig.update_traces(
            line=dict(color="#00D2FF", width=2.2),
            marker=dict(size=5, color="#38BDF8", line=dict(width=1, color="#F1F5F9")),
        )

    if payload.get("aggregation") == "mean" and unit not in {"mm", "MJ/m²", "°"}:
        for column, label, color in (
            ("min", "Nhỏ nhất trong kỳ", "#64748B"),
            ("max", "Lớn nhất trong kỳ", "#F59E0B"),
        ):
            if column in frame:
                fig.add_trace(
                    go.Scatter(
                        x=frame["time"],
                        y=frame[column],
                        mode="lines",
                        name=label,
                        line={"dash": "dot", "color": color, "width": 1.5},
                    )
                )

    if payload.get("variable") == "wind_direction":
        fig.update_traces(mode="markers", marker=dict(size=7, color="#00D2FF"))
        fig.update_yaxes(
            range=[0, 360],
            tickvals=[0, 90, 180, 270, 360],
            ticktext=["Bắc 0°", "Đông 90°", "Nam 180°", "Tây 270°", "Bắc 360°"],
        )

    apply_scientific_layout(
        fig,
        xaxis_title="Thời gian Việt Nam (UTC+7)",
        yaxis_title=f"{variable} ({unit})" if unit else variable,
        margin={"l": 15, "r": 15, "t": 35, "b": 15},
    )
    return fig


def anomaly_figure(payload: dict[str, Any]) -> go.Figure:
    frame = pd.DataFrame(payload.get("data", []))
    if frame.empty:
        frame = pd.DataFrame(columns=["time", "anomaly"])
    else:
        frame["time"] = vietnam_display_times(frame["time"])
        frame = frame.sort_values("time")
    unit = payload.get("unit", "")

    # Diverging color mapping: positive anomaly = warm coral, negative anomaly = cool marine blue
    colors = [
        "#EF4444" if val >= 0 else "#3B82F6"
        for val in frame.get("anomaly", pd.Series(dtype=float))
    ]

    fig = px.bar(frame, x="time", y="anomaly")
    fig.update_traces(marker_color=colors, marker_line_width=0, opacity=0.85)

    apply_scientific_layout(
        fig,
        xaxis_title="Thời gian Việt Nam (UTC+7)",
        yaxis_title=f"Độ lệch ({unit})" if unit else "Độ lệch",
        margin={"l": 15, "r": 15, "t": 35, "b": 15},
    )
    return fig


def comparison_figure(payload: dict[str, Any]) -> go.Figure:
    frame = pd.DataFrame(
        [
            {"period": "Giai đoạn A (Khảo sát)", "mean": payload.get("period_a", {}).get("mean")},
            {"period": "Giai đoạn B (Đối chứng)", "mean": payload.get("period_b", {}).get("mean")},
        ]
    )
    unit = payload.get("unit", "")
    colors = ["#00D2FF", "#818CF8"]

    fig = px.bar(
        frame,
        x="period",
        y="mean",
        text="mean",
        color="period",
        color_discrete_sequence=colors,
    )
    fig.update_traces(
        texttemplate="%{text:.2f}",
        textposition="outside",
        textfont=dict(family="JetBrains Mono, monospace", size=12, color="#F1F5F9"),
    )

    apply_scientific_layout(
        fig,
        xaxis_title="Giai đoạn",
        yaxis_title=f"Trung bình ({unit})" if unit else "Trung bình",
        margin={"l": 15, "r": 15, "t": 35, "b": 15},
    )
    fig.update_layout(showlegend=False)
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
        labels={"x": "Tháng", "y": "Năm", "color": unit or "Giá trị"},
        aspect="auto",
        color_continuous_scale="Viridis",
    )
    apply_scientific_layout(
        fig,
        xaxis_title="Tháng",
        yaxis_title="Năm",
        margin={"l": 15, "r": 15, "t": 30, "b": 15},
    )
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
    colorbar_title = f"giá trị ({unit})" if unit else "giá trị"

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
        landcolor="#142032",
        showocean=True,
        oceancolor="#0B1320",
        showlakes=True,
        lakecolor="#0B1320",
        showcountries=True,
        countrycolor="#475569",
        showcoastlines=True,
        coastlinecolor="#00D2FF",
        resolution=50,
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
    )
    return fig
