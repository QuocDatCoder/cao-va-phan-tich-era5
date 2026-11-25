from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

VARIABLE_LABELS = {
    "temperature": "Nhiệt độ",
    "2m_temperature": "Nhiệt độ",
    "dewpoint": "Điểm sương",
    "2m_dewpoint_temperature": "Điểm sương",
    "precipitation": "Lượng mưa",
    "total_precipitation": "Lượng mưa",
    "wind_u": "Gió thành phần u",
    "u10": "Gió thành phần u",
    "10m_u_component_of_wind": "Gió thành phần u",
    "wind_v": "Gió thành phần v",
    "v10": "Gió thành phần v",
    "10m_v_component_of_wind": "Gió thành phần v",
    "wind_speed": "Tốc độ gió",
    "wind_direction": "Hướng gió",
    "solar_radiation": "Bức xạ mặt trời",
    "ssrd": "Bức xạ mặt trời",
    "surface_solar_radiation_downwards": "Bức xạ mặt trời",
}

SERIES_STYLE = {
    "Nhiệt độ": ("line", "#c24134"),
    "Điểm sương": ("line", "#087d78"),
    "Lượng mưa": ("bar", "#2366b1"),
    "Tốc độ gió": ("line", "#6851a8"),
    "Hướng gió": ("direction", "#2366b1"),
    "Bức xạ mặt trời": ("bar", "#ad6108"),
}


def variable_label(name: Any) -> str:
    return VARIABLE_LABELS.get(str(name), str(name) if name else "Giá trị")


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
    variable = variable_label(payload.get("variable"))
    style, color = SERIES_STYLE.get(variable, ("line", "#2366b1"))
    if style == "bar" or payload.get("aggregation") == "sum":
        fig = px.bar(frame, x="time", y="value", color_discrete_sequence=[color])
        fig.update_yaxes(rangemode="tozero")
    elif style == "direction":
        frame = frame.dropna(subset=["value"]).copy()
        angles = frame["value"] % 360
        compass = ["Bắc", "Đông Bắc", "Đông", "Đông Nam", "Nam", "Tây Nam", "Tây", "Tây Bắc"]
        directions = [compass[int((angle + 22.5) // 45) % 8] for angle in angles]
        fig = go.Figure(go.Scatter(
            x=frame["time"], y=angles, mode="markers", customdata=directions,
            marker={"symbol": "triangle-up", "angle": angles, "size": 10, "color": color},
            hovertemplate="%{x|%d/%m/%Y %H:%M}<br>%{customdata} · %{y:.1f}°<extra></extra>",
        ))
        fig.update_yaxes(range=[-8, 368], tickvals=[0, 90, 180, 270, 360],
                         ticktext=["Bắc 0°", "Đông 90°", "Nam 180°", "Tây 270°", "Bắc 360°"])
    else:
        fig = px.line(frame, x="time", y="value", markers=len(frame) <= 96,
                      color_discrete_sequence=[color])
        if variable == "Tốc độ gió":
            fig.update_yaxes(rangemode="tozero")
    if style != "direction":
        fig.update_traces(
            hovertemplate=f"%{{x|%d/%m/%Y %H:%M}}<br>{variable}: %{{y:.2f}} {unit}<extra></extra>",
        )
    if payload.get("aggregation") == "mean" and unit not in {"mm", "MJ/m²", "°"}:
        for column, label in (("min", "Nhỏ nhất trong kỳ"), ("max", "Lớn nhất trong kỳ")):
            if column in frame:
                fig.add_trace(go.Scatter(x=frame["time"], y=frame[column], mode="lines",
                                         name=label, line={"dash": "dot"}))
    fig.update_layout(
        xaxis_title="Thời gian Việt Nam (UTC+7)",
        yaxis_title=f"{variable} ({unit})" if unit else variable,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
    )
    frequency = payload.get("frequency", "hourly")
    fig.update_xaxes(tickformat={"daily": "%d/%m/%Y", "monthly": "%m/%Y", "yearly": "%Y"}
                     .get(frequency, "%H:%M<br>%d/%m"))
    if len(frame)==1:
        # Plotly's automatic range around a single datetime is +/- milliseconds.
        # Show one honest period label rather than a row of meaningless tiny ticks.
        stamp=frame['time'].iloc[0]
        width=pd.Timedelta(hours=1)
        if 'period_end' in frame and pd.notna(frame['period_end'].iloc[0]):
            end=vietnam_display_times(frame['period_end']).iloc[0]
            if end>stamp: width=end-stamp
        date_format={'daily':'%d/%m/%Y','monthly':'%m/%Y','yearly':'%Y'}.get(frequency,'%d/%m/%Y %H:%M')
        fig.update_xaxes(range=[stamp-width/2,stamp+width/2],tickmode='array',tickvals=[stamp],ticktext=[stamp.strftime(date_format)])
    return fig


def anomaly_figure(payload: dict[str, Any]) -> go.Figure:
    frame = pd.DataFrame(payload.get("data", []))
    if frame.empty:
        frame = pd.DataFrame(columns=["time", "anomaly"])
    else:
        frame["time"] = vietnam_display_times(frame["time"])
        frame = frame.sort_values("time")
    unit = payload.get("unit", "")
    fig = px.bar(frame, x="time", y="anomaly")
    fig.update_layout(
        xaxis_title="Thời gian Việt Nam (UTC+7)",
        yaxis_title=f"Độ lệch ({unit})" if unit else "Độ lệch",
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
    )
    return fig


def comparison_figure(payload: dict[str, Any]) -> go.Figure:
    frame = pd.DataFrame(
        [
            {"period": "Giai đoạn A", "mean": payload.get("period_a", {}).get("mean")},
            {"period": "Giai đoạn B", "mean": payload.get("period_b", {}).get("mean")},
        ]
    )
    unit = payload.get("unit", "")
    fig = px.bar(frame, x="period", y="mean", text="mean")
    fig.update_traces(texttemplate="%{y:.2f}", textposition="outside")
    fig.update_layout(
        xaxis_title="Giai đoạn",
        yaxis_title=f"Trung bình ({unit})" if unit else "Trung bình",
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
        labels={"x": "Tháng", "y": "Năm", "color": unit or "Giá trị"},
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
    colorbar_title = f"giá trị ({unit})" if unit else "giá trị"

    lat_padding = 1.0
    lon_padding = 1.0
    lat_range = [center["lat"] - lat_padding, center["lat"] + lat_padding]
    lon_range = [center["lon"] - lon_padding, center["lon"] + lon_padding]

    single_point = len(frame) == 1
    frame["label"] = frame["value"].map(
        lambda v: f"{v:.2f} {unit}".strip() if pd.notna(v) else ""
    )
    fig = px.scatter_geo(
        frame,
        lat="latitude",
        lon="longitude",
        color="value",
        text="label" if single_point else None,
        height=520,
        color_continuous_scale="Viridis",
        labels={"value": colorbar_title},
    )
    fig.update_traces(marker={"size": 18 if single_point else 12}, textposition="top center")
    # Một điểm duy nhất thì thang màu chỉ co giãn quanh một giá trị, nên ẩn đi.
    fig.update_layout(coloraxis_showscale=not single_point)
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
