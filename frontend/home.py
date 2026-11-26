"""Climate workspace landing page with a preview of the user's saved data."""
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

from frontend.api_client import DashboardApiError
from frontend.components.charts import timeseries_figure, variable_label
from frontend.components.controls import VARIABLES
from frontend.library import STATUS, cached_read, open_chart_from_data
from frontend.workspace_sidebar import select_section

ASSETS = Path(__file__).with_name('components')
LOCAL = timezone(timedelta(hours=7))


@st.cache_data(show_spinner=False)
def _hero_assets(asset_versions):
    styles = (ASSETS / 'era5_home.css').read_text(encoding='utf-8')
    for name, marker in (('poster', 'DAY'), ('poster_night', 'NIGHT')):
        poster = (ASSETS / f'era5_home_{name}.webp').read_bytes()
        styles = styles.replace(f'ERA5_HOME_{marker}_URL', 'data:image/webp;base64,' + b64encode(poster).decode('ascii'))
    territory = (ASSETS / 'era5_home_territory.svg').read_text(encoding='utf-8')
    night = '.era5-islands{fill:#d7cb9e;stroke:#72888a;stroke-width:.5}'
    for mode, rules in [('DAY', ''), ('NIGHT', night)]:
        artwork = territory.replace('/*NIGHT*/', rules).encode('utf-8')
        styles = styles.replace(f'TERRITORY_{mode}_URL', 'data:image/svg+xml;base64,' + b64encode(artwork).decode('ascii'))
    script = (ASSETS / 'era5_home.js').read_text(encoding='utf-8')
    return f'<style>{styles}</style><script>{script}</script>'


def _open_section(section):
    select_section(section)
    st.rerun(scope='app')


def _render_hero():
    asset_versions = tuple((ASSETS / name).stat().st_mtime_ns for name in (
        'era5_home.css', 'era5_home.js', 'era5_home_poster.webp', 'era5_home_poster_night.webp',
        'era5_home_territory.svg',
    ))
    st.html(_hero_assets(asset_versions), unsafe_allow_javascript=True)
    with st.container(key='era5_home_hero', gap='small'):
        st.html(
            '<div class="era5-home-clouds" role="img" aria-label="Việt Nam, quần đảo Hoàng Sa và Trường Sa">'
            '<canvas aria-hidden="true"></canvas></div>'
        )
        with st.container(key='era5_home_intro', gap='small'):
            with st.container(key='era5_intro_first', gap='small'):
                st.title('Khám phá khí hậu với ERA5', anchor=False)
                st.write('Chọn địa điểm và khoảng thời gian để xem dữ liệu khí hậu theo giờ.')
            st.html("""
                <div class="era5-intro-second" aria-hidden="true">
                  <div class="era5-intro-title">Ngày ấy, nơi ấy,<br>trời thế nào?</div>
                  <p>Chọn một nơi quen thuộc và một ngày bạn nhớ để xem lại nhiệt độ, lượng mưa và gió theo từng giờ.</p>
                </div>
            """)
        with st.container(horizontal=True, gap='xsmall', key='era5_home_actions'):
            if st.button('Bắt đầu thu thập', key='home_open_cloud_download', type='primary',
                         icon=':material/cloud_download:'):
                _open_section('Thu thập')
            if st.button('Mở kho dữ liệu', key='home_open_folder_open', type='primary', icon=':material/folder_open:'):
                _open_section('Dữ liệu đã lưu')


def _window(metadata):
    return tuple(datetime.fromisoformat(metadata[f'requested_{edge}']).astimezone(LOCAL)
                 for edge in ('start', 'end'))


def _place_title(metadata):
    name = metadata['place_name']
    point = metadata.get('location') or {}
    if name == 'Tọa độ nhập tay' and point.get('latitude') is not None and point.get('longitude') is not None:
        return f"{point['latitude']:.4f}, {point['longitude']:.4f}"
    return name


def _select_recent(collection_id, labels):
    st.session_state.home_recent_id = collection_id
    if labels and st.session_state.get('home_preview_variable') not in labels:
        st.session_state.home_preview_variable = labels[0]


