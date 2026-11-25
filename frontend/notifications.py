"""Notification and alert utilities for intuitive, top-positioned visual feedback."""
import re
import streamlit as st


# ---------------------------------------------------------------------------
# Error message translation table (English backend → Vietnamese user-friendly)
# ---------------------------------------------------------------------------
_ERROR_TRANSLATIONS: list[tuple[str, str]] = [
    # ERA5 availability lag
    (
        r"ERA5 data is not normally available for the requested end date (\S+)\.\s*"
        r"Latest configured available date is (\S+)[^.]*\.",
        lambda m: (
            f"⏳ Dữ liệu ERA5 chưa sẵn sàng cho ngày kết thúc **{m.group(1)}**.\n\n"
            f"Ngày muộn nhất hiện có: **{m.group(2)}**.\n\n"
            "Vui lòng chọn ngày kết thúc sớm hơn."
        ),
    ),
    # Climate / query day limit
    (
        r"Climate queries are limited to (\d+) days\.",
        lambda m: (
            f"📅 Khoảng thời gian truy vấn tối đa là **{m.group(1)} ngày**.\n\n"
            "Vui lòng thu hẹp khoảng thời gian được chọn."
        ),
    ),
    # ERA5 request day limit (Phase 4)
    (
        r"ERA5 requests are limited to (\d+) days",
        lambda m: (
            f"📅 Mỗi yêu cầu ERA5 tối đa **{m.group(1)} ngày**.\n\n"
            "Vui lòng chia nhỏ khoảng thời gian hoặc thu hẹp phạm vi."
        ),
    ),
    # Date range
    (
        r"end[_ ]?date must be greater than or equal to start[_ ]?date",
        lambda m: "📆 Ngày kết thúc phải lớn hơn hoặc bằng ngày bắt đầu.",
    ),
    (
        r"end must be greater than or equal to start",
        lambda m: "📆 Thời điểm kết thúc phải sau thời điểm bắt đầu.",
    ),
    # Area / location
    (
        r"Provide exactly one of area or location\.",
        lambda m: "📍 Vui lòng chọn chính xác một trong hai: vùng hoặc địa điểm.",
    ),
    (
        r"Land time-series collects one location, not an area\.",
        lambda m: "📍 Dữ liệu chuỗi thời gian đất chỉ hỗ trợ một điểm vị trí, không hỗ trợ vùng.",
    ),
    (
        r"ERA5 area collection requires area bounds\.",
        lambda m: "📍 Yêu cầu thu thập vùng ERA5 cần có ranh giới vùng (bắc/nam/đông/tây).",
    ),
    (
        r"north must be greater than or equal to south",
        lambda m: "🗺️ Vĩ độ Bắc phải lớn hơn hoặc bằng vĩ độ Nam.",
    ),
    (
        r"east must be greater than or equal to west",
        lambda m: "🗺️ Kinh độ Đông phải lớn hơn hoặc bằng kinh độ Tây.",
    ),
    # Unsupported variable
    (
        r"Unsupported variable '([^']+)'\.",
        lambda m: f"❓ Biến khí hậu **{m.group(1)}** không được hỗ trợ.",
    ),
    # No valid observations
    (
        r"No valid observations were imported",
        lambda m: "📭 Không có quan sát hợp lệ nào được nhập. Dữ liệu gốc được giữ nguyên.",
    ),
    (
        r"No valid observations for the requested variable and interval",
        lambda m: "📭 Không có dữ liệu hợp lệ cho biến và khoảng thời gian được yêu cầu.",
    ),
    # Legacy / config mismatch
    (
        r"This legacy job has no recorded source",
        lambda m: "⚙️ Tác vụ cũ này không có nguồn được ghi nhận. Vui lòng tạo tác vụ tải dữ liệu mới.",
    ),
    (
        r"Dataset configuration changed since this job was created",
        lambda m: "⚙️ Cấu hình bộ dữ liệu đã thay đổi kể từ khi tạo tác vụ. Vui lòng tạo tác vụ mới.",
    ),
    # Cancelled
    (
        r"Cancelled by user",
        lambda m: "🛑 Tác vụ đã bị hủy bởi người dùng.",
    ),
    # Trend analysis
    (
        r"Trend analysis requires at least two observations\.",
        lambda m: "📊 Phân tích xu hướng cần ít nhất 2 quan sát.",
    ),
    (
        r"Analysis requires at least one observation\.",
        lambda m: "📊 Phân tích cần ít nhất 1 quan sát.",
    ),
    # Wind
    (
        r"Wind direction requires circular mean",
        lambda m: "💨 Hướng gió yêu cầu dùng trung bình vòng tròn; không hỗ trợ min/max/sum.",
    ),
    (
        r"Sum aggregation is only supported for precipitation and radiation\.",
        lambda m: "🌧️ Tổng tích lũy chỉ áp dụng cho lượng mưa và bức xạ.",
    ),
    # Job persistence
    (
        r"Download job could not be marked running",
        lambda m: "💾 Không thể cập nhật trạng thái tác vụ tải. Vui lòng thử lại.",
    ),
    (
        r"Download job failure status could not be persisted",
        lambda m: "💾 Không thể lưu trạng thái lỗi tác vụ. Vui lòng kiểm tra lại.",
    ),
]


