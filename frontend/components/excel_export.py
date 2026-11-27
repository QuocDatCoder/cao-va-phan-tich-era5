"""Build in-memory Excel reports from stored climate series."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from math import isfinite
from statistics import fmean

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from backend.app.domain.wind import circular_statistics
from frontend.components.charts import variable_label

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LOCAL_TIME = timezone(timedelta(hours=7))
TIME_HEADER = "Thời gian (UTC+7)"
TIME_FORMAT = "dd/mm/yyyy hh:mm"
COLLECTION_SUMMARY_HEADERS = (
    "Biến", "Đơn vị", "Trung bình", "Lớn nhất", "Nhỏ nhất", "Độ phủ (%)",
    "Số giờ có / yêu cầu", "Địa điểm", "Nguồn",
)


def _series_header(payload: dict) -> str:
    name = variable_label(payload.get("variable"))
    unit = payload.get("unit")
    return f"{name} ({unit})" if unit else name


def _series_values(payload: dict) -> dict[datetime, float | None]:
    series = {}
    for point in payload.get("data", []):
        stamp = datetime.fromisoformat(point["time"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        local_time = stamp.astimezone(LOCAL_TIME).replace(tzinfo=None)
        value = point.get("value")
        series[local_time] = value if value is not None and isfinite(value) else None
    return series


def _style_table(sheet: Worksheet) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.row_dimensions[1].height = 22
    font = Font(bold=True, color="20354A")
    fill = PatternFill("solid", fgColor="E6EFF7")
    alignment = Alignment(horizontal="center", vertical="center")
    for index, column in enumerate(sheet.iter_cols(), start=1):
        header = column[0]
        header.font, header.fill, header.alignment = font, fill, alignment
        width = 10
        for cell in column:
            value = cell.value
            if value is None:
                continue
            if isinstance(value, str):
                # Keep labels and metadata literal even if Excel interprets them as formulas.
                cell.data_type = "s"
            if isinstance(value, datetime):
                cell.number_format = TIME_FORMAT
            width = max(width, len(str(value)))
        sheet.column_dimensions[get_column_letter(index)].width = min(width + 4, 255)


def _workbook_bytes(workbook: Workbook) -> bytes:
    for sheet in workbook.worksheets:
        _style_table(sheet)
    with BytesIO() as buffer:
        workbook.save(buffer)
        return buffer.getvalue()


def build_collection_excel(payloads: list[dict], place_name: str = "") -> bytes:
    """Export the union of observed timestamps and one summary row per variable."""
    workbook = Workbook()
    data = workbook.active
    data.title = "Du_lieu"
    data.freeze_panes = "A2"
    data.append([TIME_HEADER, *(_series_header(payload) for payload in payloads)])
    summary = workbook.create_sheet("Thong_ke")
    summary.append(COLLECTION_SUMMARY_HEADERS)

    series_list = [_series_values(payload) for payload in payloads]
    times = sorted({stamp for series in series_list for stamp in series})
    for stamp in times:
        data.append([stamp, *(series.get(stamp) for series in series_list)])

    for payload, series in zip(payloads, series_list):
        values = [value for value in series.values() if value is not None]
        variable = payload.get("variable")
        mean = None
        if values:
            mean = circular_statistics(values)[0] if variable == "wind_direction" else fmean(values)
        available = payload.get("available_count", len(values))
        expected = payload.get("expected_count", len(series))
        coverage = payload.get("coverage", available / expected if expected else 0)
        summary.append([
            variable_label(variable), payload.get("unit"), mean,
            max(values, default=None), min(values, default=None), coverage,
            f"{available} / {expected}", place_name or payload.get("place_name"), payload.get("source"),
        ])
        summary.cell(summary.max_row, 6).number_format = "0.0%"

    return _workbook_bytes(workbook)


def build_comparison_excel(
    payload_a: dict, payload_b: dict, label_a: str, label_b: str, align_by: str = "time",
) -> bytes:
    """Export the chart summary and both series with their original local timestamps."""
    from frontend.components.comparison_view import comparison_summary_rows

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Thong_ke"
    rows = comparison_summary_rows(payload_a, payload_b, label_a, label_b, align_by=align_by)
    headers = list(rows[0])
    summary.append(headers)
    for row in rows:
        summary.append([row[header] for header in headers])

    for title, payload in (("Du_lieu_A", payload_a), ("Du_lieu_B", payload_b)):
        data = workbook.create_sheet(title)
        data.freeze_panes = "A2"
        data.append([TIME_HEADER, _series_header(payload)])
        for stamp, value in sorted(_series_values(payload).items()):
            data.append([stamp, value])

    return _workbook_bytes(workbook)
