"""User workflow. Only submitted forms perform collection or data queries."""
from datetime import date, datetime, timedelta, timezone
import json
import os

import pandas as pd
import streamlit as st

from frontend.api_client import DashboardApiClient, DashboardApiError, DEFAULT_TIMEOUT_SECONDS
from frontend.components.controls import VARIABLES, local_date_range, search_places
from frontend.appearance import apply_appearance
from frontend.dashboard import _poll_collection, _show_timeseries, _show_heatmap, _show_map, _show_statistics, _show_trend

SOURCES = {
    "land": "ERA5-Land · Đất liền",
    "era5": "ERA5 · Đất liền và biển",
}
FREQUENCIES = {"Giờ": "hourly", "Ngày": "daily", "Tháng": "monthly", "Năm": "yearly"}


def client(source):
    return DashboardApiClient(base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
        timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        source=source, stored_only=True)


def render_dashboard():
    st.set_page_config(page_title="Bảng điều khiển khí hậu ERA5", layout="wide")
    apply_appearance()
    st.html("""
        <style>
        .era5-brand {position:fixed;top:1rem;left:1.25rem;z-index:999999;
            margin:0;font-size:1.75rem;font-weight:700;line-height:1.5;
            max-width:calc(100vw - 220px);white-space:nowrap;overflow:hidden;
            text-overflow:ellipsis;pointer-events:none;}
        @media(max-width:640px) {
            .era5-brand {font-size:1.5rem;max-width:calc(100vw - 155px);}
        }
        </style>
        <div class="era5-brand" role="heading" aria-level="1">ERA5 · Dữ liệu khí hậu</div>
    """)
    navigation = {
        'Thu thập': ':material/cloud_download: Thu thập',
        'Dữ liệu đã lưu': ':material/folder_open: Kho dữ liệu',
        'Biểu đồ': ':material/monitoring: Biểu đồ',
    }
    if 'workflow_section' not in st.session_state:
        requested_view = st.query_params.get('view')
        if requested_view in navigation:
            st.session_state['workflow_section'] = requested_view
    section = st.pills(
        'Chức năng', list(navigation), format_func=navigation.get,
        default=st.session_state.get('workflow_section') or 'Thu thập', required=True, width='content', wrap=False,
        key='workflow_section', label_visibility='collapsed',
    )
    st.query_params['view'] = section
    # Explicitly clear the previous screen before any blocking API read. Without
    # this delta Streamlit leaves the previous form faded until the rerun ends.
    screens = {name: st.empty() for name in ['Thu thập', 'Dữ liệu đã lưu', 'Biểu đồ']}
    for name, slot in screens.items():
        if name != section:
            slot.empty()
    with screens[section].container():
        try:
            if section == "Thu thập":
                render_collection()
            elif section == "Dữ liệu đã lưu":
                render_stored()
            else:
                render_charts()
        except DashboardApiError as exc:
            st.error(str(exc))


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
        field, action = st.columns([4, 1], vertical_alignment="bottom")
        query = field.text_input("Tìm địa điểm", key=prefix+"_place_query",
            placeholder="Tìm thành phố hoặc nhập tọa độ bên dưới",
            help="Chỉ tên tìm kiếm được gửi đến Open-Meteo khi bấm Tìm. Có thể nhập tọa độ trực tiếp.")
        submitted = action.form_submit_button("Tìm", key=prefix+"_find_place", use_container_width=True)
    if submitted:
        if len(query.strip()) < 2:
            st.info("Nhập ít nhất 2 ký tự để tìm địa điểm.")
        else:
            places = search_places(query.strip())
            if places is None:
                st.warning("Chưa kết nối được dịch vụ tìm địa điểm. Bạn vẫn có thể nhập tọa độ.")
            elif not places:
                st.info("Không tìm thấy. Thử tên khác hoặc nhập tọa độ trực tiếp.")
                st.session_state[prefix+"_places"] = []
            else:
                st.session_state[prefix+"_places"] = places
                st.session_state[prefix+"_place"] = places[0]['label']
                apply_place(prefix)
    places = st.session_state.get(prefix+"_places", [])
    if places:
        st.selectbox("Kết quả địa điểm", [p['label'] for p in places], key=prefix+"_place",
                     on_change=apply_place, args=(prefix,))


