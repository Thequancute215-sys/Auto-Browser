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
python tools/bench_smoothness.py     # đo độ mượt chế độ stream (click/rê chuột/cuộn) → tests/reports/smoothness.md
python tools/build_exe.py            # đóng gói: dist/AutoBrowser/AutoBrowser.exe + dist/AutoBrowser-Setup-<ver>.exe
python tools/test_desktop_exe.py     # kiểm tra app desktop đã đóng gói (2 cửa sổ, fps, đóng → thoát sạch)
```

**Phát hành:** phiên bản duy nhất nằm ở `version.py`. Tăng số này, viết `docs/releases/vX.Y.Z.md`, commit, rồi `git tag vX.Y.Z && git push origin main --tags`. GitHub Actions (`release.yml`) sẽ build image Docker lên GHCR, build và test bộ cài Windows, rồi tạo Release. Không đính kèm file thủ công. App đã cài sẽ tự cập nhật từ Release đó (`updater.py`), nên **mọi thay đổi phải giữ tương thích với dữ liệu cũ**: chỉ thêm key mới vào `DEFAULT_SETTINGS`, không đổi tên hay xóa key, và không đổi `AppId` trong `installer/AutoBrowser.iss`.

Docker: `docker build -t autobrowser .` rồi `docker run -p 127.0.0.1:8000:8000 --shm-size=1g autobrowser`. Phiên bản Playwright trong `requirements.lock` phải khớp tag của base image trong `Dockerfile`.

Biến môi trường: `PORT`, `ALLOW_LAN=1`, `AIB_PROFILE_DIR`, `AIB_AI_CONFIG`, `AIB_KILL_STALE=0`, `GEMINI_API_KEY`.
Muốn chạy thử song song với server đang dùng: đặt `PORT=8765`, `AIB_KILL_STALE=0` và `AIB_PROFILE_DIR` trỏ tới một thư mục tạm.

## Quy trình làm việc với Understand-Anything

Repo dùng plugin [Understand-Anything](https://github.com/Thequancute215-sys/understand-anything) để giữ một bản đồ kiến thức (knowledge graph) của toàn bộ mã. `.claude/settings.json` đã khai báo marketplace và bật plugin, nên Claude Code sẽ đề nghị cài khi mở repo (hoặc chạy `/plugin marketplace add Thequancute215-sys/understand-anything` rồi `/plugin install understand-anything`).

- **Trước khi sửa:** dùng `/understand-chat <câu hỏi>` hoặc `/understand-explain <file hoặc hàm>` để tìm đúng chỗ cần sửa, thay vì đọc mò cả repo. Người mới vào dự án: `/understand-onboard`.
- **Sau khi sửa:** chạy `/understand` (chỉ phân tích lại các file đã đổi). Hoặc chạy một lần `/understand --auto-update` để cài hook post-commit tự cập nhật sau mỗi commit.
- **Trước khi commit hoặc phát hành:** `/understand-diff` để xem thay đổi ảnh hưởng tới module nào (ví dụ đổi `page_scripts.py` thì kiểm tra lại `browser_manager.py` và các test liên quan).
- **Xem trực quan:** `/understand-dashboard`. Bản đồ nghiệp vụ (luồng giải bài, cập nhật, chọn provider): `/understand-domain`.
- Commit `.ua/knowledge-graph.json` và `.ua/config.json` cùng với thay đổi mã để bản đồ luôn khớp với code. `.ua/intermediate/` và `.ua/diff-overlay.json` là file tạm, đã nằm trong `.gitignore`.
- Thư mục bị loại khỏi bản đồ (thư viện vendored, ảnh, báo cáo, bản build) khai báo trong `.understandignore`.
- Bản đồ chỉ hỗ trợ việc tìm hiểu. Khi nó mâu thuẫn với code thì code đúng: chạy lại `/understand` và kiểm tra bằng `python -m pytest`.

## Kiến trúc tóm tắt

- `server.py`: ThreadingHTTPServer chỉ nghe 127.0.0.1. Cung cấp `/api/stream` (luồng JPEG nhị phân), `/api/events` (SSE), `/api/input` (lô sự kiện chuột/phím) và các route REST. Mọi request đều qua kiểm tra Host/Origin.
- `browser_manager.py`: **toàn bộ Playwright chạy trong một luồng worker duy nhất.** Các luồng khác chỉ được gọi `browser.execute("<lệnh>", payload)`; lệnh `foo` tương ứng method `_cmd_foo`. Khung hình đến từ CDP `Page.startScreencast` (chỉ phát khi trang vẽ lại). Engine kéo thả nằm ở `_drag` / `_dnd_*`.
- `page_scripts.py`: mọi đoạn JS chạy trong trang (đánh số phần tử `data-aid`, Set-of-Marks, chữ, vùng chọn, cuộn, ảnh chụp DOM khi kéo thả, nguồn media).
- `ai_controller.py`: vòng lặp agent Quan sát → (Nghe audio) → Suy luận → Thao tác → Kiểm tra lại; prompt hệ thống; lệnh nhanh không cần LLM; cài đặt.
- `llm.py`: `GeminiClient` (mặc định, miễn phí, nhận audio) và `ClaudeClient` (SDK `anthropic`, mặc định `claude-opus-5-5`). Cả hai có cùng `generate()` / `list_models()`. Với Claude: không gửi `temperature`, không tắt thinking (Opus 5.5 trả 400), dùng `output_config` (`effort` + `format` json_schema), `fallbacks: "default"` qua `client.beta.messages.create` với beta `server-side-fallback-2026-07-01`, và bắt lỗi bằng các exception của SDK. Bài nghe luôn chép lời bằng Gemini (Claude không nhận audio). Test không bao giờ gọi API thật: inject client giả qua `_get_client`.
- `events.py`: EventBus cho SSE.
- `cdp_fast.py`: WebSocket CDP trực tiếp tới trang đang mở (chế độ stream). Nhận khung hình và gửi input của người dùng, không đi qua luồng Playwright. Phải giữ nguyên thứ tự gửi (một WebSocket, gửi rồi không chờ phản hồi).
- `updater.py`: kiểm tra GitHub Releases, tải bộ cài, xác minh SHA-256 rồi cài đè im lặng (desktop); hướng dẫn cập nhật cho Docker và bản mã nguồn. `version.py` giữ phiên bản và tên repo.
- Ba provider AI: `gemini` (mặc định, xoay vòng Flash → Flash-Lite → Gemma khi hết quota), `claude`, `local` (Ollama). Khi cả pool đám mây hết lượt, `QuotaExhausted` khiến agent chuyển sang mô hình trên máy nếu `local_fallback` bật.
- `desktop_app.py`: launcher của exe, chạy chế độ `window`: một cửa sổ Chromium thật, cộng bảng AI (`/?view=panel`) mở bằng Chromium của Playwright với `--app` và profile riêng. **Không mở bảng bằng Edge** (Edge có thể nhét bảng vào Edge cá nhân của người dùng).
- `static/`: giao diện vanilla JS dựa trên **GitHub Primer**. `vendor/primer.css` (v22, gồm token sáng/tối) và `vendor/octicons.js` được lưu sẵn trong repo, không phụ thuộc CDN.

## Quy tắc bắt buộc

1. **Không gọi Playwright ngoài luồng worker.** Thêm khả năng mới bằng cách viết `_cmd_<tên>` rồi gọi qua `execute()`.
2. **Không báo "hoàn thành" giả.** Agent chỉ được báo xong khi màn hình chứng minh điều đó. Thao tác thất bại phải trả `status: "error"` kèm lý do (engine kéo thả trả về danh sách các cách đã thử).
3. **Không commit `ai_config.json`** (chứa API key, đã nằm trong `.gitignore`). Mẫu cấu hình ở `ai_config.example.json`. API không bao giờ trả khóa thật, chỉ trả `masked_key`.
4. Giữ nguyên lớp bảo mật: bind 127.0.0.1, `_request_allowed()`, danh sách lệnh trắng `COMMAND_WHITELIST` cho `/api/ai_command`, và giới hạn `fetch_media` (chỉ đọc file audio cục bộ).
5. Giữ nguyên `setup_windows_job_object()` trong `server.py`: khi Python thoát, Windows tự dọn mọi tiến trình Chromium con.
6. CSS: chỉ dùng token Primer (`--bgColor-*`, `--fgColor-*`, `--borderColor-*`, `--base-size-*`, `--borderRadius-*`), không viết mã màu cứng, để chế độ sáng/tối luôn đúng. Icon: dùng `oi('tên')` hoặc `<svg data-oi="tên">`.
7. Mọi nội dung đưa vào HTML phải được escape (`escapeHtml` / `md()` trong `app.js`).
8. Độ mượt: mọi thay đổi ở luồng hình/input phải chạy lại `tools/bench_smoothness.py` và so với bảng trong HANDOVER §3c (click p50 ≤ 50 ms, cuộn ≥ 40 khung/giây ở chế độ stream). Ở chế độ window, không được ép `set_viewport_size`: trang phải theo kích thước cửa sổ.
9. Sửa xong thì chạy `python -m pytest`. Thêm cơ chế kéo thả mới thì thêm một trang thử vào `tests/fixtures/dnd/` và vào danh sách `PAGES` trong `tests/test_dnd.py`.

## Kéo thả: 7 chiến lược, thử theo thứ tự

`playwright` (drag_to) → `mouse_steps` (vượt ngưỡng kéo, nhiều bước di chuyển) → `mouse_hold` (nhấn giữ 700ms) → `touch` (CDP touch events) → `html5_events` (DragEvent tổng hợp) → `pointer_events` (PointerEvent tổng hợp) → `click_place` (bấm từ rồi bấm chỗ trống).
Sau mỗi lần thử, engine so ảnh chụp DOM của nguồn và đích trước/sau (`DND_SNAPSHOT`), kiểm tra lại sau 500ms để loại trường hợp hiệu ứng trả về vị trí cũ. Chiến lược thành công được ghi nhớ theo từng website. Ma trận tương thích mới nhất: `tests/reports/dnd_matrix.md`.
