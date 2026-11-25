"""Editable collection requests and status cards for ARE."""
from datetime import date, datetime, timedelta, timezone

import streamlit as st

from frontend.api_client import DashboardApiError
from frontend.components.controls import VARIABLES, local_date_range, search_places
from frontend.chat_actions import execute_action


def _matching_collection(api, label, variable, point, start, end):
    # Search the current account's inventory, then verify actual hourly coverage.
    from backend.app.providers.era5_provider import ERA5_VARIABLES
    canonical = ERA5_VARIABLES[variable].cds_name
    listing = api.list_collections(label.split(',')[0], limit=25)
    for row in listing.get('collections', []):
        metadata = row.get('metadata', {})
        location = metadata.get('location') or {}
        if metadata.get('source') != 'land' or any(location.get(k) != v for k, v in point.items()):
            continue
        a = metadata.get('requested_start')
        b = metadata.get('requested_end')
        if not a or not b or datetime.fromisoformat(a) > datetime.fromisoformat(start) or datetime.fromisoformat(b) < datetime.fromisoformat(end):
            continue
        entry=api.get_collection(row['id'])
        grids=entry.get('grids', [])
        if len(grids)!=1 or (entry.get('summary', {}).get('quality') or {}).get('issue_count'):
            continue
        result = api.query_collection(row['id'], 'timeseries', dict(variable=canonical,
            start=start, end=end, **grids[0]))
        if result and result.get('complete') and result.get('count') == result.get('expected_count'):
            return dict(type='open_data',collection_id=row['id'],parameters=dict(
                variable=canonical,start=start,end=end,frequency='hourly',**grids[0]))
    return None


def render_collection_draft(api, draft, index):
    prefix = f'are_collect_{index}'
    message=st.session_state['ai_chat_messages'][index]['result']
    if message.get('collection_job') and not st.session_state.get(prefix+'_job'):
        st.session_state[prefix+'_job']=dict(job_id=message['collection_job'])
    if st.session_state.get(prefix+'_job'):
        _render_job(api, prefix, index)
        return
    place = st.text_input('Địa điểm', value=draft['place'], key=prefix+'_place')
    if st.button('Tìm điểm lấy dữ liệu', key=prefix+'_geocode'):
        alias = place.strip().casefold().replace('.', '')
        if alias in {'hcm', 'tphcm', 'tp hcm', 'hồ chí minh', 'tp hồ chí minh'}:
            rows = [dict(label='TP.HCM, điểm đại diện', latitude=10.8231, longitude=106.6297)]
        else:
            rows = search_places(place.strip()) if place.strip() else []
        st.session_state[prefix+'_locations'] = (place, rows)
    requested, rows = st.session_state.get(prefix+'_locations', (None, []))
    if requested != place:
        return
    if not rows:
        st.info('Chưa tìm được địa điểm. Hãy thử tên đầy đủ hoặc dùng trang Thu thập để nhập tọa độ.')
        return
    options = list(VARIABLES)
    default = next((i for i, label in enumerate(options) if VARIABLES[label] == draft.get('variable')), None)
    with st.form(prefix+'_form'):
        st.caption('Một điểm · theo giờ · UTC+7')
        chosen = st.selectbox('Điểm lấy dữ liệu', range(len(rows)), format_func=lambda i: rows[i]['label'])
        label = st.selectbox('Biến khí hậu', options, index=default)
        a = st.date_input('Từ ngày', value=date.fromisoformat(draft['start']) if draft.get('start') else None)
        b = st.date_input('Đến hết ngày', value=date.fromisoformat(draft['end']) if draft.get('end') else None)
        submitted = st.form_submit_button('Kiểm tra kho')
    if submitted:
        st.session_state.pop(prefix+'_preview', None)
        today=datetime.now(timezone(timedelta(hours=7))).date()
        if not (label and a and b) or a>b or b>today or (b-a).days>=366:
            st.error('Chọn biến và khoảng ngày hợp lệ, tối đa 366 ngày.')
            return
        point = {k: rows[chosen][k] for k in ('latitude','longitude')}
        start,end = local_date_range(a,b)
        payload = dict(source='land', variable=VARIABLES[label], frequency='hourly',
                       start_date=datetime.fromisoformat(start).astimezone(timezone.utc).date().isoformat(),
                       end_date=datetime.fromisoformat(end).astimezone(timezone.utc).date().isoformat(),
                       requested_start=start, requested_end=end, request_timezone='UTC+7',
                       location=point, selected_place=point, place_name=rows[chosen]['label'])
        try:
            existing = _matching_collection(api, payload['place_name'], payload['variable'], point, start, end)
        except DashboardApiError as exc:
            st.error(str(exc))
            return
        latest=None
        if not existing:
            try:
                latest=api.get_sync_config().get('latest_available_date')
            except DashboardApiError:
                pass
        st.session_state[prefix+'_preview'] = dict(
            payload=payload, existing=existing, label=label, place=place, latest=latest)
    preview = st.session_state.get(prefix+'_preview')
    if not preview or preview['place'] != place:
        return
    payload=preview['payload']
    st.write(f"{payload['place_name']} · {preview['label']} · {payload['requested_start'][:10]} – {payload['requested_end'][:10]}")
    st.caption(f"Điểm {payload['location']['latitude']:.4f}, {payload['location']['longitude']:.4f} · UTC+7")
    if preview['existing']:
        st.button('Xem dữ liệu đã có', key=prefix+'_existing', on_click=execute_action,
            args=(api,preview['existing'],index))
    else:
        if preview['latest'] and payload['end_date']>preview['latest']:
            st.warning(f"Ngày cuối vượt mốc khả dụng ước tính {preview['latest']} (UTC). CDS có thể chưa công bố đủ dữ liệu.")
        _confirm_collection(api, prefix, payload, message)


