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
from frontend.notifications import notify_error, notify_warning, notify_success, notify_info, notify_job_failures
from frontend.auth import render_login_gate
from frontend.home import render_home
from frontend.workspace_sidebar import HOME_SECTION, render_sidebar

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
    if st.session_state.pop('chart_handoff_rerun', False):
        st.rerun(scope='app')
    section = st.session_state.get('workflow_section', HOME_SECTION)
    st.query_params['view'] = section
    switching = st.session_state.get('rendered_workflow_section') != section
    if switching and section in {'Dữ liệu đã lưu','Biểu đồ'}:
        prefix = 'chart' if section == 'Biểu đồ' else 'stored'
        restoring = (st.session_state.get('rendered_workflow_section') is None
                     and st.query_params.get(prefix+'_mode') == 'details'
                     and bool(st.query_params.get(prefix+'_applied')))
        if not st.session_state.get(prefix+'_open_requested') and not st.session_state.get(prefix+'_action_open'):
            if prefix+'_details_visible' not in st.session_state:
                st.session_state[prefix+'_details_visible'] = restoring
            visible = st.session_state[prefix+'_details_visible']
            st.query_params[prefix+'_mode'] = 'details' if visible else 'list'
            if visible:
                st.session_state[prefix+'_restore_filters'] = True
    if section == 'Thu thập':
        with st.container(key='workflow_section_header', horizontal=True,
                          vertical_alignment='center', gap='small', wrap=False,
                          height=44, border=False):
            st.header(section, anchor=False)
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
                render_section(section)
                if switching and section == HOME_SECTION and st.session_state.get('rendered_workflow_section'):
                    from frontend.library import _scroll_to
                    _scroll_to('era5_home_hero')
        finally:
            loading.empty()
        st.session_state['rendered_workflow_section'] = section


def render_section(section):
    try:
        if section == HOME_SECTION:
            render_home(client('land'))
        elif section == "Thu thập":
            render_collection()
        elif section == "Dữ liệu đã lưu":
            render_stored()
        else:
            render_charts()
    except DashboardApiError as exc:
        notify_error(f"Lỗi API: {str(exc)}")


def source_input(prefix):
    return 'land'


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
    latitude_key, longitude_key = prefix+'_latitude', prefix+'_longitude'
    latitude = left.number_input("Vĩ độ", min_value=-90., max_value=90.,
                                 value=None if latitude_key in st.session_state else 10.8231,
                                 format="%.4f", key=latitude_key, persist_state="session")
    longitude = right.number_input("Kinh độ", min_value=-180., max_value=180.,
                                   value=None if longitude_key in st.session_state else 106.6297,
                                   format="%.4f", key=longitude_key, persist_state="session")
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
    with st.container(key=prefix+"_place_search"):
        field, action = st.columns([4, 1], vertical_alignment="bottom", wrap=False)
        query = field.text_input("Tìm địa điểm", key=prefix+"_place_query",
            persist_state="session", live="0ms", on_change="ignore",
            placeholder="Tìm thành phố hoặc nhập tọa độ bên dưới",
            help="Chỉ tên tìm kiếm được gửi đến Open-Meteo khi bấm Tìm. Có thể nhập tọa độ trực tiếp.")
        submitted = action.button("Tìm", key=prefix+"_find_place", width="stretch")
    st.html("""<script>
        if (!window.era5PlaceSearchEnter) {
            window.era5PlaceSearchEnter = true;
            document.addEventListener('keydown', event => {
                if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
                const input = event.target.closest('input');
                if (!input) return;
                const search = input.closest('[class*="_place_search "]');
                if (search) {
                    event.preventDefault();
                    const button = search.querySelector('[class*="_find_place"] button');
                    if (button) button.click();
                }
            }, true);
        }
    </script>""", unsafe_allow_javascript=True)
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
                     on_change=apply_place, args=(prefix,), persist_state="session")


def submit_collection_job(api, payload, params):
    from frontend.library import invalidate_reads, invalidate_job_list

    response = api.create_download(payload)
    source = payload['source']
    job_id = response['job_id']
    remember(source, params)
    invalidate_reads()
    invalidate_job_list(keep_history=True)
    st.session_state.setdefault('job_filters', {})[job_id] = dict(source=source, **params)
    st.session_state.setdefault('collection_jobs', {})[f'{api.base_url}|{source}'] = job_id
    st.session_state['workflow_selected_job'] = job_id
    local = timezone(timedelta(hours=7))
    start, end = (datetime.fromisoformat(params[key]).astimezone(local) for key in ('start', 'end'))
    period = f'{start:%d/%m/%Y}' if start.date() == end.date() else f'{start:%d/%m/%Y} – {end:%d/%m/%Y}'
    st.session_state['workflow_latest_submitted_job'] = dict(
        job_id=job_id, status=response.get('status', 'queued'), collection_id=response.get('collection_id'),
        place=payload.get('place_name') or f"{params['latitude']:.4f}, {params['longitude']:.4f}",
        period=period + ' (UTC+7)',
    )
    st.session_state.pop('workflow_job_detail', None)
    st.session_state.setdefault('workflow_jobs_to_notify', set()).add(job_id)
    st.session_state['latest_collection_id'] = response.get('collection_id')
    st.session_state.pop('saved_library', None)
    return response


