"""Complete monthly temperature and rainfall pairs used in the chart and ERA."""
import pandas as pd


MONTHLY_CLIMATE_LABEL = 'Khí hậu tháng'


def complete_months(payload):
    return {
        pd.Timestamp(row['time']).to_period('M'): row['value']
        for row in payload['data']
        if row.get('complete') and row['value'] is not None
    }


def read_monthly_climate(read, variables):
    temperature = next((v for v in ('2m_temperature', 'temperature') if v in variables), None)
    rainfall = next((v for v in ('total_precipitation', 'precipitation') if v in variables), None)
    if temperature is None or rainfall is None:
        raise ValueError('Bộ dữ liệu cần có cả nhiệt độ và lượng mưa.')
    thermal = read(temperature, 'mean')
    rain = read(rainfall, 'sum')
    thermal_months, rain_months = complete_months(thermal), complete_months(rain)
    paired = [(period, thermal_months[period], rain_months[period])
              for period in sorted(thermal_months.keys() & rain_months.keys())]
    return thermal, rain, paired
