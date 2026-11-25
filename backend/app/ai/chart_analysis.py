"""Bounded statistical interpretation of the plotted series, never model arithmetic."""
from datetime import datetime,timedelta,timezone
from statistics import fmean,pstdev,median
from math import isfinite
from backend.app.domain.wind import circular_statistics

LOCAL=timezone(timedelta(hours=7))
FREQUENCIES={'hourly':'giờ','daily':'ngày','monthly':'tháng','yearly':'năm'}


def label(stamp):
    return datetime.fromisoformat(stamp).astimezone(LOCAL).strftime('%d/%m/%Y %H:%M')


def summarize_plot(payload,frequency):
    rows=sorted((p for p in payload.get('data',[]) if p.get('value') is not None
                 and isfinite(p['value'])),key=lambda p:datetime.fromisoformat(p['time']))
    values=[p['value'] for p in rows]
    result=dict(point_count=len(rows),frequency=frequency,unit=payload['unit'],
                aggregation=payload.get('aggregation'),complete=bool(payload.get('complete')),
                partial_periods=sum(bool(p.get('partial_period')) for p in rows),
                warning_count=(payload.get('collection_quality') or {}).get('issue_count',0))
    if not rows:
        return result
    circular=payload['variable']=='wind_direction'
    mean,std=circular_statistics(values) if circular else (fmean(values),pstdev(values))
    result.update(mean=mean,std=std,median=None if circular else median(values),circular=circular,
                  first=dict(time=rows[0]['time'],value=values[0]),last=dict(time=rows[-1]['time'],value=values[-1]))
    if circular:
        # 359°→1° is not a large negative change; avoid all linear diagnostics.
        return result
    low,high=min(rows,key=lambda p:p['value']),max(rows,key=lambda p:p['value'])
    result.update(min=dict(time=low['time'],value=low['value']),max=dict(time=high['time'],value=high['value']),
                  amplitude=high['value']-low['value'],net_change=values[-1]-values[0])
    if payload.get('aggregation')=='sum':
        result['sum_of_plotted_periods']=sum(values)
    if len(rows)>=2 and result['complete'] and not result['partial_periods']:
        changes=[dict(start=a['time'],end=b['time'],change=b['value']-a['value']) for a,b in zip(rows,rows[1:])]
        result['largest_rise']=max(changes,key=lambda p:p['change'])
        result['largest_fall']=min(changes,key=lambda p:p['change'])
    if len(rows)>=3 and result['complete'] and not result['partial_periods']:
        origin=datetime.fromisoformat(rows[0]['time'])
        xs=[(datetime.fromisoformat(p['time'])-origin).total_seconds()/86400 for p in rows]
        xbar,ybar=fmean(xs),fmean(values)
        denominator=sum((x-xbar)**2 for x in xs)
        if denominator:
            slope=sum((x-xbar)*(y-ybar) for x,y in zip(xs,values))/denominator
            residual=sum((y-(ybar+slope*(x-xbar)))**2 for x,y in zip(xs,values))
            total=sum((y-ybar)**2 for y in values)
            flat=high['value']==low['value']
            result['trend']=dict(slope_per_day=0. if flat else slope,
                r_squared=None if flat or not total else max(0.,min(1.,1-residual/total)))
    return result


