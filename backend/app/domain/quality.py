"""Per-job quality accounting, isolated with ContextVar from concurrent requests."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, Field


class QualityReport(BaseModel):
    status: Literal["not_checked", "passed", "warnings", "failed"] = "not_checked"
    complete: bool = False
    rows_read: int = 0
    valid_values: int = 0
    imported_values: int = 0
    missing_values: int = 0
    invalid_values: int = 0
    invalid_time_rows: int = 0
    duplicate_values: int = 0
    missing_hours: int = 0
    issue_count: int = 0
    issue_counts: dict[str, int] = Field(default_factory=dict)


@dataclass
class QualityCollector:
    sink: Callable
    report: QualityReport = field(default_factory=QualityReport)
    buffer: list = field(default_factory=list)

    def issue(self, code, message, *, source_file=None, row_number=None, variable=None,
              time_utc=None, latitude=None, longitude=None):
        self.report.issue_count += 1
        self.report.issue_counts[code] = self.report.issue_counts.get(code, 0) + 1
        if code == "missing_value": self.report.missing_values += 1
        if code in {"invalid_number", "nonfinite_value"}: self.report.invalid_values += 1
        if code == "invalid_time": self.report.invalid_time_rows += 1
        if code in {"duplicate", "duplicate_conflict"}: self.report.duplicate_values += 1
        if code == "missing_hour": self.report.missing_hours += 1
        self.buffer.append(dict(code=code, severity="warning", message=message,
            source_file=source_file, row_number=row_number, variable=variable,
            time_utc=time_utc.isoformat() if time_utc is not None else None,
            latitude=latitude, longitude=longitude))
        if len(self.buffer) >= 200: self.flush()

    def flush(self):
        if self.buffer:
            self.sink(self.buffer)
            self.buffer.clear()

    def finish(self, failed=False):
        self.flush()
        self.report.complete = not failed and not self.report.issue_count
        self.report.status = "failed" if failed else "warnings" if self.report.issue_count else "passed"
        return self.report


current_quality: ContextVar[QualityCollector | None] = ContextVar("collection_quality", default=None)
