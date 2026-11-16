"""Browse real stored series, then inspect or chart a selected series."""
from datetime import datetime, timedelta
import streamlit as st
from frontend.components.charts import variable_label


def identity(e):
    return '|'.join(str(e[k]) for k in ['source','variable','grid_latitude','grid_longitude','start','end'])


def load_library(client):
    entries = []
    for source in ['land','era5','mock']:
        offset = 0
        while True:
            page = client(source).get_catalog(limit=500, offset=offset)['entries']
            entries.extend(dict(source=source, **e) for e in page)
            if len(page) < 500:
                break
            offset += 500
    return entries


def select_saved(client, prefix):
    if st.button('Làm mới thư viện', key=prefix+'_refresh_library') or 'saved_library' not in st.session_state:
        st.session_state['saved_library'] = load_library(client)
    entries = st.session_state['saved_library']
    st.caption('Mỗi mục là một biến tại một điểm lưới trong database. Không phải một file hay một lần tải riêng.')
    with st.form(prefix+'_library_search', border=False):
        text = st.text_input('Tìm trong dữ liệu đã lưu', placeholder='Tên biến, nguồn, tọa độ hoặc ngày', key=prefix+'_library_text')
        find = st.form_submit_button('Tìm dữ liệu')
    if find:
        st.session_state[prefix+'_library_search_text'] = text.casefold().strip()
    search = st.session_state.get(prefix+'_library_search_text','')
    def label(e):
        unit = '°C' if e['unit']=='K' else e['unit']
        a,b = (datetime.fromisoformat(e[k]).strftime('%d/%m/%Y %H:%M') for k in ['start','end'])
        return f"{variable_label(e['variable'])} · {unit} · {e['source']} · ({e['grid_latitude']}, {e['grid_longitude']}) · {a} – {b}"
    visible = [e for e in entries if not search or search in (label(e)+' '+e['dataset']).casefold()]
    if not visible:
        st.info('Không có dữ liệu phù hợp trong database. Hãy đổi từ khóa hoặc sang Thu thập.')
        return None
    choices = {identity(e):e for e in visible}
    preferred = st.session_state.pop('preferred_saved_filter', None)
    if preferred:
        aliases={'temperature':'2m_temperature','dewpoint':'2m_dewpoint_temperature',
                 'precipitation':'total_precipitation','solar_radiation':'surface_solar_radiation_downwards',
                 'wind_u':'10m_u_component_of_wind','wind_v':'10m_v_component_of_wind'}
        variable=aliases.get(preferred['variable'],preferred['variable'])
        candidates=[e for e in visible if e['source']==preferred['source'] and e['variable']==variable]
        if candidates:
            nearest=min(candidates,key=lambda e:(e['grid_latitude']-preferred['latitude'])**2+(e['grid_longitude']-preferred['longitude'])**2)
            st.session_state['selected_saved_series']=identity(nearest)
            st.session_state['library_screen']=None
            st.session_state['preferred_saved_range']=(identity(nearest),preferred['start'],preferred['end'])
    selected = st.session_state.get('selected_saved_series')
    # Cross-screen selection survives Streamlit widget cleanup.
    key = prefix+'_saved_series'
    if selected in choices and st.session_state.get('library_screen') != prefix:
        st.session_state[key] = selected
    if st.session_state.get(key) not in choices:
        st.session_state[key] = next(iter(choices))
    chosen = st.selectbox('Chọn dữ liệu', list(choices), format_func=lambda k:label(choices[k]), key=key)
    st.session_state['selected_saved_series'] = chosen
    st.session_state['library_screen'] = prefix
    entry = choices[chosen]
    st.write(f"**{variable_label(entry['variable'])}** · Điểm lưới {entry['grid_latitude']}, {entry['grid_longitude']}")
    a,b,c = st.columns(3)
    a.metric('Quan sát đã lưu', f"{entry['count']:,}")
    b.metric('Giờ trống giữa đầu–cuối', f"{max(0,entry['expected_count']-entry['count']):,}")
    c.metric('Độ phủ đầu–cuối', f"{entry['coverage']:.1%}")
    if entry['coverage'] < 1:
        st.warning('Có khoảng trống giữa thời điểm đầu và cuối. Khoảng này không phải dữ liệu liên tục.')
    st.caption(f"Dataset: {entry['dataset']} · Thời gian UTC+7 · Đơn vị xem: {'°C' if entry['unit']=='K' else entry['unit']}")
    return entry


def selected_params(entry):
    return dict(variable=entry['variable'],latitude=entry['grid_latitude'],longitude=entry['grid_longitude'],
        start=entry['start'],end=entry['end'],frequency='hourly',aggregation='auto')


