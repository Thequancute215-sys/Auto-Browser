# CLAUDE.md: Auto Browser

Hướng dẫn cho Claude Code và các AI coding agent làm việc trong repo này. Chi tiết kiến trúc xem `HANDOVER.md`.

## Lệnh thường dùng

```powershell
pip install -r requirements-dev.txt; python -m playwright install chromium
python server.py                     # http://localhost:8000  (hoặc start.bat)
python -m pytest                     # bộ test nhanh (~5 phút), không cần mạng hay API key
python -m pytest -m llm              # test end-to-end với Gemini thật (tốn quota, đọc key từ ai_config.json)
python -m pytest tests/test_dnd.py   # chỉ test kéo thả; ghi tests/reports/dnd_matrix.md
python tools/build_icons.py          # sinh lại static/vendor/octicons.js sau khi thêm icon
```

Biến môi trường: `PORT`, `ALLOW_LAN=1`, `AIB_PROFILE_DIR`, `AIB_AI_CONFIG`, `AIB_KILL_STALE=0`, `GEMINI_API_KEY`.
Muốn chạy thử song song với server đang dùng: đặt `PORT=8765`, `AIB_KILL_STALE=0` và `AIB_PROFILE_DIR` trỏ tới một thư mục tạm.

## Kiến trúc tóm tắt

- `server.py`: ThreadingHTTPServer chỉ nghe 127.0.0.1. Cung cấp `/api/stream` (luồng JPEG nhị phân), `/api/events` (SSE), `/api/input` (lô sự kiện chuột/phím) và các route REST. Mọi request đều qua kiểm tra Host/Origin.
- `browser_manager.py`: **toàn bộ Playwright chạy trong một luồng worker duy nhất.** Các luồng khác chỉ được gọi `browser.execute("<lệnh>", payload)`; lệnh `foo` tương ứng method `_cmd_foo`. Khung hình đến từ CDP `Page.startScreencast` (chỉ phát khi trang vẽ lại). Engine kéo thả nằm ở `_drag` / `_dnd_*`.
- `page_scripts.py`: mọi đoạn JS chạy trong trang (đánh số phần tử `data-aid`, Set-of-Marks, chữ, vùng chọn, cuộn, ảnh chụp DOM khi kéo thả, nguồn media).
- `ai_controller.py`: vòng lặp agent Quan sát → (Nghe audio) → Suy luận → Thao tác → Kiểm tra lại; prompt hệ thống; lệnh nhanh không cần LLM; cài đặt.
- `llm.py`: client Gemini (timeout 90s, thử lại, chuỗi model dự phòng, ghi nhớ model đang hoạt động, liệt kê model, file đính kèm audio).
- `events.py`: EventBus cho SSE.
- `static/`: giao diện vanilla JS dựa trên **GitHub Primer**. `vendor/primer.css` (v22, gồm token sáng/tối) và `vendor/octicons.js` được lưu sẵn trong repo, không phụ thuộc CDN.

## Quy tắc bắt buộc

1. **Không gọi Playwright ngoài luồng worker.** Thêm khả năng mới bằng cách viết `_cmd_<tên>` rồi gọi qua `execute()`.
2. **Không báo "hoàn thành" giả.** Agent chỉ được báo xong khi màn hình chứng minh điều đó. Thao tác thất bại phải trả `status: "error"` kèm lý do (engine kéo thả trả về danh sách các cách đã thử).
3. **Không commit `ai_config.json`** (chứa API key, đã nằm trong `.gitignore`). Mẫu cấu hình ở `ai_config.example.json`. API không bao giờ trả khóa thật, chỉ trả `masked_key`.
4. Giữ nguyên lớp bảo mật: bind 127.0.0.1, `_request_allowed()`, danh sách lệnh trắng `COMMAND_WHITELIST` cho `/api/ai_command`, và giới hạn `fetch_media` (chỉ đọc file audio cục bộ).
5. Giữ nguyên `setup_windows_job_object()` trong `server.py`: khi Python thoát, Windows tự dọn mọi tiến trình Chromium con.
6. CSS: chỉ dùng token Primer (`--bgColor-*`, `--fgColor-*`, `--borderColor-*`, `--base-size-*`, `--borderRadius-*`), không viết mã màu cứng, để chế độ sáng/tối luôn đúng. Icon: dùng `oi('tên')` hoặc `<svg data-oi="tên">`.
7. Mọi nội dung đưa vào HTML phải được escape (`escapeHtml` / `md()` trong `app.js`).
8. Sửa xong thì chạy `python -m pytest`. Thêm cơ chế kéo thả mới thì thêm một trang thử vào `tests/fixtures/dnd/` và vào danh sách `PAGES` trong `tests/test_dnd.py`.

## Kéo thả: 7 chiến lược, thử theo thứ tự

`playwright` (drag_to) → `mouse_steps` (vượt ngưỡng kéo, nhiều bước di chuyển) → `mouse_hold` (nhấn giữ 700ms) → `touch` (CDP touch events) → `html5_events` (DragEvent tổng hợp) → `pointer_events` (PointerEvent tổng hợp) → `click_place` (bấm từ rồi bấm chỗ trống).
Sau mỗi lần thử, engine so ảnh chụp DOM của nguồn và đích trước/sau (`DND_SNAPSHOT`), kiểm tra lại sau 500ms để loại trường hợp hiệu ứng trả về vị trí cũ. Chiến lược thành công được ghi nhớ theo từng website. Ma trận tương thích mới nhất: `tests/reports/dnd_matrix.md`.
