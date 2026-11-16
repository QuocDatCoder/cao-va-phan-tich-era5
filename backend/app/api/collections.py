from typing import Annotated, Literal
import csv
from io import StringIO
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from backend.app.services.dependencies import get_stored_climate_service
from backend.app.services.collection_service import CollectionService

router = APIRouter(prefix='/api/v1/collections', tags=['collections (SQLite only)'])

def service(stored=Depends(get_stored_climate_service)):
    return CollectionService(stored)

Service = Annotated[CollectionService, Depends(service)]

@router.get('')
def listing(s: Service, search: str='', limit: int=Query(100,ge=1,le=500), offset: int=Query(0,ge=0)):
    return dict(collections=s.repository.list(search,limit,offset),limit=limit,offset=offset)

@router.get('/{collection_id}')
def detail(collection_id: str, s: Service):
    try: return s.get(collection_id)
    except LookupError as exc: raise HTTPException(404,str(exc)) from exc


@router.delete('/{collection_id}')
def delete(collection_id: str, s: Service):
    try:
        return s.delete(collection_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

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
