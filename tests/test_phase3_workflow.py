"""Exercise actual Streamlit forms against isolated local API fixtures; no CDS."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from test_source_downloads import collection, POINT, DATES
from test_stored_queries import stored, save
from backend.app.domain.schemas import DownloadRequest
from backend.app.storage.job_repository import JobRepository
from frontend.api_client import DashboardApiClient
import frontend.workflow as workflow


class Response:
    def __init__(self, response):
        self.response = response
        self.status_code = response.status_code
        self.ok = response.is_success

    def json(self):
        return self.response.json()


class Bridge:
    def __init__(self, api):
        self.api, self.calls = api, []

    def get(self, url, params=None, timeout=None):
        path = url.split('http://phase3.test')[-1]
        self.calls.append(('GET', path, params))
        return Response(self.api.get(path, params=params))

    def delete(self, url, timeout=None):
        path = url.split('http://phase3.test')[-1]
        self.calls.append(('DELETE', path, None))
        return Response(self.api.delete(path))

    def post(self, url, json=None, timeout=None):
        path = url.split('http://phase3.test')[-1]
        self.calls.append(('POST', path, json))
        return Response(self.api.post(path, json=json))


@pytest.fixture
def ui(collection, monkeypatch):
    from frontend.library import _shared_read
    _shared_read.clear()
    monkeypatch.setattr(workflow, 'render_login_gate', lambda *_: True)
    monkeypatch.setattr(workflow, 'SOURCES', {**workflow.SOURCES, 'mock': 'Test fixture'})
    bridge = Bridge(collection[-1])
    monkeypatch.setattr(workflow, 'client', lambda source: DashboardApiClient(
        base_url='http://phase3.test', source=source, stored_only=True, session=bridge))
    from concurrent.futures import Future
    def immediate(api):
        future = Future()
        try:
            future.set_result(api.list_jobs())
        except Exception as exc:
            future.set_exception(exc)
        return future
    monkeypatch.setattr(workflow, '_submit_job_list', immediate)
    app = AppTest.from_file(str(Path('app.py').resolve())).run(timeout=15)
    assert not app.exception and not app.error
    return app, bridge, collection


def test_collection_form_does_not_wait_for_job_history(ui, monkeypatch):
    from concurrent.futures import Future
    app, bridge, _ = ui
    delayed = Future()
    monkeypatch.setattr(workflow, '_submit_job_list', lambda api: delayed)
    app.button(key='refresh_jobs').click().run()
    assert not app.exception
    assert app.text_input(key='collect_place_query')
    assert app.button(key='collect_now')
    assert any('Đang tải tác vụ' in info.value for info in app.info)
    before=len(bridge.calls)
    app.selectbox(key='collect_source').set_value('era5').run()
    assert app.number_input(key='collect_north')
    assert len(bridge.calls)==before  # Same pending request, no duplicate fetch.
    delayed.set_result([])
    app.run()
    assert not app.exception
    assert not any('Đang tải tác vụ' in info.value for info in app.info)


def test_old_background_history_cannot_replace_refreshed_list(ui,monkeypatch):
    from concurrent.futures import Future
    app, _, _ = ui
    old,new=Future(),Future()
    old.set_running_or_notify_cancel()
    futures=iter([old,new])
    monkeypatch.setattr(workflow,'_submit_job_list',lambda api:next(futures))
    app.button(key='refresh_jobs').click().run()
    app.button(key='refresh_jobs').click().run()
    new.set_result([])
    app.run()
    old.set_result([{'job_id':'stale-response'}])
    app.run()
    assert not app.exception
    assert app.session_state['workflow_job_list']==[]


def open_latest_collection(app):
    cid = app.session_state['latest_collection_id']
    app.session_state['workflow_section'] = 'Dữ liệu đã lưu'
    app.run()
    app.button(key='stored_view_'+cid).click().run()


def test_editing_collection_form_does_not_create_job_or_query(ui):
    app, bridge, _ = ui
    before = len(bridge.calls)
    app.selectbox(key='collect_source').set_value('era5').run()
    app.number_input(key='collect_north').set_value(12.).run()
    assert not any(widget.key == 'collect_variable' for widget in app.selectbox)
    assert len(bridge.calls) == before
    assert not any(call[0] == 'POST' for call in bridge.calls)
    assert not app.exception


def test_mock_collection_only_on_submit_and_no_hidden_query_panels(ui):
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    assert not app.exception and not app.error
    assert len([c for c in bridge.calls if c[0] == 'POST']) == 1
    assert all('/climate/' not in c[1] for c in bridge.calls)
    assert app.session_state['workflow_section'] in {'Thu thập','Dữ liệu đã lưu','Biểu đồ'}






def test_duplicate_active_requests_are_atomic_and_source_specific(collection):
    settings, _, jobs, service, _, _, _ = collection
    request = DownloadRequest(source='land', variable='temperature', location=POINT, **DATES)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.create_job(request), range(16)))
    ids = {r.job_id for r in results}
    assert len(ids) == 1
    job_id = next(iter(ids))
    assert jobs.claim_running(job_id)
    assert not jobs.claim_running(job_id)
    assert service.create_job(request).job_id == job_id
    mock_request = request.model_copy(update={'source': 'mock'})
    assert service.create_job(mock_request).job_id != job_id
    assert JobRepository(settings.job_metadata_db_path).create('not-used', request,
        dataset=settings.era5_land_dataset, retrieval_variables=jobs.get(job_id).retrieval_variables).job_id == job_id
    jobs.mark_failed(job_id, 'fixture failure')
    assert service.create_job(request).job_id != job_id


def test_resubmitted_background_task_does_not_execute_provider_twice(collection, monkeypatch):
    _, _, jobs, service, providers, _, _ = collection
    request = DownloadRequest(source='mock', variable='temperature', location=POINT, **DATES)
    first = service.create_job(request)
    second = service.create_job(request)
    assert first.job_id == second.job_id
    calls = []
    original = providers['mock'].download_point
    def download(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(providers['mock'], 'download_point', download)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(service.run_job, [first.job_id, second.job_id]))
    assert len(calls) == 1 and jobs.get(first.job_id).status == 'completed'


def test_catalog_uses_only_stored_dataset_and_reports_internal_gaps(stored):
    settings, writer, _, api = stored
    save(writer, settings, source='land', hours=(0, 2))
    save(writer, settings, source='era5', hours=(0, 1, 2))
    land = api.get('/api/v1/stored/climate/catalog', params={'source': 'land'}).json()
    era5 = api.get('/api/v1/stored/climate/catalog', params={'source': 'era5'}).json()
    assert land['timezone'] == 'UTC+7'
    assert land['entries'][0]['coverage'] == pytest.approx(2/3)
    assert era5['entries'][0]['coverage'] == 1
    assert land['dataset'] != era5['dataset']
    assert land['entries'][0]['start'].endswith('+07:00')
    assert api.get('/api/v1/stored/climate/catalog', params={'source':'mock'}).json()['entries'] == []




def test_collection_shows_only_supported_spatial_inputs(ui):
    app, bridge, _ = ui
    assert any(n.key == 'collect_latitude' for n in app.number_input)
    assert not any(n.key == 'collect_north' for n in app.number_input)
    app.selectbox(key='collect_source').set_value('era5').run()
    assert any(n.key == 'collect_north' for n in app.number_input)
    assert not any(n.key == 'collect_latitude' for n in app.number_input)
    assert not any(c[0] == 'POST' for c in bridge.calls)




def notices(app):
    return [element for element in app.get('html') if '<template id="are-notice-' in element.value]


def test_geocoding_failure_keeps_manual_coordinates_usable(ui, monkeypatch):
    app, bridge, _ = ui
    monkeypatch.setattr(workflow, 'search_places', lambda query: None)
    app.text_input(key='collect_place_query').set_value('Hà Nội')
    app.button(key='collect_find_place').click().run()
    assert len(notices(app)) == 1 and not app.exception and not app.warning
    assert not any(c[0] == 'POST' for c in bridge.calls)
    app.selectbox(key='collect_source').set_value('mock').run()
    app.number_input(key='collect_latitude').set_value(21.)
    app.button(key='collect_now').click().run()
    assert not app.exception
    assert next(c[2] for c in bridge.calls if c[0] == 'POST')['location']['latitude'] == 21.


def test_native_theme_is_not_overridden_or_duplicated(ui):
    app, bridge, _ = ui
    before = len(bridge.calls)
    assert not app.sidebar.radio
    assert app.session_state['workflow_section'] is not None
    assert not any(r.key == 'appearance_mode' for r in app.radio)
    styles = '\n'.join(item.value for item in app.markdown)
    assert 'color-scheme:' not in styles and '--primary-color:' not in styles
    assert 'background:' not in styles and '[data-baseweb=' not in styles
    app.run()
    assert not app.exception and not app.error
    assert len(bridge.calls) == before


def test_visible_labels_have_valid_vietnamese_and_readable_job_names(ui):
    import io
    import tokenize
    app, _, _ = ui
    for path in ['frontend/workflow.py', 'frontend/dashboard.py', 'frontend/library.py']:
        strings = [t.string for t in tokenize.generate_tokens(io.StringIO(Path(path).read_text(encoding='utf-8')).readline)
                   if t.type == tokenize.STRING]
        assert not any('?' in s or '\ufffd' in s for s in strings)
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    options = next(w for w in app.selectbox if (w.key or '').startswith('workflow_job_choice_')).options
    assert all('đến' in option and 'mock' in option and 'Nhiệt độ' in option for option in options)
    assert any('Lịch sử thu thập' in exp.label for exp in app.expander)


@pytest.mark.parametrize('section,prefix',[('Dữ liệu đã lưu','stored'),('Biểu đồ','chart')])
def test_saved_library_selects_actual_database_data_and_never_collects(ui,section,prefix):
    app,bridge,collection=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    before=len([c for c in bridge.calls if c[0]=='POST'])
    app.session_state['workflow_section'] = section
    app.run()
    assert not any(s.key==prefix+'_source' for s in app.selectbox)
    cid=app.session_state['latest_collection_id']
    app.button(key=prefix+('_draw_' if prefix=='chart' else '_view_')+cid).click().run()
    app.button(key=prefix+'_selected_submit').click().run()
    assert not app.exception and not app.error
    assert len([c for c in bridge.calls if c[0]=='POST'])==before
    assert bridge.calls[-1][1].startswith('/api/v1/collections/')
    assert bridge.calls[-1][1].endswith('/timeseries')
    if prefix == 'chart':
        assert not any(d.key=='download_timeseries_csv' for d in app.get('download_button'))
        app.session_state['timeseries_table_panel'] = True
        app.run()
    assert any(d.key=='download_timeseries_csv' for d in app.get('download_button'))
    if prefix == 'chart':
        app.button(key='chart_view_'+cid).click().run()
        assert app.session_state['workflow_section']=='Dữ liệu đã lưu'
        assert not app.exception and not app.error
        assert app.button(key='stored_selected_submit')
    if not section=='Biểu đồ':
        chosen=app.session_state['selected_collection_id']
        next(b for b in app.button if b.label=='Vẽ biểu đồ từ dữ liệu này').click().run()
        assert app.session_state['selected_collection_id']==chosen


def test_empty_database_has_no_fictitious_chart_choices(ui):
    app,bridge,_=ui
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    assert not app.exception
    assert not any(b.key=='chart_selected_submit' for b in app.button)
    assert not any(c[0]=='POST' for c in bridge.calls)


@pytest.mark.parametrize('section,prefix',[('Dữ liệu đã lưu','stored'),('Biểu đồ','chart')])
def test_search_and_clear_reopen_collapsed_library(ui,section,prefix):
    app,bridge,_=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    app.session_state['workflow_section'] = section
    app.run()
    app.button(key=prefix+('_draw_' if prefix=='chart' else '_view_')+cid).click().run()
    assert app.session_state[prefix+'_library_cards'] is False
    app.text_input(key=prefix+'_library_text').set_value('mock')
    app.button(key=prefix+'_find').click().run()
    assert not app.exception and not app.error
    assert app.session_state[prefix+'_library_cards'] is True
    assert app.button(key=prefix+'_view_'+cid)
    app.session_state[prefix+'_library_cards']=False
    app.run()
    app.button(key=prefix+'_clear_search').click().run()
    assert app.session_state[prefix+'_library_cards'] is True
    assert app.session_state[prefix+'_search']==''


@pytest.mark.parametrize('section,prefix',[('Dữ liệu đã lưu','stored'),('Biểu đồ','chart')])
def test_card_draw_opens_result_with_library_and_filters_collapsed(ui,section,prefix):
    app, _, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    app.session_state['workflow_section'] = section
    app.run()
    app.button(key=prefix+'_draw_'+cid).click().run()
    assert not app.exception and not app.error
    assert app.session_state['workflow_section']=='Biểu đồ'
    assert app.session_state['chart_library_cards'] is False
    assert app.session_state['chart_filters_'+cid] is False
    assert app.session_state['chart_result'][0]==cid


def test_chart_opens_list_and_reload_restores_only_explicit_chart(ui):
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    open_latest_collection(app)
    cid=app.session_state['latest_collection_id']
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    assert not any(b.key=='chart_selected_submit' for b in app.button)
    assert app.session_state['chart_library_cards'] is True
    assert not any(b.key == 'chart_delete_confirm_'+cid for b in app.button)
    app.button(key='chart_draw_'+cid).click().run()
    result=app.session_state['chart_result']
    refreshed=AppTest.from_file(str(Path('app.py').resolve()))
    refreshed.query_params.update(dict(app.query_params))
    refreshed.run(timeout=15)
    assert not refreshed.exception and not refreshed.error
    assert refreshed.session_state['chart_result']==result
    assert refreshed.button(key='chart_selected_submit')
    refreshed.button(key='chart_back_to_list').click().run()
    assert not any(b.key=='chart_selected_submit' for b in refreshed.button)


def test_chart_failed_filter_keeps_applied_result_and_url(ui, monkeypatch):
    from frontend.api_client import DashboardApiError
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    app.button(key='chart_draw_'+cid).click().run()
    old=app.session_state['chart_result']
    url=app.query_params['chart_applied']
    calls=len(bridge.calls)
    app.selectbox(key='saved_chart_kind').set_value('Bản đồ nhiệt').run()
    assert len(bridge.calls)==calls
    assert not any(w.key=='saved_chart_frequency' for w in app.selectbox)
    def fail(*a,**kw):
        raise DashboardApiError('Fixture failed chart query')
    monkeypatch.setattr(DashboardApiClient,'query_collection',fail)
    app.button(key='chart_selected_submit').click().run()
    assert not app.exception
    assert notices(app) and not app.error
    assert app.session_state['chart_result']==old
    assert app.query_params['chart_applied']==url
    assert app.get('plotly_chart')


def test_invalid_chart_dates_keep_old_plot_and_do_not_query(ui):
    from datetime import date
    app,bridge,_=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.date_input(key='collect_end').set_value(date(2025,1,2))
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    app.button(key='chart_draw_'+cid).click().run()
    old=app.session_state['chart_result']
    app.session_state['chart_filters_'+cid]=True
    app.run()
    before=len(bridge.calls)
    app.date_input(key='chart_range_start_'+cid).set_value(date(2025,1,2))
    app.date_input(key='chart_range_end_'+cid).set_value(date(2025,1,1))
    app.button(key='chart_selected_submit').click().run()
    assert not app.exception and notices(app) and not app.error
    assert app.session_state['chart_result']==old
    assert app.session_state['chart_filters_'+cid] is True
    assert app.get('plotly_chart')
    assert len(bridge.calls)==before


@pytest.mark.parametrize('raw', ['null','{}','[{}, [], {}, []]',
    json.dumps(['missing','Unknown chart',{},'timeseries']),
    json.dumps(['missing','Chuỗi thời gian',{'variable':'temperature'},'timeseries'])])
def test_invalid_saved_chart_url_falls_back_without_querying(ui,raw):
    app, bridge, _ = ui
    before=len(bridge.calls)
    fresh=AppTest.from_file(str(Path('app.py').resolve()))
    fresh.query_params.update({'view':'Biểu đồ','chart_mode':'details','chart_applied':raw})
    fresh.run(timeout=15)
    assert not fresh.exception and not fresh.error
    assert fresh.session_state['chart_details_visible'] is False
    assert notices(fresh) and not fresh.warning
    assert not any('/timeseries' in c[1] for c in bridge.calls[before:])


def test_deleted_in_another_session_returns_to_library_on_refresh(ui):
    app, bridge, collection = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    open_latest_collection(app)
    cid=app.session_state['latest_collection_id']
    assert collection[-1].delete(f'/api/v1/collections/{cid}').status_code==200
    app.button(key='stored_refresh_library').click().run()
    assert not app.exception and not app.error
    assert app.session_state['stored_details_visible'] is False
    assert not any(b.key=='stored_selected_submit' for b in app.button)
    assert notices(app) and not app.warning


def test_tabs_keep_independent_results_and_applied_chart_controls(ui):
    app, bridge, collection = ui
    service=collection[3]
    ids=[]
    for variable in ['temperature','dewpoint']:
        cid=service.create_job(DownloadRequest(source='mock',variable=variable,location=POINT,**DATES)).job_id
        service.run_job(cid)
        ids.append(cid)
    app.session_state['workflow_section'] = 'Dữ liệu đã lưu'
    app.run()
    app.button(key='stored_view_'+ids[0]).click().run()
    stored=app.session_state['stored_result']
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    app.button(key='chart_draw_'+ids[1]).click().run()
    app.selectbox(key='saved_chart_frequency').set_value('daily')
    app.button(key='chart_selected_submit').click().run()
    chart=app.session_state['chart_result']
    before=len(bridge.calls)
    app.session_state['workflow_section'] = 'Dữ liệu đã lưu'
    app.run()
    assert app.session_state['stored_result']==stored
    assert app.session_state['stored_collection']==ids[0]
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    assert not app.exception and not app.error
    assert app.session_state['chart_result']==chart
    assert app.session_state['chart_collection']==ids[1]
    assert app.selectbox(key='saved_chart_frequency').value=='daily'
    assert len(bridge.calls)==before


def test_library_actual_land_grid_reads_saved_data_without_download(stored):
    settings,writer,_,api=stored
    save(writer,settings,source='land')
    entries=api.get('/api/v1/stored/climate/catalog',params={'source':'land'}).json()['entries']
    e=entries[0]
    response=api.get('/api/v1/stored/climate/timeseries',params=dict(source='land',variable=e['variable'],
        latitude=e['grid_latitude'],longitude=e['grid_longitude'],start=e['start'],end=e['end']))
    assert response.status_code==200
    assert response.json()['count']==3 and response.json()['complete']






def test_city_persisted_and_extra_variable_carried_to_chart(ui,monkeypatch):
    app,bridge,collection=ui
    place=dict(label='Thành phố Hồ Chí Minh, Việt Nam',latitude=10.8231,longitude=106.6297)
    monkeypatch.setattr(workflow,'search_places',lambda query:[place])
    app.text_input(key='collect_place_query').set_value('hồ chí minh')
    app.button(key='collect_find_place').click().run()
    app.button(key='collect_now').click().run()  # Fake CDS fixture, never real CDS.
    assert not app.exception and not app.error
    cid=app.session_state['workflow_selected_job']
    assert collection[3].collections.get(cid)['metadata']['place_name']==place['label']
    open_latest_collection(app)
    assert app.session_state['selected_collection_id']==cid
    app.checkbox(key='stored_var_2m_temperature_'+cid).uncheck()
    app.checkbox(key='stored_var_2m_dewpoint_temperature_'+cid).check()
    app.button(key='stored_selected_submit').click().run()
    assert not app.exception and not app.error
    before=sum(c[0]=='POST' for c in bridge.calls)
    next(b for b in app.button if b.label=='Vẽ biểu đồ từ dữ liệu này').click().run()
    assert app.checkbox(key='stored_var_2m_dewpoint_temperature_'+cid).value
    app.button(key='chart_selected_submit').click().run()
    assert app.session_state['chart_result'][2]['variable']=='2m_dewpoint_temperature'
    assert sum(c[0]=='POST' for c in bridge.calls)==before
    assert not app.exception and not app.error



@pytest.mark.parametrize('section,prefix',[('Dữ liệu đã lưu','stored'),('Biểu đồ','chart')])
@pytest.mark.parametrize('with_collection',[False,True])
def test_saved_views_do_not_offer_legacy_library(ui,section,prefix,with_collection):
    app,bridge,collection=ui
    if with_collection:
        app.selectbox(key='collect_source').set_value('mock').run()
        app.button(key='collect_now').click().run()
        cid=app.session_state['latest_collection_id']
    app.session_state['workflow_section'] = section
    app.run()
    if with_collection:
        app.button(key=prefix+('_draw_' if prefix=='chart' else '_view_')+cid).click().run()
        app.button(key=prefix+'_selected_submit').click().run()
    before=sum(c[0]=='POST' for c in bridge.calls)
    assert not app.exception and not app.error
    assert not any('_legacy' in (w.key or '') for w in app.checkbox)
    assert sum(c[0]=='POST' for c in bridge.calls)==before



def test_open_collection_loads_data_once_and_search_can_be_cleared(ui):
    app,bridge,_=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid=app.session_state['latest_collection_id']
    open_latest_collection(app)
    assert not app.exception and not app.error
    assert app.session_state['stored_result'][0]==cid
    assert app.session_state['stored_result'][2]['data']
    reads=sum(c[1].endswith('/timeseries') for c in bridge.calls)
    app.run()
    assert sum(c[1].endswith('/timeseries') for c in bridge.calls)==reads
    app.text_input(key='stored_library_text').set_value('does-not-exist')
    app.button(key='stored_find').click().run()
    assert not app.exception
    assert not any(b.key=='stored_view_'+cid for b in app.button)
    app.button(key='stored_clear_search').click().run()
    assert app.button(key='stored_view_'+cid)
    assert not app.exception and not app.error



def test_details_filter_applies_only_on_submit_and_reuses_identical_read(ui):
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid = app.session_state['latest_collection_id']
    open_latest_collection(app)
    assert app.session_state['stored_result'][0] == cid
    reads = lambda: sum(c[1].endswith('/timeseries') for c in bridge.calls)
    before = reads()
    widget = app.selectbox(key='stored_variable_'+cid)
    target = widget.options[-1]
    widget.select_index(len(widget.options)-1).run()
    assert reads() == before
    app.button(key='stored_selected_submit').click().run()
    assert not app.exception and not app.error
    result = app.session_state['stored_result']
    from frontend.components.charts import variable_label
    assert variable_label(result[2]['variable']) == target
    after = reads()
    app.button(key='stored_selected_submit').click().run()
    assert reads() == after
    assert app.session_state['stored_result'] == result



def test_reload_restores_explicit_detail_view_without_api_reload(ui):
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    open_latest_collection(app)
    result = app.session_state['stored_result']
    before = len(bridge.calls)
    refreshed = AppTest.from_file(str(Path('app.py').resolve()))
    refreshed.query_params.update(dict(app.query_params))
    refreshed.run(timeout=15)
    assert not refreshed.exception and not refreshed.error
    assert refreshed.session_state['workflow_section'] == app.session_state['workflow_section'], dict(app.query_params)
    assert refreshed.button(key='stored_selected_submit')
    assert refreshed.session_state['stored_library_cards'] is False
    assert refreshed.session_state['stored_result'] == result
    assert len(bridge.calls) == before
    refreshed.button(key='stored_refresh_library').click().run()
    assert not refreshed.exception and not refreshed.error
    assert len(bridge.calls) > before


def test_back_to_library_keeps_search_and_reload_stays_in_list(ui):
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    open_latest_collection(app)
    app.text_input(key='stored_library_text').set_value('mock')
    app.button(key='stored_find').click().run()
    page=app.session_state.get('stored_page',0)
    app.button(key='stored_back_to_list').click().run()
    assert not app.exception
    assert app.session_state['stored_library_cards'] is True
    assert app.session_state['stored_search']=='mock'
    assert app.session_state.get('stored_page',0)==page
    assert not any(b.key=='stored_selected_submit' for b in app.button)
    refreshed=AppTest.from_file(str(Path('app.py').resolve()))
    refreshed.query_params.update(dict(app.query_params))
    refreshed.run(timeout=15)
    assert not refreshed.exception
    assert not any(b.key=='stored_selected_submit' for b in refreshed.button)


def test_returning_to_stored_keeps_view_until_back_to_list_is_clicked(ui):
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    open_latest_collection(app)
    cid=app.session_state['latest_collection_id']
    assert app.session_state['stored_library_cards'] is False
    app.session_state['workflow_section'] = 'Thu thập'
    app.run()
    app.session_state['workflow_section'] = 'Dữ liệu đã lưu'
    app.run()
    assert app.button(key='stored_selected_submit')
    assert any(d.key=='download_timeseries_csv' for d in app.get('download_button'))
    app.button(key='stored_back_to_list').click().run()
    app.session_state['workflow_section'] = 'Thu thập'
    app.run()
    app.session_state['workflow_section'] = 'Dữ liệu đã lưu'
    app.run()
    assert app.session_state['stored_library_cards'] is True
    assert not any(b.key=='stored_selected_submit' for b in app.button)
    assert not any(d.key=='download_timeseries_csv' for d in app.get('download_button'))
    app.button(key='stored_view_'+cid).click().run()
    assert app.session_state['stored_library_cards'] is False
    assert app.button(key='stored_selected_submit')
    assert not app.exception and not app.error


def test_delete_requires_confirmation_and_clears_saved_view(ui):
    app, bridge, collection = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid = app.session_state['latest_collection_id']
    open_latest_collection(app)
    assert collection[3].collections.get(cid)
    assert not any(call[0]=='DELETE' for call in bridge.calls)
    app.button(key='stored_delete_confirm_'+cid).click().run()
    assert not app.exception and not app.error
    assert collection[3].collections.get(cid) is None
    assert 'stored_applied' not in app.query_params
    assert 'stored_result' not in app.session_state
    assert not any(b.key=='stored_view_'+cid for b in app.button)
    app.run()
    assert not app.exception and not app.error


def test_delete_api_error_keeps_collection_and_applied_result(ui, monkeypatch):
    from frontend.api_client import DashboardApiError
    app, _, collection = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    cid = app.session_state['latest_collection_id']
    open_latest_collection(app)
    original = app.session_state['stored_result']
    def fail(*args):
        raise DashboardApiError('Delete fixture failure')
    monkeypatch.setattr(DashboardApiClient, 'delete_collection', fail)
    app.button(key='stored_delete_confirm_'+cid).click().run()
    assert not app.exception
    assert any('Delete fixture failure' in e.value for e in notices(app))
    assert collection[3].collections.get(cid) is not None
    assert app.session_state['stored_result'] == original
    assert 'stored_applied' in app.query_params


def test_quality_details_load_only_when_open_and_terminal_job_is_cached(ui):
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    jid = app.session_state['workflow_selected_job']
    assert not any(c[1].endswith('/quality') for c in bridge.calls)
    app.run()
    before = len(bridge.calls)
    app.run()
    assert len(bridge.calls) == before
    next(w for w in app.selectbox if (w.key or '').startswith('workflow_job_choice_')).select(jid).run()
    app.session_state['job_quality_'+jid] = True
    app.run()
    assert any(c[1].endswith('/quality') for c in bridge.calls)
    assert not app.exception and not app.error


def test_job_refresh_keeps_one_status_and_updates_history_without_resubmit(ui):
    app, bridge, collection = ui
    service = collection[3]
    created = service.create_job(DownloadRequest(source='mock', variable='temperature', location=POINT, **DATES))
    app.button(key='refresh_jobs').click().run()
    jid = created.job_id
    assert app.session_state['workflow_selected_job'] == jid
    def assert_single_status(expected):
        statuses = [m.value for m in app.html if 'class="job-state job-state-' in m.value]
        assert len(statuses) == 1
        assert '<strong>Trạng thái thu thập: ' + expected + '</strong>' in statuses[0]
        assert sum(e.label == 'Chi tiết nguồn và biến' for e in app.expander) == 1
        assert not app.exception and not app.error
    assert_single_status('Đang chờ')
    service.run_job(jid)
    app.button(key='refresh_jobs').click().run()
    assert not any('class="job-state job-state-' in m.value for m in app.html)
    assert any('Đã hoàn tất thu thập' in toast.value for toast in notices(app))
    assert 'completed' in next(w for w in app.selectbox if (w.key or '').startswith('workflow_job_choice_')).options[0]
    before = len(bridge.calls)
    app.run()
    assert not any('class="job-state job-state-' in m.value for m in app.html)
    assert not any('Đã hoàn tất thu thập' in toast.value for toast in notices(app))
    assert len(bridge.calls) == before
    next(w for w in app.selectbox if (w.key or '').startswith('workflow_job_choice_')).select(jid).run()
    assert_single_status('Hoàn tất')
    assert not any(call[0] == 'POST' for call in bridge.calls)


def test_cancel_button_stops_selected_queued_job_and_updates_status(ui):
    app, bridge, collection = ui
    jid = collection[3].create_job(DownloadRequest(source='mock',variable='temperature',location=POINT,**DATES)).job_id
    app.button(key='refresh_jobs').click().run()
    app.button(key='cancel_job_'+jid).click().run()
    assert not app.exception and not app.error
    assert collection[2].get(jid).status == 'cancelled'
    assert any('Trạng thái thu thập: Đã hủy' in h.value for h in app.html)
    assert not any(b.key=='cancel_job_'+jid for b in app.button)
    assert len([c for c in bridge.calls if c[0]=='POST' and c[1].endswith('/cancel')]) == 1


def test_library_survives_cache_expiry_and_deletion_without_reloading_list(ui):
    from frontend.library import _shared_read
    app, bridge, collection = ui
    service = collection[3]
    ids = []
    for variable in ['temperature','dewpoint']:
        jid=service.create_job(DownloadRequest(source='mock',variable=variable,location=POINT,**DATES)).job_id
        service.run_job(jid)
        ids.append(jid)
    app.session_state['workflow_section'] = 'Dữ liệu đã lưu'
    app.run()
    app.button(key='stored_view_'+ids[1]).click().run()
    list_reads=lambda:sum(c[0]=='GET' and c[1]=='/api/v1/collections' for c in bridge.calls)
    before=list_reads()
    _shared_read.clear()  # Simulate the short-lived shared cache no longer holding the page.
    app.run()
    assert list_reads()==before
    app.button(key='stored_delete_confirm_'+ids[0]).click().run()
    assert not app.exception and not app.error
    assert list_reads()==before
    assert not any(b.key=='stored_view_'+ids[0] for b in app.button)
    assert app.session_state['stored_result'][0]==ids[1]
    app.session_state['workflow_section'] = 'Biểu đồ'
    app.run()
    app.session_state['workflow_section'] = 'Dữ liệu đã lưu'
    app.run()
    assert not app.exception and not app.error
    assert list_reads()==before
    app.button(key='stored_refresh_library').click().run()
    assert list_reads()==before+1
