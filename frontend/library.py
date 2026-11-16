"""A library of named collection snapshots, not a merged cache inventory."""
from datetime import datetime, timezone, timedelta
import json
from uuid import uuid4
from contextlib import nullcontext
import pandas as pd
import streamlit as st
from frontend.components.charts import variable_label
from frontend.api_client import DashboardApiError

STATUS = dict(queued='Đang chờ', running='Đang thu thập', complete='Đủ dữ liệu',
              partial='Thiếu một phần', failed='Thất bại', interrupted='Bị gián đoạn')


@st.cache_data(ttl=60, max_entries=128, show_spinner=False)
def _shared_read(base_url, source, stored_only, method, args_json, _api):
    """Cache local API reads across browser reloads, independently of sessions."""
    return getattr(_api, method)(*json.loads(args_json))


def cached_read(api, method, *args):
    return _shared_read(api.base_url, api.source, api.stored_only, method,
                        json.dumps(args, sort_keys=True), _api=api)


def label(entry):
    m=entry['metadata']
    a,b=(datetime.fromisoformat(m[k]).strftime('%d/%m/%Y') for k in ['requested_start','requested_end'])
    return f"{variable_label(m['primary_variable'])} — {m['place_name']} — {a} đến {b} · {m['source']} · {entry['id'][:8]}"


def open_chart(collection_id, variable=None, grid=None, interval=None):
    st.session_state['selected_collection_id']=collection_id
    st.session_state['preferred_collection_id']=collection_id
    st.session_state['workflow_section']='Biểu đồ'
    st.session_state['chart_open_requested']=collection_id
    if variable:
        st.session_state['chart_variable_'+collection_id]=variable
    if grid is not None:
        st.session_state['chart_grid_'+collection_id]=grid
    if interval:
        for name,value in zip(['start','end'],interval):
            st.session_state['chart_range_'+name+'_'+collection_id]=datetime.fromisoformat(value).date()


def open_details(collection_id):
    st.session_state['selected_collection_id']=collection_id
    st.session_state['preferred_collection_id']=collection_id
    st.session_state['workflow_section']='Dữ liệu đã lưu'
    st.session_state['stored_open_requested']=collection_id


def clear_search(prefix):
    st.session_state[prefix+'_search']=''
    st.session_state[prefix+'_library_text']=''
    st.session_state[prefix+'_page']=0


