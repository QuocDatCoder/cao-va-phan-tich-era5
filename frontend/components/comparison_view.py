"""Compare two saved collections (different locations or time periods) on one chart."""
from functools import partial
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backend.app.ai.chart_analysis import render_comparison_analysis, summarize_plot
from backend.app.domain.public_variables import public_variables
from backend.app.domain.ui_actions import UIAction
from frontend.api_client import DashboardApiError
from frontend.components.charts import timeseries_frame, variable_label
from frontend.components.excel_export import EXCEL_MIME, build_comparison_excel
from frontend.notifications import notify_error, notify_warning

COMPARE_COLORS = ("#c24134", "#2366b1")


def _numeric_values(frame: pd.DataFrame) -> pd.Series:
    if frame.empty or "value" not in frame:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame["value"], errors="coerce").dropna()


def _stats_for(frame: pd.DataFrame) -> dict[str, float | int | None]:
    vals = _numeric_values(frame)
    if vals.empty:
        return {"mean": None, "max": None, "min": None, "count": 0}
    return {
        "mean": float(vals.mean()),
        "max": float(vals.max()),
        "min": float(vals.min()),
        "count": int(len(vals)),
    }


def _diff(a: float | int | None, b: float | int | None) -> float | None:
    if a is None or b is None:
        return None
    return float(a) - float(b)


def build_comparison_series(
    payload_a: dict[str, Any],
    payload_b: dict[str, Any],
    label_a: str,
    label_b: str,
    *,
    align_by: str = "time",
) -> dict[str, Any]:
    """Prepare two aligned DataFrames and summary statistics for side-by-side comparison."""
    frame_a = timeseries_frame(payload_a)
    frame_b = timeseries_frame(payload_b)

    if align_by == "elapsed":
        frame_a["x"] = range(len(frame_a))
        frame_b["x"] = range(len(frame_b))
    else:
        frame_a["x"] = frame_a["time"]
        frame_b["x"] = frame_b["time"]

    stats_a = _stats_for(frame_a)
    stats_b = _stats_for(frame_b)

    return {
        "frame_a": frame_a,
        "frame_b": frame_b,
        "label_a": label_a,
        "label_b": label_b,
        "align_by": align_by,
        "variable": variable_label(payload_a.get("variable") or payload_b.get("variable")),
        "unit": payload_a.get("unit") or payload_b.get("unit") or "",
        "frequency": payload_a.get("frequency") or payload_b.get("frequency") or "hourly",
        "stats_a": stats_a,
        "stats_b": stats_b,
        "diff_mean": _diff(stats_a["mean"], stats_b["mean"]),
        "diff_max": _diff(stats_a["max"], stats_b["max"]),
        "diff_min": _diff(stats_a["min"], stats_b["min"]),
    }


def comparison_summary_rows(
    payload_a: dict[str, Any],
    payload_b: dict[str, Any],
    label_a: str,
    label_b: str,
    *,
    align_by: str = "time",
) -> list[dict[str, Any]]:
    """Build display rows comparing mean, max, min, and point count between A and B."""
    bundle = build_comparison_series(payload_a, payload_b, label_a, label_b, align_by=align_by)
    return _summary_rows(bundle)


