"""Display hourly observations and summaries without changing the hourly export."""
from datetime import datetime
from math import ceil

import pandas as pd
import streamlit as st

from backend.app.domain.wind import circular_statistics
from backend.app.services.aggregation import resolve_aggregation
from frontend.components.charts import variable_label


PAGE_SIZE = 25
LEVELS = ('Giờ', 'Ngày', 'Tháng')
HOUR_LABEL = 'Thời gian (UTC+7)'


def summarize(frame, times, variables, level, start, end):
    frequency = 'D' if level == 'Ngày' else 'M'
    periods = times.dt.tz_localize(None).dt.to_period(frequency)
    summary = {}
    for item in variables:
        variable, unit = item['variable'], item['unit']
        label = variable_label(variable)
        values = frame[f'{label} ({unit})'].groupby(periods)
        method = resolve_aggregation(variable, 'auto')
        if method == 'sum':
            summary[f'Tổng {label.lower()} ({unit})'] = values.sum(min_count=1)
        elif method == 'circular_mean':
            summary[f'{label} trung bình góc ({unit})'] = values.agg(
                lambda group: circular_statistics(group.dropna().tolist())[0] if group.notna().any() else None
            )
        else:
            summary[f'{label} trung bình ({unit})'] = values.mean()
    index = pd.period_range(start.replace(tzinfo=None), end.replace(tzinfo=None), freq=frequency)
    return pd.DataFrame(summary).reindex(index)


def _open_month(key, selection_key, months):
    rows = st.session_state[selection_key].selection.rows
    if rows:
        month = months[rows[0]]
        st.session_state[key+'_month'] = month
        st.session_state[key+'_level'] = 'Ngày'
        st.session_state[f'{key}_Ngày_page_{month}'] = 0


@st.fragment
def render_stored_table(frame, timestamps, variables, collection_id, start, end):
    if frame.empty:
        st.info('Không có dòng dữ liệu trong khoảng đang xem.')
        return
    times = pd.to_datetime(timestamps, utc=True).dt.tz_convert('Asia/Ho_Chi_Minh')
    start = datetime.fromisoformat(start).astimezone(times.dt.tz)
    end = datetime.fromisoformat(end).astimezone(times.dt.tz)
    days = (end.date() - start.date()).days + 1
    key = f'stored_table_{collection_id}_{start.isoformat()}_{end.isoformat()}'
    default = 'Giờ' if days <= 7 else 'Ngày' if days <= 31 else 'Tháng'
    st.session_state.setdefault(key+'_level', default)
    level = st.segmented_control('Chi tiết bảng', LEVELS, required=True, key=key+'_level')

    if level == 'Giờ':
        day_key = key+'_day'
        with st.container(horizontal=True, vertical_alignment='bottom'):
            selected_day = st.date_input(
                'Lọc giờ theo ngày', value=None, min_value=start.date(), max_value=end.date(),
                key=day_key, format='DD/MM/YYYY',
                help='Gõ hoặc dán ngày theo dd/mm/yyyy, hoặc chọn trong lịch.',
            )
            if selected_day is not None:
                st.button('Xóa ngày', key=key+'_clear_day', icon=':material/close:',
                          on_click=st.session_state.update, args=({day_key: None},))
        page_key = f'{key}_hour_page_{selected_day}'
        table = frame if selected_day is None else frame[times.dt.date == selected_day]
    else:
        st.caption('Tổng hợp theo các giờ có dữ liệu trong khoảng đã lọc.')
        table = summarize(frame, times, variables, level, start, end)
        month = st.session_state.get(key+'_month') if level == 'Ngày' else None
        if month is not None:
            st.button('Xem tất cả ngày', icon=':material/close:', key=key+'_clear_month',
                      on_click=st.session_state.pop, args=(key+'_month', None))
            table = table[table.index.asfreq('M') == month]
        page_key = f'{key}_{level}_page_{month}'

    pages = max(1, ceil(len(table) / PAGE_SIZE))
    page = min(st.session_state.get(page_key, 0), pages - 1)
    first = page * PAGE_SIZE
    visible = table.iloc[first:first + PAGE_SIZE].copy()
    if level != 'Giờ':
        label = 'Ngày (UTC+7)' if level == 'Ngày' else 'Tháng (UTC+7)'
        visible.insert(0, label, visible.index.strftime('%d/%m/%Y' if level == 'Ngày' else '%m/%Y'))
    if level == 'Tháng':
        st.caption('Chọn một dòng tháng để xem từng ngày trong tháng đó.')
        selection_key = f'{key}_months_{page}'
        st.dataframe(visible, hide_index=True, width='stretch', height='content', alt='Tổng hợp dữ liệu theo tháng',
                     key=selection_key, selection_mode='single-row',
                     on_select=lambda: _open_month(key, selection_key, visible.index))
    else:
        st.dataframe(visible, hide_index=True, width='stretch', alt=f'Dữ liệu theo {level.lower()}')
    if pages > 1:
        with st.container(horizontal=True, vertical_alignment='center'):
            st.button('Trang trước', key=page_key+'_prev', disabled=page == 0,
                      on_click=st.session_state.update, args=({page_key: page-1},))
            st.caption(f'Trang {page+1}/{pages} · {len(table):,} dòng')
            st.button('Trang sau', key=page_key+'_next', disabled=page + 1 == pages,
                      on_click=st.session_state.update, args=({page_key: page+1},))
