"""Immutable collection provenance and exact DB-only selection, fake CDS only."""
from datetime import datetime
from pathlib import Path
import csv
import io
import sqlite3
import pytest
from test_source_downloads import collection, POINT, AREA, DATES
from test_collection_quality import collect, land_rows
from backend.app.domain.schemas import DownloadRequest, ClimatePoint, Location
from backend.app.storage.collection_repository import CollectionRepository
from backend.app.services.collection_service import CollectionService
from backend.app.services.stored_climate_service import StoredClimateService
from backend.app.storage.climate_repository import ClimateRepository


def local_request(**updates):
    return dict(source='land',variable='temperature',location=POINT,
                place_name='Thành phố Hồ Chí Minh, Việt Nam',selected_place=POINT,
                requested_start='2025-01-01T00:00:00+07:00',requested_end='2025-01-02T23:00:00+07:00',
                request_timezone='UTC+7',start_date='2024-12-31',end_date='2025-01-02',**updates)


def create(api,payload):
    r=api.post('/api/v1/downloads',json=payload)
    assert r.status_code==202,r.text
    return r.json()['collection_id']


def test_hcm_48_hours_metadata_restart_extras_artifacts_and_csv(collection,monkeypatch):
    settings,writer,jobs,service,providers,cds,api=collection
    cid=create(api,local_request())
    entry=CollectionRepository(settings.climate_data_db_path,read_only=True).get(cid)
    assert entry['status']=='complete'
    assert entry['metadata']['place_name']=='Thành phố Hồ Chí Minh, Việt Nam'
    assert entry['metadata']['requested_start_utc']=='2024-12-31T17:00:00.000000'
    assert entry['summary']['expected_count']==entry['summary']['available_count']==48
    assert entry['summary']['imported_values']==72*6
    assert len(entry['variables'])==6
    assert entry['artifacts'] and len(entry['artifacts'][0]['sha256'])==64
    assert entry['grids']==[dict(latitude=10.8,longitude=106.7)]
    def forbidden(*a,**kw): raise AssertionError('Read attempted provider/cache access')
    monkeypatch.setattr(cds,'retrieve',forbidden)
    monkeypatch.setattr(writer,'read_available',forbidden)
    for p in providers.values():
        monkeypatch.setattr(p,'download_point',forbidden)
        monkeypatch.setattr(p,'download_area',forbidden)
    # Recreate the read service to establish that session state is not necessary.
    reader=CollectionService(StoredClimateService(ClimateRepository(settings.climate_data_db_path,read_only=True),settings))
    result=reader.query(cid)
    assert result['count']==48 and result['data'][0]['time']=='2025-01-01T00:00:00+07:00'
    assert result['data'][-1]['time']=='2025-01-02T23:00:00+07:00'
    assert result['data'][0]['value']==pytest.approx(26.85) and result['unit']=='°C'
    exported=api.get(f'/api/v1/collections/{cid}/timeseries.csv')
    rows=list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert len(rows)==48 and {r['collection_id'] for r in rows}=={cid}
    assert api.get('/api/v1/collections',params={'search':'hồ chí minh'}).json()['collections'][0]['id']==cid
    for operation in ['aggregate','statistics','trend','heatmap','map']:
        r=api.get(f'/api/v1/collections/{cid}/{operation}',params={'frequency':'daily'})
        assert r.status_code==200,r.text
        assert r.json()['collection_id']==cid
        if operation=='map':
            assert r.json()['data'][0]['latitude']==10.8
            assert r.json()['data'][0]['longitude']==106.7
    assert api.get(f'/api/v1/collections/{cid}/timeseries',params={'latitude':40.,'longitude':-74.}).status_code==422
    assert api.get(f'/api/v1/collections/{cid}/timeseries',params={'start':'2020-01-01'}).status_code==422