def render_collection():
    restore_filter('collect')
    st.header("Thu thập", anchor=False)
    source = source_input("collect")
    location_search("collect")
    with st.form("collection_form"):
        variable = VARIABLES[st.selectbox("Biến khí hậu", list(VARIABLES), key="collect_variable",
            help="Land thu thập cả sáu biến gốc trong một lần. ERA5 thu thập biến đang chọn.")]
        left, right = st.columns(2)
        start = left.date_input("Ngày bắt đầu (UTC+7)", value=date(2025, 1, 1), key="collect_start")
        end = right.date_input("Ngày kết thúc (UTC+7)", value=date(2025, 1, 1), key="collect_end")
        if source in {"land", "mock"}:
            point = point_inputs("collect")
        else:
            top, bottom = st.columns(2)
            north = top.number_input("Bắc", min_value=-90., max_value=90., value=11., key="collect_north")
            south = top.number_input("Nam", min_value=-90., max_value=90., value=10., key="collect_south")
            west = bottom.number_input("Tây", min_value=-180., max_value=180., value=106., key="collect_west")
            east = bottom.number_input("Đông", min_value=-180., max_value=180., value=107., key="collect_east")
        submitted = st.form_submit_button("Bắt đầu thu thập", key="collect_now", type="primary")
    with st.popover("Thông tin nguồn và thời gian"):
        st.write("Land: một điểm, sáu biến gốc. ERA5: một vùng, gồm đất liền và biển.")
        st.write("Chọn ngày Việt Nam. Ứng dụng lấy các ngày UTC bao phủ khoảng này để đủ giờ khi xem; có thể tải lại phần đã có, không chỉ từng giờ thiếu.")
    if submitted:
        if start > end or (source == "era5" and (north < south or west > east)):
            st.error("Khoảng ngày hoặc các biên vùng không hợp lệ.")
        else:
            a,b = local_date_range(start,end)
            centre = point if source != 'era5' else dict(latitude=(north+south)/2,longitude=(west+east)/2)
            params = dict(variable=variable, **centre, start=a, end=b, frequency='hourly',aggregation='auto')
            remember(source, params)
            utc = timezone.utc
            payload = dict(source=source, variable=variable,
                start_date=datetime.fromisoformat(a).astimezone(utc).date().isoformat(),
                end_date=datetime.fromisoformat(b).astimezone(utc).date().isoformat(), frequency="hourly")
            selected_place = next((p for p in st.session_state.get('collect_places', [])
                if p['label'] == st.session_state.get('collect_place')), None)
            payload.update(requested_start=a, requested_end=b, request_timezone='UTC+7')
            if source != 'era5' and selected_place:
                payload.update(place_name=selected_place['label'], selected_place={
                    k:selected_place[k] for k in ['latitude','longitude']})
            payload.update({"area": dict(north=north, south=south, west=west, east=east)}
                           if source == "era5" else {"location": point})
            api = client(source)
            response = api.create_download(payload)
            from frontend.library import _shared_read
            _shared_read.clear()
            st.session_state.setdefault('job_filters', {})[response['job_id']] = dict(source=source, **params)
            st.session_state["collection_jobs"] = {**st.session_state.get("collection_jobs", {}),
                                                    f"{api.base_url}|{source}": response["job_id"]}
            st.session_state["workflow_selected_job"] = response["job_id"]
            st.session_state['latest_collection_id'] = response.get('collection_id')
            st.session_state.pop("workflow_job_list", None)
            st.session_state.pop('saved_library', None)
            st.success("Đã tiếp nhận tác vụ.")
    api = client(source)
    st.divider()
    heading, refresh = st.columns([4, 1], vertical_alignment="center")
    heading.subheader("Tác vụ thu thập", anchor=False)
    from frontend.library import cached_read, _shared_read
    if refresh.button("Làm mới", key="refresh_jobs", width="stretch", icon=":material/refresh:"):
        _shared_read.clear()
        st.session_state.pop('workflow_job_list', None)
    if "workflow_job_list" not in st.session_state:
        st.session_state["workflow_job_list"] = cached_read(api, 'list_jobs')
    jobs = st.session_state.get("workflow_job_list", [])
    if not jobs:
        st.info("Chưa có tác vụ thu thập. Bạn có thể gửi yêu cầu bằng form ở trên.")
        return
    rows = []
    for job in jobs:
        quality = job.get("quality") or {}
        created = datetime.fromisoformat(job['created_at'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=7)))
        rows.append({"Bộ dữ liệu": job.get('collection_name') or 'Tác vụ cũ — thiếu thông tin yêu cầu',
            "Mã tác vụ": job['job_id'], "Nguồn": job.get('source'), "Dataset": job.get('dataset'),
            "Biến": job['variable'], "Từ UTC": job['start_date'], "Đến UTC": job['end_date'],
            "Tạo lúc UTC+7": created.isoformat(), "Trạng thái": job['status'],
            "Chất lượng": quality.get('status', 'Chưa kiểm tra'), "Cảnh báo": quality.get('issue_count')})
    with st.expander("Lịch sử thu thập · "+str(len(rows))+" tác vụ", expanded=False):
        st.caption("100 tác vụ gần nhất. Bấm Làm mới để cập nhật bảng; tác vụ đang xem cập nhật mỗi 15 giây.")
        st.dataframe(rows, hide_index=True, use_container_width=True)
    ids = [j['job_id'] for j in jobs]
    if st.session_state.get("workflow_selected_job") not in ids:
        st.session_state["workflow_selected_job"] = ids[0]
    labels = {j['job_id']: f"{j.get('collection_name') or (j['start_date']+' → '+j['end_date']+' UTC')} · {j.get('source') or 'Nguồn cũ'} · {j['status']} · {j['job_id'][:8]}" for j in jobs}
    selected = st.selectbox("Xem tác vụ và báo cáo", ids, format_func=labels.get, key="workflow_selected_job")
    context = "workflow_selected"
    st.session_state["collection_jobs"] = {**st.session_state.get("collection_jobs", {}), context: selected}
    _poll_collection(api, context)
    current = api.get_job(selected)
    if current.get('collection_id') and current['status'] in {'completed','failed'}:
        st.button("Xem dữ liệu vừa thu thập", key='open_collected', type='primary',
            on_click=open_saved_collection, args=(current['collection_id'],))
        if current['status'] == 'completed':
            from frontend.library import open_chart
            st.button('Vẽ biểu đồ vừa thu thập', key='chart_collected',
                      on_click=open_chart, args=(current['collection_id'],))


def open_saved_collection(collection_id):
    st.session_state['selected_collection_id'] = collection_id
    st.session_state['preferred_collection_id'] = collection_id
    st.session_state['stored_open_requested'] = collection_id
    st.session_state.pop('saved_library', None)
    st.session_state['workflow_section'] = 'Dữ liệu đã lưu'


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
