"""A library of named collection snapshots, not a merged cache inventory."""
from datetime import datetime, timezone, timedelta
import json
from copy import deepcopy
from uuid import uuid4
from contextlib import nullcontext
import pandas as pd
import streamlit as st
from frontend.components.charts import variable_label
from frontend.api_client import DashboardApiError
from frontend.notifications import notify_error, notify_warning, notify_success, notify_info, render_empty_data_notice
from backend.app.domain.public_variables import public_variables

STATUS = dict(queued='Đang chờ', running='Đang thu thập', complete='Đủ dữ liệu',
              partial='Thiếu một phần', failed='Thất bại', interrupted='Bị gián đoạn', cancelling='Đang hủy', cancelled='Đã hủy')


@st.cache_data(ttl=60, max_entries=128, show_spinner=False)
def _shared_read(base_url, source, stored_only, method, args_json, _api):
    """Cache local API reads across browser reloads, independently of sessions."""
    return getattr(_api, method)(*json.loads(args_json))


def cached_read(api, method, *args):
    if method == 'list_collections':
        key = json.dumps([api.base_url, api.source, api.stored_only, args], sort_keys=True)
        pages = st.session_state.setdefault('library_pages', {})
        if key not in pages:
            with st.spinner('Đang đọc thư viện dữ liệu…'):
                payload = _shared_read(api.base_url, api.source, api.stored_only, method,
                                       json.dumps(args, sort_keys=True), _api=api)
            if len(pages) >= 128:
                pages.pop(next(iter(pages)))
            pages[key] = payload
        return deepcopy(pages[key])
    return _shared_read(api.base_url, api.source, api.stored_only, method,
                        json.dumps(args, sort_keys=True), _api=api)


def invalidate_reads():
    st.session_state.pop('library_pages', None)
    _shared_read.clear()


def invalidate_job_list():
    st.session_state.pop('workflow_job_list', None)
    st.session_state['workflow_jobs_revision'] = st.session_state.get('workflow_jobs_revision', 0) + 1
    st.session_state['workflow_jobs_due'] = 0


def label(entry):
    m=entry['metadata']
    a,b=(datetime.fromisoformat(m[k]).strftime('%d/%m/%Y') for k in ['requested_start','requested_end'])
    return f"{variable_label(m['primary_variable'])} — {m['place_name']} — {a} đến {b} · {m['source']} · {entry['id'][:8]}"


def decode_saved_view(raw):
    """URL state is untrusted; validate before issuing API reads or setting widgets."""
    saved = json.loads(raw)
    if not isinstance(saved, list) or len(saved) != 4:
        raise ValueError('Invalid saved view')
    cid, kind, params, operation = saved
    if not isinstance(cid,str) or not cid or not isinstance(params,dict):
        raise ValueError('Invalid saved collection')
    operations = {'Chuỗi thời gian': {'timeseries','aggregate'}, 'Bản đồ nhiệt': {'heatmap'},
                  'Không gian (điểm)': {'map'}, 'Thống kê và xu hướng': {'statistics'}}
    if not isinstance(kind,str) or kind not in operations or not isinstance(operation,str) or operation not in operations[kind]:
        raise ValueError('Invalid saved chart operation')
    if not isinstance(params.get('variable'),str) or not params['variable']:
        raise ValueError('Missing saved variable')
    for coordinate,limit in [('latitude',90),('longitude',180)]:
        number = params.get(coordinate)
        if type(number) not in (int,float) or not -limit <= number <= limit:
            raise ValueError('Invalid saved grid')
    if params.get('frequency') not in ['hourly','daily','monthly','yearly']:
        raise ValueError('Invalid saved frequency')
    if params.get('aggregation') not in ['auto','mean','sum','min','max']:
        raise ValueError('Invalid saved aggregation')
    start,end = (datetime.fromisoformat(params[edge]) for edge in ['start','end'])
    if start.utcoffset() is None or end.utcoffset() is None or start>end:
        raise ValueError('Invalid saved interval')
    return cid,kind,params,operation


def open_chart(collection_id, variable=None, grid=None, interval=None, variables=None,
               available_variables=None, handoff_payloads_ready=False):
    st.session_state['chart_filters_'+collection_id] = False
    st.session_state['saved_chart_frequency'] = 'hourly'
    st.session_state['saved_chart_kind'] = 'Chuỗi thời gian'
    st.session_state['chart_collection'] = collection_id
    st.session_state['chart_details_visible'] = True
    st.query_params['chart_mode'] = 'details'
    st.session_state['chart_library_cards'] = False
    st.session_state['stored_library_cards'] = False
    st.session_state['selected_collection_id']=collection_id
    st.session_state['chart_preferred_collection_id']=collection_id
    st.session_state['workflow_section']='Biểu đồ'
    st.session_state['chart_open_requested']=collection_id
    if variable:
        st.session_state['chart_variable_'+collection_id]=variable
    selected_variables = list(dict.fromkeys(variables or ([variable] if variable else [])))
    for name in available_variables or selected_variables:
        st.session_state[f'stored_var_{name}_{collection_id}'] = name in selected_variables
    if available_variables:
        st.session_state[f'stored_var_all_{collection_id}'] = (
            bool(available_variables) and set(available_variables) == set(selected_variables)
        )
    if not handoff_payloads_ready:
        st.session_state.pop('chart_handoff_payloads_'+collection_id, None)
    if grid is not None:
        st.session_state['chart_grid_'+collection_id]=grid
    if interval:
        for name,value in zip(['start','end'],interval):
            stamp=datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=7)))
            st.session_state['chart_range_'+name+'_'+collection_id]=stamp.date()
            st.session_state['chart_hour_'+name+'_'+collection_id]=stamp.time().replace(tzinfo=None)


