"""Resolve UI proposals against fresh DB metadata. Never writes or calls CDS."""
from datetime import datetime,timedelta,timezone

from backend.app.domain.ui_actions import PrepareView,UIAction,ViewParameters
from backend.app.domain.public_variables import PUBLIC_VARIABLES


def resolve_action(tools,action: UIAction):
    if action.type=='find_data':
        return dict(status='ok',action=action.model_dump(mode='json'))
    if action.parameters and action.parameters.variable not in PUBLIC_VARIABLES:
        return dict(status='invalid_arguments',message='Chỉ hỗ trợ 6 biến chính: nhiệt độ, lượng mưa, điểm sương, tốc độ gió, hướng gió và bức xạ mặt trời. Gió u/v là dữ liệu bổ sung nội bộ.')
    info=tools.execute('describe_collection',dict(collection_id=action.collection_id))
    if info['status']!='ok':
        return info
    entry=info['data']
    if action.parameters is None:
        return dict(status='ok',action=action.model_dump(mode='json'),name=entry['name'])
    params=action.parameters.model_dump()
    if action.type=='open_data':
        params['frequency']='hourly'
    elif action.view_kind=='heatmap':
        params['frequency']='monthly'
    coverage=tools.execute('analyze_collection',dict(collection_id=action.collection_id,operation='coverage',
        **{k:v for k,v in params.items() if k!='aggregation'}))
    if coverage['status'] not in {'ok','partial','no_data','undefined'}:
        return coverage
    metadata=coverage.get('metadata') or {}
    if not metadata.get('variable'):
        return coverage
    quality=metadata.get('collection_quality') or {}
    variable=metadata.get('variable')
    if action.type=='open_chart':
        if variable in {'2m_temperature','2m_dewpoint_temperature'} and quality.get('issue_counts',{}).get('suspicious_value',0):
            return dict(status='quality_review_required',message='Bộ có nhiệt độ/điểm sương nghi vấn đơn vị. Xem chất lượng trước khi vẽ; biểu đồ cũ được giữ nguyên.')
        if not metadata.get('count'):
            return dict(status='no_data',message='Không có giá trị xác định trong khoảng này để vẽ. Kết quả cũ được giữ nguyên.')
    params['variable']=variable
    # Canonical local timestamps; no rounding or extending the requested interval.
    local=timezone(timedelta(hours=7))
    for edge in ['start','end']:
        params[edge]=datetime.fromisoformat(params[edge]).astimezone(local).isoformat()
    normalized=action.model_copy(update={'parameters':ViewParameters(**params)})
    return dict(status='ok',action=normalized.model_dump(mode='json'),name=entry['name'],
                metadata=metadata,provenance=coverage.get('provenance'))


def prepare_view(tools,args: PrepareView,*,selected=None,context=None):
    if args.target=='open_chart' and args.view_kind=='heatmap' and args.frequency not in {None,'monthly'}:
        return dict(status='route_required',data=None,message='Heatmap chỉ theo tháng. Biểu đồ nhiệt độ theo giờ/ngày/tháng/năm thông thường phải dùng view_kind=timeseries; không được đổi chu kỳ người dùng đã chọn.')
    cid=args.collection_id or selected or (context.collection_id if context else None)
    if cid is None:
        return dict(status='needs_clarification',missing_fields=['collection_id'],data=None)
    info=tools.execute('describe_collection',dict(collection_id=cid))
    if info['status']!='ok':
        return info
    if not info['data']['grid_count']:
        return dict(status='no_data',data=None,message='Bộ này chưa có điểm lưới để mở kết quả. Có thể xem thông tin bộ trong Kho dữ liệu.')
    known=context.parameters.model_dump() if context and context.collection_id==cid and context.parameters else {}
    values={k:getattr(args,k) if getattr(args,k) is not None else known.get(k)
            for k in ['variable','latitude','longitude','start','end','frequency']}
    if (args.latitude is None)!=(args.longitude is None):
        values.update(latitude=args.latitude,longitude=args.longitude)
    # A new variable/interval can reuse the actual grid; never carry context from another collection.
    if values['latitude'] is None and values['longitude'] is None and info['data']['grid_count']==1:
        values.update(info['data']['grids'][0])
    if args.target=='open_data':
        values['frequency']='hourly'
    elif args.view_kind=='heatmap':
        values['frequency']='monthly'
    elif args.view_kind in {'map','statistics'}:
        values['frequency']='hourly'
    missing=[k for k,v in values.items() if v is None]
    if missing:
        return dict(status='needs_clarification',missing_fields=missing,
                    data=dict(collection_id=cid,choices=info['data'],draft={**args.model_dump(),'collection_id':cid,
                        'view_kind':'timeseries' if args.target=='open_data' else args.view_kind,**values}))
    action=UIAction(type=args.target,collection_id=cid,parameters=ViewParameters(**values),
                    view_kind='timeseries' if args.target=='open_data' else args.view_kind)
    result=resolve_action(tools,action)
    if result['status']=='ok':
        result['data']=dict(name=result['name'],action=result['action'])
    return result


def actions_from_evidence(evidence):
    if not evidence:
        return []
    last=evidence[-1]
    r=last['result']
    if last['tool']=='prepare_view':
        return [r['action']] if r['status']=='ok' else []
    if last['tool']=='search_collections' and r['status'] in {'ok','no_data'}:
        return [UIAction(type='find_data',search=last['arguments'].get('search','')).model_dump(mode='json')]
    p=r.get('provenance') or {}
    data=r.get('data') or {}
    cid=p.get('collection_id') or (data.get('collection_id') if isinstance(data,dict) else None)
    if not cid:
        return []
    actions=[UIAction(type='open_data',collection_id=cid).model_dump(mode='json')]
    if last['tool'] in {'analyze_collection','analyze_chart'} and r['status'] in {'ok','partial'}:
        m=r['metadata']
        args=last['arguments']
        params=ViewParameters(variable=m['variable'],**m['grid_location'],start=p['start'],end=p['end'],
                              frequency=args.get('frequency') or 'hourly')
        kind='statistics' if args.get('operation')=='statistics' else 'timeseries'
        actions=[UIAction(type='open_chart',collection_id=cid,parameters=params,view_kind=kind).model_dump(mode='json'),
                 UIAction(type='open_data',collection_id=cid,parameters=params.model_copy(update={'frequency':'hourly'})).model_dump(mode='json')]
    elif r['status']=='ok':
        actions.append(UIAction(type='open_chart',collection_id=cid).model_dump(mode='json'))
    return actions
