"""
Module Visualizer: Trực quan hóa dữ liệu khí tượng ERA5 bằng Plotly
Cung cấp biểu đồ tương tác phục vụ xuất giao diện Frontend, Streamlit và phân tích đồ án
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import config


def get_temperature_figure(df: pd.DataFrame, title: str = "Diễn biến Nhiệt độ & Điểm sương (ERA5)") -> go.Figure:
    """Tạo biểu đồ chuỗi thời gian nhiệt độ 2m và điểm sương."""
    fig = go.Figure()

    time_col = "datetime_vn" if "datetime_vn" in df.columns else df.columns[0]

    # Tìm cột nhiệt độ
    t_col = None
    for c in df.columns:
        if "2m_temperature" in c or c == "t2m":
            t_col = c
            break

    # Tìm cột điểm sương
    d_col = None
    for c in df.columns:
        if "2m_dewpoint" in c or c == "d2m":
            d_col = c
            break

    if t_col:
        fig.add_trace(go.Scatter(
            x=df[time_col],
            y=df[t_col],
            mode="lines",
            name="Nhiệt độ 2m (°C)",
            line=dict(color="#FF5722", width=2.5),
            hovertemplate="<b>%{x}</b><br>Nhiệt độ: %{y:.1f} °C<extra></extra>",
        ))

    if d_col:
        fig.add_trace(go.Scatter(
            x=df[time_col],
            y=df[d_col],
            mode="lines",
            name="Điểm sương (°C)",
            line=dict(color="#00BCD4", width=1.5, dash="dash"),
            hovertemplate="<b>%{x}</b><br>Điểm sương: %{y:.1f} °C<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=18, family="Arial")),
        xaxis=dict(title="Thời gian (Giờ Việt Nam - UTC+7)", showgrid=True),
        yaxis=dict(title="Nhiệt độ (°C)", showgrid=True),
        hovermode="x unified",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=60, b=40),
    )
    return fig


def get_precipitation_figure(df: pd.DataFrame, title: str = "Diễn biến Lượng mưa (ERA5)") -> go.Figure:
    """Tạo biểu đồ lượng mưa tích lũy và theo từng mốc thời gian."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    time_col = "datetime_vn" if "datetime_vn" in df.columns else df.columns[0]

    p_col = None
    for c in df.columns:
        if "precipitation" in c or c == "tp":
            p_col = c
            break

    if p_col:
        # Cột lượng mưa
        fig.add_trace(
            go.Bar(
                x=df[time_col],
                y=df[p_col],
                name="Lượng mưa (mm)",
                marker_color="#2196F3",
                hovertemplate="<b>%{x}</b><br>Lượng mưa: %{y:.2f} mm<extra></extra>",
            ),
            secondary_y=False,
        )

        # Đường lượng mưa tích lũy
        cumulative_p = df[p_col].cumsum()
        fig.add_trace(
            go.Scatter(
                x=df[time_col],
                y=cumulative_p,
                name="Mưa tích lũy (mm)",
                line=dict(color="#1565C0", width=2),
                hovertemplate="<b>%{x}</b><br>Tích lũy: %{y:.2f} mm<extra></extra>",
            ),
            secondary_y=True,
        )

    fig.update_layout(
        title=dict(text=title, font=dict(size=18)),
        xaxis=dict(title="Thời gian (Giờ Việt Nam - UTC+7)", showgrid=True),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=60, b=40),
    )
    fig.update_yaxes(title_text="Lượng mưa từng mốc (mm)", secondary_y=False)
    fig.update_yaxes(title_text="Lượng mưa tích lũy (mm)", secondary_y=True)
    return fig