def _render_recent(entries, active_id):
    with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='center',
                      gap='xsmall', key='era5_home_recent_header'):
        st.subheader('Dữ liệu gần đây', anchor=False, width='content')
        if entries and st.button('Xem tất cả', key='home_recent_library', type='primary',
                                 help='Mở tất cả dữ liệu đã lưu'):
            _open_section('Dữ liệu đã lưu')
    if not entries:
        st.write('Chưa có bộ dữ liệu nào thu thập thành công.')
        if st.button('Thu thập bộ đầu tiên', key='home_collect_first', icon=':material/add:'):
            _open_section('Thu thập')
        return
    for index, entry in enumerate(entries):
        metadata = entry['metadata']
        start, end = _window(metadata)
        period = start.strftime('%d/%m/%Y') if start.date() == end.date() else f'{start:%d/%m/%Y} – {end:%d/%m/%Y}'
        sample = ' · Giả lập' if metadata.get('source') == 'mock' else ''
        summary = entry.get('summary') or {}
        expected = summary.get('expected_count', 0)
        coverage = STATUS.get(entry['status'], entry['status'])
        if expected:
            available = summary.get('available_count', 0)
            coverage = f'{available / expected:.0%} độ phủ ({available:,}/{expected:,} giờ)'
        lines = [f"**{_place_title(metadata)}**", period + sample, coverage]
        if entry.get('created_at'):
            collected = datetime.fromisoformat(entry['created_at']).astimezone(LOCAL)
            lines.append(f'Thu thập lúc {collected:%H:%M · %d/%m/%Y}')
        st.button('  \n'.join(lines), key=f"home_dataset_row_{index}_{entry['id']}", width='stretch',
                  on_click=_select_recent, args=(entry['id'], [variable_label(v) for v in _entry_variables(entry)]))

    active_index = next((i for i, entry in enumerate(entries) if entry['id'] == active_id), None)
    if active_index is not None:
        st.html(f'<style>.st-key-era5_home_recent [class*="st-key-home_dataset_row_{active_index}_"] '
                'button {border-color: var(--recent-accent); box-shadow: inset 3px 0 var(--recent-accent);}</style>')


def _entry_variables(entry):
    from backend.app.domain.public_variables import public_variables

    return public_variables([entry['metadata']['primary_variable'], *(item['variable'] for item in entry['variables'])])



def _find_preview(api, entries, label):
    for entry in entries:
        if not entry['grids'] or entry['status'] not in {'complete', 'partial'}:
            continue
        metadata = entry['metadata']
        names = _entry_variables(entry)
        variable = next((name for name in names if variable_label(name) == label), None)
        if not variable:
            continue
        start, end = _window(metadata)
        start = max(start, end - timedelta(hours=167))
        try:
            payload = cached_read(api, 'query_collection', entry['id'], 'timeseries', dict(
                **entry['grids'][0], variable=variable, start=start.isoformat(), end=end.isoformat(),
            ))
        except DashboardApiError:
            continue
        if payload.get('available_count'):
            return entry, payload
    return None


def _render_preview_header(preview):
    with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='center',
                      gap='xsmall', key='era5_home_preview_header'):
        st.subheader('Biểu đồ xem nhanh', anchor=False)
        if preview:
            entry, data = preview
            if st.button('Mở trong Biểu đồ', key='home_open_monitoring', type='primary', icon=':material/arrow_forward:'):
                open_chart_from_data(entry['id'], [data], 0)
                st.session_state.pop('chart_handoff_rerun', None)
                st.rerun(scope='app')
        elif st.button('Đi đến Biểu đồ', key='home_open_monitoring', type='primary', icon=':material/monitoring:'):
            _open_section('Biểu đồ')


def _render_preview(preview, label, details):
    if preview:
        entry, data = preview
        start, end = _window(data)
        sample = ' · Giả lập' if entry['metadata'].get('source') == 'mock' else ''
        with details, st.container(gap='xxsmall'):
            st.markdown(f"**{label} · {_place_title(entry['metadata'])}**")
            st.caption(f'{start:%d/%m/%Y %H:%M} – {end:%d/%m/%Y %H:%M} (UTC+7){sample}')
        figure = timeseries_figure(data)
        figure.update_layout(height=275, margin=dict(l=8, r=12, t=12, b=12))
        figure.update_xaxes(nticks=5, title_text='Giờ (UTC+7)')
        st.plotly_chart(figure, key='home_preview_plot', width='stretch',
                        config={'displayModeBar': False}, alt=f'Biểu đồ xem nhanh {label.lower()}')
    else:
        st.info(f'Chưa có dữ liệu {label.lower()} trong các bộ gần đây để xem nhanh.')


def _open_are(api):
    from frontend.ai_chat import _submit

    st.session_state['ai_chat_open'] = True
    st.session_state['ai_chat_unread'] = False
    _submit(api, 'Mình mới dùng ERA5. Hướng dẫn mình bắt đầu thu thập và xem dữ liệu nhé.')
    st.rerun(scope='app')


