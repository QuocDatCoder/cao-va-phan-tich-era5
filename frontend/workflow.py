"""User workflow. Only submitted forms perform collection or data queries."""
from datetime import date, datetime, timedelta, timezone
import json
import os
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

import pandas as pd
import streamlit as st

from frontend.api_client import DashboardApiClient, DashboardApiError, DEFAULT_TIMEOUT_SECONDS
from frontend.components.controls import VARIABLES, local_date_range, search_places
from frontend.appearance import apply_appearance
from frontend.dashboard import _translate_status
from frontend.dashboard import _render_job_status, _show_timeseries, _show_heatmap, _show_map, _show_statistics, _show_trend
from frontend.notifications import notify_error, notify_warning, notify_success, notify_info
from frontend.auth import render_login_gate
from frontend.workspace_sidebar import render_sidebar

SOURCES = {
    "land": "ERA5-Land · Đất liền",
    "era5": "ERA5 · Đất liền và biển",
}
FREQUENCIES = {"Giờ": "hourly", "Ngày": "daily", "Tháng": "monthly", "Năm": "yearly"}


def client(source):
    return DashboardApiClient(base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
        timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        source=source, stored_only=True,
        access_token=st.session_state.get("auth_access_token"))


def render_dashboard():
    st.set_page_config(page_title="Bảng điều khiển khí hậu ERA5", layout="wide")
    apply_appearance()
    if st.query_params.get('retry_admin') == '1':
        st.session_state['admin_handoff_done'] = False
        del st.query_params['retry_admin']
    if not render_login_gate(client(None)):
        return
    if st.session_state.pop('are_open_collection_jobs', False):
        st.session_state['workflow_section'] = 'Thu thập'
        st.session_state['apply_shared'] = True
        st.session_state['ai_chat_open'] = False
    st.session_state.setdefault('auth_admin_username', os.getenv('ADMIN_USERNAME', 'admin').casefold())
    render_sidebar(client(None))
    from frontend.ai_chat import render_ai_chat
    with st.container(key='era5_floating_chat'):
        render_ai_chat(client(None))
    with st.container(key='are_workspace'):
        render_workspace()


@st.fragment
def render_workspace():
    section = st.session_state.get('workflow_section', 'Thu thập')
    st.query_params['view'] = section
    # One persistent header row for all sections, outside the loading/content area.
    with st.container(key='workflow_section_header', horizontal=True,
                      vertical_alignment='center', gap='small', wrap=False,
                      height=44, border=False):
        st.header(section, anchor=False)
        if section in {'Dữ liệu đã lưu','Biểu đồ'}:
            prefix = 'chart' if section == 'Biểu đồ' else 'stored'
            if st.button('Làm mới',key=prefix+'_refresh_library',icon=':material/refresh:',width='content'):
                from frontend.library import invalidate_reads
                if prefix == 'chart':
                    st.session_state['chart_force_refresh'] = True
                else:
                    st.session_state.pop(prefix+'_result', None)
                invalidate_reads()
    switching = st.session_state.get('rendered_workflow_section') != section
    if switching and section in {'Dữ liệu đã lưu','Biểu đồ'}:
        prefix = 'chart' if section == 'Biểu đồ' else 'stored'
        restoring = (st.session_state.get('rendered_workflow_section') is None
                     and st.query_params.get(prefix+'_mode') == 'details'
                     and bool(st.query_params.get(prefix+'_applied')))
        if not st.session_state.get(prefix+'_open_requested') and not st.session_state.get(prefix+'_action_open'):
            if prefix+'_details_visible' not in st.session_state:
                st.session_state[prefix+'_details_visible'] = restoring
                st.session_state[prefix+'_library_cards'] = not restoring
            visible = st.session_state[prefix+'_details_visible']
            st.query_params[prefix+'_mode'] = 'details' if visible else 'list'
            if visible:
                st.session_state[prefix+'_restore_filters'] = True
    with st.container(key='workflow_stage'):
        loading = st.empty()
        if switching and section == 'Dữ liệu đã lưu':
            loading.html("""
                <style>
                .st-key-workflow_stage {position:relative;min-height:65vh;}
                .st-key-workflow_stage .st-key-workflow_content {visibility:hidden;opacity:0;pointer-events:none;}
                .workflow-loading {position:absolute;inset:0;z-index:10;
                    display:flex;align-items:center;justify-content:center;gap:.75rem;
                    min-height:65vh;}
                .workflow-loading-ring {width:1.5rem;height:1.5rem;flex:none;
                    border:3px solid var(--secondary-background-color, #d5e2df);
                    border-top-color:var(--primary-color, #0f766e);border-radius:50%;
                    animation:workflow-spin .8s linear infinite;}
                @keyframes workflow-spin {to {transform:rotate(360deg);}}
                @media(prefers-reduced-motion:reduce) {
                    .workflow-loading-ring {animation:none;}
                }
                </style>
                <div class="workflow-loading" role="status" aria-live="polite">
                    <span class="workflow-loading-ring" aria-hidden="true"></span>
                    <span>Đang tải nội dung…</span>
                </div>
            """)
        try:
            with st.container(key='workflow_content'):
                screen = st.empty()
                screen.empty()
                with screen.container():
                    render_section(section)
        finally:
            loading.empty()
        st.session_state['rendered_workflow_section'] = section