def _confirm_collection(api, prefix, payload, message):
    st.caption('CDS có thể công bố dữ liệu gần đây chậm hơn ngày yêu cầu. Tác vụ sẽ báo lỗi nếu nguồn chưa có dữ liệu.')
    queued=st.session_state.pop(prefix+'_submission', None)
    if queued is not None:
        try:
            with st.spinner('Đang gửi yêu cầu thu thập…', show_time=True):
                from frontend.workflow import submit_collection_job
                params=dict(variable=queued['variable'], **queued['location'],
                            start=queued['requested_start'], end=queued['requested_end'],
                            frequency=queued['frequency'], aggregation='auto')
                job=submit_collection_job(api, queued, params)
        except DashboardApiError as exc:
            st.session_state[prefix+'_submit_error']=str(exc)
        else:
            st.session_state[prefix+'_job']=job
            message['collection_job']=job['job_id']
            st.session_state['are_open_collection_jobs']=True
            st.rerun()
    if error:=st.session_state.get(prefix+'_submit_error'):
        st.error(f'Chưa xác nhận được tác vụ: {error}')
    st.button('Xác nhận thu thập', key=prefix+'_confirm',type='primary',
              on_click=_queue_submission,args=(prefix,payload))


def _queue_submission(prefix, payload):
    if not st.session_state.get(prefix+'_job'):
        st.session_state[prefix+'_submission']=payload
        st.session_state.pop(prefix+'_submit_error', None)


@st.fragment(run_every=15)
def _render_job(api, prefix, index):
    job=st.session_state[prefix+'_job']
    if job.get('status') in {'queued','running','cancelling',None}:
        try:
            job={**job,**api.get_job(job['job_id'])}
            st.session_state[prefix+'_job']=job
        except DashboardApiError as exc:
            st.warning(str(exc))
    labels={'queued':'Đang chờ','running':'Đang thu thập','completed':'Hoàn tất',
            'failed':'Thất bại','cancelled':'Đã hủy','cancelling':'Đang hủy','partial':'Có dữ liệu một phần'}
    st.info(f"Tác vụ đã tiếp nhận · {labels.get(job.get('status'), 'Đang kiểm tra')}")
    st.caption(f"Mã tác vụ: {job['job_id']}")
    if job.get('error'):
        st.error(job['error'])
    if job.get('status') in {'completed','partial'}:
        cid=job.get('collection_id') or job['job_id']
        st.button('Xem dữ liệu vừa thu thập', key=prefix+'_result',on_click=execute_action,
                  args=(api,dict(type='open_data',collection_id=cid),index))
        st.button('Mở biểu đồ để phân tích', key=prefix+'_chart',on_click=execute_action,
                  args=(api,dict(type='open_chart',collection_id=cid),index,True))