def forget_deleted(collection_id, job_id):
    _shared_read.clear()
    for key in list(st.session_state):
        value = st.session_state[key]
        if (key in {'selected_collection_id', 'preferred_collection_id', 'latest_collection_id',
                    'stored_collection', 'chart_collection', 'stored_open_requested', 'chart_open_requested'}
                and value == collection_id):
            del st.session_state[key]
        elif key in {'stored_result', 'chart_result', 'stored_quality_result', 'chart_quality_result'} and value and value[0] == collection_id:
            del st.session_state[key]
    st.session_state.pop('workflow_job_list', None)
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
    with st.container(horizontal=True, vertical_alignment='center', gap='small', wrap=False):
        st.header('Biểu đồ' if charts else 'Dữ liệu đã lưu', anchor=False)
        if st.button('Làm mới',key=prefix+'_refresh_library',icon=':material/refresh:',width='content'):
            st.session_state.pop(prefix+'_result',None)
            _shared_read.clear()
    with st.form(prefix+'_library_search',border=False):
        field,action=st.columns([4,1],vertical_alignment='bottom',wrap=False)
        text=field.text_input('Tìm bộ dữ liệu đã lưu',placeholder='Ví dụ: Hà Nội, nhiệt độ, 2025-01',key=prefix+'_library_text')
        find=action.form_submit_button('Tìm dữ liệu',key=prefix+'_find',width='stretch')
    if find:
        st.session_state[prefix+'_search']=text.strip()
        st.session_state[prefix+'_page']=0
    if st.session_state.get(prefix+'_search'):
        st.button('Xóa tìm kiếm',key=prefix+'_clear_search',on_click=clear_search,args=(prefix,))
    saved_view = st.query_params.get(prefix+'_applied')
    if prefix+'_result' not in st.session_state and saved_view:
        try:
            saved = json.loads(saved_view)
            cid, saved_kind, saved_params, operation = saved
            if not isinstance(cid, str) or not isinstance(saved_params, dict):
                raise ValueError('Invalid saved view')
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
                st.session_state[prefix+'_range_'+edge+'_'+cid] = datetime.fromisoformat(saved_params[edge]).date()
            if charts:
                st.session_state['saved_chart_frequency'] = saved_params['frequency']
                st.session_state['saved_chart_kind'] = saved_kind
        except (ValueError, TypeError, KeyError):
            del st.query_params[prefix+'_applied']
    list_slot = st.container()
    detail_slot = st.empty()
    with list_slot:
        has_entries = render_library_list(api, prefix, detail_slot)
    if not has_entries:
        return
    with detail_slot.container():
        selected=st.session_state.get('selected_collection_id') or st.session_state.get(prefix+'_collection')
        if not selected:
            return
        st.session_state[prefix+'_collection']=selected
        entry=cached_read(api, 'get_collection', selected)
        st.session_state['selected_collection_id']=selected
        m,s=entry['metadata'],entry['summary']
        chart_output = st.container(key='chart_result') if charts else None
        st.divider()
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
            if entry['status'] in {'queued','running'}:
                st.info('Tác vụ chưa kết thúc. Nếu backend bị dừng giữa chừng, hãy kiểm tra tác vụ trước khi tạo lại; trạng thái này không chứng minh bộ dữ liệu đã hoàn tất.')
            if entry['status']=='partial': st.warning('Bộ dữ liệu chưa đủ. Giá trị thiếu không được thay bằng 0.')
            if s.get('grid_coordinates_verified') is False:
                st.warning('File nguồn thiếu tọa độ lưới. Điểm lưu là điểm thay thế do parser ghi nhận, chưa xác minh là điểm lưới thực.')
            if entry.get('error'): st.error(entry['error'])
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
            st.info('Bộ dữ liệu chưa có điểm lưới hợp lệ để xem hoặc vẽ.')
            return
        variables=[m['primary_variable']]+[v['variable'] for v in entry['variables'] if v['variable']!=m['primary_variable']]
        st.caption('Chọn biến, điểm lưới và khoảng ngày, rồi bấm nút bên dưới để cập nhật kết quả. Thay đổi trong form chưa được áp dụng.')
        with st.form(prefix+'_selected_form'):
            variable=st.selectbox('Biến trong bộ dữ liệu',variables,format_func=variable_label,key=prefix+'_variable_'+selected)
            grids=entry['grids']
            idx=st.selectbox('Điểm lưới thực',range(len(grids)),format_func=lambda i:f"{grids[i]['latitude']}, {grids[i]['longitude']}",key=prefix+'_grid_'+selected)
            local=timezone(timedelta(hours=7))
            lo,hi=(datetime.fromisoformat(m[k]).astimezone(local) for k in ['requested_start','requested_end'])
            with st.expander('Khoảng xem trong bộ dữ liệu'):
                st.caption('Chỉ chọn các ngày thuộc bộ này. Với bộ dài, mặc định xem 30 ngày đầu để giữ truy vấn gọn.')
                one,two=st.columns(2)
                first=one.date_input('Từ ngày (UTC+7)',value=lo.date(),min_value=lo.date(),max_value=hi.date(),key=prefix+'_range_start_'+selected)
                default_end=min(hi.date(),(lo+timedelta(days=29)).date())
                last=two.date_input('Đến ngày (UTC+7)',value=default_end,min_value=lo.date(),max_value=hi.date(),key=prefix+'_range_end_'+selected)
            frequency,kind='hourly','Chuỗi thời gian'
            if charts:
                one,two=st.columns(2)
                frequency=one.selectbox('Chu kỳ',['hourly','daily','monthly','yearly'],format_func=lambda v:dict(hourly='Giờ',daily='Ngày',monthly='Tháng',yearly='Năm')[v],key='saved_chart_frequency')
                kind=two.selectbox('Loại biểu đồ',['Chuỗi thời gian','Bản đồ nhiệt','Không gian (điểm)','Thống kê và xu hướng'],key='saved_chart_kind')
                st.caption('Chu kỳ áp dụng cho chuỗi thời gian. Bản đồ nhiệt tổng hợp theo tháng; thống kê và xu hướng dùng toàn bộ khoảng đang xem.')
            submit=st.form_submit_button('Vẽ dữ liệu đã chọn' if charts else 'Áp dụng bộ lọc',key=prefix+'_selected_submit',type='primary')
        opening=st.session_state.pop(prefix+'_open_requested',None)==selected
        previous=st.session_state.get(prefix+'_result')
        if submit or opening or not previous or previous[0]!=selected:
            if first>last:
                st.error('Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
                return
            a=max(lo,datetime.combine(first,datetime.min.time(),local))
            b=min(hi,datetime.combine(last,datetime.min.time(),local)+timedelta(hours=23))
            params=dict(variable=variable,**grids[idx],frequency=frequency,aggregation='auto',start=a.isoformat(),end=b.isoformat())
            operation=('timeseries' if frequency=='hourly' else 'aggregate') if kind=='Chuỗi thời gian' else {'Bản đồ nhiệt':'heatmap','Không gian (điểm)':'map','Thống kê và xu hướng':'statistics'}[kind]
            payload=cached_read(api, 'query_collection', selected,operation,params)
            if kind=='Thống kê và xu hướng' and variable!='wind_direction':
                payload['trend_result']=cached_read(api, 'query_collection', selected,'trend',params)
            st.session_state[prefix+'_result']=(selected,kind,payload)
            st.query_params[prefix+'_applied'] = json.dumps([selected, kind, params, operation], ensure_ascii=False)
        result=st.session_state.get(prefix+'_result')
        if not result or result[0]!=selected: return
        _,kind,payload=result
        with chart_output if charts else st.container():
            grid=payload['grid_location']
            st.caption(f"Kết quả đã áp dụng: {variable_label(payload['variable'])} · {payload['unit']} · UTC+7 · điểm {grid['latitude']:.4f}, {grid['longitude']:.4f}")
            st.caption(payload['requested_start']+' → '+payload['requested_end']+' (UTC+7)')
            if not payload['complete']:
                st.warning(f"Có {payload['available_count']}/{payload['expected_count']} giờ. Khoảng thiếu được giữ trống.")
                with st.expander('Khoảng giờ thiếu'): st.json(payload['missing_ranges'])
            if not charts:
                frame=pd.DataFrame([{'Thời gian (UTC+7)':datetime.fromisoformat(p['time']).strftime('%d/%m/%Y %H:%M'),f"{variable_label(payload['variable'])} ({payload['unit']})":p['value']} for p in payload['data']])
                export=pd.DataFrame(payload['data'])
                for k in ['collection_id','place_name','source','dataset','variable','unit']: export[k]=payload[k]
                for k,v in payload['grid_location'].items(): export['grid_'+k]=v
                selected_grid=entry['grids'].index(payload['grid_location'])
                with st.container(horizontal=True):
                    st.button('Vẽ biểu đồ từ dữ liệu này',type='primary',icon=':material/bar_chart:',on_click=open_chart,args=(selected,payload['variable'],selected_grid,
                              (payload['requested_start'],payload['requested_end'])))
                    st.download_button('Tải CSV',export.to_csv(index=False).encode('utf-8-sig'),file_name=f'collection_{selected}.csv',mime='text/csv',key='download_timeseries_csv',on_click='ignore',icon=':material/download:')
                st.dataframe(frame,hide_index=True,width='stretch')
            elif kind=='Thống kê và xu hướng':
                _show_statistics(payload)
                trend=payload.get('trend_result')
                if trend:
                    if trend.get('message'): st.info(trend['message'])
                    else: _show_trend(trend)
            else:
                {'Chuỗi thời gian':_show_timeseries,'Bản đồ nhiệt':_show_heatmap,'Không gian (điểm)':_show_map}[kind](payload)

        if charts and (submit or opening):
            # A unique script runs for every explicit draw, including identical queries.
            st.html(f"""
                <style>.st-key-chart_result {{scroll-margin-top:5rem;}}</style>
                <script data-draw="{uuid4().hex}">
                requestAnimationFrame(() => requestAnimationFrame(() => {{
                    document.querySelector('.st-key-chart_result')?.scrollIntoView(
                        {{behavior: 'instant', block: 'start'}}
                    );
                }}));
                </script>
            """, unsafe_allow_javascript=True)


def delete_confirmed(api, collection_id, prefix, detail_slot):
    try:
        result = api.delete_collection(collection_id)
    except DashboardApiError as exc:
        st.session_state[prefix+'_delete_error'] = str(exc)
        return
    selected = st.session_state.get('selected_collection_id') or st.session_state.get(prefix+'_collection')
    st.session_state[prefix+'_delete_popover_'+collection_id] = False
    forget_deleted(collection_id, result['job_id'])
    if selected == collection_id:
        detail_slot.empty()


def set_library_page(prefix, page):
    st.session_state[prefix+'_page'] = page


@st.fragment
def render_library_list(api, prefix, detail_slot):
    if st.session_state.pop('collection_deleted_notice', False):
        st.success('Đã xóa bộ dữ liệu và lịch sử tác vụ.')
    error = st.session_state.pop(prefix+'_delete_error', None)
    if error:
        st.error(error)
    page=st.session_state.get(prefix+'_page',0)
    with st.spinner('Đang đọc thư viện dữ liệu…'):
        entries=cached_read(api, 'list_collections', st.session_state.get(prefix+'_search',''),51,page*50)['collections']
    has_next = len(entries) > 50
    entries = entries[:50]
    preferred=st.session_state.pop('preferred_collection_id',None)
    if preferred:
        target=cached_read(api, 'get_collection', preferred)
        if not any(e['id']==preferred for e in entries): entries.insert(0,target)
        st.session_state[prefix+'_collection']=preferred
    if not entries:
        st.info('Chưa có bộ dữ liệu phù hợp. Bộ dữ liệu mới sẽ xuất hiện ở đây sau khi bạn bấm Bắt đầu thu thập.')
        if page:
            st.button('Về trang đầu', on_click=set_library_page, args=(prefix, 0))
        return
    selected_now=st.session_state.get(prefix+'_collection')
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
                with st.popover('Xóa', key=prefix+'_delete_popover_'+item['id'], icon=':material/delete:', on_change='rerun', disabled=item['status'] in {'queued', 'running'}):
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
