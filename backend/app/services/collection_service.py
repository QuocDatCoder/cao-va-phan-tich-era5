"""Exact collection reads: no nearest-cache fallback and no provider invocation."""
from datetime import datetime
from backend.app.domain.schemas import ClimatePoint, Location, TimeSeriesRequest
from backend.app.storage.collection_repository import CollectionRepository, stamp
from backend.app.services.stored_climate_service import StoredClimateService
from backend.app.domain.wind import DERIVED_WIND_UNITS, WIND_COMPONENTS


class SnapshotReader:
    def __init__(self, repository, collection_id, grid):
        self.repository, self.collection_id, self.grid = repository, collection_id, grid

    def read_available(self, *, dataset, variable, location, unit, start, end):
        with self.repository._connect() as c:
            rows = c.execute('''SELECT time_utc,value FROM collection_observations
                WHERE collection_id=? AND dataset=? AND variable=? AND grid_latitude=? AND grid_longitude=?
                AND unit=? AND time_utc>=? AND time_utc<=? ORDER BY time_utc''',
                (self.collection_id,dataset,variable,self.grid.latitude,self.grid.longitude,unit,stamp(start),stamp(end))).fetchall()
        return self.grid, [ClimatePoint(time=datetime.fromisoformat(r[0]),value=r[1]) for r in rows]


class CollectionService:
    def __init__(self, stored):
        self.settings = stored.settings
        self.repository = CollectionRepository(stored.repository._db_path, read_only=True)

    def get(self, collection_id):
        entry = self.repository.get(collection_id)
        if entry is None:
            raise LookupError('Collection not found; historical cache is not a collection.')
        return entry

    def delete(self, collection_id):
        self.get(collection_id)
        writer = CollectionRepository(self.repository._db_path)
        return writer.delete(collection_id, self.settings.job_metadata_db_path)

    def query(self, collection_id, operation='timeseries', variable=None, latitude=None, longitude=None,
              start=None, end=None, frequency='hourly', aggregation='auto'):
        entry = self.get(collection_id)
        m = entry['metadata']
        variable = variable or m['primary_variable']
        allowed = {v['variable'] for v in entry['variables']} | {m['primary_variable']}
        if variable in DERIVED_WIND_UNITS and set(WIND_COMPONENTS).issubset(allowed):
            allowed.add(variable)
        if variable not in allowed:
            raise ValueError('Variable is not part of this collection.')
        grids = entry['grids']
        if latitude is None and longitude is None and len(grids)==1:
            latitude,longitude = grids[0]['latitude'],grids[0]['longitude']
        if {'latitude':latitude,'longitude':longitude} not in grids:
            raise ValueError('Select an actual grid from this collection.')
        a,b = datetime.fromisoformat(start or m['requested_start']),datetime.fromisoformat(end or m['requested_end'])
        if stamp(a)<m['requested_start_utc'] or stamp(b)>m['requested_end_utc'] or stamp(a)>stamp(b):
            raise ValueError('Query interval must lie within the original collection request.')
        grid = Location(latitude=latitude,longitude=longitude)
        location = Location(**m['location']) if m.get('location') else grid
        request = TimeSeriesRequest(variable=variable,location=location,start=a,end=b)
        # Recorded dataset remains authoritative even when deployment config changes.
        service = StoredClimateService(SnapshotReader(self.repository,collection_id,grid),self.settings,
                                       recorded_dataset=m['dataset'])
        payload = service.query(operation,m['source'],request,frequency,aggregation)
        if operation == 'map':
            payload['data'] = [dict(point, latitude=grid.latitude, longitude=grid.longitude)
                               for point in payload.get('data', [])]
        return dict(payload,collection_id=collection_id,place_name=m['place_name'],
                    collection_status=entry['status'],collection_quality=entry['summary'].get('quality'),
                    requested_start=a.isoformat(),requested_end=b.isoformat())