def render_chart_analysis(result):
    d,m,p=result['data'],result['metadata'],result['provenance']
    f=lambda v:format(v,'.6g') if v is not None else 'không xác định'
    unit=d['unit']
    names={'temperature':'Nhiệt độ','2m_temperature':'Nhiệt độ','dewpoint':'Điểm sương','2m_dewpoint_temperature':'Điểm sương',
           'precipitation':'Lượng mưa','total_precipitation':'Lượng mưa','wind_speed':'Tốc độ gió','wind_direction':'Hướng gió',
           'solar_radiation':'Bức xạ mặt trời','surface_solar_radiation_downwards':'Bức xạ mặt trời'}
    lines=[f"Phân tích {names.get(m['variable'],m['variable'])} · {unit} · chu kỳ {FREQUENCIES[d['frequency']]} · UTC+7",
        f"Nguồn: {p['dataset']} · {p['place_name']} · lưới {p['grid_location']['latitude']}, {p['grid_location']['longitude']}.",
        f"Khoảng áp dụng: {label(p['start'])} → {label(p['end'])}.",
        f"Độ phủ giờ nguồn: {m['available_count']}/{m['expected_count']}; có {d['point_count']} điểm biểu diễn hợp lệ."]
    if not d['point_count']:
        return '\n'.join(lines+['Không có giá trị xác định để phân tích; không thay dữ liệu thiếu bằng 0.'])
    if not d['complete']:
        lines.append('Dữ liệu thiếu: các thống kê chỉ mô tả phần đã lưu, có thể bị lệch do khoảng trống. Không khớp xu hướng hoặc kết luận về toàn khoảng.')
    if d['circular']:
        lines.append(f"Hướng trung bình vòng tròn: {f(d['mean'])}°; độ lệch chuẩn vòng tròn: {f(d['std'])}°.")
        lines.append('Hướng gió là đại lượng vòng tròn; không dùng min/max góc, chênh lệch tuyến tính hoặc hồi quy tuyến tính. Null khi lặng gió/triệt tiêu không có nghĩa là Bắc hoặc 0.')
    else:
        lines.extend([
            f"Mức điển hình: trung bình {f(d['mean'])}, trung vị {f(d['median'])} {unit}.",
            f"Cực trị đường biểu diễn: thấp nhất {f(d['min']['value'])} {unit} tại {label(d['min']['time'])}; cao nhất {f(d['max']['value'])} {unit} tại {label(d['max']['time'])}. Nếu đồng hạng, hiển thị mốc đầu tiên.",
            f"Biến động: biên độ {f(d['amplitude'])}; độ lệch chuẩn {f(d['std'])}; cuối trừ đầu {f(d['net_change'])} {unit}."])
        for key,title,sign in [('largest_rise','Mức tăng lớn nhất giữa hai điểm liên tiếp',1),('largest_fall','Mức giảm lớn nhất giữa hai điểm liên tiếp',-1)]:
            change=d.get(key)
            if change and change['change']*sign>0:
                lines.append(f"{title}: {f(change['change'])} {unit}, từ {label(change['start'])} đến {label(change['end'])}. Đây là chênh lệch giá trị, không phải tốc độ mỗi giờ.")
        if 'trend' in d:
            t=d['trend']
            direction='tăng' if t['slope_per_day']>1e-10 else 'giảm' if t['slope_per_day'] < -1e-10 else 'ổn định'
            lines.append(f"Xu hướng mô tả: {direction}; hệ số hồi quy {f(t['slope_per_day'])} {unit}/ngày; R² {f(t['r_squared'])}. Trục thời gian dùng số ngày thực trôi qua, không coi tháng dài bằng nhau.")
            lines.append('R² chỉ mô tả độ khớp đường thẳng, không chứng minh ý nghĩa thống kê, nguyên nhân hoặc biến đổi khí hậu dài hạn; không dùng đường khớp này để dự báo.')
        else:
            lines.append('Chưa đánh giá hồi quy: cần ít nhất ba điểm, đủ độ phủ và không có kỳ lịch chưa trọn.')
        if 'sum_of_plotted_periods' in d:
            lines.append(f"Tổng các kỳ đang biểu diễn: {f(d['sum_of_plotted_periods'])} {unit}; không suy rộng ra các giờ/kỳ chưa có dữ liệu.")
    if d['frequency']!='hourly':
        lines.append('Các thống kê trên tính trên giá trị đại diện mỗi kỳ của biểu đồ; cực trị đường biểu diễn không phải cực trị quan trắc từng giờ. Trung bình các kỳ không phải trung bình giờ có trọng số.')
    if d['partial_periods']:
        lines.append(f"Có {d['partial_periods']} kỳ lịch chưa trọn; không so sánh tổng kỳ ngắn với kỳ đầy đủ như cùng độ dài.")
    if d['warning_count']:
        lines.append(f"Bộ có {d['warning_count']} cảnh báo chất lượng; cần đối chiếu báo cáo trước khi dùng kết luận.")
    if p.get('synthetic'):
        lines.append('Đây là dữ liệu giả lập (mock), không phải quan trắc ERA5 thật.')
    if p.get('provisional'):
        lines.append('Snapshot tạm thời: tác vụ chưa kết thúc.')
    lines.append('Giới hạn: một chuỗi tại một điểm chưa đủ xác định nguyên nhân, bất thường khí hậu hoặc dự báo. Muốn kết luận cần chuỗi nền dài hơn và các biến/nguồn đối chứng. Báo cáo không đọc ảnh hoặc dữ liệu ngoài DB.')
    if d.get('focus','overview')!='overview':
        prefixes={
            'peaks':('Cực trị','Hướng trung bình','Hướng gió'),
            'trend':('Xu hướng','R²','Chưa đánh giá','Hướng gió'),
            'variability':('Mức điển hình','Biến động','Mức tăng','Mức giảm','Hướng trung bình','Hướng gió'),
            'quality':(),
            'causes':('Cực trị','Biến động','Xu hướng','R²','Hướng gió'),
        }[d['focus']]
        common=('Phân tích','Nguồn:','Khoảng áp dụng','Độ phủ','Dữ liệu thiếu','Có ',
                'Bộ có','Đây là','Snapshot','Các thống kê','Giới hạn:')
        lines=[line for line in lines if line.startswith(common+prefixes)]
    return '\n\n'.join(lines)
