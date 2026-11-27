"""Browser-local chat persistence. No history in URLs or a shared server cache."""
from hashlib import sha256
import json
from typing import Any,Literal

from pydantic import BaseModel,ConfigDict,Field,ValidationError
import streamlit as st


class SavedMessage(BaseModel):
    model_config=ConfigDict(extra='ignore',strict=True)
    role: Literal['user','assistant']
    content: str=Field(min_length=1,max_length=16000)
    result: dict[str,Any] | None=None


class SavedChat(BaseModel):
    model_config=ConfigDict(extra='ignore',strict=True)
    version: Literal[1]=1
    messages: list[SavedMessage]=Field(default_factory=list)
    selected: str | None=Field(default=None,max_length=128)
    selected_name: str | None=Field(default=None,max_length=1000)
    opened: bool=False
    unread: bool=False
    theme: Literal['auto','cloud','sun','rain']='auto'
    in_flight: bool=False
    reset_token: str=Field(default='',max_length=64)


def storage_key(base_url, username=None):
    return 'era5.are.chat.v1.'+sha256((base_url.rstrip('/')+('\0'+username if username else '')).encode()).hexdigest()[:20]


def snapshot(state):
    messages=[]
    for message in state.get('ai_chat_messages',[]):
        saved=dict(role=message['role'],content=message['content'])
        result=message.get('result')
        if result:
            # Keep visible evidence/actions, not request arguments, usage or code excerpts.
            saved['result']={key:result[key] for key in ['answer_kind','completed','verified','actions','collection_job'] if key in result}
            saved['result']['evidence']=[dict(tool=e['tool'],result=e['result'])
                for e in result.get('evidence',[]) if e.get('tool')!='project_knowledge']
        messages.append(saved)
    return dict(version=1,messages=messages,selected=state.get('ai_chat_selected'),
        selected_name=state.get('ai_chat_selected_name'),opened=bool(state.get('ai_chat_open')),
        unread=bool(state.get('ai_chat_unread')),theme=state.get('ai_pet_theme','auto'),
        in_flight=bool(state.get('ai_chat_pending')),reset_token=state.get('ai_chat_reset_token',''))


def restore(state,raw):
    """Validate browser data before using it as UI state. Never resubmit pending work."""
    saved=SavedChat.model_validate({} if raw is None else raw)
    messages=[]
    for message in saved.messages:
        item=message.model_dump(exclude_none=True)
        item['restored']=True
        messages.append(item)
    if saved.in_flight:
        messages.append(dict(role='assistant',content='Lượt trước chưa lưu xong câu trả lời trước khi tải lại trang. Câu hỏi đã được giữ; ERA chưa tự gửi lại.'))
    state['ai_chat_messages']=messages
    state['ai_chat_open']=saved.opened
    state['ai_chat_unread']=saved.unread
    state['ai_pet_theme']=saved.theme
    state['ai_chat_reset_token']=saved.reset_token
    for key,value in [('ai_chat_selected',saved.selected),('ai_chat_selected_name',saved.selected_name)]:
        if value is not None:
            state[key]=value
        else:
            state.pop(key,None)


_BRIDGE_JS = """
export default function(component) {
  const {data, parentElement, setStateValue, setTriggerValue} = component;
  if (!data) return;
  const root = parentElement.host || parentElement;
  const state = root.__areHistory || (root.__areHistory = {});
  if (data.pending && !state.poll) {
    state.poll = window.setInterval(() => setTriggerValue('poll', Date.now()), 1000);
  } else if (!data.pending && state.poll) {
    window.clearInterval(state.poll);
    state.poll = null;
  }
  if (data.action === 'load' && state.loaded !== data.storage_key) {
    state.loaded = data.storage_key;
    try {
      const raw = window.localStorage.getItem(data.storage_key);
      const snapshot = raw ? JSON.parse(raw) : null;
      setStateValue('loaded', {ok:true, snapshot});
    } catch (_) {
      setStateValue('loaded', {ok:false});
    }
  }
  if (data.action === 'save' && state.revision !== data.revision) {
    state.revision = data.revision;
    try {
      window.localStorage.setItem(data.storage_key, JSON.stringify(data.snapshot));
      // Successful storage needs no server ACK: it would redraw the chat and
      // close native selectors while the user is interacting. Notify only when
      // recovering from a previously reported storage error.
      if (state.failed) setStateValue('saved', {ok:true, revision:data.revision});
      state.failed = false;
    } catch (_) {
      state.failed = true;
      setStateValue('saved', {ok:false, revision:data.revision});
    }
  }
  return () => {
    if (state.poll) window.clearInterval(state.poll);
    state.poll = null;
  };
}
"""

_BRIDGE=st.components.v2.component('era5_era_history',html='<span aria-hidden="true"></span>',js=_BRIDGE_JS)


def _rerender_chat():
    # The bridge belongs to the chat fragment; events rerun that fragment.
    pass


def _saved_callback():
    state=st.session_state.get('ai_chat_browser_history',{})
    event=state.get('saved') if isinstance(state,dict) else None
    if isinstance(event,dict) and (not event.get('ok') or st.session_state.get('ai_chat_history_error')):
        _rerender_chat()


def sync_history(api):
    state=st.session_state
    ready=state.get('ai_chat_history_loaded',False)
    data=dict(action='load',storage_key=storage_key(api.base_url,state.get('auth_username')),pending=bool(state.get('ai_chat_pending')))
    if ready:
        payload=snapshot(state)
        encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False)
        revision=sha256(encoded.encode()).hexdigest()
        if state.get('ai_chat_history_blocked') or state.get('ai_chat_history_ack')==revision:
            data.update(action='idle',revision=revision)
        else:
            data.update(action='save',snapshot=payload,revision=revision)
    with st.container(key='ai_chat_persistence'):
        result=_BRIDGE(key='ai_chat_browser_history',data=data,height=0,
                       on_loaded_change=_rerender_chat,on_saved_change=_saved_callback,on_poll_change=_rerender_chat)
    loaded=getattr(result,'loaded',None)
    if not ready and isinstance(loaded,dict):
        try:
            if loaded.get('ok') is not True:
                raise ValueError('Browser storage unavailable')
            restore(state,loaded.get('snapshot'))
            state.pop('ai_chat_history_error',None)
        except (ValidationError,ValueError,TypeError):
            # Do not overwrite a damaged/unreadable old history with an empty chat.
            state['ai_chat_history_blocked']=True
            state['ai_chat_history_error']='Chưa đọc được lịch sử trên trình duyệt. Dữ liệu cũ chưa bị xóa; bấm Làm mới trong chat nếu muốn bắt đầu lại.'
        state['ai_chat_history_loaded']=True
        st.rerun(scope='fragment')
    saved=getattr(result,'saved',None)
    if isinstance(saved,dict) and data.get('revision')==saved.get('revision'):
        state['ai_chat_history_ack']=saved['revision']
        if saved.get('ok') is False:
            state['ai_chat_history_error']='Trình duyệt chưa lưu được lịch sử (có thể đã đầy hoặc chặn lưu trữ). Đừng tải lại trang nếu muốn giữ các tin mới.'
        else:
            state.pop('ai_chat_history_error',None)