def render_section(section):
    try:
        if section == "Thu thập":
            render_collection()
        elif section == "Dữ liệu đã lưu":
            render_stored()
        else:
            render_charts()
    except DashboardApiError as exc:
        notify_error(f"Lỗi API: {str(exc)}")


def source_input(prefix):
    if st.session_state.get(prefix+'_source') not in SOURCES:
        st.session_state[prefix+'_source'] = 'land'
    return st.selectbox("Nguồn dữ liệu", list(SOURCES), format_func=SOURCES.get, key=prefix+"_source")


def remember(source, params):
    st.session_state['shared_filter'] = dict(source=source, **params)


def navigate(section, source, params):
    remember(source, params)
    st.session_state['workflow_section'] = section
    st.session_state['apply_shared'] = True


def restore_filter(prefix):
    shared = st.session_state.get('shared_filter')
    if not shared:
        return
    forced = st.session_state.pop('apply_shared', False)
    if st.session_state.get('active_filter_prefix') != prefix or forced:
        st.session_state[prefix+'_source'] = shared['source']
        for name in ['latitude','longitude']:
            st.session_state[prefix+'_'+name] = shared[name]
        for name in ['start','end']:
            st.session_state[prefix+'_'+name] = datetime.fromisoformat(shared[name]).date()
        aliases = {'10m_u_component_of_wind':'wind_u','10m_v_component_of_wind':'wind_v',
                   'surface_solar_radiation_downwards':'solar_radiation'}
        selected_variable = aliases.get(shared['variable'],shared['variable'])
        label = next((k for k,v in VARIABLES.items() if v == selected_variable), list(VARIABLES)[0])
        st.session_state[prefix+'_variable'] = label
        if prefix == 'collect':
            for edge, delta, coord in [('north',.25,'latitude'),('south',-.25,'latitude'),('west',-.25,'longitude'),('east',.25,'longitude')]:
                limit = 90 if coord == 'latitude' else 180
                st.session_state['collect_'+edge] = max(-limit,min(limit,shared[coord]+delta))
    st.session_state['active_filter_prefix'] = prefix


def point_inputs(prefix):
    left, right = st.columns(2)
    latitude = left.number_input("Vĩ độ", min_value=-90., max_value=90., value=10.8231,
                                 format="%.4f", key=prefix+"_latitude")
    longitude = right.number_input("Kinh độ", min_value=-180., max_value=180., value=106.6297,
                                   format="%.4f", key=prefix+"_longitude")
    return {"latitude": latitude, "longitude": longitude}