def test_mutable_cache_and_later_conflict_cannot_change_earlier_collection(collection):
    settings,writer,_,_,_,cds,api=collection
    first=create(api,local_request())
    entry=api.get(f'/api/v1/collections/{first}').json()
    raw=Path(entry['artifacts'][0]['path'])
    # Test fixture only: simulate a corrected upstream file for a second explicit import.
    raw.write_text(raw.read_text(encoding='utf-8').replace(',300,',',310,'),encoding='utf-8')
    second=create(api,local_request())
    assert first!=second
    assert api.get(f'/api/v1/collections/{first}/timeseries').json()['data'][0]['value']==pytest.approx(26.85)
    assert api.get(f'/api/v1/collections/{second}/timeseries').json()['data'][0]['value']==pytest.approx(36.85)
    assert api.get(f'/api/v1/collections/{first}').json()['artifacts'][0]['sha256']!=api.get(f'/api/v1/collections/{second}').json()['artifacts'][0]['sha256']
    with sqlite3.connect(settings.climate_data_db_path) as db:
        db.execute('DELETE FROM climate_points')  # Isolated fixture: prove no fallback dependency.
    assert api.get(f'/api/v1/collections/{first}/timeseries').json()['count']==48


def test_active_duplicate_same_snapshot_and_edited_city_is_manual(collection):
    settings,_,_,service,_,_,api=collection
    request=DownloadRequest(**local_request())
    first,second=service.create_job(request),service.create_job(request)
    assert first.collection_id==second.collection_id
    edited=local_request()
    edited['location']=dict(latitude=21.,longitude=105.)
    third=service.create_job(DownloadRequest(**edited))
    assert third.collection_id!=first.collection_id
    m=service.collections.get(third.collection_id)['metadata']
    assert m['place_name']=='Tọa độ nhập tay'
    assert m['selected_place']==POINT


@pytest.mark.parametrize('problem,expected', [('empty_cell',23),('nan',23),('bad_time',23),('missing_column',0),('missing_hour',23),('conflict',24),('empty_file',0)])
def test_collection_quality_counts_match_snapshots_and_restart(collection,problem,expected):
    rows=land_rows()
    header='valid_time,t2m,d2m,u10,v10,tp,ssrd,latitude,longitude'
    if problem=='empty_cell': rows[0][1]=''
    if problem=='nan': rows[0][1]='NaN'
    if problem=='bad_time': rows[0][0]='bad'
    if problem=='missing_column':
        header=header.replace('t2m,',''); rows=[r[:1]+r[2:] for r in rows]
    if problem=='missing_hour': rows.pop(0)
    if problem=='conflict':
        row=rows[0].copy(); row[1]='999'; rows.append(row)
    text='' if problem=='empty_file' else header+'\n'+'\n'.join(','.join(r) for r in rows)
    job,api=collect(collection,'land',text)
    repo=CollectionRepository(collection[0].climate_data_db_path,read_only=True)
    entry=repo.get(job.job_id)
    assert entry['summary']['available_count']==expected
    assert entry['summary']['expected_count']==24
    assert entry['summary']['missing_count']==24-expected
    assert entry['status']==('failed' if not expected else 'complete' if expected==24 else 'partial')
    warnings=repo.issues(job.job_id)
    assert warnings['total']==job.quality.issue_count>0
    with sqlite3.connect(collection[0].climate_data_db_path) as db:
        count=db.execute('SELECT COUNT(*) FROM collection_observations WHERE collection_id=?',(job.job_id,)).fetchone()[0]
    assert count==entry['summary']['imported_values']==job.quality.imported_values
    if expected:
        response=api.get(f'/api/v1/collections/{job.job_id}/timeseries').json()
        assert response['count']==expected
        assert response['complete']==(expected==24)


