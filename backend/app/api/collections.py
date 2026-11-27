from typing import Annotated, Literal
import csv
from io import StringIO
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, StrictBool, field_validator

from backend.app.core.user_context import current_user_id
from backend.app.domain.live_refresh import DEFAULT_LIVE_INTERVAL_HOURS
from backend.app.services.dependencies import get_auth_service, get_stored_climate_service
from backend.app.services.collection_service import CollectionService
from backend.app.services.live_refresh_service import collection_lock, refresh_collection, require_open_meteo
from backend.app.storage.auth_repository import LiveScheduleLimitError

router = APIRouter(prefix='/api/v1/collections', tags=['collections (SQLite only)'])

def service(stored=Depends(get_stored_climate_service)):
    return CollectionService(stored)

Service = Annotated[CollectionService, Depends(service)]


class LiveScheduleUpdate(BaseModel):
    enabled: StrictBool
    interval_hours: Literal[1, 3, 6, 24] = DEFAULT_LIVE_INTERVAL_HOURS

    @field_validator("interval_hours", mode="before")
    @classmethod
    def require_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Chu kỳ cập nhật phải là số nguyên.")
        return value


def live_user_id():
    user_id = current_user_id.get()
    if user_id is None:
        raise HTTPException(401, "Cần đăng nhập để cập nhật bộ dữ liệu.")
    return user_id


LiveUser = Annotated[int, Depends(live_user_id)]


@router.get('')
def listing(s: Service, search: str='', limit: int=Query(100,ge=1,le=500), offset: int=Query(0,ge=0),
            pinned_ids: Annotated[list[str] | None, Query()] = None):
    return dict(collections=s.repository.list(search, limit, offset, pinned_ids=pinned_ids),
                limit=limit, offset=offset)


@router.get('/live')
def list_live(user_id: LiveUser):
    return {"schedules": get_auth_service()._repository.list_live_schedules(int(time.time()), user_id=user_id)}

@router.get('/{collection_id}')
def detail(collection_id: str, s: Service):
    try: return s.get(collection_id)
    except LookupError as exc: raise HTTPException(404,str(exc)) from exc


@router.delete('/{collection_id}')
def delete(collection_id: str, s: Service):
    try:
        user_id = current_user_id.get()
        with collection_lock(user_id, collection_id):
            result = s.delete(collection_id)
            if user_id is not None:
                get_auth_service()._repository.delete_live_schedule(user_id, collection_id)
            return result
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/{collection_id}/live')
def get_live(collection_id: str, user_id: LiveUser, s: Service):
    try:
        require_open_meteo(s.repository, collection_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return get_auth_service()._repository.get_live_schedule(user_id, collection_id, int(time.time()))


@router.put('/{collection_id}/live')
def put_live(collection_id: str, body: LiveScheduleUpdate, user_id: LiveUser, s: Service):
    try:
        with collection_lock(user_id, collection_id):
            entry = require_open_meteo(s.repository, collection_id)
            return get_auth_service()._repository.update_live_schedule(
                user_id, collection_id, entry['metadata']['place_name'],
                body.enabled, body.interval_hours, int(time.time()),
            )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LiveScheduleLimitError as exc:
        raise HTTPException(409, "Mỗi tài khoản chỉ có thể bật tối đa 5 bộ dữ liệu.") from exc


@router.post('/{collection_id}/refresh')
def refresh(collection_id: str, user_id: LiveUser, automatic: bool = False):
    try:
        return refresh_collection(get_auth_service()._repository, user_id, collection_id, automatic=automatic)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/{collection_id}/quality')
def quality(collection_id: str, s: Service, limit: int=Query(500,ge=1,le=500), offset: int=Query(0,ge=0)):
    entry=detail(collection_id,s)
    return dict(collection_id=collection_id,summary=entry['summary'],**s.repository.issues(collection_id,limit,offset))

@router.get('/{collection_id}/{operation}')
def query(collection_id: str, operation: Literal['timeseries','aggregate','statistics','trend','heatmap','map','timeseries.csv'],
          s: Service, variable: str|None=None, latitude: float|None=None, longitude: float|None=None,
          start: str|None=None, end: str|None=None,
          frequency: Literal['hourly','daily','monthly','yearly']='hourly',
          aggregation: Literal['auto','mean','min','max','sum']='auto'):
    try:
        payload=s.query(collection_id,'timeseries' if operation=='timeseries.csv' else operation,
                        variable,latitude,longitude,start,end,frequency,aggregation)
    except LookupError as exc: raise HTTPException(404,str(exc)) from exc
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    if operation!='timeseries.csv': return payload
    stream=StringIO(newline='')
    fields=['collection_id','place_name','source','dataset','variable','unit','grid_latitude','grid_longitude','time','value','coverage']
    writer=csv.DictWriter(stream,fieldnames=fields)
    writer.writeheader()
    for point in payload['data']:
        writer.writerow({**{k:payload[k] for k in fields if k in payload},
                         'grid_latitude':payload['grid_location']['latitude'],
                         'grid_longitude':payload['grid_location']['longitude'],**point})
    return Response(stream.getvalue().encode('utf-8-sig'),media_type='text/csv',
                    headers={'Content-Disposition':f'attachment; filename="collection_{collection_id}.csv"'})