def apply_place(prefix):
    label = st.session_state.get(prefix+"_place")
    place = next((p for p in st.session_state.get(prefix+"_places", []) if p['label'] == label), None)
    if place:
        st.session_state[prefix+"_latitude"] = place['latitude']
        st.session_state[prefix+"_longitude"] = place['longitude']
        # A city is a centre for the area, never mislabelled as a full Land region.
        for edge, value in {'north': min(90., place['latitude']+.25),
                            'south': max(-90., place['latitude']-.25),
                            'west': max(-180., place['longitude']-.25),
                            'east': min(180., place['longitude']+.25)}.items():
            st.session_state[prefix+'_'+edge] = value


def location_search(prefix):
    """Geocoding is explicit and separate from collection/data query submission."""
    with st.form(prefix+"_place_search", border=False):
        field, action = st.columns([4, 1], vertical_alignment="bottom", wrap=False)
        query = field.text_input("Tìm địa điểm", key=prefix+"_place_query",
            placeholder="Tìm thành phố hoặc nhập tọa độ bên dưới",
            help="Chỉ tên tìm kiếm được gửi đến Open-Meteo khi bấm Tìm. Có thể nhập tọa độ trực tiếp.")
        submitted = action.form_submit_button("Tìm", key=prefix+"_find_place", width="stretch")
    if submitted:
        if len(query.strip()) < 2:
            msg = "Nhập ít nhất 2 ký tự để tìm địa điểm."
            notify_warning(msg, icon="ℹ️")
        else:
            places = search_places(query.strip())
            if places is None:
                msg = "Chưa kết nối được dịch vụ tìm địa điểm. Bạn vẫn có thể nhập tọa độ."
                notify_warning(msg, icon="⚠️")
            elif not places:
                msg = f"Không tìm thấy địa điểm '{query.strip()}'. Thử tên khác hoặc nhập tọa độ trực tiếp."
                notify_warning(msg, icon="📍")
            else:
                st.session_state[prefix+"_places"] = places
                st.session_state[prefix+"_place"] = places[0]['label']
                apply_place(prefix)
                notify_success(f"Đã tìm thấy {len(places)} địa điểm phù hợp!", icon="📍")
    places = st.session_state.get(prefix+"_places", [])
    if places:
        st.selectbox("Kết quả địa điểm", [p['label'] for p in places], key=prefix+"_place",
                     on_change=apply_place, args=(prefix,))


def submit_collection_job(api, payload, params):
    from frontend.library import invalidate_reads, invalidate_job_list

    response = api.create_download(payload)
    source = payload['source']
    job_id = response['job_id']
    remember(source, params)
    invalidate_reads()
    invalidate_job_list()
    st.session_state.setdefault('job_filters', {})[job_id] = dict(source=source, **params)
    st.session_state.setdefault('collection_jobs', {})[f'{api.base_url}|{source}'] = job_id
    st.session_state['workflow_selected_job'] = job_id
    st.session_state.pop('workflow_job_detail', None)
    st.session_state.setdefault('workflow_jobs_to_notify', set()).add(job_id)
    st.session_state['latest_collection_id'] = response.get('collection_id')
    st.session_state.pop('saved_library', None)
    return response