def _summary_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    label_a, label_b = bundle["label_a"], bundle["label_b"]
    if label_a == label_b or {label_a, label_b} & {"Chỉ số", "Chênh lệch (A - B)"}:
        label_a, label_b = f"A · {label_a}", f"B · {label_b}"
    sa, sb = bundle["stats_a"], bundle["stats_b"]
    unit = bundle["unit"]
    suffix = f" ({unit})" if unit else ""

    def _fmt(val: float | int | None, is_int: bool = False) -> str:
        if val is None:
            return "—"
        return f"{int(val):,}" if is_int else f"{val:.2f}"

    def _fmt_diff(val: float | None) -> str:
        if val is None:
            return "—"
        sign = "+" if val > 0 else ""
        return f"{sign}{val:.2f}"

    return [
        {
            "Chỉ số": f"Trung bình{suffix}",
            label_a: _fmt(sa["mean"]),
            label_b: _fmt(sb["mean"]),
            "Chênh lệch (A - B)": _fmt_diff(bundle["diff_mean"]),
        },
        {
            "Chỉ số": f"Lớn nhất{suffix}",
            label_a: _fmt(sa["max"]),
            label_b: _fmt(sb["max"]),
            "Chênh lệch (A - B)": _fmt_diff(bundle["diff_max"]),
        },
        {
            "Chỉ số": f"Nhỏ nhất{suffix}",
            label_a: _fmt(sa["min"]),
            label_b: _fmt(sb["min"]),
            "Chênh lệch (A - B)": _fmt_diff(bundle["diff_min"]),
        },
        {
            "Chỉ số": "Số điểm dữ liệu",
            label_a: _fmt(sa["count"], is_int=True),
            label_b: _fmt(sb["count"], is_int=True),
            "Chênh lệch (A - B)": _fmt(sa["count"] - sb["count"], is_int=True),
        },
    ]


def multi_series_comparison_figure(
    payload_a: dict[str, Any],
    payload_b: dict[str, Any],
    label_a: str,
    label_b: str,
    *,
    align_by: str = "time",
) -> go.Figure:
    """Create a Plotly figure overlaying two climate series."""
    bundle = build_comparison_series(payload_a, payload_b, label_a, label_b, align_by=align_by)
    return _comparison_figure(bundle)


def _comparison_figure(bundle: dict[str, Any]) -> go.Figure:
    fa, fb = bundle["frame_a"], bundle["frame_b"]
    label_a, label_b = bundle["label_a"], bundle["label_b"]
    align_by = bundle["align_by"]
    variable, unit, frequency = bundle["variable"], bundle["unit"], bundle["frequency"]

    fig = go.Figure()
    for frame, name, color in (
        (fa, label_a, COMPARE_COLORS[0]),
        (fb, label_b, COMPARE_COLORS[1]),
    ):
        if align_by == "elapsed":
            custom = frame["time"].dt.strftime("%d/%m/%Y %H:%M") if not frame.empty else []
            hover = f"{name}<br>Mốc: %{{customdata}}<br>{variable}: %{{y:.2f}} {unit}<extra></extra>"
        else:
            custom = None
            hover = f"{name}<br>%{{x|%d/%m/%Y %H:%M}}<br>{variable}: %{{y:.2f}} {unit}<extra></extra>"
        fig.add_trace(
            go.Scatter(
                x=frame["x"],
                y=frame["value"] if "value" in frame else [],
                mode="lines+markers" if len(frame) <= 72 else "lines",
                name=name,
                line={"color": color, "width": 2},
                customdata=custom,
                hovertemplate=hover,
            )
        )

    if align_by == "elapsed":
        unit_step = {"hourly": "giờ", "daily": "ngày", "monthly": "tháng", "yearly": "năm"}.get(
            frequency, "bước"
        )
        xaxis_title = f"Thứ tự {unit_step} tính từ mốc bắt đầu"
    else:
        xaxis_title = "Thời gian Việt Nam (UTC+7)"

    fig.update_layout(
        xaxis_title=xaxis_title,
        yaxis_title=f"{variable} ({unit})" if unit else variable,
        margin={"l": 12, "r": 16, "t": 24, "b": 16},
        legend={"orientation": "h", "yanchor": "top", "y": -0.18},
        hovermode="x unified" if align_by == "time" else "closest",
    )
    if align_by == "time":
        fig.update_xaxes(
            tickformat={"daily": "%d/%m/%Y", "monthly": "%m/%Y", "yearly": "%Y"}.get(
                frequency, "%H:%M<br>%d/%m"
            )
        )
    return fig


def _collection_vars(entry: dict[str, Any]) -> list[str]:
    m = entry["metadata"]
    return public_variables([m["primary_variable"]] + [v["variable"] for v in entry.get("variables", [])])