def render_collection():
    restore_filter('collect')
    source = source_input("collect")
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
            start = st.date_input("Ngày bắt đầu (UTC+7)", value=None if 'collect_start' in st.session_state else date(2025, 1, 1), key="collect_start", format="DD/MM/YYYY")
        with c_end:
            end = st.date_input("Ngày kết thúc (UTC+7)", value=None if 'collect_end' in st.session_state else date(2025, 1, 1), key="collect_end", format="DD/MM/YYYY")
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
        if start is None or end is None:
            notify_error('Chọn đầy đủ ngày bắt đầu và ngày kết thúc.')
        elif start > end:
            msg = "Khoảng ngày không hợp lệ: Ngày bắt đầu phải trước hoặc bằng ngày kết thúc!"
            notify_error(msg)
        elif source in {'land', 'mock'} and None in point.values():
            notify_error('Nhập đầy đủ vĩ độ và kinh độ của điểm thu thập.')
        elif source == 'era5' and None in (north, south, west, east):
            notify_error('Nhập đầy đủ tọa độ giới hạn vùng thu thập.')
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
                place_point = {key: selected_place[key] for key in point}
                if point == place_point:
                    payload.update(place_name=selected_place['label'], selected_place=place_point)
            payload.update({"area": dict(north=north, south=south, west=west, east=east)}
                           if source == "era5" else {"location": point})
            api = client(source)
            try:
                submit_collection_job(api, payload, params)
            except DashboardApiError as exc:
                notify_error(f'Chưa gửi được tác vụ thu thập: {exc}')
            else:
                notify_success("Đã tiếp nhận tác vụ thu thập ERA5 thành công!")
    render_collection_jobs(source)


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
            failures = []
            for job in jobs:
                jid, status = job['job_id'], job['status']
                if status in {'queued','running','cancelling'}:
                    tracked.add(jid)
                elif jid in tracked:
                    tracked.discard(jid)
                    if status == 'completed':
                        name = job.get('collection_name')
                        notify_success(f'Đã hoàn tất thu thập: {name}' if name else 'Đã hoàn tất thu thập dữ liệu.')
                    elif status in {'failed', 'interrupted'}:
                        failures.append(job)
            if failures:
                notify_job_failures(failures)
            st.session_state['workflow_job_list'] = jobs
            latest = st.session_state.get('workflow_latest_submitted_job')
            if latest:
                match = next((job for job in jobs if job['job_id'] == latest['job_id']), None)
                if match is None and len(jobs) >= 100:
                    # The job may be older than the first history page.
                    try:
                        match = api.get_job(latest['job_id'])
                    except DashboardApiError as exc:
                        if exc.status_code != 404:
                            raise
                if match is None:
                    tracked.discard(latest['job_id'])
                    st.session_state.pop('workflow_latest_submitted_job', None)
                else:
                    latest.update(status=match['status'], error=match.get('error'), checked_at=checked_at)
            st.session_state.pop('workflow_jobs_error', None)
        except DashboardApiError as exc:
            st.session_state['workflow_jobs_error'] = str(exc)
        pending = None
    jobs = st.session_state.get('workflow_job_list')
    active = any(j['status'] in {'queued','running','cancelling'} for j in jobs or [])
    due = st.session_state.get('workflow_jobs_due', 0)
    if pending is None and (jobs is None or active or due == 0) and monotonic() >= due:
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
            invalidate_job_list(keep_history=True)
    _update_job_list(api)
    jobs = st.session_state.get("workflow_job_list", [])
    latest = st.session_state.get('workflow_latest_submitted_job')
    if latest:
        _render_latest_submission(latest)
    error = st.session_state.get('workflow_jobs_error')
    if error:
        notify_error(f"Lỗi tải danh sách tác vụ: {error}")
    if 'workflow_job_list' not in st.session_state:
        if 'workflow_jobs_pending' in st.session_state:
            st.caption('Đang tải tác vụ thu thập…')
        return
    if not jobs:
        st.info("Chưa có tác vụ thu thập. Bạn có thể gửi yêu cầu bằng form ở trên.")
        return
    rows = []
    local = timezone(timedelta(hours=7))
    for job in jobs:
        created = datetime.fromisoformat(job['created_at']).astimezone(local)
        place = job.get('place_name')
        if point := job.get('location'):
            coordinates = f"{point['latitude']:.4f}, {point['longitude']:.4f}"
            place = (place or 'Tọa độ nhập tay').removesuffix(f' ({coordinates})')
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
            'Khoảng ngày dữ liệu': period,
            'Tạo lúc (UTC+7)': created.strftime('%d/%m/%Y %H:%M:%S'),
            'Trạng thái': _translate_status(job['status']),
        })
    with st.expander("Lịch sử thu thập · "+str(len(rows))+" tác vụ", key='collection_job_history'):
        st.dataframe(rows, hide_index=True, width='stretch')
        jobs_by_id = {job['job_id']: (job, row) for job, row in zip(jobs, rows)}
        labels = {
            job_id: f"{row['Địa điểm / vùng']} · {row['Khoảng ngày dữ liệu']}"
                    + (" · Giả lập" if job.get('source') == 'mock' else "")
                    + f" · {row['Trạng thái']}"
            for job_id, (job, row) in jobs_by_id.items()
        }
        ids = list(jobs_by_id)
        remembered = st.session_state.get('workflow_job_detail')
        choice_key = 'workflow_job_choice_' + str(hash(tuple(labels.items())))
        selected = st.selectbox("Xem chi tiết tác vụ", ids, format_func=labels.get,
                                index=ids.index(remembered) if remembered in ids else None,
                                placeholder='Chọn tác vụ để xem trạng thái', key=choice_key,
                                on_change=_choose_job_detail, args=(choice_key,))
        if selected is None:
            return
        job, row = jobs_by_id[selected]
        with st.container(border=True, key='collection_job_detail', gap='xsmall'):
            with st.container(horizontal=True, vertical_alignment='center', gap='xsmall'):
                st.markdown(f"**{row['Địa điểm / vùng']} · {row['Khoảng ngày dữ liệu']}**")
                _job_status_badge(job['status'])
                collection_id = job.get('collection_id')
                if job['status'] == 'completed' and collection_id:
                    from frontend.library import open_details
                    if st.button('Mở dữ liệu', key='history_job_open_data', icon=':material/table_view:',
                                 on_click=open_details, args=(collection_id,)):
                        st.rerun(scope='app')
            if job['status'] in {'queued', 'running', 'cancelling'} and job.get('_status_checked_at'):
                st.caption(f"Kiểm tra lần cuối lúc {job['_status_checked_at']} (UTC+7)")
            context = "workflow_selected"
            st.session_state["collection_jobs"] = {**st.session_state.get("collection_jobs", {}), context: selected}
            _render_job_status(api, context, job=job, show_quality=False, compact=True)
            with st.expander('Thông tin kỹ thuật', key='collection_job_technical'):
                st.caption(f'Mã tác vụ: {selected}')
                if point := job.get('location'):
                    st.caption(f"Tọa độ: {point['latitude']:.4f}, {point['longitude']:.4f}")
                if job.get('dataset'):
                    st.caption(f"Nguồn: {job['dataset']}")


