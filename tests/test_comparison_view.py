"""Unit tests for the 2-collection comparison view component."""
from streamlit.testing.v1 import AppTest

from frontend.components.comparison_view import (
    build_comparison_series,
    comparison_summary_rows,
    multi_series_comparison_figure,
)


def _sample_payload(values, *, start_day=1, variable="temperature", unit="°C", frequency="hourly"):
    return {
        "variable": variable,
        "unit": unit,
        "frequency": frequency,
        "data": [
            {"time": f"2026-11-{start_day:02d}T{hour:02d}:00:00+00:00", "value": val}
            for hour, val in enumerate(values)
        ],
    }


def test_build_comparison_series_time_alignment():
    a = _sample_payload([20.0, 22.0, 24.0])
    b = _sample_payload([28.0, 30.0, 32.0])

    bundle = build_comparison_series(a, b, "Hà Nội", "TP.HCM", align_by="time")

    assert bundle["variable"] == "Nhiệt độ"
    assert bundle["unit"] == "°C"
    assert bundle["stats_a"]["mean"] == 22.0
    assert bundle["stats_b"]["mean"] == 30.0
    assert bundle["diff_mean"] == -8.0
    assert bundle["diff_max"] == -8.0
    assert bundle["diff_min"] == -8.0


def test_build_comparison_series_elapsed_alignment():
    a = _sample_payload([18.0, 20.0], start_day=1)
    b = _sample_payload([21.0, 25.0], start_day=15)

    bundle = build_comparison_series(a, b, "Đợt 1", "Đợt 2", align_by="elapsed")

    assert list(bundle["frame_a"]["x"]) == [0, 1]
    assert list(bundle["frame_b"]["x"]) == [0, 1]
    assert bundle["diff_mean"] == -4.0


def test_comparison_summary_rows_and_empty_handling():
    a = _sample_payload([10.0, 20.0])
    b = _sample_payload([])

    rows = comparison_summary_rows(a, b, "Bộ A", "Bộ B")

    assert len(rows) == 4
    assert rows[0]["Bộ A"] == "15.00"
    assert rows[0]["Bộ B"] == "—"
    assert rows[0]["Chênh lệch (A - B)"] == "—"
    assert rows[3]["Bộ A"] == "2"
    assert rows[3]["Bộ B"] == "0"


def test_multi_series_comparison_figure_builds_two_traces():
    a = _sample_payload([25.0, 26.0, 27.0])
    b = _sample_payload([30.0, 31.0, 29.0])

    fig = multi_series_comparison_figure(a, b, "Hà Nội", "TP.HCM", align_by="time")

    assert len(fig.data) == 2
    assert fig.data[0].name == "Hà Nội"
    assert fig.data[1].name == "TP.HCM"
    assert "Nhiệt độ" in fig.layout.yaxis.title.text


def test_comparison_submit_updates_era_rendered_before_workspace(monkeypatch):
    entries = {
        cid: {
            "id": cid, "status": "complete",
            "metadata": {
                "place_name": cid, "primary_variable": "temperature", "source": "mock",
                "dataset": "mock", "requested_start": "2026-11-01T00:00:00+00:00",
                "requested_end": "2026-11-01T02:00:00+00:00",
            },
            "grids": [{"latitude": 21.0, "longitude": 105.8}],
        }
        for cid in ("site-a", "site-b")
    }

    def read(api, method, *args):
        if method == "list_collections":
            return {"collections": list(entries.values())}
        if method == "get_collection":
            return entries[args[0]]
        return {
            **_sample_payload([20.0, 22.0]),
            "available_count": 2, "expected_count": 2, "coverage": 1.0, "complete": True,
        }

    def app():
        import streamlit as st
        from frontend.chat_actions import applied_comparison
        from frontend.components.comparison_view import render_comparison_panel

        @st.fragment
        def era():
            context = applied_comparison(st.session_state)
            st.caption(context["place_b"] if context else "No comparison")

        era()
        render_comparison_panel(None)

    monkeypatch.setattr("frontend.library.cached_read", read)
    at = AppTest.from_function(app)
    at.session_state["workflow_section"] = "Biểu đồ"
    at.session_state["chart_compare_enabled"] = True
    at.session_state["ai_chat_open"] = True
    at.run()
    assert at.caption[0].value == "No comparison"
    at.button(key="compare_submit").click().run()

    assert not at.exception
    assert at.caption[0].value == "site-b"
    assert at.session_state["chart_compare_context"]["context"]["collection_id_b"] == "site-b"
    assert len(at.get("plotly_chart")) == 1