def open_details(collection_id):
    st.session_state['stored_collection'] = collection_id
    st.session_state['stored_details_visible'] = True
    st.query_params['stored_mode'] = 'details'
    st.session_state['stored_library_cards'] = False
    st.session_state['chart_library_cards'] = False
    st.session_state['selected_collection_id']=collection_id
    st.session_state['stored_preferred_collection_id']=collection_id
    st.session_state['workflow_section']='Dữ liệu đã lưu'
    st.session_state['stored_open_requested']=collection_id


def back_to_library(prefix='stored'):
    st.session_state[prefix+'_details_visible'] = False
    st.session_state[prefix+'_library_cards'] = True
    st.session_state[prefix+'_scroll_to_list'] = True
    st.query_params[prefix+'_mode'] = 'list'


def _scroll_to(target):
    st.html(f"""
        <style>.st-key-{target} {{scroll-margin-top:5rem;}}</style>
        <script data-scroll="{uuid4().hex}">
        (() => {{
            let frame;
            let stopped = false;
            const deadline = performance.now()+1500;
            const scroll = () => {{
                if (stopped || performance.now()>deadline) {{stop(); return;}}
                const roots = document.querySelectorAll('.st-key-{target}');
                const root = roots[roots.length-1];
                if (root) {{
                    const target = root.matches('.st-key-chart_result')
                        && root.querySelector('.js-plotly-plot') || root;
                    target.style.scrollMarginTop = '5rem';
                    target.scrollIntoView({{behavior: 'instant', block: 'start'}});
                }}
                frame = requestAnimationFrame(scroll);
            }};
            const stop = () => {{ stopped = true; cancelAnimationFrame(frame);
                window.removeEventListener('wheel', stop); window.removeEventListener('touchstart', stop);
                window.removeEventListener('pointerdown', stop); }};
            window.addEventListener('pointerdown', stop, {{once:true, passive:true}});
            window.addEventListener('wheel', stop, {{once:true, passive:true}});
            window.addEventListener('touchstart', stop, {{once:true, passive:true}});
            frame = requestAnimationFrame(scroll);
        }})();
        </script>
    """, unsafe_allow_javascript=True)


def clear_search(prefix):
    st.session_state[prefix+'_search']=''
    st.session_state[prefix+'_library_text']=''
    st.session_state[prefix+'_page']=0
    st.session_state[prefix+'_library_cards']=True


def forget_deleted(collection_id, job_id):
    for prefix in ['stored','chart']:
        if st.session_state.get(prefix+'_collection') == collection_id:
            st.session_state[prefix+'_details_visible'] = False
            st.session_state[prefix+'_library_cards'] = True
            st.query_params[prefix+'_mode'] = 'list'
    # Successful deletion already tells us which item disappeared. Reuse known
    # pages, invalidate offset-dependent later pages, and backfill only if needed.
    pages = st.session_state.get('library_pages', {})
    for key in list(pages):
        entries = pages[key]['collections']
        _, _, _, args = json.loads(key)
        if args[2] > 0:
            del pages[key]
            continue
        filtered = [e for e in entries if e['id'] != collection_id]
        pages[key] = {**pages[key], 'collections': filtered}
        if len(entries) == 51 and len(filtered) < len(entries):
            # A full page may need a new lookahead row; refresh it on next entry.
            del pages[key]
    _shared_read.clear()
    for key in list(st.session_state):
        value = st.session_state[key]
        if (key in {'selected_collection_id', 'preferred_collection_id', 'latest_collection_id',
                    'stored_collection', 'chart_collection', 'stored_open_requested', 'chart_open_requested',
                    'stored_preferred_collection_id','chart_preferred_collection_id'}
                and value == collection_id):
            del st.session_state[key]
        elif key in {'stored_result', 'chart_result', 'stored_quality_result', 'chart_quality_result'} and value and value[0] == collection_id:
            del st.session_state[key]
    invalidate_job_list()
    if st.session_state.get('workflow_selected_job') == job_id:
        st.session_state.pop('workflow_selected_job', None)
    for prefix in ['stored', 'chart']:
        raw = st.query_params.get(prefix+'_applied')
        if raw:
            try:
                if json.loads(raw)[0] == collection_id:
                    del st.query_params[prefix+'_applied']
            except (ValueError, TypeError, IndexError):
                pass
        st.session_state[prefix+'_page'] = 0
    st.session_state['collection_deleted_notice'] = True