def _job_status_badge(status):
    color = 'green' if status == 'completed' else 'red' if status in {'failed', 'interrupted'} else 'orange'
    st.badge(_translate_status(status), color=color)
    if status in {'queued', 'running', 'cancelling'}:
        st.html('<span class="latest-job-pulse" aria-hidden="true"><i></i><i></i><i></i></span>')


def _render_latest_submission(job):
    from frontend.library import open_details

    status = job['status']
    with st.container(border=True, key='latest_collection_job', horizontal=True,
                      horizontal_alignment='distribute', vertical_alignment='center', gap='medium'):
        with st.container(width='content', gap='xxsmall'):
            st.markdown(f"**{job['place']}**")
            st.caption(job['period'])
        with st.container(width='content', gap='xxsmall'):
            with st.container(horizontal=True, vertical_alignment='center', gap='xsmall', width='content'):
                _job_status_badge(status)
                collection_id = job.get('collection_id')
                if status == 'completed' and collection_id and collection_id == st.session_state.get('latest_collection_id'):
                    if st.button('Mở dữ liệu', key='latest_job_open_data', icon=':material/table_view:',
                                 on_click=open_details, args=(collection_id,)):
                        st.rerun(scope='app')
                if job.get('error') and status in {'failed', 'interrupted'}:
                    with st.popover('Xem lý do', icon=':material/info:'):
                        st.text(job['error'])
            checked_at = job.get('checked_at')
            st.caption(f'Kiểm tra lần cuối lúc {checked_at} (UTC+7)' if checked_at else 'Đang chờ cập nhật trạng thái')


def _choose_job_detail(key):
    selected = st.session_state.get(key)
    st.session_state['workflow_job_detail'] = selected
    if selected:
        st.session_state['workflow_selected_job'] = selected


def query_form(prefix, charts=False):
    restore_filter(prefix)
    source = source_input(prefix)
    location_search(prefix)
    with st.form(prefix+"_form"):
        variable = VARIABLES[st.selectbox("Biến khí hậu", list(VARIABLES), key=prefix+"_variable")]
        point = point_inputs(prefix)
        left, right = st.columns(2)
        start = left.date_input("Ngày bắt đầu (UTC+7)", value=date(2025, 1, 1), key=prefix+"_start", format="DD/MM/YYYY")
        end = right.date_input("Ngày kết thúc (UTC+7)", value=date(2025, 1, 2), key=prefix+"_end", format="DD/MM/YYYY")
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
    st.caption("UTC+7")
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