def get_wind_figure(df: pd.DataFrame, title: str = "Tốc độ và Hướng gió (ERA5)") -> go.Figure:
    """Tạo biểu đồ tốc độ gió kèm thông tin hướng gió theo công thức Slide 11."""
    fig = go.Figure()
    time_col = "datetime_vn" if "datetime_vn" in df.columns else df.columns[0]

    if "wind_speed" in df.columns:
        custom_data = []
        for _, row in df.iterrows():
            dir_str = f"{row.get('wind_direction_label', '')} ({row.get('wind_direction', 0):.0f}°)"
            custom_data.append(dir_str)

        fig.add_trace(go.Scatter(
            x=df[time_col],
            y=df["wind_speed"],
            mode="lines+markers",
            name="Tốc độ gió (m/s)",
            line=dict(color="#4CAF50", width=2),
            marker=dict(size=4),
            customdata=custom_data,
            hovertemplate="<b>%{x}</b><br>Tốc độ: %{y:.1f} m/s<br>Hướng: %{customdata}<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=18)),
        xaxis=dict(title="Thời gian (Giờ Việt Nam - UTC+7)", showgrid=True),
        yaxis=dict(title="Tốc độ gió (m/s)", showgrid=True),
        template="plotly_white",
        margin=dict(l=40, r=40, t=60, b=40),
    )
    return fig


def get_comprehensive_dashboard(df: pd.DataFrame, location_name: str = "TP. Hồ Chí Minh") -> go.Figure:
    """
    Tạo Dashboard tổng hợp đa chỉ số (Nhiệt độ, Mưa, Gió, Bức xạ) với trục thời gian đồng bộ.
    """
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=(
            f"1. Nhiệt độ & Điểm sương (°C) - {location_name}",
            f"2. Lượng mưa (mm) - {location_name}",
            f"3. Tốc độ gió (m/s) & Hướng gió - {location_name}",
        ),
    )

    time_col = "datetime_vn" if "datetime_vn" in df.columns else df.columns[0]

    # Row 1: Nhiệt độ
    t_col = next((c for c in df.columns if "2m_temperature" in c or c == "t2m"), None)
    d_col = next((c for c in df.columns if "2m_dewpoint" in c or c == "d2m"), None)
    if t_col:
        fig.add_trace(go.Scatter(
            x=df[time_col], y=df[t_col], mode="lines", name="Nhiệt độ 2m",
            line=dict(color="#FF5722", width=2),
        ), row=1, col=1)
    if d_col:
        fig.add_trace(go.Scatter(
            x=df[time_col], y=df[d_col], mode="lines", name="Điểm sương",
            line=dict(color="#00BCD4", width=1.5, dash="dash"),
        ), row=1, col=1)

    # Row 2: Lượng mưa
    p_col = next((c for c in df.columns if "precipitation" in c or c == "tp"), None)
    if p_col:
        fig.add_trace(go.Bar(
            x=df[time_col], y=df[p_col], name="Lượng mưa (mm)",
            marker_color="#2196F3",
        ), row=2, col=1)

    # Row 3: Gió
    if "wind_speed" in df.columns:
        fig.add_trace(go.Scatter(
            x=df[time_col], y=df["wind_speed"], mode="lines", name="Tốc độ gió (m/s)",
            line=dict(color="#4CAF50", width=2),
        ), row=3, col=1)

    fig.update_layout(
        height=850,
        title_text=f"BẢNG ĐIỀU KHIỂN KHÍ TƯỢNG TOÀN DIỆN ERA5 - {location_name}",
        title_font=dict(size=20),
        template="plotly_white",
        hovermode="x unified",
        showlegend=True,
    )
    return fig


def export_figure_to_json(fig: go.Figure) -> str:
    """Xuất đối tượng Plotly Figure thành chuỗi JSON để phía Frontend dễ dàng render."""
    return json.dumps(fig.to_plotly_json(), default=str)


def save_figure_html(fig: go.Figure, file_path: Path) -> Path:
    """Lưu biểu đồ thành file HTML độc lập có thể mở trực tiếp trên trình duyệt."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(file_path), include_plotlyjs="cdn")
    return file_path
