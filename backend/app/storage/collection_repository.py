"""Additive, immutable collection snapshots alongside the legacy mutable cache.

Import writes use the SAME transaction/connection as climate_points. Readers never
instantiate providers or copy observations from the legacy cache.
"""
from contextvars import ContextVar
from datetime import datetime, time, UTC
from hashlib import sha256
import json
from pathlib import Path
import os

from backend.app.storage.climate_repository import ClimateRepository
from backend.app.domain.time_utils import to_utc_naive, hourly_timestamps
from backend.app.domain.wind import WIND_COMPONENTS, DERIVED_WIND_UNITS

current_collection = ContextVar('immutable_collection', default=None)
PARSER_VERSION = 'collection-snapshot-v1'


def stamp(value):
    return to_utc_naive(value).isoformat(timespec='microseconds')


def capture(connection, records):
    """Called before the mutable cache upsert, in its transaction. First value wins."""
    context = current_collection.get()
    if context is None:
        return
    collection_id, dataset = context
    for record in records:
        # Normalize binary float noise only in the new snapshot, preserving legacy cache keys.
        record = (*record[:2], round(record[2],10), round(record[3],10), *record[4:])
        if record[0] != dataset:
            raise ValueError('Collection import dataset mismatch.')
        state = connection.execute('SELECT status FROM collections WHERE id=?', (collection_id,)).fetchone()
        if state is None or state[0] != 'running':
            raise ValueError('Collection snapshot is not open for import.')
        previous = connection.execute('''SELECT unit,value FROM collection_observations WHERE
            collection_id=? AND dataset=? AND variable=? AND grid_latitude=? AND grid_longitude=? AND time_utc=?''',
            (collection_id,*record[:5])).fetchone()
        if previous is not None:
            from backend.app.domain.quality import current_quality
            quality = current_quality.get()
            if quality:
                quality.issue('duplicate' if tuple(previous)==tuple(record[5:]) else 'duplicate_conflict',
                              'Repeated observation across imported chunks; first collection value retained.',
                              variable=record[1],latitude=record[2],longitude=record[3],
                              time_utc=datetime.fromisoformat(record[4]))
        connection.execute('INSERT OR IGNORE INTO collection_observations VALUES (?,?,?,?,?,?,?,?)',
                           (collection_id, *record))