def render_saved(client, charts=False):
    from frontend.dashboard import _show_timeseries, _show_heatmap, _show_map, _show_statistics, _show_trend
    api=client('land')  # Collection ID, rather than this client's default source, controls reads.
    prefix='chart' if charts else 'stored'
    with st.form(prefix+'_library_search',border=False):
        field,action=st.columns([4,1],vertical_alignment='bottom',wrap=False)
        text=field.text_input('Tìm bộ dữ liệu đã lưu',placeholder='Ví dụ: Hà Nội, nhiệt độ, 2025-01',key=prefix+'_library_text')
        find=action.form_submit_button('Tìm dữ liệu',key=prefix+'_find',width='stretch')
    if find:
        st.session_state[prefix+'_search']=text.strip()
        st.session_state[prefix+'_page']=0
        st.session_state[prefix+'_library_cards']=True
    if st.session_state.get(prefix+'_search'):
        st.button('Xóa tìm kiếm',key=prefix+'_clear_search',on_click=clear_search,args=(prefix,))
    saved_view = st.query_params.get(prefix+'_applied')
    show_details = st.session_state.get(prefix+'_details_visible', False)
    if show_details and prefix+'_result' not in st.session_state and saved_view:
        try:
            cid, saved_kind, saved_params, operation = decode_saved_view(saved_view)
            if st.session_state.get(prefix+'_collection') not in {None, cid}:
                raise ValueError('Saved view belongs to another collection')
            if not isinstance(cid, str) or not isinstance(saved_params, dict):
                raise ValueError('Invalid saved view')
            st.session_state[prefix+'_collection'] = cid
            payload = cached_read(api, 'query_collection', cid, operation, saved_params)
            if operation == 'statistics' and saved_params.get('variable') != 'wind_direction':
                payload['trend_result'] = cached_read(api, 'query_collection', cid, 'trend', saved_params)
            st.session_state[prefix+'_result'] = (cid, saved_kind, payload)
            st.session_state['selected_collection_id'] = cid
            st.session_state[prefix+'_collection'] = cid
            st.session_state[prefix+'_variable_'+cid] = saved_params['variable']
            entry = cached_read(api, 'get_collection', cid)
            grid = dict(latitude=saved_params['latitude'], longitude=saved_params['longitude'])
            st.session_state[prefix+'_grid_'+cid] = entry['grids'].index(grid)
            for edge in ['start', 'end']:
                stamp=datetime.fromisoformat(saved_params[edge]).astimezone(timezone(timedelta(hours=7)))
                st.session_state[prefix+'_range_'+edge+'_'+cid] = stamp.date()
                st.session_state[prefix+'_hour_'+edge+'_'+cid] = stamp.time().replace(tzinfo=None)
            if charts:
                st.session_state['saved_chart_frequency'] = saved_params['frequency']
                st.session_state['saved_chart_kind'] = saved_kind
        except (ValueError, TypeError, KeyError):
            del st.query_params[prefix+'_applied']
            back_to_library(prefix)
            show_details = False
            msg = 'Cấu hình trên đường dẫn không còn hợp lệ. Hãy chọn lại bộ dữ liệu.'
            st.warning(msg)
            notify_warning(msg)
        except DashboardApiError as exc:
            if exc.status_code == 404:
                forget_deleted(cid, cid)
                st.session_state.pop('collection_deleted_notice', None)
                show_details = False
                msg = 'Bộ dữ liệu đang xem không còn tồn tại. Đã trở về danh sách.'
                st.warning(msg)
                notify_warning(msg)
            else:
                msg = 'Chưa khôi phục được kết quả: '+str(exc)
                st.error(msg)
                notify_error(msg)
    list_slot = st.container(key=prefix+'_collection_list')
    detail_slot = st.empty()
    with list_slot:
        has_entries = render_library_list(api, prefix, detail_slot)
    if st.session_state.pop(prefix+'_scroll_to_list', False):
        _scroll_to(prefix+'_collection_list')
    if not has_entries or not show_details:
        return
    with detail_slot.container(key=prefix+'_collection_detail'):
        selected=st.session_state.get(prefix+'_collection')
        if not selected:
            return
        action_open=st.session_state.pop(prefix+'_action_open',None)==selected
        action_scroll=st.session_state.pop(prefix+'_action_scroll',None)==selected
        st.session_state[prefix+'_collection']=selected
        try:
            entry=cached_read(api, 'get_collection', selected)
        except DashboardApiError as exc:
            if exc.status_code != 404:
                raise
            forget_deleted(selected, selected)
            st.session_state.pop('collection_deleted_notice', None)
            st.session_state['missing_collection_notice'] = True
            st.rerun()
        if st.session_state.pop(prefix+'_restore_filters', False):
            raw = st.query_params.get(prefix+'_applied')
            if raw:
                try:
                    cid, saved_kind, params, _ = decode_saved_view(raw)
                    if cid == selected:
                        st.session_state[prefix+'_variable_'+selected] = params['variable']
                        grid = dict(latitude=params['latitude'], longitude=params['longitude'])
                        st.session_state[prefix+'_grid_'+selected] = entry['grids'].index(grid)
                        for edge in ['start','end']:
                            stamp=datetime.fromisoformat(params[edge]).astimezone(timezone(timedelta(hours=7)))
                            st.session_state[prefix+'_range_'+edge+'_'+selected] = stamp.date()
                            st.session_state[prefix+'_hour_'+edge+'_'+selected] = stamp.time().replace(tzinfo=None)
                        if charts:
                            st.session_state['saved_chart_kind'] = saved_kind
                            st.session_state['saved_chart_frequency'] = params['frequency']
                except (ValueError, TypeError, KeyError):
                    pass
        st.session_state['selected_collection_id']=selected
        m,s=entry['metadata'],entry['summary']
        st.divider()
        st.button('Quay lại danh sách', key=prefix+'_back_to_list',
                  icon=':material/arrow_back:', on_click=back_to_library, args=(prefix,))
        st.subheader(f"{variable_label(m['primary_variable'])} · {m['place_name']}", anchor=False)
        a,b=(datetime.fromisoformat(m[k]).astimezone(timezone(timedelta(hours=7))).strftime('%d/%m/%Y %H:%M') for k in ['requested_start','requested_end'])
        st.write(f"{a} → {b} (UTC+7)")
        with st.expander('Chi tiết bộ dữ liệu và chất lượng', expanded=False) if charts else nullcontext():
            st.caption(f"{m['dataset']} · {STATUS.get(entry['status'],entry['status'])}" + (' · DỮ LIỆU GIẢ LẬP' if m['source']=='mock' else ''))
            if m.get('location'):
                st.caption(f"Điểm yêu cầu: {m['location']['latitude']:.4f}, {m['location']['longitude']:.4f}")
            else: st.caption('Phạm vi yêu cầu: '+str(m.get('area')))
            x,y,z=st.columns(3)
            x.metric('Giá trị chính đã lưu / yêu cầu',f"{s.get('available_count',0)} / {s.get('expected_count','—')}")
            y.metric('Độ phủ biến chính',f"{s.get('coverage',0):.1%}")
            z.metric('Cảnh báo',(s.get('quality') or {}).get('issue_count',0))
            if entry['status'] in {'queued','running','cancelling'}:
                st.info('Tác vụ chưa kết thúc. Nếu backend bị dừng giữa chừng, hãy kiểm tra tác vụ trước khi tạo lại; trạng thái này không chứng minh bộ dữ liệu đã hoàn tất.')
            if entry['status']=='partial': st.warning('Bộ dữ liệu chưa đủ. Giá trị thiếu không được thay bằng 0.')
            if s.get('grid_coordinates_verified') is False:
                st.warning('File nguồn thiếu tọa độ lưới. Điểm lưu là điểm thay thế do parser ghi nhận, chưa xác minh là điểm lưới thực.')
            if entry.get('error'):
                st.error(entry['error'])
                notify_error(f"Lỗi bộ dữ liệu: {entry['error']}")
            with st.expander('Thông tin lần thu thập, file nguồn và các biến bổ sung'):
                st.write('Mã bộ dữ liệu: '+entry['id'])
                st.write('Tạo lúc: '+entry['created_at'])
                st.write('Khoảng tải UTC: '+m['start_date']+' → '+m['end_date'])
                st.caption('Số bên dưới gồm cả giờ UTC dư và biến bổ sung; không phải số giá trị chính trong khoảng bạn chọn.')
                st.dataframe([{'Biến':variable_label(v['variable']),'Vai trò':'Yêu cầu' if v['role']=='requested' else 'Bổ sung',
                               'Số giá trị lưu':s.get('counts_by_variable',{}).get(v['variable'],0)} for v in entry['variables']],hide_index=True)
                if entry['artifacts']: st.dataframe(entry['artifacts'],hide_index=True)
            if st.button('Chuẩn bị báo cáo chất lượng',key=prefix+'_quality'):
                st.session_state[prefix+'_quality_result']=(selected,api.collection_quality(selected))
            q=st.session_state.get(prefix+'_quality_result')
            if q and q[0]==selected:
                st.download_button('Tải cảnh báo (JSON)',json.dumps(q[1],ensure_ascii=False,indent=2),
                                   file_name=f'quality_{selected}.json',mime='application/json',on_click='ignore')
                with st.expander('Chi tiết cảnh báo'): st.dataframe(q[1]['issues'][:100],hide_index=True)
        if not entry['grids']:
            msg = 'Bộ dữ liệu chưa có điểm lưới hợp lệ để xem hoặc vẽ.'
            st.info(msg)
            notify_warning(msg, icon='📍')
            if action_scroll: _scroll_to(prefix+'_collection_detail')
            return
        filter_key = 'chart_filters_'+selected
        if charts and filter_key not in st.session_state:
            saved = st.session_state.get('chart_result')
            st.session_state[filter_key] = not bool(saved and saved[0] == selected)
        if charts and st.session_state.pop('chart_collapse_filters', None) == selected:
            st.session_state[filter_key] = False
        with st.expander('Bộ lọc biểu đồ', expanded=True, key=filter_key, on_change='rerun') if charts else nullcontext():
            variables=public_variables([m['primary_variable']]+[v['variable'] for v in entry['variables']])
            if not variables:
                st.info('Bộ dữ liệu chưa có biến chính được hỗ trợ để xem hoặc vẽ.')
                return
            primary=next((v for v in variables if variable_label(v)==variable_label(m['primary_variable'])),None)
            if primary:
                variables=[primary]+[v for v in variables if v!=primary]
            variable_key=prefix+'_variable_'+selected
            if st.session_state.get(variable_key) not in variables:
                st.session_state[variable_key]=variables[0]
            variable = st.session_state[variable_key]
            st.caption('Chọn biến, điểm lưới và khoảng ngày, rồi bấm nút bên dưới để cập nhật kết quả. Thay đổi trong form chưa được áp dụng.')
            if charts:
                kind = st.selectbox('Loại biểu đồ',['Chuỗi thời gian','Bản đồ nhiệt','Không gian (điểm)','Thống kê và xu hướng'],key='saved_chart_kind')
                st.caption('Các lựa chọn biến được đồng bộ với Kho dữ liệu.')
            with st.form(prefix+'_selected_form'):
                if not charts:
                    with st.container(key='hide_stored_variable_selectbox'):
                        variable = st.selectbox('Biến trong bộ dữ liệu', variables, format_func=variable_label, key=prefix+'_variable_'+selected)
                st.write('**Chọn biến trong bộ dữ liệu:**')
                all_var_key = 'stored_var_all_' + selected
                all_checked = st.checkbox('Tất cả các biến (lấy full dữ liệu)', key=all_var_key)
                var_cols = st.columns(min(len(variables), 3))
                for vi, v in enumerate(variables):
                    cb_key = f'stored_var_{v}_{selected}'
                    var_cols[vi % len(var_cols)].checkbox(
                        variable_label(v),
                        value=True if all_checked else st.session_state.get(cb_key, v == variable),
                        disabled=all_checked,
                        key=cb_key,
                    )
                grids=entry['grids']
                idx=st.selectbox('Điểm lưới thực',range(len(grids)),format_func=lambda i:f"{grids[i]['latitude']}, {grids[i]['longitude']}",key=prefix+'_grid_'+selected)
                local=timezone(timedelta(hours=7))
                lo,hi=(datetime.fromisoformat(m[k]).astimezone(local) for k in ['requested_start','requested_end'])
                with nullcontext() if charts else st.expander('Khoảng xem trong bộ dữ liệu'):
                    st.caption('Chỉ chọn các ngày thuộc bộ này. Với bộ dài, mặc định xem 30 ngày đầu để giữ truy vấn gọn.')
                    one,two=st.columns(2)
                    start_key=prefix+'_range_start_'+selected
                    first=one.date_input('Từ ngày (UTC+7)',value=None if start_key in st.session_state else lo.date(),min_value=lo.date(),max_value=hi.date(),key=start_key)
                    default_end=min(hi.date(),(lo+timedelta(days=29)).date())
                    end_key=prefix+'_range_end_'+selected
                    last=two.date_input('Đến ngày (UTC+7)',value=None if end_key in st.session_state else default_end,min_value=lo.date(),max_value=hi.date(),key=end_key)
                    from_hour_key=prefix+'_hour_start_'+selected
                    to_hour_key=prefix+'_hour_end_'+selected
                    from_hour=one.time_input('Từ giờ (UTC+7)',value=None if from_hour_key in st.session_state else lo.time().replace(tzinfo=None),key=from_hour_key,step=3600)
                    to_hour=two.time_input('Đến giờ (UTC+7)',value=None if to_hour_key in st.session_state else hi.time().replace(tzinfo=None),key=to_hour_key,step=3600)
                frequency='hourly'
                if not charts:
                    kind='Chuỗi thời gian'
                if charts:
                    if kind == 'Chuỗi thời gian':
                        frequency=st.selectbox('Chu kỳ',['hourly','daily','monthly','yearly'],format_func=lambda v:dict(hourly='Giờ',daily='Ngày',monthly='Tháng',yearly='Năm')[v],key='saved_chart_frequency')
                    elif kind == 'Bản đồ nhiệt':
                        frequency='monthly'
                        st.caption('Bản đồ nhiệt tổng hợp theo tháng.')
                col_sub, col_rst = st.columns(2)
                submit = col_sub.form_submit_button('Vẽ dữ liệu đã chọn' if charts else 'Áp dụng bộ lọc', key=prefix+'_selected_submit', type='primary')
                reset_filters = col_rst.form_submit_button('Làm mới bộ lọc', key=prefix+'_reset_filters', icon=':material/refresh:')
        if reset_filters:
            for k in [
                prefix + '_range_start_' + selected,
                prefix + '_range_end_' + selected,
                prefix + '_hour_start_' + selected,
                prefix + '_hour_end_' + selected,
                prefix + '_grid_' + selected,
                prefix + '_variable_' + selected,
                'stored_var_all_' + selected,
            ]:
                st.session_state.pop(k, None)
            for v in variables:
                st.session_state.pop(f'stored_var_{v}_{selected}', None)
            if charts:
                st.session_state.pop('saved_chart_kind', None)
                st.session_state.pop('saved_chart_frequency', None)
            st.session_state.pop(prefix + '_result', None)
            notify_info('Đã làm mới bộ lọc về mặc định.')
            st.rerun()
        if all_checked:
            selected_vars = list(variables)
        else:
            checked_vars = [v for v in variables if st.session_state.get(f'stored_var_{v}_{selected}')]
            selected_vars = checked_vars
        if not selected_vars:
            if submit:
                st.session_state.pop(prefix+'_result', None)
                st.warning('Chọn ít nhất một biến bằng checkbox để vẽ biểu đồ.')
            if action_scroll:
                _scroll_to(prefix+'_collection_detail')
            return
        variable = selected_vars[0]
        opening=st.session_state.pop(prefix+'_open_requested',None)==selected
        handoff_payloads = (
            st.session_state.pop('chart_handoff_payloads_'+selected, None)
            if charts and opening else None
        )
        if handoff_payloads:
            kind = 'Chuỗi thời gian'
            first_payload = deepcopy(handoff_payloads[0])
            first_payload['chart_payloads'] = deepcopy(handoff_payloads)
            first_payload['chart_payload_errors'] = []
            st.session_state['chart_result'] = (selected, kind, first_payload)
            grid_location = first_payload['grid_location']
            chart_params = dict(
                variable=first_payload['variable'],
                latitude=grid_location['latitude'], longitude=grid_location['longitude'],
                frequency='hourly', aggregation='auto',
                start=first_payload['requested_start'], end=first_payload['requested_end'],
            )
            st.query_params['chart_applied'] = json.dumps(
                [selected, kind, chart_params, 'timeseries'], ensure_ascii=False
            )
            opening = False
        previous=st.session_state.get(prefix+'_result')
        applied = False
        force_refresh = st.session_state.pop('chart_force_refresh', False) if charts else False
        deferred=st.session_state.get(prefix+'_defer_query')==selected
        querying=submit or opening or (force_refresh and not deferred) or (not charts and not deferred and (not previous or previous[0]!=selected))
        invalid_window=(first is None or last is None or from_hour is None or to_hour is None
                        or first>last or (first==last and from_hour>to_hour)
                        or bool(from_hour and (from_hour.minute or from_hour.second))
                        or bool(to_hour and (to_hour.minute or to_hour.second)))
        if invalid_window and querying:
            msg = 'Khoảng thời gian không hợp lệ: Chọn ngày/giờ bắt đầu trước hoặc bằng ngày/giờ kết thúc và phải là giờ tròn (00 phút)!'
            st.error(msg)
            notify_error(msg)
        elif querying:
            a=max(lo,datetime.combine(first,from_hour,local))
            b=min(hi,datetime.combine(last,to_hour,local))
            params=dict(variable=variable,**grids[idx],frequency=frequency,aggregation='auto',start=a.isoformat(),end=b.isoformat())
            operation=('timeseries' if frequency=='hourly' else 'aggregate') if kind=='Chuỗi thời gian' else {'Bản đồ nhiệt':'heatmap','Không gian (điểm)':'map','Thống kê và xu hướng':'statistics'}[kind]
            if force_refresh and not submit and not opening and st.query_params.get('chart_applied'):
                _,kind,params,operation = decode_saved_view(st.query_params['chart_applied'])
            with st.spinner('Đang cập nhật kết quả…'):
                try:
                    payload=cached_read(api, 'query_collection', selected,operation,params)
                    if kind=='Thống kê và xu hướng' and variable!='wind_direction':
                        payload['trend_result']=cached_read(api, 'query_collection', selected,'trend',params)
                    if charts and len(selected_vars) > 1:
                        chart_payloads = [payload]
                        chart_payload_errors = []
                        for other_var in selected_vars:
                            if other_var == variable:
                                continue
                            try:
                                other_p = cached_read(api, 'query_collection', selected, operation, dict(params, variable=other_var))
                                if kind == 'Thống kê và xu hướng' and other_var != 'wind_direction':
                                    other_p['trend_result'] = cached_read(api, 'query_collection', selected, 'trend', dict(params, variable=other_var))
                                chart_payloads.append(other_p)
                            except DashboardApiError as exc:
                                chart_payload_errors.append((other_var, str(exc)))
                        payload['chart_payloads'] = chart_payloads
                        payload['chart_payload_errors'] = chart_payload_errors
                    if not charts and len(selected_vars) > 1:
                        multi_payloads = [payload]
                        for other_var in selected_vars:
                            if other_var == variable:
                                continue
                            try:
                                other_payload = cached_read(api, 'query_collection', selected, operation, dict(params, variable=other_var))
                                multi_payloads.append(other_payload)
                            except DashboardApiError:
                                pass
                        payload['multi_payloads'] = multi_payloads
                except DashboardApiError as exc:
                    msg = 'Chưa áp dụng bộ lọc mới: ' + str(exc)
                    st.error(msg)
                    notify_error(msg)
                else:
                    st.session_state.pop(prefix+'_defer_query',None)
                    st.session_state[prefix+'_result']=(selected,kind,payload)
                    st.query_params[prefix+'_applied'] = json.dumps([selected, kind, params, operation], ensure_ascii=False)
                    applied = True
                    if charts and submit:
                        st.session_state['chart_collapse_filters'] = selected
                        st.session_state['chart_scroll_result'] = selected
                        st.rerun()
        result=st.session_state.get(prefix+'_result')
        if not result or result[0]!=selected:
            if action_scroll: _scroll_to(prefix+'_collection_detail')
            return
        _,kind,payload=result
        with st.container(key='chart_result' if charts else 'stored_data_result'):
            grid=payload['grid_location']
            multi = payload.get('multi_payloads') if not charts else None
            if multi and len(multi) > 1:
                var_names = ", ".join(variable_label(p['variable']) for p in multi)
                st.caption(f"Kết quả đã áp dụng: {var_names} ({len(multi)} biến) · UTC+7 · điểm {grid['latitude']:.4f}, {grid['longitude']:.4f}")
            else:
                st.caption(f"Kết quả đã áp dụng: {variable_label(payload['variable'])} · {payload['unit']} · UTC+7 · điểm {grid['latitude']:.4f}, {grid['longitude']:.4f}")
            st.caption(payload['requested_start']+' → '+payload['requested_end']+' (UTC+7)')
            if charts:
                c_list = payload.get('chart_payloads')
                plotted = c_list if c_list else [payload]
                var_names = ", ".join(variable_label(p['variable']) for p in plotted)
                st.caption(
                    f"Đang hiển thị {len(plotted)}/{len(selected_vars)} biểu đồ "
                    f"({var_names}) · Chu kỳ: {payload.get('frequency', 'hourly')}"
                )
                for failed_var, error in payload.get('chart_payload_errors', []):
                    st.warning(f"Không thể vẽ {variable_label(failed_var)}: {error}")
            if not payload['complete']:
                if payload['available_count'] == 0:
                    notify_warning(f"Chưa có dữ liệu cho khoảng thời gian này (0/{payload['expected_count']} giờ).", icon="⚠️")
                st.warning(f"Có {payload['available_count']}/{payload['expected_count']} giờ. Khoảng thiếu được giữ trống.")
                with st.expander('Khoảng giờ thiếu'): st.json(payload['missing_ranges'])
            if not charts:
                if multi and len(multi) > 1:
                    time_map = {}
                    for p in multi:
                        col_label = f"{variable_label(p['variable'])} ({p['unit']})"
                        for pt in p.get('data', []):
                            t = pt['time']
                            if t not in time_map:
                                time_map[t] = {
                                    'Thời gian (UTC+7)': datetime.fromisoformat(t).strftime('%d/%m/%Y %H:%M'),
                                    'time': t,
                                }
                            time_map[t][col_label] = pt['value']
                            time_map[t][p['variable']] = pt['value']
                    sorted_rows = sorted(time_map.values(), key=lambda r: r['time'])
                    display_cols = ['Thời gian (UTC+7)'] + [f"{variable_label(p['variable'])} ({p['unit']})" for p in multi]
                    frame = pd.DataFrame([{c: r.get(c, None) for c in display_cols} for r in sorted_rows])
                    export_rows = []
                    for r in sorted_rows:
                        row_data = {
                            'time': r['time'],
                            'collection_id': payload.get('collection_id', selected),
                            'place_name': payload.get('place_name', ''),
                            'source': payload.get('source', ''),
                            'dataset': payload.get('dataset', ''),
                            'grid_latitude': payload.get('grid_location', {}).get('latitude'),
                            'grid_longitude': payload.get('grid_location', {}).get('longitude'),
                        }
                        for p in multi:
                            v_name = p['variable']
                            row_data[f"{v_name} ({p['unit']})"] = r.get(v_name, None)
                        export_rows.append(row_data)
                    export = pd.DataFrame(export_rows)
                else:
                    frame=pd.DataFrame([{'Thời gian (UTC+7)':datetime.fromisoformat(p['time']).strftime('%d/%m/%Y %H:%M'),f"{variable_label(payload['variable'])} ({payload['unit']})":p['value']} for p in payload['data']])
                    export=pd.DataFrame(payload['data'])
                    for k in ['collection_id','place_name','source','dataset','variable','unit']: export[k]=payload[k]
                    for k,v in payload['grid_location'].items(): export['grid_'+k]=v
                selected_grid=entry['grids'].index(payload['grid_location'])
                handoff_payloads = multi if multi else [payload]
                multi_vars = [p['variable'] for p in handoff_payloads]
                st.session_state['chart_handoff_payloads_'+selected] = deepcopy(handoff_payloads)
                with st.container(horizontal=True):
                    st.button('Vẽ biểu đồ từ dữ liệu này',type='primary',icon=':material/bar_chart:',on_click=open_chart,args=(selected,payload['variable'],selected_grid,
                              (payload['requested_start'],payload['requested_end']), multi_vars, variables, True))
                    st.download_button('Tải CSV',export.to_csv(index=False).encode('utf-8-sig'),file_name=f'collection_{selected}.csv',mime='text/csv',key='download_timeseries_csv',on_click='ignore',icon=':material/download:')
                st.dataframe(frame,hide_index=True,width='stretch')
            else:
                charts_list = payload.get('chart_payloads') if charts else None
                if charts_list and len(charts_list) > 1:
                    render_func = {'Chuỗi thời gian': _show_timeseries,
                                   'Bản đồ nhiệt': _show_heatmap,
                                   'Không gian (điểm)': _show_map,
                                   'Thống kê và xu hướng': _show_statistics}[kind]
                    for p_idx, p_item in enumerate(charts_list):
                        chart_key = f"saved_{selected}_{kind}_{p_item['variable']}"
                        st.markdown(f"#### {variable_label(p_item['variable'])} ({p_item['unit']})")
                        if kind == 'Thống kê và xu hướng':
                            _show_statistics(p_item)
                            trend = p_item.get('trend_result')
                            if trend:
                                if trend.get('message'): st.info(trend['message'])
                                else: _show_trend(trend)
                        else:
                            render_func(p_item, chart_key=chart_key)
                        if p_idx < len(charts_list) - 1:
                            st.divider()
                elif kind == 'Thống kê và xu hướng':
                    _show_statistics(payload)
                    trend = payload.get('trend_result')
                    if trend:
                        if trend.get('message'): st.info(trend['message'])
                        else: _show_trend(trend)
                else:
                    {'Chuỗi thời gian': _show_timeseries, 'Bản đồ nhiệt': _show_heatmap, 'Không gian (điểm)': _show_map}[kind](payload)

        scroll_result = charts and st.session_state.pop('chart_scroll_result', None) == selected
        if action_scroll:
            _scroll_to('chart_result' if charts else 'stored_data_result')
        elif scroll_result or (charts and opening) or (not charts and opening):
            scroll_target = 'chart_collection_detail' if charts else 'stored_collection_detail'
            _scroll_to(scroll_target)


