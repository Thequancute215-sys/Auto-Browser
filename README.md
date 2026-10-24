# Auto Browser

[![CI](https://github.com/Thequancute215-sys/Auto-Browser/actions/workflows/ci.yml/badge.svg)](https://github.com/Thequancute215-sys/Auto-Browser/actions/workflows/ci.yml)

Trình duyệt có trợ lý AI **tự đọc đề, làm bài (điền từ, trắc nghiệm, kéo thả, bài nghe) rồi kiểm tra lại kết quả** trên Cambridge One, Rikkei LMS và các trang khác. Chạy với Google Gemini/Gemma (miễn phí), Claude, hoặc một mô hình AI chạy ngay trên máy bạn (Ollama, không giới hạn lượt).

## Cài đặt

**→ [Hướng dẫn cài đặt đầy đủ (INSTALL.md)](INSTALL.md)**

- **Windows:** tải bộ cài ở [Releases](https://github.com/Thequancute215-sys/Auto-Browser/releases/latest), chạy, rồi mở biểu tượng trên Desktop.
- **Docker (Windows / macOS / Linux):** `docker compose up -d` rồi mở http://localhost:8000.
- **Mã nguồn:** `git clone` rồi chạy `start.bat`.

Cập nhật không bao giờ cần gỡ app: bản Windows tự cập nhật, bản Docker chỉ cần `docker compose pull && docker compose up -d`.

## Tính năng

- **Cửa sổ trình duyệt thật** (bản Windows), mượt như trình duyệt thường, có âm thanh. Bảng AI đặt bên phải.
- **AI thấy toàn bộ trang**, kể cả phần chưa cuộn tới, và **không bao giờ nộp bài khi còn câu trống**.
- **"Vào mục X"** ở bất kỳ đâu trên trang mà không tốn lượt gọi AI; gõ có dấu hay không dấu đều được.
- **Kéo thả:** 7 cách, tự chọn cách hợp với từng trang.
- **Bài nghe:** tự bấm Play, chép lời audio rồi làm bài.
- **Xoay vòng mô hình:** khi một mô hình hết lượt miễn phí, app tự chuyển sang mô hình khác, rồi sang AI trên máy.
- Chế độ **Gợi ý**: chỉ hiện đáp án kèm giải thích.
- **Phân tích AI bằng chuột phải:** bôi đen một câu hỏi, bấm chuột phải rồi chọn *Phân tích AI*. Đáp án hiện trong một khung nhỏ, mờ ở góc dưới bên trái (chỉnh độ trong suốt trong Cài đặt).

## Phát triển

```powershell
pip install -r requirements-dev.txt; python -m playwright install chromium
python -m pytest                     # bộ test đầy đủ, không cần mạng hay API key
python -m pytest -m llm              # agent end-to-end với Gemini thật
python tools/bench_smoothness.py     # đo độ mượt chế độ web
python tools/build_exe.py            # đóng gói exe + bộ cài Windows
docker build -t autobrowser .        # image Docker
```

Phát hành: tăng `version.py`, commit, rồi `git tag vX.Y.Z && git push --tags`. GitHub Actions sẽ chạy test, build image Docker (amd64 + arm64) lên GHCR và bộ cài Windows lên Releases.

Kiến trúc, API, kết quả kiểm thử và giới hạn: [HANDOVER.md](HANDOVER.md). Quy tắc cho AI coding agent: [CLAUDE.md](CLAUDE.md).