@st.fragment
def render_comparison_panel(api: Any) -> None:
    """Render the interactive 2-collection comparison panel inside the Charts screen."""
    from frontend.library import cached_read, label

    st.subheader("So sánh 2 bộ dữ liệu", anchor=False)
    st.caption(
        "Chọn 2 bộ dữ liệu đã lưu (khác địa điểm hoặc khác khoảng thời gian) để vẽ chồng trên cùng một biểu đồ."
    )

    try:
        listing = cached_read(api, "list_collections", "", 50, 0)
    except DashboardApiError as exc:
        notify_error(f"Không tải được danh sách bộ dữ liệu: {exc}")
        return

    entries = [e for e in listing.get("collections", []) if e.get("status") in {"complete", "partial"}]
    if len(entries) < 2:
        st.info("Cần ít nhất 2 bộ dữ liệu đã lưu (đủ hoặc thiếu một phần) để so sánh.")
        return

    by_id = {e["id"]: e for e in entries}
    ids = list(by_id)
    labels = {cid: label(entry) for cid, entry in by_id.items()}

    with st.form("chart_compare_form"):
        col_a, col_b = st.columns(2)
        id_a = col_a.selectbox(
            "Bộ dữ liệu A",
            ids,
            index=0,
            format_func=labels.__getitem__,
            key="compare_collection_a",
        )
        id_b = col_b.selectbox(
            "Bộ dữ liệu B",
            ids,
            index=1 if len(ids) > 1 else 0,
            format_func=labels.__getitem__,
            key="compare_collection_b",
        )

        vars_a = _collection_vars(by_id[id_a])
        vars_b = set(_collection_vars(by_id[id_b]))
        common_vars = [v for v in vars_a if v in vars_b and v != "wind_direction"]
        if not common_vars:
            st.warning("Hai bộ dữ liệu chưa có biến khí tượng chung để so sánh.")
            st.form_submit_button("So sánh", disabled=True)
            return

        c1, c2, c3 = st.columns(3)
        variable = c1.selectbox(
            "Biến khí tượng",
            common_vars,
            format_func=variable_label,
            key="compare_variable",
        )
        freq_map = {"hourly": "Giờ", "daily": "Ngày", "monthly": "Tháng"}
        frequency = c2.selectbox(
            "Chu kỳ",
            list(freq_map),
            format_func=freq_map.get,
            key="compare_frequency",
        )
        align_map = {
            "time": "Theo thời gian thực (cùng kỳ)",
            "elapsed": "Theo thứ tự từ mốc đầu (khác kỳ)",
        }
        align_by = c3.selectbox(
            "Căn trục thời gian",
            list(align_map),
            format_func=align_map.get,
            key="compare_align_by",
        )
        submitted = st.form_submit_button("So sánh ngay", type="primary", key="compare_submit")

    if submitted:
        if id_a == id_b:
            notify_warning("Bạn đang chọn cùng một bộ dữ liệu cho A và B.")
        operation = "timeseries" if frequency == "hourly" else "aggregate"
        try:
            detail_a = cached_read(api, "get_collection", id_a)
            detail_b = cached_read(api, "get_collection", id_b)
            grid_a = detail_a["grids"][0]
            grid_b = detail_b["grids"][0]
            meta_a, meta_b = detail_a["metadata"], detail_b["metadata"]
            params_a = dict(
                variable=variable,
                latitude=grid_a["latitude"],
                longitude=grid_a["longitude"],
                start=meta_a["requested_start"],
                end=meta_a["requested_end"],
                frequency=frequency,
                aggregation="auto",
            )
            params_b = dict(
                variable=variable,
                latitude=grid_b["latitude"],
                longitude=grid_b["longitude"],
                start=meta_b["requested_start"],
                end=meta_b["requested_end"],
                frequency=frequency,
                aggregation="auto",
            )
            payload_a = cached_read(api, "query_collection", id_a, operation, params_a)
            payload_b = cached_read(api, "query_collection", id_b, operation, params_b)
            sa = summarize_plot(payload_a, frequency)
            sb = summarize_plot(payload_b, frequency)
            diff_val = (
                sa["mean"] - sb["mean"]
                if sa.get("mean") is not None and sb.get("mean") is not None and not sa.get("circular")
                else None
            )
            prov_a = dict(
                collection_id=id_a,
                place_name=meta_a["place_name"],
                source=meta_a["source"],
                dataset=meta_a["dataset"],
                timezone="UTC+7",
                start=meta_a["requested_start"],
                end=meta_a["requested_end"],
                grid_location=payload_a.get("grid_location"),
                synthetic=meta_a["source"] == "mock",
            )
            prov_b = dict(
                collection_id=id_b,
                place_name=meta_b["place_name"],
                source=meta_b["source"],
                dataset=meta_b["dataset"],
                timezone="UTC+7",
                start=meta_b["requested_start"],
                end=meta_b["requested_end"],
                grid_location=payload_b.get("grid_location"),
                synthetic=meta_b["source"] == "mock",
            )
            meta_out_a = {
                k: payload_a.get(k)
                for k in ("variable", "unit", "grid_location", "expected_count", "available_count", "coverage", "complete")
            }
            meta_out_b = {
                k: payload_b.get(k)
                for k in ("variable", "unit", "grid_location", "expected_count", "available_count", "coverage", "complete")
            }
            tool_res = dict(
                status="ok" if payload_a.get("complete") and payload_b.get("complete") else "partial",
                query_mode="db_only",
                data=dict(
                    series_a=sa,
                    series_b=sb,
                    diff_mean=diff_val,
                    align_by=align_by,
                    metadata_a=meta_out_a,
                    metadata_b=meta_out_b,
                    provenance_a=prov_a,
                    provenance_b=prov_b,
                ),
                provenance=prov_a,
                metadata=meta_out_a,
            )
            fallback_chat = dict(
                answer=render_comparison_analysis(tool_res),
                evidence=[dict(tool="analyze_comparison", result=tool_res)],
                actions=[
                    UIAction(type="open_chart", collection_id=id_a).model_dump(mode="json"),
                    UIAction(type="open_chart", collection_id=id_b).model_dump(mode="json"),
                ],
                verified=True,
                completed=True,
            )
            st.session_state["chart_compare_context"] = dict(
                context=dict(
                    collection_id_a=id_a,
                    collection_id_b=id_b,
                    parameters_a=params_a,
                    parameters_b=params_b,
                    align_by=align_by,
                ),
                place_a=meta_a["place_name"],
                place_b=meta_b["place_name"],
                variable=variable,
                frequency=frequency,
                fallback_result=fallback_chat,
            )
            st.session_state["chart_compare_result"] = (
                payload_a,
                payload_b,
                label(detail_a),
                label(detail_b),
                align_by,
            )
        except (DashboardApiError, KeyError, IndexError) as exc:
            notify_error(f"Không thể so sánh 2 bộ dữ liệu: {exc}")
            return
        if st.session_state.get("ai_chat_open"):
            # ERA renders before the workspace and needs the new context for its prompts.
            st.rerun(scope="app")

    saved = st.session_state.get("chart_compare_result")
    if not saved:
        return

    payload_a, payload_b, name_a, name_b, saved_align = saved
    bundle = build_comparison_series(payload_a, payload_b, name_a, name_b, align_by=saved_align)
    unit = bundle["unit"]
    sa, sb = bundle["stats_a"], bundle["stats_b"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Trung bình Bộ A", f"{sa['mean']:.2f} {unit}" if sa["mean"] is not None else "—")
    m2.metric("Trung bình Bộ B", f"{sb['mean']:.2f} {unit}" if sb["mean"] is not None else "—")
    diff_mean = bundle["diff_mean"]
    m3.metric(
        "Chênh lệch (A - B)",
        f"{diff_mean:+.2f} {unit}" if diff_mean is not None else "—",
    )

    fig = _comparison_figure(bundle)
    st.plotly_chart(fig, width="stretch", key="chart_compare_plotly")

    rows = _summary_rows(bundle)
    st.dataframe(rows, hide_index=True, width="stretch")
    st.download_button(
        "Tải Excel (.xlsx)", data=partial(build_comparison_excel, payload_a, payload_b, name_a, name_b, saved_align),
        file_name="comparison.xlsx", mime=EXCEL_MIME,
        key="download_comparison_excel", on_click="ignore", icon=":material/download:",
    )
