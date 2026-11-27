"""Verify the sheets and values users receive in the Excel downloads."""
import io
from datetime import datetime

import pytest
from openpyxl import load_workbook

from frontend.components.excel_export import build_collection_excel, build_comparison_excel


def _payload(variable, unit, points, **metadata):
    return {
        "variable": variable,
        "unit": unit,
        "data": [{"time": time, "value": value} for time, value in points],
        **metadata,
    }


def test_collection_excel_merges_times_and_preserves_missing_values():
    temperature = _payload("temperature", "°C", [
        ("2026-11-01T02:00:00Z", 24.0),
        ("2026-11-01T00:00:00Z", 20.0),
        ("2026-11-01T01:00:00Z", None),
    ], available_count=2, expected_count=4, coverage=0.5, place_name="Hà Nội", source="cds")
    rain = _payload("precipitation", "mm", [
        ("2026-11-01T07:00:00+07:00", 0.0),
        ("2026-11-01T10:00:00+07:00", float("nan")),
    ], expected_count=4, place_name="Hà Nội", source="cds")

    workbook = load_workbook(io.BytesIO(build_collection_excel([temperature, rain])))

    assert workbook.sheetnames == ["Du_lieu", "Thong_ke"]
    data = workbook["Du_lieu"]
    assert list(data.values) == [
        ("Thời gian (UTC+7)", "Nhiệt độ (°C)", "Lượng mưa (mm)"),
        (datetime(2026, 11, 1, 7), 20, 0),
        (datetime(2026, 11, 1, 8), None, None),
        (datetime(2026, 11, 1, 9), 24, None),
        (datetime(2026, 11, 1, 10), None, None),
    ]
    summary = workbook["Thong_ke"]
    assert list(summary.values) == [
        ("Biến", "Đơn vị", "Trung bình", "Lớn nhất", "Nhỏ nhất", "Độ phủ (%)",
         "Số giờ có / yêu cầu", "Địa điểm", "Nguồn"),
        ("Nhiệt độ", "°C", 22, 24, 20, 0.5, "2 / 4", "Hà Nội", "cds"),
        ("Lượng mưa", "mm", 0, 0, 0, 0.25, "1 / 4", "Hà Nội", "cds"),
    ]
    assert data["A2"].number_format == "dd/mm/yyyy hh:mm"
    assert summary["F2"].number_format == "0.0%"
    for sheet in workbook.worksheets:
        assert sheet["A1"].font.bold
        assert sheet["A1"].fill.patternType == "solid"
        assert sheet.column_dimensions["A"].width > len(sheet["A1"].value)


def test_collection_excel_handles_empty_series_and_place_override():
    empty = _payload("temperature", "°C", [], expected_count=24, place_name="Hà Nội")
    workbook = load_workbook(io.BytesIO(build_collection_excel([empty], place_name="Đà Nẵng")))

    assert list(workbook["Du_lieu"].values) == [("Thời gian (UTC+7)", "Nhiệt độ (°C)")]
    assert list(workbook["Thong_ke"].values)[1] == (
        "Nhiệt độ", "°C", None, None, None, 0, "0 / 24", "Đà Nẵng", None,
    )


@pytest.mark.parametrize("align_by", ["time", "elapsed"])
def test_comparison_excel_exports_summary_and_each_original_series(align_by):
    a = _payload("temperature", "°C", [
        ("2026-11-01T17:00:00+00:00", 10.0),
        ("2026-11-01T18:00:00+00:00", None),
        ("2026-11-01T19:00:00+00:00", 20.0),
    ])
    b = _payload("temperature", "°C", [("2026-11-15T07:00:00+07:00", 12.0)])

    workbook = load_workbook(io.BytesIO(build_comparison_excel(a, b, "Bộ A", "Bộ B", align_by)))

    assert workbook.sheetnames == ["Thong_ke", "Du_lieu_A", "Du_lieu_B"]
    assert list(workbook["Thong_ke"].values) == [
        ("Chỉ số", "Bộ A", "Bộ B", "Chênh lệch (A - B)"),
        ("Trung bình (°C)", "15.00", "12.00", "+3.00"),
        ("Lớn nhất (°C)", "20.00", "12.00", "+8.00"),
        ("Nhỏ nhất (°C)", "10.00", "12.00", "-2.00"),
        ("Số điểm dữ liệu", "2", "1", "1"),
    ]
    header = ("Thời gian (UTC+7)", "Nhiệt độ (°C)")
    assert list(workbook["Du_lieu_A"].values) == [
        header,
        (datetime(2026, 11, 2, 0), 10),
        (datetime(2026, 11, 2, 1), None),
        (datetime(2026, 11, 2, 2), 20),
    ]
    assert list(workbook["Du_lieu_B"].values) == [header, (datetime(2026, 11, 15, 7), 12)]


def test_comparison_excel_keeps_both_columns_when_labels_match():
    a = _payload("temperature", "°C", [("2026-11-01T00:00:00Z", 10)])
    b = _payload("temperature", "°C", [])
    workbook = load_workbook(io.BytesIO(build_comparison_excel(a, b, "Hà Nội", "Hà Nội")))

    rows = list(workbook["Thong_ke"].values)
    assert rows[0] == ("Chỉ số", "A · Hà Nội", "B · Hà Nội", "Chênh lệch (A - B)")
    assert rows[1] == ("Trung bình (°C)", "10.00", "—", "—")
    assert list(workbook["Du_lieu_B"].values) == [("Thời gian (UTC+7)", "Nhiệt độ (°C)")]