def open_chart():
    st.session_state['workflow_section'] = 'Biểu đồ'
    st.session_state['chart_legacy'] = True
    st.session_state['chart_legacy_empty'] = True


def render_saved(client, charts=False):
    from frontend.dashboard import _show_timeseries, _show_heatmap, _show_map, _show_statistics, _show_trend
    from frontend.workflow import show_metadata
    prefix = 'legacy_chart' if charts else 'legacy_stored'
    st.header('Biểu đồ' if charts else 'Dữ liệu đã lưu', anchor=False)
    entry = select_saved(client, prefix)
    if entry is None:
        return
    params = selected_params(entry)
    with st.form(prefix+'_selected_form'):
        lo,hi = datetime.fromisoformat(entry['start']),datetime.fromisoformat(entry['end'])
        # Exact times avoid silently expanding partial UTC days into whole Vietnam days.
        default_start = max(lo,hi-timedelta(days=30))
        default_end = hi
        preferred_range=st.session_state.get('preferred_saved_range')
        if preferred_range and preferred_range[0]==identity(entry):
            default_start=max(lo,datetime.fromisoformat(preferred_range[1]))
            default_end=min(hi,datetime.fromisoformat(preferred_range[2]))
        st.caption('Chọn khoảng xem trong dữ liệu này. Mặc định tối đa 30 ngày gần nhất; nhập giờ theo UTC+7.')
        a,b = st.columns(2)
        start = a.text_input('Từ (UTC+7)', value=default_start.strftime('%Y-%m-%d %H:%M'), key=prefix+'_range_start_'+identity(entry))
        end = b.text_input('Đến (UTC+7)', value=default_end.strftime('%Y-%m-%d %H:%M'), key=prefix+'_range_end_'+identity(entry))
        kind,frequency,aggregation = 'Chuỗi thời gian','hourly','auto'
        if charts:
            a,b,c=st.columns(3)
            frequency = a.selectbox('Chu kỳ',['hourly','daily','monthly','yearly'],
                format_func=lambda v:dict(hourly='Giờ',daily='Ngày',monthly='Tháng',yearly='Năm')[v],key=prefix+'_frequency')
            kind=b.selectbox('Loại biểu đồ',['Chuỗi thời gian','Bản đồ nhiệt','Không gian (điểm)','Thống kê và xu hướng'])
            aggregation=c.selectbox('Tổng hợp',['auto','mean','min','max','sum'])
        submit=st.form_submit_button('Vẽ dữ liệu đã chọn' if charts else 'Xem chi tiết',type='primary',key=prefix+'_selected_submit')
    if submit:
        try:
            tz=lo.tzinfo
            first=datetime.strptime(start,'%Y-%m-%d %H:%M').replace(tzinfo=tz)
            last=datetime.strptime(end,'%Y-%m-%d %H:%M').replace(tzinfo=tz)
            if first<lo or last>hi or first>last:
                raise ValueError()
        except ValueError:
            st.error('Nhập đúng YYYY-MM-DD HH:MM trong khoảng dữ liệu đã chọn.')
            return
        params.update(start=first.isoformat(),end=last.isoformat(),frequency=frequency,aggregation=aggregation)
        api=client(entry['source'])
        if not charts or kind=='Chuỗi thời gian':
            payload=api.get_timeseries(params) if frequency=='hourly' else api.get_aggregate(params)
        elif kind=='Bản đồ nhiệt': payload=api.get_heatmap(params)
        elif kind=='Không gian (điểm)': payload=api.get_map(params)
        else:
            payload=api.get_statistics(params)
            if entry['variable']!='wind_direction': payload['trend_result']=api.get_trend(params)
        st.session_state[prefix+'_library_result']=(identity(entry),kind,payload)
    result=st.session_state.get(prefix+'_library_result')
    if result and result[0]==identity(entry):
        _,kind,payload=result
        show_metadata(payload,prefix)
        if not charts:
            _show_timeseries(payload,show_chart=False)
            st.session_state['preferred_saved_range']=(identity(entry),payload['data'][0]['time'],payload['data'][-1]['time']) if payload.get('data') else None
            st.button('Vẽ biểu đồ từ dữ liệu này',on_click=open_chart)
        elif kind=='Thống kê và xu hướng':
            _show_statistics(payload)
            trend=payload.get('trend_result')
            if trend:
                if trend.get('message'): st.info(trend['message'])
                else: _show_trend(trend)
        else:
            {'Chuỗi thời gian':_show_timeseries,'Bản đồ nhiệt':_show_heatmap,'Không gian (điểm)':_show_map}[kind](payload)