def render_collection():
    restore_filter('collect')
    col_src, col_loc = st.columns([1, 2], vertical_alignment="top")
    with col_src:
        source = source_input("collect")
    with col_loc:
        location_search("collect")
    with st.form("collection_form"):
        if source in {"land", "era5"}:
            variable = "temperature"  # Primary variable required by the existing download API.
            st.caption("Một lần thu thập lấy đủ 6 biến khí hậu.")
            c_start, c_end = st.columns(2)
        else:
            c_var, c_start, c_end = st.columns([1.4, 1, 1])
            with c_var:
                variable = VARIABLES[st.selectbox("Biến khí hậu", list(VARIABLES), key="collect_variable")]
        with c_start:
            start = st.date_input("Ngày bắt đầu (UTC+7)", value=None if 'collect_start' in st.session_state else date(2025, 1, 1), key="collect_start")
        with c_end:
            end = st.date_input("Ngày kết thúc (UTC+7)", value=None if 'collect_end' in st.session_state else date(2025, 1, 1), key="collect_end")
        if source in {"land", "mock"}:
            point = point_inputs("collect")
        else:
            c_n, c_s, c_w, c_e = st.columns(4)
            north = c_n.number_input("Bắc", min_value=-90., max_value=90., value=11., key="collect_north")
            south = c_s.number_input("Nam", min_value=-90., max_value=90., value=10., key="collect_south")
            west = c_w.number_input("Tây", min_value=-180., max_value=180., value=106., key="collect_west")
            east = c_e.number_input("Đông", min_value=-180., max_value=180., value=107., key="collect_east")
        submitted = st.form_submit_button("Bắt đầu thu thập", key="collect_now", type="primary")

    if submitted:
        if start > end:
            msg = "Khoảng ngày không hợp lệ: Ngày bắt đầu phải trước hoặc bằng ngày kết thúc!"
            notify_error(msg)
        elif source == "era5" and (north < south or west > east):
            msg = "Tọa độ vùng không hợp lệ: Vĩ độ Bắc phải lớn hơn Nam, và Kinh độ Tây phải nhỏ hơn Đông!"
            notify_error(msg)
        else:
            a,b = local_date_range(start,end)
            centre = point if source != 'era5' else dict(latitude=(north+south)/2,longitude=(west+east)/2)
            params = dict(variable=variable, **centre, start=a, end=b, frequency='hourly',aggregation='auto')
            utc = timezone.utc
            payload = dict(source=source, variable=variable,
                start_date=datetime.fromisoformat(a).astimezone(utc).date().isoformat(),
                end_date=datetime.fromisoformat(b).astimezone(utc).date().isoformat(), frequency="hourly")
            selected_place = next((p for p in st.session_state.get('collect_places', [])
                if p['label'] == st.session_state.get('collect_place')), None)
            payload.update(requested_start=a, requested_end=b, request_timezone='UTC+7')
            if source == "era5":
                payload['all_variables'] = True
            if source != 'era5' and selected_place:
                payload.update(place_name=selected_place['label'], selected_place={
                    k:selected_place[k] for k in ['latitude','longitude']})
            payload.update({"area": dict(north=north, south=south, west=west, east=east)}
                           if source == "era5" else {"location": point})
            api = client(source)
            submit_collection_job(api, payload, params)
            notify_success("Đã tiếp nhận tác vụ thu thập ERA5 thành công!")
    render_collection_jobs(source)
    _render_page_footer()


