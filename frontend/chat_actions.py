"""Validated navigation. Fetch first, then atomically replace the target view."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json

import streamlit as st

from backend.app.domain.ui_actions import AppliedView, ComparisonView, UIAction, VIEW_LABELS, operation_for
from frontend.api_client import DashboardApiError


def applied_comparison(state):
    """Return validated active comparison metadata when the Charts comparison panel is open."""
    if state.get('workflow_section') != 'Biểu đồ' or state.get('chart_details_visible'):
        return None
    if not state.get('chart_compare_enabled'):
        return None
    saved = state.get('chart_compare_context')
    if not isinstance(saved, dict) or not isinstance(saved.get('context'), dict):
        return None
    try:
        validated = ComparisonView.model_validate(saved['context']).model_dump(mode='json')
    except ValueError:
        return None
    return {**saved, 'context': validated}


def applied_view(state,query):
    page={'Dữ liệu đã lưu':'stored','Biểu đồ':'chart'}.get(state.get('workflow_section'))
    if not page or not state.get(page+'_details_visible'):
        return None
    cid=state.get(page+'_collection')
    if not cid:
        return None
    context=dict(page=page,collection_id=cid,view_kind='timeseries',parameters=None)
    result=state.get(page+'_result')
    raw=query.get(page+'_applied')
    if result and result[0]==cid and raw:
        try:
            from frontend.library import decode_saved_view
            saved_id,label,params,_=decode_saved_view(raw)
            if saved_id==cid:
                from backend.app.domain.monthly_climate import MONTHLY_CLIMATE_LABEL
                if page == 'chart' and label == MONTHLY_CLIMATE_LABEL and result[1] == label:
                    params = {**params, 'variable': result[2]['variable'], 'frequency': 'monthly'}
                    context.update(view_kind='monthly_climate', parameters=params)
                else:
                    context.update(view_kind=next(k for k,v in VIEW_LABELS.items() if v==label),parameters=params)
        except (ValueError,KeyError,TypeError,StopIteration):
            pass
    try:
        return AppliedView.model_validate(context).model_dump(mode='json')
    except ValueError:
        # An old URL can contain filters unsupported by the action contract.
        return AppliedView(page=page,collection_id=cid).model_dump(mode='json')


def execute_action(api,raw,index,keep_chat=False):
    try:
        action=UIAction.model_validate(raw)
        api=replace(api,timeout_seconds=min(api.timeout_seconds,15))
        resolved=api.resolve_agent_action(action.model_dump(mode='json'))
        if resolved.get('status')!='ok':
            raise DashboardApiError(resolved.get('message') or 'Tham số chưa đủ hoặc không còn phù hợp. Hãy chọn lại bộ, biến, lưới và thời gian.')
        action=UIAction.model_validate(resolved['action'])
        prefix='chart' if action.type=='open_chart' else 'stored'
        payload=None
        entry=None
        operation=None
        params=action.parameters.model_dump() if action.parameters else None
        if action.type!='find_data':
            entry=api.get_collection(action.collection_id)
            if params:
                grid_index=entry['grids'].index(dict(latitude=params['latitude'],longitude=params['longitude']))
                operation=operation_for(action)
                payload=api.query_collection(action.collection_id,operation,params)
                if action.type=='open_chart' and action.view_kind=='statistics' and params['variable']!='wind_direction':
                    payload['trend_result']=api.query_collection(action.collection_id,'trend',params)
    except (ValueError,KeyError,TypeError,DashboardApiError) as exc:
        message=str(exc) if isinstance(exc,DashboardApiError) else 'Hành động không hợp lệ hoặc bộ dữ liệu đã thay đổi. Hãy tìm/chọn lại.'
        st.session_state['ai_chat_action_error']=(index,message)
        return
    # Everything above is read-only; no navigation or applied filter mutation on failure.
    from frontend.library import invalidate_reads
    invalidate_reads()
    state=st.session_state
    state.pop('ai_chat_action_error',None)
    state['workflow_section']='Biểu đồ' if prefix=='chart' else 'Dữ liệu đã lưu'
    if action.type=='find_data':
        state['stored_search']=action.search.strip()
        state['stored_library_text']=action.search.strip()
        state['stored_page']=0
        state['stored_details_visible']=False
        state['stored_scroll_to_list']=True
        st.query_params['stored_mode']='list'
    else:
        cid=action.collection_id
        state[prefix+'_collection']=cid
        state[prefix+'_preferred_collection_id']=cid
        state[prefix+'_details_visible']=True
        state[prefix+'_action_open']=cid
        state[prefix+'_action_scroll']=cid
        state[prefix+'_restore_filters']=False
        state.pop(prefix+'_open_requested',None)
        st.query_params[prefix+'_mode']='details'
        if params:
            state.pop(prefix+'_defer_query',None)
            state[prefix+'_variable_'+cid]=params['variable']
            state[prefix+'_variables_'+cid]=[params['variable']]
            state[prefix+'_grid_'+cid]=grid_index
            local=timezone(timedelta(hours=7))
            for edge in ['start','end']:
                stamp=datetime.fromisoformat(params[edge]).astimezone(local)
                state[prefix+'_range_'+edge+'_'+cid]=stamp.date()
                state[prefix+'_hour_'+edge+'_'+cid]=stamp.time().replace(tzinfo=None)
            label=VIEW_LABELS[action.view_kind]
            state[prefix+'_result']=(cid,label,payload)
            st.query_params[prefix+'_applied']=json.dumps([cid,label,params,operation],ensure_ascii=False)
            if prefix=='chart':
                state['saved_chart_kind']=label
                state['saved_chart_frequency']=params['frequency']
                state['chart_filters_'+cid]=False
        else:
            state[prefix+'_defer_query']=cid
            if prefix=='chart':
                state['chart_filters_'+cid]=True
            previous=state.get(prefix+'_result')
            if prefix+'_applied' in st.query_params and not (previous and previous[0]==cid):
                del st.query_params[prefix+'_applied']
        # The visible view now supplies context. Don't leave an invisible sticky
        # pin that would override the next dataset the user opens in the UI.
        state.pop('ai_chat_selected',None)
        state.pop('ai_chat_selected_name',None)
    state['ai_chat_expanded']=False
    state['ai_chat_open']=keep_chat
    st.rerun(scope='app')
