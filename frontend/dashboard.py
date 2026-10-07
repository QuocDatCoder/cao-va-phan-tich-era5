import os
from typing import Any, Callable

import pandas as pd
import streamlit as st

from frontend.api_client import DEFAULT_TIMEOUT_SECONDS, DashboardApiClient, DashboardApiError
from frontend.components.charts import (
    anomaly_figure,
    comparison_figure,
    heatmap_figure,
    map_figure,
    timeseries_figure,
)
from frontend.components.controls import (
    render_baseline_controls,
    render_comparison_controls,
    render_filter_controls,
)


def build_client() -> DashboardApiClient:
    return DashboardApiClient(
        base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
        timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
    )


def main() -> None:
    st.set_page_config(page_title="ERA5 Climate Dashboard", layout="wide")
    st.title("ERA5 Climate Dashboard")
    st.caption("FastAPI-backed dashboard for climate data and analysis")

    client = build_client()
    params = render_filter_controls()
    baseline_params = render_baseline_controls()
    comparison_params = render_comparison_controls()

    with st.sidebar:
        st.header("Backend")
        st.code(client.base_url)
        if st.button("Check backend"):
            _run_request("Backend health", client.health, lambda payload: st.success(payload["status"]))

    tabs = st.tabs(
        [
            "Overview",
            "Time Series",
            "Spatial",
            "Data Jobs",
        ]
    )

    with tabs[0]:
        _render_overview(client, params, comparison_params)
    with tabs[1]:
        _render_temporal_analysis(client, params, baseline_params)
    with tabs[2]:
        _render_map(client, params)
    with tabs[3]:
        _render_downloads(client, params)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_get(base_url: str, timeout_seconds: float, endpoint: str, params_key: tuple) -> dict[str, Any]:
    client = DashboardApiClient(base_url=base_url, timeout_seconds=timeout_seconds)
    params = dict(params_key)
    if endpoint == "timeseries":
        return client.get_timeseries(params)
    if endpoint == "statistics":
        return client.get_statistics(params)
    if endpoint == "trend":
        return client.get_trend(params)
    if endpoint == "anomaly":
        return client.get_anomaly(params)
    if endpoint == "compare":
        return client.get_compare(params)
    if endpoint == "heatmap":
        return client.get_heatmap(params)
    if endpoint == "map":
        return client.get_map(params)
    raise DashboardApiError(f"Unsupported dashboard endpoint: {endpoint}")


def _params_key(params: dict[str, Any]) -> tuple:
    return tuple(sorted(params.items()))


