"""Kiểm tra nhanh các API mà nhóm AI Agent sẽ gọi. Chạy khi backend đang chạy:

    .\\.venv\\Scripts\\python.exe smoke_test_api.py                 # theo backend đang chạy
    .\\.venv\\Scripts\\python.exe smoke_test_api.py --variables temperature

Với ERA5 thật, các khoảng ngày bên dưới phải được tải sẵn (nếu không sẽ chờ CDS rất lâu).
Mã thoát 0 = tất cả PASS, 1 = có FAIL.
"""
import argparse
import sys
from datetime import datetime

import requests

FAILURES: list[str] = []


def report(ok: bool, name: str, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def call(args, path: str, params: dict | None = None, expect: int = 200):
    try:
        response = requests.get(args.base_url + path, params=params, timeout=args.timeout)
    except requests.RequestException as exc:
        report(False, path, f"không gọi được: {exc}")
        return None
    if response.status_code != expect:
        report(False, f"{path} {params.get('variable', '') if params else ''}".strip(),
               f"HTTP {response.status_code}, mong đợi {expect}: {response.text[:200]}")
        return None
    try:
        return response.json()
    except ValueError:
        report(False, path, "phản hồi không phải JSON")
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--lat", type=float, default=10.8231)
    p.add_argument("--lon", type=float, default=106.6297)
    p.add_argument("--start", default="2025-01-01T00:00:00+07:00")
    p.add_argument("--end", default="2025-01-02T23:00:00+07:00")
    p.add_argument("--baseline-start", default="2024-12-30T00:00:00+07:00")
    p.add_argument("--baseline-end", default="2024-12-31T23:00:00+07:00")
    p.add_argument("--b-start", default="2025-01-03T00:00:00+07:00")
    p.add_argument("--b-end", default="2025-01-04T23:00:00+07:00")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--variables", default=(
        "temperature,dewpoint,precipitation,wind_u,wind_v,wind_speed,wind_direction,solar_radiation"))
    args = p.parse_args()

    base = {"latitude": args.lat, "longitude": args.lon, "start": args.start, "end": args.end}
    expected_points = int(
        (datetime.fromisoformat(args.end) - datetime.fromisoformat(args.start)).total_seconds() // 3600
    ) + 1

    health = call(args, "/health")
    report(health is not None, "/health")
    config = call(args, "/api/v1/synchronization/config")
    report(config is not None, "/api/v1/synchronization/config")

    for variable in [v.strip() for v in args.variables.split(",") if v.strip()]:
        params = {**base, "variable": variable}
        series = call(args, "/api/v1/climate/timeseries", params)
        if series is not None:
            points = series.get("data", [])
            times_ok = all(str(pt.get("time", "")).endswith("+07:00") for pt in points)
            # Hướng gió bỏ giờ lặng gió nên có thể ít điểm hơn.
            count_ok = (0 < len(points) <= expected_points) if variable == "wind_direction" \
                else len(points) == expected_points
            report(count_ok and times_ok and bool(series.get("unit")),
                   f"timeseries {variable}",
                   f"{len(points)}/{expected_points} điểm, đơn vị {series.get('unit')}, mốc +07:00: {times_ok}")
        stats = call(args, "/api/v1/climate/statistics", params)
        report(stats is not None, f"statistics {variable}")
        agg = call(args, "/api/v1/climate/aggregate",
                   {**params, "frequency": "daily", "aggregation": "auto"})
        report(agg is not None, f"aggregate daily {variable}")

    first = args.variables.split(",")[0].strip() or "temperature"
    params = {**base, "variable": first}
    report(call(args, "/api/v1/climate/trend", params) is not None, f"trend {first}")
    report(call(args, "/api/v1/climate/anomaly", {
        **params, "baseline_start": args.baseline_start, "baseline_end": args.baseline_end,
    }) is not None, f"anomaly {first}")
    report(call(args, "/api/v1/climate/compare", {
        "variable": first, "latitude": args.lat, "longitude": args.lon,
        "period_a_start": args.start, "period_a_end": args.end,
        "period_b_start": args.b_start, "period_b_end": args.b_end,
    }) is not None, f"compare {first}")
    report(call(args, "/api/v1/climate/heatmap", params) is not None, f"heatmap {first}")
    report(call(args, "/api/v1/climate/map", params) is not None, f"map {first}")

    # Hướng gió không có xu hướng tuyến tính: API phải từ chối bằng 422, không được trả số bịa.
    if "wind_direction" in args.variables:
        try:
            r = requests.get(args.base_url + "/api/v1/climate/trend",
                             params={**base, "variable": "wind_direction"}, timeout=args.timeout)
            report(r.status_code == 422, "trend wind_direction trả 422 đúng thiết kế", f"HTTP {r.status_code}")
        except requests.RequestException as exc:
            report(False, "trend wind_direction", f"không gọi được: {exc}")

    print()
    print("TẤT CẢ ĐỀU PASS" if not FAILURES else f"CÓ {len(FAILURES)} MỤC FAIL: {', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())