def _render_page_footer():
    """Render a website-style footer at the bottom of the collection tab."""
    st.html("""
<style>
.era5-footer {
    --footer-heading: #1a202c;
    --footer-text: #4a5568;
    --footer-link: #0f766e;
    --footer-border: #cbd5e0;
    margin-top: 3rem;
    border-top: 1px solid var(--footer-border);
    padding-top: 2rem;
    font-size: 0.82rem;
    color: var(--footer-text);
}
body:has(.era5-shell[data-theme="dark"]) .era5-footer {
    --footer-heading: #f3f3f3;
    --footer-text: #cbd5e1;
    --footer-link: #a7cbfc;
    --footer-border: #505050;
}
.era5-footer-top {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 1.5rem 2rem;
    padding-bottom: 1.75rem;
}
.era5-footer-col h4 {
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    margin: 0 0 .75rem;
    color: var(--footer-heading);
}
.era5-footer-col ul {
    list-style: none;
    margin: 0;
    padding: 0;
}
.era5-footer-col ul li {
    margin-bottom: .4rem;
    line-height: 1.4;
}
.era5-footer-col ul li a {
    color: var(--footer-link);
    text-decoration: none;
}
.era5-footer-col ul li a:hover {
    text-decoration: underline;
}
.era5-footer-bottom {
    border-top: 1px solid var(--footer-border);
    padding-top: 1.25rem;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem 2rem;
}
.era5-footer-org h5 {
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
    margin: 0 0 .5rem;
    color: var(--footer-heading);
}
.era5-footer-org p {
    margin: 0;
    line-height: 1.6;
    color: var(--footer-text);
}
.era5-footer-badges {
    margin-top: 1.25rem;
    padding-top: 1rem;
    border-top: 1px solid var(--footer-border);
    display: flex;
    align-items: center;
    gap: .75rem;
    flex-wrap: wrap;
    font-size: 0.75rem;
}
.era5-footer-badge {
    display: inline-flex;
    align-items: center;
    gap: .3rem;
    padding: .2rem .55rem;
    border: 1px solid var(--footer-border);
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 600;
}
@media(max-width: 640px) {
    .era5-footer-bottom { grid-template-columns: 1fr; }
}
</style>

<div class="era5-footer">
  <!-- TOP: 4 columns -->
  <div class="era5-footer-top">
    <div class="era5-footer-col">
      <h4>Nguồn dữ liệu</h4>
      <ul>
        <li><a href="https://www.ecmwf.int/en/forecasts/dataset/ecmwf-reanalysis-v5" target="_blank">ERA5 · ECMWF</a></li>
        <li><a href="https://cds.climate.copernicus.eu/" target="_blank">Copernicus CDS</a></li>
        <li><a href="https://open-meteo.com/" target="_blank">Open-Meteo (Geocoding)</a></li>
        <li>ERA5-Land · Phân giải cao</li>
        <li>ERA5 · Đất liền &amp; Biển</li>
      </ul>
    </div>
    <div class="era5-footer-col">
      <h4>Biến khí hậu</h4>
      <ul>
        <li>Nhiệt độ 2m</li>
        <li>Lượng mưa</li>
        <li>Tốc độ &amp; Hướng gió 10m</li>
        <li>Độ ẩm tương đối</li>
        <li>Bức xạ mặt trời</li>
      </ul>
    </div>
    <div class="era5-footer-col">
      <h4>Hướng dẫn sử dụng</h4>
      <ul>
        <li>Chọn nguồn &amp; vị trí</li>
        <li>Đặt khoảng thời gian</li>
        <li>Bắt đầu thu thập dữ liệu</li>
        <li>Xem và phân tích ở Biểu đồ</li>
        <li>Xuất dữ liệu JSON / CSV</li>
      </ul>
    </div>
    <div class="era5-footer-col">
      <h4>Về ứng dụng</h4>
      <ul>
        <li>ERA5 · Dữ liệu khí hậu</li>
        <li>Phân giải: 0.1° × 0.1° (Land)</li>
        <li>Múi giờ UTC+7 (Việt Nam)</li>
        <li>Dữ liệu tái phân tích ECMWF</li>
        <li>Phiên bản ERA5: v5</li>
      </ul>
    </div>
  </div>

  <!-- BOTTOM: 2 org blocks -->
  <div class="era5-footer-bottom">
    <div class="era5-footer-org">
      <h5>Dữ liệu tái phân tích ERA5</h5>
      <p>
        Nguồn: European Centre for Medium-Range Weather Forecasts (ECMWF)<br>
        Chương trình Copernicus Climate Change Service (C3S)<br>
        Tần suất: Hàng giờ · Từ 1940 đến nay<br>
        Phân giải không gian: 0.25° (ERA5) · 0.1° (ERA5-Land)
      </p>
    </div>
    <div class="era5-footer-org">
      <h5>Thông tin thu thập</h5>
      <p>
        Dữ liệu được tải từ Copernicus Data Store (CDS) và lưu trữ cục bộ.<br>
        Ngày hiển thị theo múi giờ UTC+7 (Việt Nam).<br>
        Dữ liệu ERA5 thường có độ trễ khoảng 5 ngày so với ngày hiện tại.<br>
        Land: Thu một điểm, 6 biến. ERA5: Thu một vùng, 1 biến / lần.
      </p>
    </div>
  </div>

  <!-- BADGES -->
  <div class="era5-footer-badges">
    <span>© 2024–2026 ERA5 Dashboard</span>
    <span class="era5-footer-badge">🛰️ Copernicus C3S</span>
    <span class="era5-footer-badge">🌍 ECMWF ERA5</span>
    <span class="era5-footer-badge">🐍 Python · Streamlit</span>
    <span class="era5-footer-badge">📡 Open-Meteo</span>
  </div>
</div>
""")


