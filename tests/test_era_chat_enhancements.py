"""Unit tests for ERA chat enhancements (contextual quick prompts, comparison context & Markdown export)."""
from backend.app.ai.chart_analysis import render_comparison_analysis, summarize_plot
from frontend.ai_chat import contextual_quick_prompts, format_chat_export
from frontend.chat_actions import applied_comparison


def test_contextual_quick_prompts_with_active_view():
    context = {
        "page": "chart",
        "collection_id": "abc123",
        "parameters": {"variable": "temperature"},
    }
    prompts = contextual_quick_prompts(context)

    assert len(prompts) == 3
    assert prompts[0][0] == "Phân tích bộ đang xem"
    assert any("cực trị" in label.lower() for label, _ in prompts)
    assert any("thiếu" in label.lower() for label, _ in prompts)


def test_contextual_quick_prompts_with_comparison_mode():
    comp = {"place_a": "Hà Nội", "place_b": "TP.HCM"}
    prompts = contextual_quick_prompts(None, comparison=comp)

    assert len(prompts) == 2
    assert prompts[0][0] == "Phân tích so sánh A & B"


def test_contextual_quick_prompts_with_selected_collection_only():
    prompts = contextual_quick_prompts(None, selected_name="Đà Nẵng")

    assert len(prompts) == 2
    assert "Đà Nẵng" in prompts[0][1]


def test_contextual_quick_prompts_default_when_no_context():
    prompts = contextual_quick_prompts(None)

    assert len(prompts) == 3
    assert "Kho đang có" in prompts[0][1]


def test_applied_comparison_validates_state():
    params = {
        "variable": "solar_radiation",
        "latitude": 21.0,
        "longitude": 105.8,
        "start": "2026-11-20T00:00:00+07:00",
        "end": "2026-11-21T00:00:00+07:00",
        "frequency": "daily",
        "aggregation": "auto",
    }
    state = {
        "workflow_section": "Biểu đồ",
        "chart_details_visible": False,
        "chart_compare_enabled": True,
        "chart_compare_context": {
            "context": {
                "collection_id_a": "col_a",
                "collection_id_b": "col_b",
                "parameters_a": params,
                "parameters_b": {**params, "latitude": 10.8, "longitude": 106.7},
                "align_by": "time",
            },
            "place_a": "Hà Nội",
            "place_b": "TP.HCM",
            "variable": "solar_radiation",
        },
    }
    result = applied_comparison(state)
    assert result is not None
    assert result["place_a"] == "Hà Nội"

    state["chart_compare_enabled"] = False
    assert applied_comparison(state) is None


def test_render_comparison_analysis_formats_verified_diff():
    pa = {
        "variable": "solar_radiation",
        "unit": "MJ/m²",
        "complete": True,
        "data": [
            {"time": "2026-11-20T00:00:00+07:00", "value": 10.0},
            {"time": "2026-11-21T00:00:00+07:00", "value": 12.0},
        ],
    }
    pb = {
        "variable": "solar_radiation",
        "unit": "MJ/m²",
        "complete": True,
        "data": [
            {"time": "2026-11-20T00:00:00+07:00", "value": 19.0},
            {"time": "2026-11-21T00:00:00+07:00", "value": 21.0},
        ],
    }
    sa = summarize_plot(pa, "daily")
    sb = summarize_plot(pb, "daily")
    text = render_comparison_analysis(
        {
            "data": {
                "series_a": sa,
                "series_b": sb,
                "diff_mean": sa["mean"] - sb["mean"],
                "metadata_a": {"variable": "solar_radiation", "available_count": 48, "expected_count": 48, "coverage": 1.0},
                "metadata_b": {"variable": "solar_radiation", "available_count": 48, "expected_count": 48, "coverage": 1.0},
                "provenance_a": {"place_name": "Hà Nội", "start": "2026-11-20T00:00:00+07:00", "end": "2026-11-21T23:00:00+07:00", "synthetic": False},
                "provenance_b": {"place_name": "TP.HCM", "start": "2026-11-20T00:00:00+07:00", "end": "2026-11-21T23:00:00+07:00", "synthetic": False},
            }
        }
    )

    assert "Hà Nội" in text and "TP.HCM" in text
    assert "-9.00 MJ/m²" in text
    assert "thấp hơn" in text


def test_format_chat_export_markdown():
    md = format_chat_export(
        [
            {"role": "user", "content": "Nhiệt độ trung bình bao nhiêu?"},
            {"role": "assistant", "content": "**Trung bình:** 26.5 °C"},
        ]
    )

    assert md.startswith("# Hội thoại với Trợ lý ERA")
    assert "## Người dùng" in md
    assert "## Trợ lý ERA" in md
    assert "**Trung bình:** 26.5 °C" in md