def delete_confirmed(api, collection_id, prefix, detail_slot):
    try:
        result = api.delete_collection(collection_id)
    except DashboardApiError as exc:
        st.session_state[prefix+'_delete_error'] = str(exc)
        return
    selected = st.session_state.get(prefix+'_collection')
    st.session_state[prefix+'_delete_popover_'+collection_id] = False
    forget_deleted(collection_id, result['job_id'])
    if selected == collection_id:
        detail_slot.empty()


def set_library_page(prefix, page):
    st.session_state[prefix+'_page'] = page


@st.fragment
def render_library_list(api, prefix, detail_slot):
    if st.session_state.pop('missing_collection_notice', False):
        msg = 'Bộ dữ liệu đang xem không còn tồn tại. Đã trở về danh sách.'
        st.warning(msg)
        notify_warning(msg)
    if st.session_state.pop('collection_deleted_notice', False):
        msg = 'Đã xóa bộ dữ liệu và lịch sử tác vụ.'
        st.success(msg)
        notify_success(msg)
    error = st.session_state.pop(prefix+'_delete_error', None)
    if error:
        st.error(error)
        notify_error(f"Lỗi khi xóa: {error}")
    page=st.session_state.get(prefix+'_page',0)
    search_query = st.session_state.get(prefix+'_search','').strip()
    entries=cached_read(api, 'list_collections', search_query,51,page*50)['collections']
    has_next = len(entries) > 50
    entries = entries[:50]
    preferred=st.session_state.pop(prefix+'_preferred_collection_id',None)
    if preferred:
        target=cached_read(api, 'get_collection', preferred)
        if not any(e['id']==preferred for e in entries): entries.insert(0,target)
        st.session_state[prefix+'_collection']=preferred
    if not entries:
        if search_query:
            msg = f"Không tìm thấy bộ dữ liệu nào khớp với '{search_query}'."
            st.info(msg)
            notify_warning(msg, icon="🔍")
        else:
            notify_warning("Chưa có dữ liệu: Bạn chưa tải bộ dữ liệu nào. Vui lòng vào mục Thu thập để tải dữ liệu ERA5 trước!", icon="⚠️")
            render_empty_data_notice("dữ liệu" if prefix == "stored" else "biểu đồ")
        if page:
            st.button('Về trang đầu', on_click=set_library_page, args=(prefix, 0))
        return False
    selected_now=st.session_state.get(prefix+'_collection')
    if not st.session_state.get(prefix+'_details_visible', False):
        selected_now = None
    cards=st.expander('Chọn bộ dữ liệu khác · Mới nhất trước',expanded=not bool(selected_now), key=prefix+'_library_cards', on_change='rerun')
    with cards:
      for item in entries:
        meta,summary=item['metadata'],item['summary']
        with st.container(border=True):
            info=st.container()
            info.markdown(f"**{variable_label(meta['primary_variable'])} · {meta['place_name']}**")
            dates=[datetime.fromisoformat(meta[k]).strftime('%d/%m/%Y') for k in ['requested_start','requested_end']]
            source={'land':'ERA5-Land','era5':'ERA5','mock':'Demo · giả lập'}.get(meta['source'],meta['source'])
            badge=' · Vừa thu thập' if item['id']==st.session_state.get('latest_collection_id') else ''
            info.caption(f"{dates[0]} → {dates[1]} · {source}{badge}")
            status=STATUS.get(item['status'],item['status'])
            color='green' if item['status']=='complete' else 'orange' if item['status'] in {'partial','queued','running'} else 'red'
            info.badge(status,color=color)
            if summary:
                info.caption(f"Đã lưu {summary.get('available_count',0)}/{summary.get('expected_count','—')} giá trị chính")
            info.caption('Thu thập lúc: '+datetime.fromisoformat(item['created_at']).astimezone(timezone(timedelta(hours=7))).strftime('%d/%m/%Y %H:%M')+' (UTC+7)')
            with st.container(horizontal=True):
                if st.button('Xem dữ liệu',icon=':material/table_view:',key=prefix+'_view_'+item['id'],on_click=open_details,args=(item['id'],)):
                    st.rerun()
                if st.button('Vẽ biểu đồ',icon=':material/bar_chart:',key=prefix+'_draw_'+item['id'],on_click=open_chart,args=(item['id'],),disabled=not item['grids'],help='Bộ dữ liệu chưa có điểm lưới để vẽ.' if not item['grids'] else None):
                    st.rerun()
                with st.popover('Xóa', key=prefix+'_delete_popover_'+item['id'], icon=':material/delete:', on_change='rerun', disabled=item['status'] in {'queued', 'running', 'cancelling'}):
                    st.write('Xóa bộ dữ liệu này và lịch sử tác vụ. Thao tác không thể hoàn tác.')
                    st.caption(label(item))
                    st.caption('File nguồn và cache dùng chung được giữ lại.')
                    st.button('Xác nhận xóa', key=prefix+'_delete_confirm_'+item['id'], type='primary',
                              on_click=delete_confirmed, args=(api, item['id'], prefix, detail_slot))
    if page > 0 or has_next:
        with cards:
            with st.container(horizontal=True, vertical_alignment='center', gap='small'):
                st.button('Trang trước', disabled=page==0, key=prefix+'_prev', icon=':material/chevron_left:',
                          on_click=set_library_page, args=(prefix, page-1))
                st.caption(f'Trang {page+1}')
                st.button('Trang sau', disabled=not has_next, key=prefix+'_next', icon=':material/chevron_right:',
                          on_click=set_library_page, args=(prefix, page+1))
    return True