@st.cache_resource
def _job_reader():
    return ThreadPoolExecutor(max_workers=4, thread_name_prefix='job-list-read')


def _submit_job_list(api):
    # Worker only performs HTTP I/O; all Streamlit/session mutations stay on UI thread.
    return _job_reader().submit(api.list_jobs)


def _update_job_list(api):
    revision = st.session_state.get('workflow_jobs_revision', 0)
    pending = st.session_state.get('workflow_jobs_pending')
    if pending and pending[0] != revision:
        pending[1].cancel()
        st.session_state.pop('workflow_jobs_pending', None)
        pending = None
    if pending and pending[1].done():
        st.session_state.pop('workflow_jobs_pending', None)
        st.session_state['workflow_jobs_due'] = monotonic() + 15
        try:
            checked_at = datetime.now(timezone(timedelta(hours=7))).strftime('%H:%M:%S')
            jobs = [dict(job, _status_checked_at=checked_at) for job in pending[1].result()]
            tracked = st.session_state.setdefault('workflow_jobs_to_notify', set())
            for job in jobs:
                jid, status = job['job_id'], job['status']
                if status in {'queued','running','cancelling'}:
                    tracked.add(jid)
                elif jid in tracked:
                    tracked.discard(jid)
                    if status == 'completed':
                        if st.session_state.get('workflow_job_detail') == jid:
                            st.session_state.pop('workflow_job_detail', None)
                        name = job.get('collection_name')
                        notify_success(f'Đã hoàn tất thu thập: {name}' if name else 'Đã hoàn tất thu thập dữ liệu.')
            st.session_state['workflow_job_list'] = jobs
            st.session_state.pop('workflow_jobs_error', None)
        except DashboardApiError as exc:
            st.session_state['workflow_jobs_error'] = str(exc)
        pending = None
    jobs = st.session_state.get('workflow_job_list')
    active = any(j['status'] in {'queued','running','cancelling'} for j in jobs or [])
    if (pending is None and (jobs is None or active)
            and monotonic() >= st.session_state.get('workflow_jobs_due', 0)):
        future = _submit_job_list(api)
        st.session_state['workflow_jobs_pending'] = (revision, future)
        # Handles already-completed reads without adding an artificial delay.
        if future.done():
            _update_job_list(api)


@st.fragment(run_every=1)
def render_collection_jobs(source):
    # All job widgets and details belong to one fragment. Polling replaces this
    # region instead of leaving outputs owned by a nested polling fragment.
    try:
        _render_collection_jobs(client(source))
    except DashboardApiError as exc:
        notify_error(f"Lỗi API: {str(exc)}")


