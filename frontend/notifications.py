"""Notification and alert utilities for intuitive, top-positioned visual feedback."""
import re
from html import escape
from uuid import uuid4
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


def _show_notice(message: str, icon: str, kind: str, duration: int = 0) -> None:
    # Mount outside Streamlit's fragment tree so a polling rerun cannot dismiss it.
    notice_id = f"are-notice-{uuid4().hex}"
    st.html(f"""
        <template id="{notice_id}">
            <div class="are-notice are-notice-{kind}" role="{'alert' if kind == 'error' else 'status'}">
                <span aria-hidden="true">{escape(icon)}</span>
                <span class="are-notice-text">{escape(message.replace('**', ''))}</span>
                <button type="button" aria-label="Đóng thông báo">×</button>
            </div>
        </template>
        <script>
        (() => {{
            const template = document.getElementById('{notice_id}');
            if (!template) return;
            document.querySelector('.are-notice')?.querySelector('button').click();
            const notice = template.content.firstElementChild.cloneNode(true);
            const dismiss = () => {{
                clearTimeout(timer);
                notice.remove();
            }};
            const timer = {duration} ? setTimeout(dismiss, {duration}) : null;
            notice.querySelector('button').addEventListener('click', dismiss);
            document.body.append(notice);
            // The header and chat can create their own layers during a rerun.
            if (typeof notice.showPopover === 'function') {{
                notice.popover = 'manual';
                notice.showPopover();
            }}
        }})();
        </script>
    """, unsafe_allow_javascript=True)


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
    _show_notice(translated, icon, "error")
    st.session_state[key] = True


def notify_warning(message: str, icon: str = "⚠️"):
    """Show a warning once per session until its deduplication key is cleared."""
    key = _toast_key(message)
    if st.session_state.get(key):
        return
    translated = _translate_error(message)
    _show_notice(translated, icon, "warning", 8000)
    st.session_state[key] = True


def notify_success(message: str, icon: str = "✅"):
    """Show a brief success toast (auto-dismisses, not de-duplicated)."""
    _show_notice(message, icon, "success", 4000)


def notify_job_failures(jobs: list[dict]) -> None:
    messages = []
    for job in jobs:
        place = job.get('place_name') or job.get('collection_name') or job['job_id']
        status = 'bị gián đoạn' if job['status'] == 'interrupted' else 'thất bại'
        reason = _translate_error(job['error']) if job.get('error') else 'Tác vụ chưa ghi nhận lý do.'
        messages.append(f'Thu thập tại {place} {status}. {reason}')
    _show_notice('\n'.join(messages), '❌', 'error')


def notify_info(message: str, icon: str = "ℹ️"):
    """Show a brief info toast (auto-dismisses, not de-duplicated)."""
    _show_notice(message, icon, "info", 4000)


def render_empty_data_notice(section_name: str = "dữ liệu"):
    """Offer the next action after the empty-library toast."""
    st.button("Sang mục Thu thập", key=f"empty_goto_collect_{section_name}",
              type="primary", on_click=_go_to_collection)


def _go_to_collection():
    st.session_state['workflow_section'] = 'Thu thập'
    st.query_params['view'] = 'Thu thập'
    st.rerun()
