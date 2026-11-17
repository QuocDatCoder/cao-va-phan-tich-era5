"""Synthetic snapshots for repeatable Agent evaluation, never the user's database."""
from datetime import datetime, timedelta, UTC
import json
from types import SimpleNamespace

from backend.app.core.config import Settings
from backend.app.domain.schemas import DownloadRequest
from backend.app.storage.collection_repository import CollectionRepository, stamp


def create_fixture(root):
    root.mkdir(parents=True, exist_ok=False)
    settings = Settings(_env_file=None, climate_provider='mock',data_dir=root,
                        climate_data_db_path=root/'climate.sqlite',job_metadata_db_path=root/'jobs.sqlite')
    repository = CollectionRepository(settings.climate_data_db_path)
    start=datetime.fromisoformat('2025-01-01T00:00:00+07:00')
    end=start+timedelta(hours=23)
    for cid, place, warning, count in [
        ('fixture-hanoi','Hà Nội fixture',False,24),
        ('fixture-empty','Bộ rỗng fixture',False,0),
        ('fixture-warning','Bộ cảnh báo fixture',True,24),
    ]:
        request=DownloadRequest(source='mock',variable='temperature',place_name=place,
            location=dict(latitude=21,longitude=105),selected_place=dict(latitude=21,longitude=105),start_date=start.astimezone(UTC).date(),end_date=end.astimezone(UTC).date(),
            requested_start=start,requested_end=end,request_timezone='UTC+7')
        job=SimpleNamespace(job_id=cid,dataset='mock',source='mock',
                            retrieval_variables=['2m_temperature'],created_at=datetime.now(UTC))
        repository.create(job,request,'2m_temperature')
        quality=dict(status='warnings' if warning else 'passed',issue_count=int(warning),
                     issue_counts={'suspicious_value':1} if warning else {})
        with repository._connect() as db:
            db.execute('UPDATE collections SET status=?,summary=? WHERE id=?',
                ('completed',json.dumps(dict(quality=quality,coverage=count/24,available_count=count,expected_count=24)),cid))
            db.execute('INSERT INTO collection_grids VALUES (?,?,?)',(cid,21,105))
            for i in range(count):
                db.execute('INSERT INTO collection_observations VALUES (?,?,?,?,?,?,?,?)',
                    (cid,'mock','2m_temperature',21,105,stamp(start+timedelta(hours=i)),'K',25 if warning else 300))
            if warning:
                db.execute('INSERT INTO collection_quality_issues VALUES (?,?,?)',
                    (cid,0,json.dumps(dict(code='suspicious_value',severity='warning',variable='2m_temperature',
                                         message='Synthetic suspicious temperature unit.'))))
    return settings