class CollectionRepository(ClimateRepository):
    def __init__(self, db_path, *, read_only=False):
        super().__init__(db_path, read_only=read_only)
        if read_only:
            return
        with self._connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL, metadata TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
                    summary TEXT NOT NULL DEFAULT '{}', error TEXT);
                CREATE TABLE IF NOT EXISTS collection_variables (
                    collection_id TEXT NOT NULL, variable TEXT NOT NULL, role TEXT NOT NULL,
                    PRIMARY KEY(collection_id,variable));
                CREATE TABLE IF NOT EXISTS collection_observations (
                    collection_id TEXT NOT NULL, dataset TEXT NOT NULL, variable TEXT NOT NULL,
                    grid_latitude REAL NOT NULL, grid_longitude REAL NOT NULL, time_utc TEXT NOT NULL,
                    unit TEXT NOT NULL, value REAL NOT NULL,
                    PRIMARY KEY(collection_id,dataset,variable,grid_latitude,grid_longitude,time_utc));
                CREATE TABLE IF NOT EXISTS collection_artifacts (
                    collection_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL, imported_at TEXT NOT NULL, parser_version TEXT NOT NULL,
                    PRIMARY KEY(collection_id,path));
                CREATE TABLE IF NOT EXISTS collection_quality_issues (
                    collection_id TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(collection_id,ordinal));
                CREATE TABLE IF NOT EXISTS collection_grids (
                    collection_id TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
                    PRIMARY KEY(collection_id,latitude,longitude));
            ''')
            columns={r[1] for r in c.execute('PRAGMA table_info(collections)')}
            for name,kind in [('worker_pid','INTEGER'),('worker_identity','TEXT')]:
                if name not in columns:
                    c.execute(f'ALTER TABLE collections ADD COLUMN {name} {kind}')

    def create(self, job, request, canonical):
        a = request.requested_start or datetime.combine(request.start_date, time.min, UTC)
        b = request.requested_end or datetime.combine(request.end_date, time(hour=23), UTC)
        metadata = dict(request.model_dump(mode='json'), dataset=job.dataset, source=job.source,
                        primary_variable=canonical, requested_start=a.isoformat(), requested_end=b.isoformat(),
                        requested_start_utc=stamp(a), requested_end_utc=stamp(b),
                        place_name=request.place_name or ('Tọa độ nhập tay' if request.location else 'Vùng tọa độ'),
                        retrieval_variables=job.retrieval_variables, parser_version=PARSER_VERSION)
        names = {'2m_temperature':'Nhiệt độ','2m_dewpoint_temperature':'Điểm sương',
                 'total_precipitation':'Lượng mưa','10m_u_component_of_wind':'Gió thành phần u',
                 '10m_v_component_of_wind':'Gió thành phần v','wind_speed':'Tốc độ gió',
                 'wind_direction':'Hướng gió','surface_solar_radiation_downwards':'Bức xạ mặt trời'}
        metadata['name'] = f"{names.get(canonical,canonical)} — {metadata['place_name']} — {a:%d/%m/%Y} đến {b:%d/%m/%Y} ({request.request_timezone})"
        components = WIND_COMPONENTS if canonical in DERIVED_WIND_UNITS else [canonical]
        from backend.app.services.worker_identity import process_identity
        with self._connect() as c:
            c.execute('INSERT OR IGNORE INTO collections(id,job_id,metadata,status,created_at,worker_pid,worker_identity) VALUES (?,?,?,?,?,?,?)',
                      (job.job_id,job.job_id,json.dumps(metadata,ensure_ascii=False),'queued',job.created_at.isoformat(),os.getpid(),process_identity(os.getpid())))
            c.executemany('INSERT OR IGNORE INTO collection_variables VALUES (?,?,?)',
                          [(job.job_id,v,'requested' if v in components else 'additional') for v in job.retrieval_variables])

    def get(self, collection_id):
        if not self._db_path.exists():
            return None
        with self._connect() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='collections'").fetchone():
                return None
            row = c.execute('SELECT * FROM collections WHERE id=?',(collection_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result['metadata'] = json.loads(result['metadata'])
            result['summary'] = json.loads(result['summary'])
            result['variables'] = [dict(r) for r in c.execute('SELECT variable,role FROM collection_variables WHERE collection_id=? ORDER BY role DESC,variable',(collection_id,))]
            result['grids'] = [dict(r) for r in c.execute('SELECT latitude,longitude FROM collection_grids WHERE collection_id=? ORDER BY latitude,longitude',(collection_id,))]
            result['artifacts'] = [dict(r) for r in c.execute('SELECT path,sha256,bytes,imported_at,parser_version FROM collection_artifacts WHERE collection_id=?',(collection_id,))]
            return result

    def delete(self, collection_id, jobs_path):
        """Remove one finished snapshot and its job in one SQLite transaction.

        Source files and the shared compatibility cache may serve other collections.
        They are deliberately outside this operation.
        """
        with self._connect() as c:
            jobs_schema = None
            if Path(jobs_path).is_file():
                if Path(jobs_path).resolve() == self._db_path.resolve():
                    jobs_schema = 'main'
                else:
                    c.execute('ATTACH DATABASE ? AS jobs_db', (str(Path(jobs_path).resolve()),))
                    jobs_schema = 'jobs_db'
            c.execute('BEGIN IMMEDIATE')
            entry = c.execute('SELECT job_id,status FROM collections WHERE id=?', (collection_id,)).fetchone()
            if entry is None:
                raise LookupError('Bộ dữ liệu không còn tồn tại.')
            if entry['status'] in {'queued', 'running'}:
                raise ValueError('Không thể xóa bộ dữ liệu đang chờ hoặc đang thu thập.')
            job_id = entry['job_id']
            if jobs_schema:
                job = c.execute(f'SELECT status FROM {jobs_schema}.download_jobs WHERE job_id=?', (job_id,)).fetchone()
                if job and job['status'] in {'queued', 'running'}:
                    raise ValueError('Không thể xóa khi tác vụ vẫn đang chờ hoặc đang chạy.')
                c.execute(f'DELETE FROM {jobs_schema}.download_quality_issues WHERE job_id=?', (job_id,))
                c.execute(f'DELETE FROM {jobs_schema}.download_jobs WHERE job_id=?', (job_id,))
            deleted = {}
            for table in ['collection_observations', 'collection_variables', 'collection_grids',
                          'collection_artifacts', 'collection_quality_issues']:
                deleted[table] = c.execute(f'DELETE FROM {table} WHERE collection_id=?', (collection_id,)).rowcount
            c.execute('DELETE FROM collections WHERE id=?', (collection_id,))
        return dict(collection_id=collection_id, job_id=job_id, deleted=deleted,
                    source_files_preserved=True)

    def list(self, search='', limit=100, offset=0):
        if not self._db_path.exists():
            return []
        with self._connect() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='collections'").fetchone():
                return []
            # One connection and batched related rows, rather than reopening SQLite per card.
            rows = c.execute('SELECT * FROM collections ORDER BY created_at DESC,id').fetchall()
            rows = [r for r in rows if search.casefold() in (r['metadata']+' '+r['created_at']).casefold()][offset:offset+limit]
            result = []
            for row in rows:
                entry = dict(row)
                entry['metadata'] = json.loads(entry['metadata'])
                entry['summary'] = json.loads(entry['summary'])
                entry.update(variables=[], grids=[], artifacts=[])
                result.append(entry)
            if not result:
                return result
            by_id = {entry['id']: entry for entry in result}
            placeholders = ','.join('?' for _ in result)
            for table, name, fields, order in [
                ('collection_variables', 'variables', 'variable,role', 'role DESC,variable'),
                ('collection_grids', 'grids', 'latitude,longitude', 'latitude,longitude'),
                ('collection_artifacts', 'artifacts', 'path,sha256,bytes,imported_at,parser_version', 'path'),
            ]:
                related = c.execute(f'SELECT collection_id,{fields} FROM {table} WHERE collection_id IN ({placeholders}) ORDER BY {order}', tuple(by_id))
                for row in related:
                    item = dict(row)
                    by_id[item.pop('collection_id')][name].append(item)
            return result

    def running(self, collection_id):
        from backend.app.services.worker_identity import process_identity
        with self._connect() as c:
            c.execute("UPDATE collections SET status='running',worker_pid=?,worker_identity=? WHERE id=? AND status='queued'",
                      (os.getpid(),process_identity(os.getpid()),collection_id))

    def abandoned(self):
        from backend.app.services.worker_identity import process_identity
        with self._connect() as c:
            rows=c.execute("SELECT id,worker_pid,worker_identity FROM collections WHERE status IN ('queued','running')").fetchall()
        return [r['id'] for r in rows if r['worker_pid'] and r['worker_identity'] and
                (identity := process_identity(r['worker_pid'])) is not None and identity != r['worker_identity']]

    def interrupted(self, collection_id):
        with self._connect() as c:
            c.execute("UPDATE collections SET status='interrupted' WHERE id=?",(collection_id,))

    def artifact(self, collection_id, path):
        path = Path(path)
        if not path.is_file():
            return
        digest = sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):
                digest.update(chunk)
        with self._connect() as c:
            c.execute('INSERT OR IGNORE INTO collection_artifacts VALUES (?,?,?,?,?,?)',
                      (collection_id,str(path.resolve()),digest.hexdigest(),path.stat().st_size,datetime.now(UTC).isoformat(),PARSER_VERSION))

    def finish(self, collection_id, issues, quality, error=None):
        entry = self.get(collection_id)
        if entry is None:
            return None
        m = entry['metadata']
        primary = m['primary_variable']
        components = WIND_COMPONENTS if primary in DERIVED_WIND_UNITS else [primary]
        expected_hours = len(hourly_timestamps(datetime.fromisoformat(m['requested_start_utc']),datetime.fromisoformat(m['requested_end_utc'])))
        with self._connect() as c:
            counts = c.execute('''SELECT variable,COUNT(*) AS count FROM collection_observations
                WHERE collection_id=? GROUP BY variable''',(collection_id,)).fetchall()
            grids = c.execute('SELECT latitude,longitude FROM collection_grids WHERE collection_id=?',(collection_id,)).fetchall()
            valid = 0
            for grid in grids:
                sets = []
                for variable in components:
                    sets.append({r[0] for r in c.execute('''SELECT time_utc FROM collection_observations
                        WHERE collection_id=? AND variable=? AND grid_latitude=? AND grid_longitude=?
                        AND time_utc>=? AND time_utc<=?''',(collection_id,variable,*grid,m['requested_start_utc'],m['requested_end_utc']))})
                valid += len(set.intersection(*sets))
            expected = expected_hours * (len(grids) if grids else 1)
            summary = dict(expected_count=expected, available_count=valid, missing_count=expected-valid,
                           coverage=valid/expected if expected else 0, primary_variable=primary,
                           expected_hours_per_grid=expected_hours, grid_count=len(grids),
                           imported_values=sum(r['count'] for r in counts), counts_by_variable={r['variable']:r['count'] for r in counts},
                           quality=quality)
            summary['grid_coordinates_verified'] = not any(i.get('code')=='missing_coordinates' for i in issues)
            state = 'failed' if error or not valid else 'complete' if valid==expected else 'partial'
            c.execute('UPDATE collections SET status=?,finished_at=?,summary=?,error=? WHERE id=?',
                      (state,datetime.now(UTC).isoformat(),json.dumps(summary),error,collection_id))
            c.executemany('INSERT OR REPLACE INTO collection_quality_issues VALUES (?,?,?)',
                          [(collection_id,i,json.dumps(issue,ensure_ascii=False)) for i,issue in enumerate(issues)])
        return summary

    def issues(self, collection_id, limit=500, offset=0):
        with self._connect() as c:
            total=c.execute('SELECT COUNT(*) FROM collection_quality_issues WHERE collection_id=?',(collection_id,)).fetchone()[0]
            rows=c.execute('SELECT payload FROM collection_quality_issues WHERE collection_id=? ORDER BY ordinal LIMIT ? OFFSET ?', (collection_id,limit,offset)).fetchall()
        return dict(total=total,issues=[json.loads(r[0]) for r in rows],limit=limit,offset=offset)


def record_artifact(path, db_path):
    context = current_collection.get()
    if context:
        CollectionRepository(db_path).artifact(context[0],path)


def recover_abandoned(collections, jobs):
    """Reconcile dead workers at startup without constructing any data provider."""
    abandoned = set(collections.abandoned()) | set(jobs.abandoned_ids())
    for collection_id in abandoned:
        message = 'Collection worker exited before completion; retained observations have not been discarded. Submit a new collection to retry.'
        job = jobs.get(collection_id)
        if job is None:
            continue
        issues=[]
        while True:
            page=jobs.quality_issues(collection_id,len(issues),500)
            issues.extend(page['issues'])
            if len(issues)>=page['total']:
                break
        if job.quality:
            job.quality.complete=False
            job.quality.status='failed'
            jobs.save_quality(collection_id,job.quality)
        if collections.get(collection_id):
            collections.finish(collection_id,issues,job.quality.model_dump(mode='json') if job.quality else None,error=message)
            collections.interrupted(collection_id)
        jobs.mark_failed(collection_id,message)