def test_source_identity_never_mixed(collection):
    *_,api=collection
    land=create(api,dict(source='land',variable='temperature',location=POINT,**DATES))
    era5=create(api,dict(source='era5',variable='temperature',area=AREA,**DATES))
    a=api.get(f'/api/v1/collections/{land}/timeseries').json()
    b=api.get(f'/api/v1/collections/{era5}/timeseries').json()
    assert a['dataset']!=b['dataset']
    assert a['data'][0]['value']==26.85 and b['data'][0]['value']==16.85


def test_additive_migration_never_invents_old_collection(tmp_path):
    path=tmp_path/'legacy.sqlite3'
    repo=ClimateRepository(path)
    repo.save(dataset='old',variable='2m_temperature',requested_location=Location(latitude=10,longitude=106),grid_location=Location(latitude=10,longitude=106),unit='K',points=[ClimatePoint(time=datetime(2025,1,1),value=300)])
    with sqlite3.connect(path) as db: before=db.execute('SELECT * FROM climate_points').fetchall()
    collections=CollectionRepository(path)
    assert collections.list()==[]
    with sqlite3.connect(path) as db: assert db.execute('SELECT * FROM climate_points').fetchall()==before
    CollectionRepository(path)  # Idempotent migration.
    assert collections.list()==[]


def test_interrupted_worker_keeps_partial_snapshot_and_can_create_new_job(collection, monkeypatch):
    from backend.app.services.download_job_service import DownloadJobService
    from backend.app.storage.collection_repository import current_collection
    import backend.app.services.worker_identity as workers
    settings,writer,jobs,service,providers,_,api=collection
    request=DownloadRequest(**local_request())
    created=service.create_job(request)
    jobs.claim_running(created.job_id)
    service.collections.running(created.job_id)
    token=current_collection.set((created.job_id,settings.era5_land_dataset))
    try:
        writer.save(dataset=settings.era5_land_dataset,variable='2m_temperature',requested_location=Location(**POINT),
                    grid_location=Location(latitude=10.8,longitude=106.7),unit='K',
                    points=[ClimatePoint(time=datetime(2025,1,1),value=300)])
    finally: current_collection.reset(token)
    monkeypatch.setattr(workers,'process_identity',lambda pid:'absent')
    restarted=DownloadJobService(providers['mock'],jobs,provider_resolver=providers.__getitem__,settings=settings)
    entry=restarted.collections.get(created.collection_id)
    assert entry['status']=='interrupted' and entry['summary']['available_count']==1
    assert jobs.get(created.job_id).status=='failed'
    assert restarted.create_job(request).collection_id!=created.collection_id
    assert api.get(f'/api/v1/collections/{created.collection_id}/timeseries').json()['count']==1


def test_live_worker_is_not_declared_interrupted(collection):
    settings,_,jobs,service,providers,_,_=collection
    from backend.app.services.download_job_service import DownloadJobService
    created=service.create_job(DownloadRequest(**local_request()))
    jobs.claim_running(created.job_id)
    service.collections.running(created.job_id)
    restarted=DownloadJobService(providers['mock'],jobs,provider_resolver=providers.__getitem__,settings=settings)
    assert restarted.collections.get(created.job_id)['status']=='running'
    assert jobs.get(created.job_id).status=='running'


def test_finalized_snapshot_rejects_import_write(collection):
    from backend.app.storage.collection_repository import current_collection
    settings,writer,*rest=collection
    api=rest[-1]
    cid=create(api,local_request())
    token=current_collection.set((cid,settings.era5_land_dataset))
    try:
        with pytest.raises(ValueError,match='not open'):
            writer.save(dataset=settings.era5_land_dataset,variable='2m_temperature',requested_location=Location(**POINT),
                        grid_location=Location(latitude=10.8,longitude=106.7),unit='K',
                        points=[ClimatePoint(time=datetime(2025,1,1),value=999)])
    finally: current_collection.reset(token)
    assert api.get(f'/api/v1/collections/{cid}/timeseries').json()['data'][0]['value']==26.85


