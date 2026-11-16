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

    def post(self, url, json=None, timeout=None):
        path = url.split('http://phase3.test')[-1]
        self.calls.append(('POST', path, json))
        return Response(self.api.post(path, json=json))


@pytest.fixture
def ui(collection, monkeypatch):
    from frontend.library import _shared_read
    _shared_read.clear()
    monkeypatch.setattr(workflow, 'SOURCES', {**workflow.SOURCES, 'mock': 'Test fixture'})
    bridge = Bridge(collection[-1])
    monkeypatch.setattr(workflow, 'client', lambda source: DashboardApiClient(
        base_url='http://phase3.test', source=source, stored_only=True, session=bridge))
    app = AppTest.from_file(str(Path('app.py').resolve())).run(timeout=15)
    assert not app.exception and not app.error
    return app, bridge, collection


def test_editing_collection_form_does_not_create_job_or_query(ui):
    app, bridge, _ = ui
    before = len(bridge.calls)
    app.selectbox(key='collect_source').set_value('era5').run()
    app.number_input(key='collect_north').set_value(12.).run()
    app.selectbox(key='collect_variable').select_index(2).run()
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
    assert 'Chatbot' not in app.pills(key='workflow_section').options






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




def test_geocoding_failure_keeps_manual_coordinates_usable(ui, monkeypatch):
    app, bridge, _ = ui
    monkeypatch.setattr(workflow, 'search_places', lambda query: None)
    app.text_input(key='collect_place_query').set_value('Hà Nội')
    app.button(key='collect_find_place').click().run()
    assert len(app.warning) == 1 and not app.exception
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
    assert app.pills(key='workflow_section').value is not None
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
    options = app.selectbox(key='workflow_selected_job').options
    assert all('đến' in option and 'mock' in option and 'Nhiệt độ' in option for option in options)
    assert any('Lịch sử thu thập' in exp.label for exp in app.expander)


@pytest.mark.parametrize('section,prefix',[('Dữ liệu đã lưu','stored'),('Biểu đồ','chart')])
def test_saved_library_selects_actual_database_data_and_never_collects(ui,section,prefix):
    app,bridge,collection=ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    before=len([c for c in bridge.calls if c[0]=='POST'])
    app.pills(key='workflow_section').set_value(section).run()
    assert not any(s.key==prefix+'_source' for s in app.selectbox)
    cid=app.session_state['latest_collection_id']
    app.button(key=prefix+('_draw_' if prefix=='chart' else '_view_')+cid).click().run()
    app.button(key=prefix+'_selected_submit').click().run()
    assert not app.exception and not app.error
    assert len([c for c in bridge.calls if c[0]=='POST'])==before
    assert bridge.calls[-1][1].startswith('/api/v1/collections/')
    assert bridge.calls[-1][1].endswith('/timeseries')
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
    app.pills(key='workflow_section').set_value('Biểu đồ').run()
    assert not app.exception
    assert not any(b.key=='chart_selected_submit' for b in app.button)
    assert not any(c[0]=='POST' for c in bridge.calls)


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
    app.button(key='open_collected').click().run()
    assert app.session_state['selected_collection_id']==cid
    app.selectbox(key='stored_variable_'+cid).set_value('2m_dewpoint_temperature')
    app.button(key='stored_selected_submit').click().run()
    assert not app.exception and not app.error
    before=sum(c[0]=='POST' for c in bridge.calls)
    next(b for b in app.button if b.label=='Vẽ biểu đồ từ dữ liệu này').click().run()
    assert app.selectbox(key='chart_variable_'+cid).value=='2m_dewpoint_temperature'
    app.button(key='chart_selected_submit').click().run()
    assert bridge.calls[-1][2]['variable']=='2m_dewpoint_temperature'
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
    app.pills(key='workflow_section').set_value(section).run()
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
    app.button(key='open_collected').click().run()
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
    app.button(key='open_collected').click().run()
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



def test_new_browser_session_restores_applied_data_without_api_reload(ui):
    app, bridge, _ = ui
    app.selectbox(key='collect_source').set_value('mock').run()
    app.button(key='collect_now').click().run()
    app.button(key='open_collected').click().run()
    result = app.session_state['stored_result']
    before = len(bridge.calls)
    refreshed = AppTest.from_file(str(Path('app.py').resolve()))
    refreshed.query_params.update(dict(app.query_params))
    refreshed.run(timeout=15)
    assert not refreshed.exception and not refreshed.error
    assert refreshed.pills(key='workflow_section').value == app.pills(key='workflow_section').value, dict(app.query_params)
    assert refreshed.session_state['stored_result'] == result
    assert len(bridge.calls) == before
    refreshed.button(key='stored_refresh_library').click().run()
    assert not refreshed.exception and not refreshed.error
    assert len(bridge.calls) > before