def _render_collection_jobs(api):
    st.divider()
    from frontend.library import invalidate_reads, invalidate_job_list
    with st.container(horizontal=True, vertical_alignment='center', gap='small', wrap=False):
        st.header("Tác vụ thu thập", anchor=False)
        if st.button("Làm mới", key="refresh_jobs", width="content", icon=":material/refresh:"):
            invalidate_reads()
            invalidate_job_list()
    _update_job_list(api)
    error = st.session_state.get('workflow_jobs_error')
    if error:
        notify_error(f"Lỗi tải danh sách tác vụ: {error}")
    if 'workflow_jobs_pending' in st.session_state:
        st.info('Đang tải tác vụ thu thập… Bạn vẫn có thể tìm địa điểm và điền form phía trên.', icon=':material/sync:')
    if 'workflow_job_list' not in st.session_state:
        return
    jobs = st.session_state.get("workflow_job_list", [])
    if not jobs:
        st.info("Chưa có tác vụ thu thập. Bạn có thể gửi yêu cầu bằng form ở trên.")
        return
    rows = []
    local = timezone(timedelta(hours=7))
    source_names = {'land': 'ERA5-Land', 'era5': 'ERA5', 'mock': 'Giả lập'}
    for job in jobs:
        created = datetime.fromisoformat(job['created_at']).astimezone(local)
        place = job.get('place_name')
        if point := job.get('location'):
            coordinates = f"{point['latitude']:.4f}, {point['longitude']:.4f}"
            place = coordinates if place in (None, 'Tọa độ nhập tay') else f"{place} · {coordinates}"
        elif area := job.get('area'):
            place = f"{area['south']:g}–{area['north']:g}° vĩ, {area['west']:g}–{area['east']:g}° kinh"
        if job.get('requested_start') and job.get('requested_end'):
            start, end = (datetime.fromisoformat(job[k]).astimezone(local)
                          for k in ('requested_start', 'requested_end'))
            period = f'{start:%d/%m/%Y}' if start.date() == end.date() else f'{start:%d/%m/%Y} – {end:%d/%m/%Y}'
            period += ' (UTC+7)'
        else:
            period = f"{job['start_date']} – {job['end_date']} (UTC)"
        rows.append({
            'Địa điểm / vùng': place or 'Chưa ghi nhận',
            'Nguồn': source_names.get(job.get('source'), 'Chưa ghi nhận'),
            'Khoảng ngày dữ liệu': period,
            'Tạo lúc (UTC+7)': created.strftime('%d/%m/%Y %H:%M:%S'),
            'Trạng thái': _translate_status(job['status']),
        })
    with st.expander("Lịch sử thu thập · "+str(len(rows))+" tác vụ", expanded=False):
        st.caption("100 tác vụ gần nhất. Tự cập nhật mỗi 15 giây khi có tác vụ đang chờ/chạy; bấm Làm mới để kiểm tra ngay.")
        st.dataframe(rows, hide_index=True, width='stretch')
    ids = [j['job_id'] for j in jobs]
    if st.session_state.get("workflow_selected_job") not in ids:
        st.session_state["workflow_selected_job"] = ids[0]
    labels = {
        job['job_id']: (
            f"{row['Địa điểm / vùng']} · {row['Khoảng ngày dữ liệu']}"
            f" · {row['Nguồn']} · {row['Trạng thái']}"
        )
        for job, row in zip(jobs, rows)
    }
    # Streamlit can retain the displayed formatted label for an unchanged ID.
    # Remount only when labels change; preserve the selected job independently.
    automatic = st.session_state['workflow_selected_job']
    show_default = (next(j['status'] for j in jobs if j['job_id']==automatic) != 'completed'
                    or st.session_state.get('workflow_job_detail') == automatic)
    choice_key = 'workflow_job_choice_' + str(hash((tuple(labels.items()), automatic, show_default)))
    selected = st.selectbox("Xem chi tiết tác vụ", ids, format_func=labels.get,
                            index=ids.index(automatic) if show_default else None,
                            placeholder='Chọn tác vụ để xem trạng thái', key=choice_key,
                            on_change=_choose_job_detail,args=(choice_key,))
    if selected is None:
        return
    st.session_state['workflow_selected_job'] = selected
    st.caption(f'Mã tác vụ: {selected}')
    context = "workflow_selected"
    st.session_state["collection_jobs"] = {**st.session_state.get("collection_jobs", {}), context: selected}
    _render_job_status(api, context, job=next(job for job in jobs if job['job_id'] == selected), show_quality=False)


def _choose_job_detail(key):
    selected = st.session_state.get(key)
    if selected:
        st.session_state['workflow_selected_job'] = selected
        st.session_state['workflow_job_detail'] = selected