def test_identical_grid_time_variable_stays_isolated_between_datasets(collection,monkeypatch):
    import xarray as xr
    import numpy as np
    settings,writer,jobs,service,providers,cds,api=collection
    original=cds.retrieve
    def retrieve(dataset,request,target):
        if dataset==settings.era5_land_dataset: return original(dataset,request,target)
        times=np.arange(np.datetime64('2025-01-01T00'),np.datetime64('2025-01-02T00'),np.timedelta64(1,'h'))
        xr.Dataset({'t2m':(('valid_time','latitude','longitude'),np.full((24,1,1),280.))},
                   coords={'valid_time':times,'latitude':[10.8],'longitude':[106.7]}).to_netcdf(target,engine='scipy')
    monkeypatch.setattr(cds,'retrieve',retrieve)
    land=create(api,dict(source='land',variable='temperature',location=POINT,**DATES))
    era5=create(api,dict(source='era5',variable='temperature',area=AREA,**DATES))
    a=api.get(f'/api/v1/collections/{land}/timeseries').json()
    b=api.get(f'/api/v1/collections/{era5}/timeseries').json()
    assert a['grid_location']==b['grid_location']
    assert a['data'][0]['time']==b['data'][0]['time']
    assert a['data'][0]['value']==26.85 and b['data'][0]['value']==6.85


@pytest.mark.parametrize('frequency,count',[('hourly',48),('daily',2),('monthly',1),('yearly',1)])
def test_collection_aggregation_four_frequencies(collection,frequency,count):
    api=collection[-1]
    cid=create(api,local_request())
    response=api.get(f'/api/v1/collections/{cid}/aggregate',params={'frequency':frequency}).json()
    assert len(response['data'])==count
    assert sum(p['count'] for p in response['data'])==48
    assert response['collection_id']==cid and response['available_count']==48


def test_missing_snapshot_does_not_fall_back_to_populated_cache(collection,monkeypatch):
    settings,writer,jobs,service,providers,cds,api=collection
    cid=create(api,local_request())
    with sqlite3.connect(settings.climate_data_db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM climate_points').fetchone()[0]>0
        db.execute('DELETE FROM collection_observations WHERE collection_id=?',(cid,))
    def forbidden(*args,**kwargs): raise AssertionError('No CDS on collection reads')
    monkeypatch.setattr(cds,'retrieve',forbidden)
    result=api.get(f'/api/v1/collections/{cid}/timeseries').json()
    assert result['count']==0 and result['available_count']==0 and result['coverage']==0
    assert not result['complete'] and result['missing_ranges']


def test_cross_chunk_duplicate_warns_and_keeps_first(collection):
    from backend.app.domain.quality import current_quality, QualityCollector
    from backend.app.storage.collection_repository import current_collection
    settings,writer,jobs,service,*_=collection
    created=service.create_job(DownloadRequest(**local_request()))
    service.collections.running(created.job_id)
    quality=QualityCollector(lambda issues:jobs.add_quality_issues(created.job_id,issues))
    q=current_quality.set(quality)
    token=current_collection.set((created.job_id,settings.era5_land_dataset))
    try:
        for value in [300,999]:
            writer.save(dataset=settings.era5_land_dataset,variable='2m_temperature',requested_location=Location(**POINT),
                        grid_location=Location(latitude=10.8,longitude=106.7),unit='K',
                        points=[ClimatePoint(time=datetime(2025,1,1),value=value)])
    finally:
        current_collection.reset(token)
        current_quality.reset(q)
    summary=service._finish_collection(created.job_id,quality)
    assert summary['imported_values']==1
    assert summary['quality']['imported_values']==1
    assert summary['quality']['issue_counts']['duplicate_conflict']==1
    reader=CollectionService(StoredClimateService(ClimateRepository(settings.climate_data_db_path,read_only=True),settings))
    assert reader.query(created.job_id)['data'][0]['value']==26.85
