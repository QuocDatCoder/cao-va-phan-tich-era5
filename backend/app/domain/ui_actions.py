"""Read-only UI navigation contract, shared by API and frontend."""
from datetime import datetime,timezone
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator

ViewKind=Literal['timeseries','heatmap','map','statistics']
VIEW_LABELS={'timeseries':'Chuỗi thời gian','heatmap':'Bản đồ nhiệt','map':'Không gian (điểm)','statistics':'Thống kê và xu hướng'}


class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)


class ViewParameters(Contract):
    variable: str=Field(min_length=1,max_length=100)
    latitude: float=Field(ge=-90,le=90)
    longitude: float=Field(ge=-180,le=180)
    start: str=Field(max_length=64)
    end: str=Field(max_length=64)
    frequency: Literal['hourly','daily','monthly','yearly']='hourly'
    aggregation: Literal['auto']='auto'

    @field_validator('start','end')
    @classmethod
    def timestamp(cls,value):
        stamp=datetime.fromisoformat(value)
        if stamp.utcoffset() is None or stamp.minute or stamp.second or stamp.microsecond:
            raise ValueError('Use an hourly timestamp with explicit timezone.')
        try:
            utc=stamp.astimezone(timezone.utc)
        except OverflowError:
            raise ValueError('Timestamp is outside the supported range.') from None
        if utc.minute or utc.second or utc.microsecond:
            raise ValueError('Timestamp must also align with an actual UTC observation hour.')
        return value


class UIAction(Contract):
    type: Literal['find_data','open_data','open_chart']
    collection_id: str | None=Field(default=None,min_length=1,max_length=128)
    parameters: ViewParameters | None=None
    view_kind: ViewKind='timeseries'
    search: str | None=Field(default=None,max_length=200)

    @model_validator(mode='after')
    def combination(self):
        if self.type=='find_data':
            if self.collection_id is not None or self.parameters is not None or self.search is None:
                raise ValueError('Search actions only accept search text.')
        elif self.collection_id is None or self.search is not None:
            raise ValueError('Collection actions need an exact ID and no search.')
        if self.type=='open_data' and self.view_kind!='timeseries':
            raise ValueError('Data table uses timeseries.')
        if self.type=='open_chart' and self.view_kind=='heatmap' and self.parameters and self.parameters.frequency!='monthly':
            raise ValueError('Monthly heatmaps require monthly frequency; use timeseries for daily charts.')
        return self


class AppliedView(Contract):
    page: Literal['stored','chart']
    collection_id: str=Field(min_length=1,max_length=128)
    view_kind: ViewKind | Literal['monthly_climate']='timeseries'
    parameters: ViewParameters | None=None


class ComparisonView(Contract):
    collection_id_a: str=Field(min_length=1,max_length=128)
    collection_id_b: str=Field(min_length=1,max_length=128)
    parameters_a: ViewParameters
    parameters_b: ViewParameters
    align_by: Literal['time','elapsed']='time'


class PrepareView(Contract):
    target: Literal['open_data','open_chart']
    collection_id: str | None=Field(default=None,min_length=1,max_length=128)
    variable: str | None=Field(default=None,min_length=1,max_length=100)
    latitude: float | None=Field(default=None,ge=-90,le=90)
    longitude: float | None=Field(default=None,ge=-180,le=180)
    start: str | None=Field(default=None,max_length=64)
    end: str | None=Field(default=None,max_length=64)
    frequency: Literal['hourly','daily','monthly','yearly'] | None=None
    view_kind: ViewKind=Field(default='timeseries',description='Use timeseries for temperature (nhiệt độ) over time by hour/day/month/year. Use heatmap ONLY for an explicit heatmap/bản đồ nhiệt request; heatmap is monthly.')

    @field_validator('start','end')
    @classmethod
    def timestamp(cls,value):
        return ViewParameters.timestamp(value) if value is not None else None


def operation_for(action: UIAction):
    if action.type=='open_data':
        return 'timeseries'
    if action.view_kind=='timeseries':
        return 'timeseries' if action.parameters.frequency=='hourly' else 'aggregate'
    return {'heatmap':'heatmap','map':'map','statistics':'statistics'}[action.view_kind]
