"""Floating native Streamlit chat; model work runs outside the UI script thread."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from html import escape
from pathlib import Path
from time import monotonic
from uuid import uuid4

import streamlit as st

from frontend.api_client import DashboardApiError
from frontend.chat_history import sync_history
from frontend.chat_actions import applied_view,execute_action
from backend.app.domain.ui_actions import UIAction,VIEW_LABELS
from frontend.components.charts import variable_label
from frontend.components.weather_pet import PET_CSS, PET_THEMES, pet_mode, pet_art

SUGGESTIONS = ['Kho đang có những bộ dữ liệu nào?', 'Làm sao vẽ biểu đồ?', 'Ứng dụng hỗ trợ hỏi những gì?']
CSS_PATH = Path(__file__).with_name('components').joinpath('ai_chat.css')


@st.cache_data(max_entries=2,show_spinner=False)
def _styles(modified):
    return '<style>'+CSS_PATH.read_text(encoding='utf-8')+PET_CSS+'</style>'


@st.cache_resource
def _workers():
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix='era5-chat')


def _toggle():
    st.session_state['ai_chat_open'] = not st.session_state.get('ai_chat_open', False)
    if st.session_state['ai_chat_open']:
        st.session_state['ai_chat_unread'] = False


def _select(cid,name='Bộ đang xem'):
    st.session_state['ai_chat_selected'] = cid
    st.session_state['ai_chat_selected_name'] = name


def _choose_and_continue(api,cid,name,index):
    if st.session_state.get('ai_chat_pending'):
        return
    _select(cid,name)
    earlier=st.session_state['ai_chat_messages'][:index]
    previous=next((m['content'] for m in reversed(earlier) if m['role']=='user'),'Hãy cho biết bộ này có gì.')
    _submit(api,('Với bộ vừa chọn, '+previous)[:2000])


def _unselect():
    st.session_state.pop('ai_chat_selected',None)
    st.session_state.pop('ai_chat_selected_name',None)


def _submit_input(api):
    _submit(api,st.session_state.get('ai_chat_input',''))


def _clear():
    if st.session_state.get('ai_chat_pending'):
        return
    st.session_state['ai_chat_messages'] = []
    st.session_state['ai_chat_unread'] = False
    # Every explicit reset gets a new revision, even if an older empty save was
    # acknowledged while a more recent answer was still being saved in the browser.
    st.session_state['ai_chat_reset_token']=uuid4().hex
    st.session_state.pop('ai_chat_history_ack',None)
    st.session_state.pop('ai_chat_visible_limit',None)
    st.session_state.pop('ai_chat_history_blocked',None)
    st.session_state.pop('ai_chat_history_error',None)
    st.session_state.pop('ai_chat_action_error',None)
    _unselect()


def _chart(api,cid,index):
    execute_action(api,UIAction(type='open_chart',collection_id=cid).model_dump(mode='json'),index)


def _show_older():
    st.session_state['ai_chat_visible_limit']=st.session_state.get('ai_chat_visible_limit',40)+40


def _submit(api, text):
    if (not st.session_state.get('ai_chat_history_loaded') or not isinstance(text,str)
            or not text.strip() or len(text)>2000 or st.session_state.get('ai_chat_pending')):
        return
    messages = st.session_state['ai_chat_messages']
    history = [dict(role=m['role'],content=m['content'][:2000]) for m in messages[-8:]]
    context=applied_view(st.session_state,st.query_params)
    payload = dict(message=text.strip(),history=history,view_context=context,
                   selected_collection_id=st.session_state.get('ai_chat_selected') or (context['collection_id'] if context else None))
    messages.append(dict(role='user',content=text.strip()))
    # Preserve history until the user clears it; only the model context is bounded.
    st.session_state['ai_chat_messages'] = messages
    st.session_state['ai_chat_pending'] = (_workers().submit(api.agent_chat,payload),monotonic())


def _collect():
    pending = st.session_state.get('ai_chat_pending')
    if not pending or not pending[0].done():
        return
    st.session_state.pop('ai_chat_pending',None)
    try:
        result = pending[0].result()
    except DashboardApiError:
        result = dict(answer='Chưa kết nối được dịch vụ AI. Hãy kiểm tra backend/cấu hình và thử lại; câu hỏi chưa được tự gửi lại.',evidence=[],verified=False)
    except Exception:
        result = dict(answer='Lượt chat chưa hoàn tất. Bạn có thể thử lại; không có tác vụ thu thập nào được tạo.',evidence=[],verified=False)
    messages=st.session_state['ai_chat_messages']
    st.session_state['ai_chat_messages']=messages+[dict(role='assistant',content=result['answer'],result=result)]
    if not st.session_state.get('ai_chat_open'):
        st.session_state['ai_chat_unread'] = True


def _date_label(value):
    try:
        return datetime.fromisoformat(value).strftime('%d/%m/%Y %H:%M')
    except (ValueError,TypeError):
        return str(value or '')


def _evidence(api,result, index):
    evidence = result.get('evidence',[])
    # Search choices remain actionable even if a following clarification is needed.
    for n, step in enumerate(evidence):
        payload = step['result']
        if step['tool'] == 'search_collections':
            for row in payload.get('data') or []:
                with st.container(border=True):
                    st.text(row['name'])
                    st.caption(f"{row['source']} · {_date_label(row['requested_start'])} → {_date_label(row['requested_end'])}")
                    st.button('Chọn và tiếp tục',key=f'ai_pick_{index}_{n}_{row["collection_id"]}',
                              on_click=_choose_and_continue,args=(api,row['collection_id'],row['name'],index),
                              disabled=bool(st.session_state.get('ai_chat_pending')),help='Chọn bộ và gửi tiếp yêu cầu cho ARE')
                    with st.container(horizontal=True):
                        for target,label in [('open_data','Xem dữ liệu'),('open_chart','Biểu đồ')]:
                            action=UIAction(type=target,collection_id=row['collection_id']).model_dump(mode='json')
                            st.button(label,key=f'ai_search_{target}_{index}_{n}_{row["collection_id"]}',
                                      on_click=execute_action,args=(api,action,index),disabled=bool(st.session_state.get('ai_chat_pending')))
            if payload.get('has_more'):
                st.caption('Còn kết quả khác. Bạn có thể yêu cầu xem tiếp hoặc tìm cụ thể hơn.')
    if not evidence:
        return
    last = evidence[-1]['result']
    p = last.get('provenance') or {}
    if p:
        st.caption(f"{p.get('source')} · {p.get('timezone')}")
        if p.get('start'):
            st.caption(f"{_date_label(p['start'])} → {_date_label(p['end'])}")
    data = last.get('data')
    if evidence[-1]['tool']=='prepare_view' and last['status']=='needs_clarification' and isinstance(data,dict) and data.get('choices'):
        _view_form(api,data,index)
    if evidence[-1]['tool']=='answer_question' and isinstance(data,dict):
        references=[]
        for source in data.get('sources',[]):
            for reference in source.get('references',[]):
                label=f"{reference['path']}:{reference['line']}"
                if label not in references:
                    references.append(label)
        if references:
            with st.expander('Nguồn trong dự án'):
                for reference in references:
                    st.text(reference)
    if evidence[-1]['tool']=='describe_collection' and isinstance(data,dict):
        st.text(data['name'])
        st.caption(f"{data['source']} · {_date_label(data['requested_start'])} → {_date_label(data['requested_end'])}")
        st.caption('Biến: '+', '.join(variable_label(v) for v in data['variables']))
    if evidence[-1]['tool']=='read_collection_quality' and isinstance(data,dict):
        for issue in data.get('issues',[]):
            st.text(f"{variable_label(issue['variable']) if issue.get('variable') else 'Toàn bộ'}: {issue['message']}")
        if last.get('has_more'):
            st.caption('Còn cảnh báo khác. Bạn có thể yêu cầu xem tiếp.')
    if isinstance(data,dict) and data.get('periods'):
        st.dataframe(data['periods'],hide_index=True,width='stretch')
        if data.get('has_more'):
            st.caption('Còn kỳ tổng hợp khác. Bạn có thể yêu cầu xem tiếp.')
    metadata = last.get('metadata') or {}
    details = p or (data if evidence[-1]['tool']=='describe_collection' and isinstance(data,dict) else {})
    # Only user-facing facts. Tool names/arguments and raw JSON stay out of chat.
    if details.get('dataset') or metadata:
        with st.expander('Chi tiết dữ liệu'):
            if details.get('dataset'):
                st.text('Bộ nguồn: '+details['dataset'])
            grid=details.get('grid_location')
            if grid:
                st.text(f"Điểm lưới: {grid['latitude']:g}, {grid['longitude']:g}")
            if metadata.get('unit'):
                st.text('Đơn vị: '+metadata['unit'])
            if metadata.get('coverage') is not None:
                st.text(f"Độ phủ: {metadata['coverage']:.1%}")
            if metadata.get('expected_count') is not None:
                st.text(f"Giờ nguồn đã có: {metadata.get('available_count','—')} / {metadata['expected_count']}")
            quality=metadata.get('collection_quality') or {}
            if quality.get('issue_count') is not None:
                st.text(f"Cảnh báo chất lượng: {quality['issue_count']}")
            for gap in metadata.get('missing_ranges',[]):
                st.text(f"Thiếu {variable_label(gap.get('variable',''))}: {_date_label(gap.get('start'))} → {_date_label(gap.get('end'))} ({gap.get('count','—')} giờ)")
            if metadata.get('missing_ranges_truncated'):
                st.caption('Chưa hiển thị hết các khoảng thiếu. Bạn có thể thu hẹp khoảng ngày để kiểm tra.')
    cid = p.get('collection_id') or (data.get('collection_id') if isinstance(data,dict) else None)
    if cid and not result.get('actions'):
        st.button('Mở trong Biểu đồ',key=f'ai_chart_{index}',icon=':material/monitoring:',
                  on_click=_chart,args=(api,cid,index),type='tertiary')
    _actions(api,result,index)


def _actions(api,result,index):
    for n,raw in enumerate(result.get('actions',[])):
        try:
            action=UIAction.model_validate(raw)
        except ValueError:
            continue
        label={'find_data':'Mở kết quả trong Kho dữ liệu','open_data':'Xem dữ liệu','open_chart':'Mở trong Biểu đồ'}[action.type]
        if action.parameters and result.get('evidence') and result['evidence'][-1].get('tool')=='prepare_view':
            p=action.parameters
            st.caption(f"{variable_label(p.variable)} · {VIEW_LABELS[action.view_kind]} · "+
                       {'hourly':'Giờ','daily':'Ngày','monthly':'Tháng','yearly':'Năm'}[p.frequency])
        st.button(label,key=f'ai_action_{index}_{n}',on_click=execute_action,args=(api,action.model_dump(mode='json'),index),
                  disabled=bool(st.session_state.get('ai_chat_pending')),type='primary' if n==0 else 'secondary')
    error=st.session_state.get('ai_chat_action_error')
    if error and error[0]==index:
        st.error(error[1])


def _view_form(api,data,index):
    draft,choices=data['draft'],data['choices']
    local=timezone(timedelta(hours=7))
    lo,hi=(datetime.fromisoformat(choices[k]).astimezone(local) for k in ['requested_start','requested_end'])
    start=datetime.fromisoformat(draft['start']).astimezone(local) if draft.get('start') else None
    end=datetime.fromisoformat(draft['end']).astimezone(local) if draft.get('end') else None
    variables=choices['variables']
    selected=next((i for i,v in enumerate(variables) if variable_label(v)==variable_label(draft.get('variable') or '')),None)
    grids=choices['grids']
    grid=dict(latitude=draft.get('latitude'),longitude=draft.get('longitude'))
    with st.form(f'ai_view_form_{index}'):
        variable=st.selectbox('Biến',variables,index=selected,format_func=variable_label,key=f'ai_view_variable_{index}')
        grid_index=st.selectbox('Điểm lưới',range(len(grids)),index=grids.index(grid) if grid in grids else None,
            format_func=lambda i:f"{grids[i]['latitude']}, {grids[i]['longitude']}",key=f'ai_view_grid_{index}')
        left,right=st.columns(2)
        a=left.date_input('Từ ngày',value=start.date() if start and lo.date()<=start.date()<=hi.date() else None,
                          min_value=lo.date(),max_value=hi.date(),key=f'ai_view_start_{index}')
        b=right.date_input('Đến ngày',value=end.date() if end and lo.date()<=end.date()<=hi.date() else None,
                          min_value=lo.date(),max_value=hi.date(),key=f'ai_view_end_{index}')
        hour_a=left.time_input('Từ giờ',value=start.time() if start else datetime.min.time(),step=3600,key=f'ai_view_start_hour_{index}')
        hour_b=right.time_input('Đến giờ',value=end.time() if end else datetime.min.replace(hour=23).time(),step=3600,key=f'ai_view_end_hour_{index}')
        frequencies=(['monthly'] if draft['view_kind']=='heatmap' else ['hourly'] if draft['target']=='open_data' or draft['view_kind'] in {'map','statistics'} else ['hourly','daily','monthly','yearly'])
        frequency=st.selectbox('Chu kỳ',frequencies,index=frequencies.index(draft['frequency']) if draft.get('frequency') else None,
            format_func=lambda f:dict(hourly='Giờ',daily='Ngày',monthly='Tháng',yearly='Năm')[f],key=f'ai_view_frequency_{index}')
        if st.form_submit_button('Mở kết quả',key=f'ai_view_submit_{index}',type='primary'):
            if variable is None or grid_index is None or a is None or b is None or hour_a is None or hour_b is None or frequency is None:
                st.warning('Chọn đủ biến, điểm lưới, ngày giờ và chu kỳ.')
            else:
                action=dict(type=draft['target'],collection_id=data['collection_id'],view_kind=draft['view_kind'],
                    parameters=dict(variable=variable,**grids[grid_index],frequency=frequency,aggregation='auto',
                        start=datetime.combine(a,hour_a,local).isoformat(),end=datetime.combine(b,hour_b,local).isoformat()))
                execute_action(api,action,index)


@st.fragment(key='are_chat')
def render_ai_chat(api):
    st.session_state.setdefault('ai_chat_open',False)
    st.session_state.setdefault('ai_chat_messages',[])
    _collect()
    sync_history(api)
    pending = st.session_state.get('ai_chat_pending')
    messages = st.session_state['ai_chat_messages']
    last_result=next((m.get('result') for m in reversed(messages) if m.get('result')),None)
    mode=pet_mode(st.session_state.get('ai_pet_theme','auto'),pending=bool(pending),last_result=last_result)
    st.html(_styles(CSS_PATH.stat().st_mtime_ns))
    with st.container(key='ai_chat_launcher'):
        st.html('<div class="pet-launch-art">'+pet_art(mode,unread=st.session_state.get('ai_chat_unread',False))+
                '<span class="pet-launch-caption">ARE</span></div>')
        st.button('AI chat',key='ai_chat_toggle',type='tertiary',
                  on_click=_toggle,help='ARE có câu trả lời mới' if st.session_state.get('ai_chat_unread') else 'Trò chuyện với ARE')
    if not st.session_state['ai_chat_open']:
        return
    with st.container(key='ai_chat_shell'):
        with st.container(horizontal=True,vertical_alignment='center',wrap=False,key='ai_chat_header'):
            st.html('<div class="pet-heading"><div class="pet-heading-art">'+pet_art(mode,small=True)+
                    '</div><div><strong>ARE</strong></div></div>')
            with st.popover('Tùy chọn',icon=':material/more_horiz:',type='tertiary',key='ai_chat_preferences',help='Đổi diện mạo và trò chuyện mới'):
                with st.container(width=260):
                    st.caption('Diện mạo')
                    st.pills('Diện mạo',list(PET_THEMES),format_func=PET_THEMES.get,default=None if 'ai_pet_theme' in st.session_state else 'auto',
                             required=True,key='ai_pet_theme',label_visibility='collapsed',persist_state='session')
                    st.button('Làm mới',key='ai_chat_clear',on_click=_clear,disabled=bool(pending) or not st.session_state.get('ai_chat_history_loaded'),icon=':material/refresh:',width='stretch',help='Xóa lịch sử và bắt đầu cuộc trò chuyện mới')
            st.button('Thu gọn',icon=':material/close:',key='ai_chat_close',on_click=_toggle,type='tertiary',help='Thu gọn, giữ cuộc trò chuyện')
        selected = st.session_state.get('ai_chat_selected')
        if selected:
            with st.container(horizontal=True,vertical_alignment='center',wrap=False,key='ai_chat_context'):
                name=escape(st.session_state.get('ai_chat_selected_name','Bộ đã chọn'))
                st.html(f'<div class="chat-context-label" title="{name}">Đang hỏi · {name}</div>')
                st.button('Bỏ chọn',key='ai_chat_unselect',icon=':material/close:',type='tertiary',disabled=bool(pending),on_click=_unselect,help='Bỏ chọn bộ dữ liệu')
        else:
            context=applied_view(st.session_state,st.query_params)
            if context:
                params=context['parameters']
                text='Đang xem · '+(variable_label(params['variable']) if params else 'Thông tin bộ dữ liệu')
                with st.container(key='ai_chat_context'):
                    st.html('<div class="chat-context-label">'+escape(text)+'</div>')
        with st.container(height=320,key='ai_chat_messages',autoscroll=True,border=False):
            if not messages:
                st.html('<div class="pet-welcome"><div class="pet-welcome-art">'+pet_art('cloud',small=True)+
                        '</div><strong>Chào bạn, mình là ARE!</strong></div>')
                labels=['Tìm dữ liệu đã lưu','Hướng dẫn vẽ biểu đồ','ARE có thể giúp gì?']
                for n, suggestion in enumerate(SUGGESTIONS):
                    st.button(labels[n],key=f'ai_suggestion_{n}',disabled=bool(pending),
                              on_click=_submit,args=(api,suggestion),width='stretch',type='tertiary')
            start=max(0,len(messages)-st.session_state.get('ai_chat_visible_limit',40))
            if start:
                st.button('Xem tin trước',key='ai_chat_older',on_click=_show_older,width='stretch',type='tertiary')
            for i in range(start,len(messages)):
                message=messages[i]
                with st.chat_message(message['role'],avatar=':material/cloud:' if message['role']=='assistant' else ':material/person:'):
                    if (message.get('result') or {}).get('answer_kind') in {'general','project'}:
                        # Formatting for explanations/code; no HTML or remote image embeds.
                        st.markdown(message['content'].replace('![','['),unsafe_allow_html=False)
                    else:
                        st.text(message['content'])
                    if message.get('result'):
                        if message.get('restored'):
                            try:
                                _evidence(api,message['result'],i)
                            except (KeyError,ValueError,TypeError,AttributeError,OverflowError):
                                st.caption('Chi tiết cũ không đọc được; nội dung trả lời vẫn được giữ.')
                        else:
                            _evidence(api,message['result'],i)
            if pending:
                st.html(f'<div class="chat-wait" role="status"><span class="chat-wait-dots"><i></i><i></i><i></i></span>Đang kiểm tra dữ liệu… {int(monotonic()-pending[1])}s</div>')
        st.chat_input('Hỏi ARE…',key='ai_chat_input',max_chars=2000,
                      disabled=bool(pending) or not st.session_state.get('ai_chat_history_loaded'),submit_mode='disable',on_submit=_submit_input,args=(api,))
        if not st.session_state.get('ai_chat_history_loaded'):
            st.caption('Đang khôi phục lịch sử…')
        if st.session_state.get('ai_chat_history_error'):
            st.warning(st.session_state['ai_chat_history_error'])