def _recent_completed(api):
    recent = []
    offset = 0
    page_size = 20
    while len(recent) < 3:
        entries = cached_read(api, 'list_collections', '', page_size, offset, keep_library_pages=False)['collections']
        recent.extend(entry for entry in entries if entry['status'] == 'complete')
        if len(entries) < page_size:
            break
        offset += page_size
    return recent[:3]


def render_home(api):
    _render_hero()
    try:
        recent = _recent_completed(api)
    except DashboardApiError:
        recent = None

    if recent is None:
        st.warning('Chưa tải được dữ liệu gần đây. Bạn có thể mở Kho dữ liệu để thử lại.')
    else:
        _render_home_panels(api, recent)
    _render_footer(api)


@st.fragment
def _render_home_panels(api, recent):
    with st.container(key='era5_home_panels'):
        preview_column, recent_column = st.columns([2, 1], gap='small', border=True)
        with preview_column, st.container(key='era5_home_preview', gap='small'):
            header = st.container()
            details = st.container()
            with st.container(gap='xxsmall'):
                st.caption('Biến khí hậu')
                label = st.pills('Biến của biểu đồ xem nhanh', list(VARIABLES), default='Nhiệt độ',
                                 label_visibility='collapsed', required=True, key='home_preview_variable',
                                 persist_state='session')
            selected = next((e for e in recent if e['id'] == st.session_state.get('home_recent_id')), None)
            preview = _find_preview(api, [selected] if selected else recent, label)
            with header:
                _render_preview_header(preview)
            _render_preview(preview, label, details)
        with recent_column, st.container(key='era5_home_recent', gap='small'):
            _render_recent(recent, selected['id'] if selected else preview[0]['id'] if preview else None)



def _render_footer(api):
    with st.container(key='era5_home_sources', gap=None):
        groups = st.grid(4, min_column_width=160, gap='small', key='home_footer_directory')
        with groups.container(gap=None):
            st.subheader('Nguồn dữ liệu', anchor=False)
            st.html("""
                <nav aria-label="Nguồn dữ liệu khí hậu">
                  <a href="https://www.ecmwf.int/en/forecasts/dataset/ecmwf-reanalysis-v5">ERA5 tại ECMWF</a>
                  <a href="https://cds.climate.copernicus.eu/">Copernicus CDS</a>
                  <a href="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land">Bộ dữ liệu ERA5-Land</a>
                  <a href="https://open-meteo.com/en/docs/geocoding-api">Địa danh Open-Meteo</a>
                </nav>
            """)
        with groups.container(gap=None):
            st.subheader('Biến khí tượng', anchor=False)
            st.html('<ul class="era5-footer-list"><li>Nhiệt độ và điểm sương</li><li>Lượng mưa</li>'
                    '<li>Tốc độ và hướng gió</li><li>Bức xạ mặt trời</li></ul>')
        with groups.container(gap=None):
            st.subheader('Công cụ', anchor=False)
            for section, label in (('Thu thập', 'Thu thập theo địa điểm'), ('Dữ liệu đã lưu', 'Kho dữ liệu đã lưu'),
                                   ('Biểu đồ', 'Phân tích bằng biểu đồ')):
                st.button(label, key=f'home_footer_{section}', type='tertiary', on_click=select_section,
                          args=(section,))
            if st.button('Hỏi trợ lý ARE', key='home_ask_are', type='tertiary',
                         disabled=not st.session_state.get('ai_chat_history_loaded') or bool(st.session_state.get('ai_chat_pending'))):
                _open_are(api)
        with groups.container(gap=None):
            st.subheader('Thông tin bộ dữ liệu', anchor=False)
            st.html('<dl class="era5-footer-specs"><dt>Sản phẩm</dt><dd>ERA5-Land</dd>'
                    '<dt>Tần suất</dt><dd>Hàng giờ</dd><dt>Múi giờ</dt><dd>UTC+7</dd>'
                    '<dt>Xuất bảng</dt><dd>CSV</dd></dl>')
        st.html("""
            <div class="era5-footer-notes">
              <section><h4>Vị trí và điểm lưới</h4>
                <p>Tìm địa danh hoặc nhập tọa độ khi thu thập. Trong kho dữ liệu, bạn có thể xem điểm lưới của từng bộ.</p></section>
              <section><h4>Đọc độ phủ dữ liệu</h4>
                <p>Độ phủ cho biết số giờ có dữ liệu so với số giờ đã chọn. Xem thông tin này trước khi so sánh hai giai đoạn.</p></section>
            </div>
            <div class="era5-footer-base"><span><strong>ERA5</strong> Thu thập và phân tích khí hậu</span>
              <span>Thời gian hiển thị theo giờ Việt Nam (UTC+7).</span></div>
        """)
