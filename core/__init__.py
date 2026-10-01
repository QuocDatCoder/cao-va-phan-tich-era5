"""
Core package for ERA5 Data Pipeline:
- collector: Thu thập dữ liệu từ CDS API & đồng bộ real-time
- processor: Xử lý dữ liệu, chuyển múi giờ UTC+7, tính toán gió, đổi đơn vị
- visualizer: Vẽ biểu đồ chuỗi thời gian, xuất Plotly cho FE & Dashboard
- database: Quản lý CSDL SQLite, chống trùng lặp dữ liệu, công cụ SQL cho AI Agent
"""