def _load_cached(client: DashboardApiClient, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    return _cached_get(
        base_url=client.base_url,
        timeout_seconds=client.timeout_seconds,
        endpoint=endpoint,
        params_key=_params_key(params),
    )


def _render_overview(
    client: DashboardApiClient,
    params: dict[str, Any],
    comparison_params: dict[str, str],
) -> None:
    st.subheader("Overview")
    left, right = st.columns([3, 2])
    with left:
        _render_statistics(client, params)
    with right:
        _render_trend(client, params)
    st.divider()
    _render_comparison(client, params, comparison_params)


def _render_temporal_analysis(
    client: DashboardApiClient,
    params: dict[str, Any],
    baseline_params: dict[str, str],
) -> None:
    st.subheader("Time series")
    _render_timeseries(client, params, show_title=False)
    st.divider()
    _render_anomaly(client, params, baseline_params)
    st.divider()
    _render_heatmap(client, params)


def _render_timeseries(
    client: DashboardApiClient,
    params: dict[str, Any],
    show_title: bool = True,
) -> None:
    if show_title:
        st.subheader("Time series")
    _run_request(
        "Time series",
        lambda: _load_cached(client, "timeseries", params),
        lambda payload: _show_timeseries(payload),
    )
    if st.button("Refresh time series", key="refresh_timeseries"):
        _cached_get.clear()
        st.rerun()


def _show_timeseries(payload: dict[str, Any]) -> None:
    if not payload.get("data"):
        st.info("No observations returned for the selected filters.")
        return
    st.plotly_chart(timeseries_figure(payload), use_container_width=True)
    st.dataframe(pd.DataFrame(payload["data"]), use_container_width=True)


def _render_statistics(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Statistics")
    _run_request(
        "Statistics",
        lambda: _load_cached(client, "statistics", params),
        lambda payload: _show_statistics(payload),
    )
    if st.button("Refresh statistics", key="refresh_statistics"):
        _cached_get.clear()
        st.rerun()


def _show_statistics(payload: dict[str, Any]) -> None:
    unit = payload.get("unit", "")
    cols = st.columns(5)
    labels = [("Mean", "mean"), ("Min", "min"), ("Max", "max"), ("Std", "std"), ("Count", "count")]
    for column, (label, key) in zip(cols, labels):
        column.metric(label, f"{payload.get(key)} {unit}" if key != "count" else payload.get(key))


def _render_trend(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Trend")
    _run_request(
        "Trend",
        lambda: _load_cached(client, "trend", params),
        lambda payload: _show_trend(payload),
    )
    if st.button("Refresh trend", key="refresh_trend"):
        _cached_get.clear()
        st.rerun()


def _show_trend(payload: dict[str, Any]) -> None:
    col1, col2, col3 = st.columns(3)
    col1.metric("Direction", payload.get("direction"))
    col2.metric("Slope", payload.get("slope"))
    col3.metric("Observations", payload.get("count"))
    st.caption("Slope is reported by the backend and is not relabeled as per-day/month/year.")


def _render_anomaly(
    client: DashboardApiClient,
    params: dict[str, Any],
    baseline_params: dict[str, str],
) -> None:
    st.subheader("Anomaly")
    request_params = {**params, **baseline_params}
    _run_request(
        "Anomaly",
        lambda: _load_cached(client, "anomaly", request_params),
        lambda payload: _show_anomaly(payload),
    )
    if st.button("Refresh anomaly", key="refresh_anomaly"):
        _cached_get.clear()
        st.rerun()


def _show_anomaly(payload: dict[str, Any]) -> None:
    st.metric("Baseline mean", f"{payload.get('baseline_mean')} {payload.get('unit', '')}")
    if not payload.get("data"):
        st.info("No anomaly values returned.")
        return
    st.plotly_chart(anomaly_figure(payload), use_container_width=True)
    st.dataframe(pd.DataFrame(payload["data"]), use_container_width=True)


def _render_comparison(
    client: DashboardApiClient,
    params: dict[str, Any],
    comparison_params: dict[str, str],
) -> None:
    st.subheader("Period comparison")
    request_params = {
        "variable": params["variable"],
        "latitude": params["latitude"],
        "longitude": params["longitude"],
        "period_a_start": params["start"],
        "period_a_end": params["end"],
        **comparison_params,
    }
    _run_request(
        "Comparison",
        lambda: _load_cached(client, "compare", request_params),
        lambda payload: _show_comparison(payload),
    )
    if st.button("Refresh comparison", key="refresh_compare"):
        _cached_get.clear()
        st.rerun()


def _show_comparison(payload: dict[str, Any]) -> None:
    st.metric("Period A mean - Period B mean", f"{payload.get('difference')} {payload.get('unit', '')}")
    st.plotly_chart(comparison_figure(payload), use_container_width=True)
    st.json({"period_a": payload.get("period_a"), "period_b": payload.get("period_b")})


def _render_heatmap(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Month-by-year heatmap")
    _run_request(
        "Heatmap",
        lambda: _load_cached(client, "heatmap", params),
        lambda payload: _show_heatmap(payload),
    )
    if st.button("Refresh heatmap", key="refresh_heatmap"):
        _cached_get.clear()
        st.rerun()


def _show_heatmap(payload: dict[str, Any]) -> None:
    if not payload.get("data"):
        st.info("No heatmap cells returned.")
        return
    st.plotly_chart(heatmap_figure(payload), use_container_width=True)


def _render_map(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Map")
    st.caption("The backend currently returns point-based map data for the selected location.")
    _run_request(
        "Map",
        lambda: _load_cached(client, "map", params),
        lambda payload: _show_map(payload),
    )
    if st.button("Refresh map", key="refresh_map"):
        _cached_get.clear()
        st.rerun()


def _show_map(payload: dict[str, Any]) -> None:
    if not payload.get("data"):
        st.info("No map points returned.")
        return
    st.plotly_chart(map_figure(payload), use_container_width=True)
    st.dataframe(pd.DataFrame(payload["data"]), use_container_width=True)


def _render_downloads(client: DashboardApiClient, params: dict[str, Any]) -> None:
    st.subheader("Download job")
    st.caption("Creates a backend job and displays status metadata. Artifact download is not exposed by the API yet.")

    if st.button("Create download job", key="create_download"):
        payload = _download_payload(params)
        _run_request(
            "Download job",
            lambda: client.create_download(payload),
            lambda response: _remember_job(response),
        )

    job_id = st.session_state.get("download_job_id")
    if job_id:
        st.info(f"Current job: {job_id}")
        if st.button("Refresh job status", key="refresh_job"):
            _run_request(
                "Job status",
                lambda: client.get_job(job_id),
                lambda payload: _show_job_status(payload),
            )


def _download_payload(params: dict[str, Any]) -> dict[str, Any]:
    latitude = float(params["latitude"])
    longitude = float(params["longitude"])
    return {
        "variable": params["variable"],
        "start_date": str(params["start"])[:10],
        "end_date": str(params["end"])[:10],
        "area": {
            "north": min(90.0, latitude + 0.25),
            "west": max(-180.0, longitude - 0.25),
            "south": max(-90.0, latitude - 0.25),
            "east": min(180.0, longitude + 0.25),
        },
        "frequency": "hourly",
    }


def _remember_job(payload: dict[str, Any]) -> None:
    st.session_state["download_job_id"] = payload.get("job_id")
    st.success(f"Job queued: {payload.get('job_id')}")
    st.json(payload)


def _show_job_status(payload: dict[str, Any]) -> None:
    status = payload.get("status")
    if status == "completed":
        st.success("Job completed")
    elif status == "failed":
        st.error(payload.get("error") or "Job failed")
    else:
        st.info(f"Job status: {status}")
    st.json(payload)


def _run_request(
    label: str,
    request: Callable[[], dict[str, Any]],
    render: Callable[[dict[str, Any]], None],
) -> None:
    try:
        with st.spinner(f"Loading {label.lower()}..."):
            payload = request()
    except DashboardApiError as exc:
        st.error(str(exc))
        return
    render(payload)


if __name__ == "__main__":
    main()
