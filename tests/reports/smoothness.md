# Đo độ mượt (end-to-end, đo trong UI thật)

Đo lúc 2026-10-21 03:05 · màn hình 1280×800 · `python tools/bench_smoothness.py`

| Phép đo | p50 / chỉ số | p95 / phụ | Ghi chú |
|---|---|---|---|
| Click → điểm ảnh đổi màu | 42 ms | 50 ms | 0/25 lỗi |
| Rê chuột → ô đen dưới con trỏ | 42 ms | 45 ms | 0/25 lỗi |
| Cuộn 60 Hz | 49 khung/giây | khoảng trống lớn nhất 36 ms | khung đầu sau 42 ms |
| Đứng yên 3 giây | 0 khung |  |  |
