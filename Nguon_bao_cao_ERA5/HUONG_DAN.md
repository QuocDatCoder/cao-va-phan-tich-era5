# Gói dựng lại báo cáo ERA5

Chạy XeLaTeX từ thư mục vừa giải nén.

    xelatex -interaction=nonstopmode -halt-on-error -jobname=Bao_cao_ERA5_hoan_thien report.tex

report.tex chứa các trang cập nhật. Các trang được giữ lại được nhúng từ original.pdf.
Ảnh trong screenshots được chụp trên Dashboard, trang quản trị và Swagger UI đang chạy cục bộ.
Câu hỏi ARE dùng một lời gọi công cụ cố định. Backend truy vấn thống kê thực từ bản sao SQLite.

verify_hanoi.py tái kiểm tra mẫu ở mục 4.2. Chạy từ thư mục gốc repo ERA5 bằng
Python của môi trường dự án, truyền đường dẫn ZIP ERA5-Land và SQLite vào script.
hanoi_crosscheck.json là kết quả đã kiểm chứng. Gói không kèm cơ sở dữ liệu, khóa API hay tệp .env.