def query_form(prefix, charts=False):
    restore_filter(prefix)
    source = source_input(prefix)
    location_search(prefix)
    with st.form(prefix+"_form"):
        variable = VARIABLES[st.selectbox("Biến khí hậu", list(VARIABLES), key=prefix+"_variable")]
        point = point_inputs(prefix)
        left, right = st.columns(2)
        start = left.date_input("Ngày bắt đầu (UTC+7)", value=date(2025, 1, 1), key=prefix+"_start")
        end = right.date_input("Ngày kết thúc (UTC+7)", value=date(2025, 1, 2), key=prefix+"_end")
        frequency, aggregation, kind = "hourly", "auto", "Chuỗi thời gian"
        if charts:
            a, b, c = st.columns(3)
            frequency = FREQUENCIES[a.selectbox("Chu kỳ", list(FREQUENCIES), key=prefix+"_frequency")]
            methods = {"auto": "Tự động theo biến", "mean": "Trung bình", "min": "Nhỏ nhất", "max": "Lớn nhất", "sum": "Tổng"}
            aggregation = b.selectbox("Phép tổng hợp", list(methods), format_func=methods.get, key=prefix+"_aggregation")
            kind = c.selectbox("Loại biểu đồ", ["Chuỗi thời gian", "Bản đồ nhiệt", "Không gian (điểm)", "Thống kê và xu hướng"], key=prefix+"_kind")
            with st.popover("Cách tổng hợp dữ liệu"):
                st.write("Tự động chọn phép phù hợp theo biến. Tổng dùng cho mưa/bức xạ; hướng gió dùng trung bình vòng tròn. Chu kỳ áp dụng cho chuỗi thời gian.")
        submitted = st.form_submit_button("Vẽ biểu đồ" if charts else "Xem dữ liệu", key=prefix+"_submit", type="primary")
    a, b = local_date_range(start, end)
    if submitted and start <= end:
        remember(source, dict(variable=variable, **point, start=a, end=b, frequency=frequency, aggregation=aggregation))
    return submitted, source, dict(variable=variable, **point, start=a, end=b,
                                  frequency=frequency, aggregation=aggregation), kind, start <= end


def render_stored():
    from frontend.library import render_saved
    render_saved(client, charts=False)


def render_charts():
    from frontend.library import render_saved
    render_saved(client, charts=True)


def show_metadata(payload, prefix):
    st.caption("Kết quả của bộ lọc đã thực hiện; bản nháp trong form chưa áp dụng.")
    st.caption(f"{SOURCES[payload['source']]} · {payload['dataset']} · UTC+7")
    grid = payload.get('grid_location')
    requested = payload['location']
    st.caption(f"Điểm yêu cầu: {requested['latitude']:.4f}, {requested['longitude']:.4f}" +
               (f" · Điểm lưới thực: {grid['latitude']:.4f}, {grid['longitude']:.4f}" if grid else " · Chưa có điểm lưới"))
    a, b, c = st.columns(3)
    a.metric("Độ phủ", f"{payload['coverage']:.1%}")
    b.metric("Giờ có dữ liệu / yêu cầu", f"{payload['available_count']} / {payload['expected_count']}")
    c.metric("Đơn vị", payload['unit'])
    if not payload['complete']:
        st.warning("Chưa đủ dữ liệu. Kết quả chỉ dựa trên phần đã lưu; muốn bổ sung hãy sang mục Thu thập.")
    if payload.get('message'):
        st.info(payload['message'])
    report = {key: value for key, value in payload.items() if key != 'data'}
    st.download_button("Tải độ phủ và cảnh báo (JSON)", json.dumps(report, ensure_ascii=False, indent=2),
        file_name=f"{payload['source']}_coverage.json", mime="application/json", key=prefix+"_coverage_download", on_click="ignore")
    if payload.get('missing_ranges'):
        with st.expander("Khoảng giờ còn thiếu (UTC+7)"):
            st.json(payload['missing_ranges'])