def _translate_error(message: str) -> str:
    """Return a Vietnamese-friendly version of a known English error message, or the original."""
    for pattern, fn in _ERROR_TRANSLATIONS:
        m = re.search(pattern, message, re.IGNORECASE | re.DOTALL)
        if m:
            try:
                return fn(m)
            except Exception:
                pass  # fall through to original on any substitution error
    return message


# ---------------------------------------------------------------------------
# Session-state deduplication helpers
# ---------------------------------------------------------------------------

def _toast_key(message: str) -> str:
    return f"_toast_shown_{hash(message)}"


def _clear_toast(message: str) -> None:
    """Call when an error/warning clears so it can re-appear if it recurs."""
    st.session_state.pop(_toast_key(message), None)


# ---------------------------------------------------------------------------
# Public notification functions
# ---------------------------------------------------------------------------

def notify_error(message: str, icon: str = "❌"):
    """Show a persistent error toast at the top.

    De-duplicated per session: the same message is only toasted once so that
    polling fragments do not re-spawn it on every render cycle.
    Call ``_clear_toast(message)`` to allow re-notification after the error clears.
    """
    key = _toast_key(message)
    if st.session_state.get(key):
        return
    translated = _translate_error(message)
    st.toast(translated, icon=icon, duration="infinite")
    st.session_state[key] = True


def notify_warning(message: str, icon: str = "⚠️"):
    """Show a persistent warning toast at the top (de-duplicated)."""
    key = _toast_key(message)
    if st.session_state.get(key):
        return
    translated = _translate_error(message)
    st.toast(translated, icon=icon, duration="infinite")
    st.session_state[key] = True


def notify_success(message: str, icon: str = "✅"):
    """Show a brief success toast (auto-dismisses, not de-duplicated)."""
    st.toast(message, icon=icon)


def notify_info(message: str, icon: str = "ℹ️"):
    """Show a brief info toast (auto-dismisses, not de-duplicated)."""
    st.toast(message, icon=icon)


def render_empty_data_notice(section_name: str = "dữ liệu"):
    """Display a prominent visual card when no data is downloaded yet."""
    st.html(f"""
        <div class="era5-visual-alert warning">
            <div class="alert-icon">⚠️</div>
            <div class="alert-content">
                <div class="alert-title">Chưa tải dữ liệu khí hậu</div>
                <div class="alert-desc">
                    Hệ thống chưa tìm thấy bộ dữ liệu ERA5 nào được lưu trong kho để hiển thị {section_name}.
                    Vui lòng sang mục <strong>Thu thập</strong> để tạo tác vụ tải dữ liệu trước.
                </div>
            </div>
        </div>
    """)
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🚀 Sang mục Thu thập ngay", key=f"empty_goto_collect_{section_name}", type="primary"):
            st.session_state['workflow_section'] = 'Thu thập'
            st.query_params['view'] = 'Thu thập'
            st.rerun()
