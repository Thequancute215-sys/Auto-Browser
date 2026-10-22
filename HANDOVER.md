# Tài liệu bàn giao: Auto Browser (v6.2.0)

Dành cho người dùng và công cụ phát triển kế thừa (Claude Code / Cursor / VSCode).

## 1. Tổng quan

Python điều khiển một Chromium headless (Playwright) có profile lưu đăng nhập. Giao diện web tại `http://localhost:8000` hiển thị màn hình trực tiếp, nhận chuột/bàn phím thật của bạn, và có một **AI agent dùng Gemini** tự đọc đề, làm bài, rồi kiểm tra lại kết quả trên màn hình.

```
Trình duyệt của bạn (static/)                 server.py (ThreadingHTTPServer, 127.0.0.1)
  canvas  ◄── /api/stream  (JPEG đẩy khi trang vẽ lại) ──┐
  SSE     ◄── /api/events  (tab, url, cuộn, chat, log) ──┤◄── EventBus (events.py)
  input   ──► /api/input   (lô sự kiện, đúng thứ tự)  ───┤
                                                         ▼
                                  BrowserManager (browser_manager.py, 1 luồng Playwright)
                                    ├─ CDP Page.startScreencast → frame
                                    ├─ chuột/bàn phím thật (page.mouse / keyboard)
                                    └─ ai_observe / ai_act (đánh số phần tử mọi iframe)
                                                         ▲
                                  AIController (ai_controller.py) ── llm.py ── Gemini API
```

## 2. Các thay đổi so với bản cũ, và lý do

| Vấn đề cũ | Bản mới |
|---|---|
| Ảnh màn hình chụp liên tục (mỗi 0.8s) và frontend hỏi lại mỗi 30ms | **CDP screencast**: Chrome chỉ gửi khung hình khi trang thực sự vẽ lại; server đẩy xuống qua một luồng HTTP. Trang đứng yên thì 0 khung hình, 0 băng thông. |
| Trạng thái/chat được hỏi lại 6 lần/giây | **Server-Sent Events**: chỉ gửi khi có thay đổi. |
| Click/phím gửi rời rạc, có thể đến sai thứ tự; phím mũi tên bị chiếm để cuộn trang | **Hàng đợi input có thứ tự** (gộp mousemove/wheel/chữ). Mọi phím, tổ hợp phím (Ctrl+A, Shift+Tab...), gõ tiếng Việt (Unikey/IME) và dán (Ctrl+V) đều được chuyển vào trang. |
| Kéo chuột = cuộn trang, nên không kéo thả hay bôi đen được | Chuột gửi **down/move/up thật**: kéo thả, bôi đen, double-click chọn từ đều chạy như trình duyệt thật. Ctrl+C sao chép vùng đang bôi đen về clipboard máy bạn. Chế độ "Bôi đen & Copy" thì tự sao chép luôn. |
| Cuộn bằng JS tự viết, không cuộn được nội dung trong iframe | `mouse.wheel` gốc: cuộn đúng vùng dưới con trỏ, kể cả iframe. |
| AI giải một lần duy nhất, không nhìn được iframe (Cambridge One chạy bài trong iframe), điền bằng `el.value=` (React/ProseMirror bỏ qua) | **Agent lặp nhiều bước: Quan sát → Suy luận → Thao tác → Kiểm tra lại** (chi tiết ở mục 3). |
| Chọn tên model cố định, gọi lỗi là treo | Danh sách model lấy trực tiếp từ API; có chuỗi model dự phòng, thử lại có giãn cách, giới hạn 90s mỗi lần gọi, nút Dừng có hiệu lực ngay. |
| Server mở `0.0.0.0` + CORS `*` + endpoint `eval`: máy khác trong mạng LAN, hoặc trang web độc hại, có thể chạy JS trong trình duyệt đã đăng nhập của bạn | Chỉ nghe `127.0.0.1`; chặn request khác origin và DNS-rebinding; `/api/ai_command` chỉ cho phép một danh sách lệnh trắng. Muốn mở LAN thì đặt `ALLOW_LAN=1` (tự chịu rủi ro). |
| Chat render HTML thô (tiêu đề trang web có thể chèn mã) | Mọi nội dung đều được escape. |

## 3. Cách AI làm bài (ai_controller.py)

Mỗi bước gồm:
1. **Quan sát** (`ai_observe`): duyệt mọi frame đang hiển thị, gắn `data-aid` cho các phần tử tương tác (input, radio/label, select, contenteditable, nút, phần tử `cursor:pointer`, phần tử kéo được, ô thả), đọc giá trị hiện tại, trạng thái checked, ngữ cảnh trước/sau mỗi ô trống (`She ___ to school`). Vẽ ô số lên trang (**Set-of-Marks**), chụp ảnh rồi xóa ngay; luồng hình của bạn tạm giữ khung cũ nên không bị nháy.
2. **Nghe audio** (bài nghe): audio của trang được ghi nhận theo hai đường, thẻ `<audio>/<video>` và mọi response có kiểu `audio/*` (nhiều player không dùng thẻ audio mà chỉ tải file khi bấm Play). File được tải bằng chính cookie của phiên đăng nhập, Gemini chép lời **một lần**, và lời chép được đưa vào mọi bước sau (mục `AUDIO TRANSCRIPTS` trong prompt). Nếu trang có audio nhưng chưa tải, agent được dặn bấm Play rồi chờ. Danh sách audio của tab được xóa mỗi khi trang điều hướng sang tài liệu khác.
3. **Suy luận**: gửi ảnh, danh sách phần tử, chữ trên trang (gồm iframe), lời audio và lịch sử các bước trước cho Gemini (chế độ JSON). Mô hình trả về `observation / plan / answers / actions / status / message`.
4. **Thao tác** (`ai_act`): dùng locator của Playwright với chuột/bàn phím thật: `click`, `type` (fill, hoặc Ctrl+A + gõ với contenteditable), `select`, `drag` (engine 7 chiến lược, xem mục 3b), `hover`, `scroll`, `press`, `navigate`, `wait`.
5. **Kiểm tra lại**: bước sau quan sát lại. Agent chỉ báo "hoàn thành" khi màn hình chứng minh điều đó. Nếu màn hình không đổi sau 3 lần, agent dừng để tránh lặp vô ích.

### 3a. Hai nhà cung cấp AI: Gemini (mặc định) và Claude

Chọn trong Cài đặt (`provider` trong `ai_config.json`). Cả hai đều dùng chung vòng lặp agent, prompt và giao diện `generate()` trong `llm.py`.

| | Gemini (`GeminiClient`) | Claude (`ClaudeClient`, SDK `anthropic`) |
|---|---|---|
| Chi phí | Miễn phí, giới hạn lượt theo từng model | Trả phí (Claude Opus 5.5: $4 / $20 cho mỗi 1 triệu token vào / ra; ước tính 0,05–0,1 USD mỗi bước) |
| Định dạng trả lời | JSON mode + `parse_json_response` | **Structured output** (`output_config.format` với `AGENT_SCHEMA`): JSON luôn hợp lệ |
| Suy luận | Mặc định của model | Adaptive thinking (luôn bật trên Opus 5.5), độ kỹ chỉnh bằng `claude_effort` (mặc định `medium`) |
| Khi bị từ chối an toàn | (không có) | `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`): server tự chạy lại trên model phù hợp; nếu vẫn bị từ chối thì agent báo lỗi rõ ràng |
| Audio (bài nghe) | Có | **Không**: bài nghe luôn chép lời bằng Gemini, nên vẫn cần khóa Gemini |

Chỉ gửi những tham số Claude Opus 5.5 chấp nhận: không gửi `temperature` (bị 400), không tắt thinking. Prompt hệ thống ổn định được đánh dấu `cache_control` để có thể dùng lại cache giữa các bước. Lỗi được phân loại theo exception của SDK: sai khóa, không có quyền, không có model (chuyển model dự phòng), giới hạn tốc độ (chuyển model dự phòng), lỗi server (SDK đã tự thử lại 2 lần). **Chưa chạy thử với Claude thật** vì máy này không có Anthropic API key; `tests/test_claude.py` kiểm tra hình dạng request bằng client giả.

### 3a''. Vượt giới hạn lượt: xoay vòng mô hình + AI trên máy (v6.2)

Đo ngày 22/10/2026 trên bài trộn 6 dạng câu (cùng prompt của agent, có ảnh màn hình):

| Mô hình | Thời gian 1 bước | Đúng |
|---|---|---|
| `gemini-3.5-flash-lite` | 6,3 s | 5/5 |
| `gemma-4-26b-a4b-it` | 31 s | 5/5 |
| `gemma-4-31b-it` | 73 s (lỗi 500 khi có system instruction) | 5/5 |

- **Xoay vòng mô hình** (`GeminiClient._discover_alternatives`): khi mọi mô hình đã cấu hình đều hết lượt, app thử lần lượt Flash, rồi Flash-Lite, rồi Gemma. Mỗi mô hình có quota riêng. Với Gemma, system prompt được đặt vào nội dung tin nhắn và không bật JSON mode (kết quả vẫn được parse thành JSON). Khi toàn bộ pool đều hết lượt, `QuotaExhausted` được ném ra.
- **AI trên máy** (`OllamaClient`, provider `local`): dùng REST API của Ollama (`/api/chat`, `format` = `AGENT_SCHEMA`, có ảnh). Mặc định `gemma4:e4b`. Đo trên bài trắc nghiệm mẫu (`quiz.html`, i5-13500H, chỉ CPU): đúng 5/5 ở cả 3 lần, nhưng 181 đến 279 giây mỗi bước (Gemini Flash-Lite 6,3 giây), nên phù hợp làm dự phòng hơn là provider chính trên máy không có GPU rời. Có thể chọn làm provider chính, hoặc bật `local_fallback`: khi nhận `QuotaExhausted`, agent đổi "bộ não" ngay giữa lần chạy sang mô hình trên máy và làm tiếp. Trong Docker, mô hình được gọi qua `OLLAMA_HOST`.
- **"Vào mục X" không cần AI** (`_try_find_and_go`, `FIND_TARGETS`): tìm trên toàn trang (mọi frame, kể cả phần ngoài màn hình), không phân biệt dấu và hoa thường. Thứ tự ưu tiên: khớp hoàn toàn, rồi khớp đầu chuỗi (theo ranh giới từ), rồi khớp nguyên từ, rồi chứa chuỗi, rồi trùng một phần các từ. Kết quả rõ ràng thì mở luôn; nếu mơ hồ thì liệt kê các lựa chọn (hoặc giao cho agent nếu có khóa). Câu lệnh có nhiều bước ("…rồi làm bài") được chuyển cho agent.

### 3a'''. Docker, phát hành và cập nhật tại chỗ (v6.2)

- `Dockerfile`: dựa trên `mcr.microsoft.com/playwright/python:v1.64.0-noble` (amd64 + arm64), chạy bằng user `pwuser`. Dữ liệu nằm trong volume `/data` (`AIB_PROFILE_DIR`, `AIB_AI_CONFIG`). `AIB_BIND=0.0.0.0` chỉ đổi địa chỉ lắng nghe; kiểm tra Host/Origin vẫn chỉ cho localhost, và compose chỉ publish cổng tại `127.0.0.1:8000`. Phiên bản thư viện được ghim trong `requirements.lock`.
- `docker-compose.yml`: dịch vụ chính, profile `local-ai` (Ollama trong Docker), profile `autoupdate` (Watchtower, kiểm tra mỗi 6 giờ).
- `.github/workflows/ci.yml`: mỗi lần push chạy toàn bộ pytest trên **ubuntu-latest và windows-latest**, sau đó build image Docker và smoke test (state, UI, goto, khung hình JPEG, chặn Host lạ, `mode: docker`).
- `.github/workflows/release.yml`: push tag `vX.Y.Z` (phải khớp `version.py`) sẽ đẩy image lên GHCR (`:X.Y.Z` và `:latest`), build bộ cài Windows, cài thử im lặng rồi chạy `--selftest`, sau đó tạo GitHub Release kèm ghi chú từ `docs/releases/vX.Y.Z.md`.
- `updater.py` (cập nhật mà không cần gỡ app):
  - **Desktop:** đọc release mới nhất trên GitHub, tải bộ cài, so mã SHA-256 với `digest` mà GitHub báo (không khớp thì không chạy), rồi chạy `/VERYSILENT /CLOSEAPPLICATIONS /RELAUNCH`. Inno Setup cài đè cùng AppId; `[Code] ShouldRelaunch` mở lại app. Bộ cài được tách khỏi Job Object (`CREATE_BREAKAWAY_FROM_JOB`, job có `BREAKAWAY_OK`) để không bị kill khi app thoát. Cập nhật tự động chạy khoảng 8 giây sau khi mở app (tắt bằng `auto_update`).
  - **Docker:** `docker compose pull && docker compose up -d`, hoặc Watchtower.
  - **Mã nguồn:** `start.bat` chạy `git pull --ff-only`.
  - Kho đang riêng tư thì API trả 404, nên cần `update_token` (GitHub token chỉ đọc, lưu như API key và không bao giờ được trả qua API).

### 3a'. Trang dài cần cuộn và chống nộp bài sớm (v6.1)

Bản trước chỉ đưa cho AI những phần tử đang hiện trên màn hình, nên với trang dài, AI bỏ sót các câu ở phía dưới và từng bấm "Nộp" khi còn 4 câu trống (mất 1 lượt làm bài). Bây giờ:
- `ANNOTATE_ELEMENTS` đánh số **toàn bộ trang** (mọi frame, kể cả frame và hộp cuộn đang nằm ngoài màn hình). Phần tử ngoài màn hình được gắn `offscreen: above/below/side`. AI thao tác thẳng bằng id, Playwright tự cuộn tới. Link điều hướng ngoài màn hình bị bỏ qua cho đỡ nhiễu. Chữ trên trang tăng lên 15.000 ký tự mỗi frame, tổng 24.000.
- `FORM_STATUS` đếm số câu chưa trả lời trên **toàn trang**: ô chữ trống, nhóm radio chưa chọn, dropdown còn ở mục "-- Chọn --", ô kéo thả trống. Ô tìm kiếm, header/nav và ô chỉ-đọc không được tính. Kết quả nằm trong mục "FORM STATUS" của prompt, kèm id từng câu.
- **Rào chắn trước khi nộp** (`_submit_guard`): trước khi click một nút có chữ Nộp / Submit / Check / Kiểm tra / Hoàn thành / Chấm…, app đếm lại trên trang thật. Nếu còn câu trống thì **không bấm**, báo trong chat, và cho AI biết các id còn thiếu. Rào chắn chặn tối đa 2 lần mỗi lần chạy (phòng trường hợp đếm nhầm). Nút "Nộp luôn" trong hộp xác nhận cũng bị chặn; AI được dặn chọn "Làm tiếp".
- Kéo thả: cuộn cả nguồn và đích vào màn hình trước khi kéo.

### 3a''''. Sửa sau khi cho bot tự chạy trên trang thật (22/10/2026)

Cách thử: chạy app ở cổng 8765, chỉ gửi lệnh qua `/api/ai/command` như người dùng, không chạm vào trình duyệt, rồi xem chat và ảnh chụp. Đã thử: Cambridge "Test your English" (25/25), englishpage.com verbs 1 (100%), H5P drag-the-words (3/3), H5P fill-in-the-blanks ví dụ 1 (3/3) và ví dụ 2 sau khi bấm Next (5/5). Hôm đó Gemini miễn phí quá tải nặng, nhiều bước chạy trên Flash-Lite. Các lỗi tìm ra và cách sửa:

- **Gemini quá tải (503):** chỉ thử lại 1 lần, và không thử lại nếu chính lỗi 503 đã mất hơn 10 giây. Mô hình quá tải bị xếp cuối hàng trong 120 giây. Khi mọi mô hình đều quá tải, app tự tìm Flash-Lite/Gemma. Sau 30 giây chưa có trả lời, cùng request được gửi song song cho mô hình kế tiếp (`_hedged`); mô hình dự phòng nào hỏng thì thay ngay bằng mô hình sau nó. Mô hình không tồn tại (404) được nhớ để không gọi lại.
- **Quảng cáo chèn ngang làm AI bấm nhầm** (Google vignette): chặn các máy chủ quảng cáo (`config.AD_HOSTS`, tắt bằng `AIB_BLOCK_ADS=0` hoặc ô "Chặn quảng cáo" trong Cài đặt). Click dự phòng cuối cùng bấm vào vị trí *hiện tại* của phần tử, không dùng tọa độ cũ.
- **Rào chắn nộp bài chặn nhầm một đáp án** có chữ "check": phần tử radio/checkbox/option và câu dài hơn 4 từ không bao giờ bị coi là nút Nộp.
- **Bài trong iframe trên trang nhiều link** (h5p.org có hơn 260 link): đánh số iframe trước, trang chính giữ tối thiểu 120 phần tử. `FORM_STATUS` đếm đúng ô thả của H5P (chỉ ô trong cùng, đọc `aria-label` "is empty / contains").
- **AI báo xong khi còn ô trống** (Flash-Lite): với lệnh làm bài, "done" bị từ chối nếu `FORM_STATUS` còn câu trống (tối đa 2 lần mỗi lần chạy).
- **Link trong lệnh** ("vào https://… rồi làm bài"): mở luôn, không tốn lượt gọi AI.
- **Gõ nhầm vào ô tìm kiếm của trang:** Flash-Lite dùng lại id của bước trước nên "type" trúng phần header, và Ctrl+A / đáp án / Enter rơi vào ô tìm kiếm. Bây giờ "type" từ chối link/nút, và chỉ gõ phím khi ô đang được focus đúng là ô đó, hoặc là ô hiện ra đúng chỗ đó sau khi click (`OWNS_TEXT_FOCUS`).
- **Lệnh treo 30 giây rồi mọi lệnh sau đều hết giờ:** đọc lại giá trị ô sau khi gõ không có timeout, trong khi H5P dựng lại khung. Đã thêm timeout, và đặt `context.set_default_timeout(10000)` làm chốt an toàn (bằng 30 giây của `execute()` là quá dài).
- **Nhìn trang khi trang còn đang tải:** sau thao tác cuối của mỗi bước, app chờ tới khi không còn request mạng mới (`_wait_network_quiet`, tối đa 3 giây). Nếu trang còn hiện "Loading..." / "Đang tải", app chờ và quan sát lại trước khi hỏi AI (không tốn lượt).
- **Bấm qua lại Next/Previous mãi:** mỗi bước AI ghi `progress` (phần việc đã xong), được đưa lại vào bước sau. Cùng một màn hình lặp lại lần thứ 3 thì AI được cảnh báo, lần thứ 5 thì app dừng.

### 3b. Engine kéo thả

Mỗi trang web dùng một cơ chế kéo thả khác nhau, nên engine thử lần lượt 7 chiến lược và **kiểm tra DOM sau mỗi lần thử**: nguồn có biến mất, đổi cha hay đổi vị trí không, nội dung đích có đổi không. Sau 500ms engine kiểm tra lại để loại hiệu ứng trả về vị trí cũ (kiểu jQuery UI revert).

| Chiến lược | Dành cho |
|---|---|
| `playwright` | HTML5 DnD gốc (Playwright chặn drag của Chromium) |
| `mouse_steps` | Thư viện chuột/pointer có ngưỡng kéo (jQuery UI, interact.js, Sortable fallback, dnd-kit) |
| `mouse_hold` | Cảm biến "nhấn giữ" (delay 300–600ms) |
| `touch` | Thư viện chỉ nghe touch events (CDP `Input.dispatchTouchEvent`) |
| `html5_events` | Trang mà drag thật không kích hoạt: DragEvent tổng hợp có DataTransfer |
| `pointer_events` | PointerEvent/MouseEvent tổng hợp theo đường đi |
| `click_place` | Chế độ "bấm từ rồi bấm chỗ trống" (chế độ trợ năng của nhiều player) |

Chiến lược thành công được ghi nhớ theo từng website và thử đầu tiên ở lần sau. Nếu không cách nào có tác dụng, agent nhận lỗi kèm danh sách các cách đã thử. Phát hiện ô thả: `ondrop/ondragover`, `aria-dropeffect`, các class `drop|droppable|dropzone|blank|slot|gap|target`; phát hiện phần tử kéo được: `draggable`, `cursor: grab/move`, các class `drag|sortable|token`.

Tùy chọn (thanh bên hoặc ⚙️ Cài đặt, lưu trong `ai_config.json`):
- **Tự làm / Gợi ý**: chế độ Gợi ý chỉ hiện thẻ đáp án kèm giải thích, không đụng vào bài.
- **Tự nộp**: bấm Check/Submit sau khi điền xong, đọc phản hồi và sửa câu sai nếu trang cho phép.
- **Tự qua bài**: hoàn thành xong thì sang bài tiếp theo.
- Số bước tối đa, model chính và chuỗi model dự phòng.
- Chip "🚀 Làm liên tục" bật Tự nộp + Tự qua bài **chỉ cho lần chạy đó**.

Lệnh đơn giản không tốn lượt gọi AI: một URL bất kỳ, `mở youtube|cambridge|rikkei|<tên miền>`, `cuộn xuống 600`, `tìm kiếm <từ khóa>`, `tab mới`, `đóng tab`, `quay lại`, `tải lại`.

### 3c. Ứng dụng desktop và độ mượt

**Hai chế độ hiển thị** (`config.BROWSER_MODE`):

| | `window` (app desktop, mặc định của `AutoBrowser.exe`) | `stream` (`python server.py`, mở bằng trình duyệt bất kỳ) |
|---|---|---|
| Bạn dùng gì | Cửa sổ Chromium thật + bảng AI riêng đặt bên phải | Canvas trong trang web, hình được truyền từ Chromium chạy nền |
| Độ mượt | Như trình duyệt thường (đo được 60–92 khung/giây khi vẽ trang); âm thanh có | Click → hình ~42 ms, rê chuột ~42 ms, cuộn ~49 khung/giây |
| GPU | Bật | Tắt (tránh SwiftShader ngốn CPU) |

Giới hạn của chế độ stream nằm ở chính Chromium headless. Đo tách từng chặng: input đến trang ~4 ms, trang xử lý ~2 ms, Chrome chụp và mã hóa khung hình ~40–80 ms (`Page.captureScreenshot` riêng lẻ cũng mất ~78 ms). Vì vậy app desktop dùng cửa sổ thật, giống cách tính năng trình duyệt trong Claude hoạt động.

**Kênh CDP trực tiếp** (`cdp_fast.py`, chế độ stream): Chromium được mở với `--remote-debugging-port=0`. Một WebSocket riêng tới trang đang mở nhận khung hình (ack ngay khi nhận, không chờ luồng Playwright) và gửi chuột/phím/chữ của người dùng thẳng vào Chrome theo đúng thứ tự. Playwright vẫn lo điều hướng, AI, tab và frame. Đo A/B cùng điều kiện (`tools/bench_smoothness.py`, tắt kênh bằng `AIB_FAST_CDP=0`):

| | Đường cũ (Playwright) | Kênh CDP trực tiếp |
|---|---|---|
| Click → điểm ảnh đổi | 61 ms | **42 ms** |
| Rê chuột → điểm ảnh đổi | 50 ms | **42 ms** |
| Cuộn | 19 khung/giây | **49 khung/giây** |

Ngoài ra: server dùng HTTP/1.1 keep-alive (input không phải mở kết nối mới mỗi lần), mousemove được gom mỗi 8 ms, và kéo chuột gửi đúng nút đang giữ (cần cho bôi đen và kéo thả).

**Đóng gói** (`tools/build_exe.py`): PyInstaller (onedir, có icon và thông tin phiên bản) cùng bộ cài Inno Setup cài theo người dùng (không cần admin, có biểu tượng Desktop và Start menu, có gỡ cài đặt). Chromium không được đóng gói vào bộ cài: app dùng thư mục chung `%LOCALAPPDATA%\ms-playwright` và tự tải nếu thiếu. `desktop_app.py`:
- tính vùng làm việc của màn hình: trình duyệt bên trái, bảng AI (380–470 px) bên phải;
- mở bảng AI bằng **Chromium của Playwright ở chế độ `--app` với profile riêng**. Không dùng Edge, vì Edge có thể mở bảng bên trong Edge cá nhân đang chạy của bạn;
- đóng cửa sổ trình duyệt hoặc bảng AI là thoát (theo dõi process của bảng, kèm kết nối SSE làm dự phòng). Job Object dọn sạch mọi process con;
- mở app lần hai thì chỉ mở lại bảng AI; tự chọn cổng trống trong khoảng 8000–8010.

## 4. Cấu trúc file

```
desktop_app.py          Launcher của AutoBrowser.exe: bố trí 2 cửa sổ, vòng đời app, tải Chromium lần đầu
cdp_fast.py             Kênh CDP trực tiếp: khung hình + chuột/phím/chữ độ trễ thấp (chế độ stream)
server.py               HTTP/1.1 + SSE + luồng hình, kiểm tra origin, Windows Job Object
browser_manager.py      Luồng Playwright: screencast, tab/popup, input, trích chữ, audio, kéo thả, ai_observe / ai_act
page_scripts.py         Các đoạn JS chạy trong trang
ai_controller.py        Agent loop, prompt, chép lời audio, lệnh nhanh, cài đặt
llm.py                  Gemini client: timeout, thử lại, model dự phòng, liệt kê model, file đính kèm
events.py               EventBus (pub/sub cho SSE)
config.py               Cổng, viewport, chất lượng JPEG, cờ Chromium, URL mặc định, biến môi trường
ai_browser.py           CLI điều khiển từ dòng lệnh
static/                 index.html, style.css, app.js
static/vendor/          primer.css (GitHub Primer CSS v22) + octicons.js (Primer Octicons v19)
tests/                  pytest: test_dnd, test_browser, test_agent, test_server, test_ui, test_llm_e2e
tests/fixtures/         Trang thử: quiz.html, listening.html (+ .wav), dnd/*.html (11 cơ chế kéo thả)
tests/reports/          dnd_matrix.md (ma trận chiến lược × cơ chế) + ảnh chụp UI (không commit)
tools/build_icons.py    Sinh lại tập icon Octicons
tools/make_icon.py      assets/icon.svg → assets/icon.ico (16–256 px) + static/favicon.svg
tools/build_exe.py      Đóng gói exe + bộ cài;  installer/AutoBrowser.iss: script Inno Setup
tools/test_desktop_exe.py  Kiểm tra app đã đóng gói (cửa sổ, fps, đóng → thoát, không sót process)
tools/bench_smoothness.py  Đo độ mượt end-to-end trong UI thật → tests/reports/smoothness.md
tools/bench_chromium_modes.py  So sánh các chế độ chạy Chromium (shell/new headless, GPU, chất lượng JPEG)
scripts/debug/          Các script gỡ lỗi cũ (gọi API cổng 8000)
```

### Giao diện (GitHub Primer)

Giao diện dùng **Primer CSS v22** và **Primer Octicons**, lưu sẵn trong `static/vendor`. Màu sắc, khoảng cách, bo góc và bóng đổ lấy từ token Primer, nên chế độ sáng/tối/theo hệ thống (nút ☀/☾ trên thanh công cụ) luôn đúng. Primer CSS 22 không còn SegmentedControl, Label, ProgressBar, Overlay và flash (đã chuyển sang Primer React), nên các component này được dựng lại trong `style.css` theo spec của Primer.

## 5. API

| Phương thức | Endpoint | Mô tả |
|---|---|---|
| GET | `/api/stream` | Luồng nhị phân `[u32 độ dài][u16 rộng][u16 cao][jpeg]`…; độ dài 0 = giữ kết nối |
| GET | `/api/events` | SSE: `hello`, `state`, `log`, `message`, `ai_status`, `ai_pointer`, `config`, `toast`, `messages_cleared` |
| GET | `/api/screenshot` | Một ảnh JPEG (hỗ trợ ETag/304) |
| GET | `/api/state` | Tab, URL, cuộn, viewport, log |
| GET | `/api/selection` | Chữ đang bôi đen (tìm trong mọi frame) |
| GET | `/api/page/text` | Các đoạn văn bản trên trang (mọi frame) |
| GET | `/api/ai/status` · `/api/ai/config` · `/api/ai/models` | Trạng thái và lịch sử chat · cài đặt · danh sách model |
| POST | `/api/input` | `{"events":[{"t":"down|up|move|click|wheel|key|text", ...}]}` |
| POST | `/api/goto` · `/api/action` | `{"url"}` · `{"action":"back|forward|reload|stop_loading"}` |
| POST | `/api/tab/new|switch|close` · `/api/viewport` · `/api/scroll_ratio` | Tab · kích thước màn hình · kéo thanh cuộn |
| POST | `/api/ai/command` | `{"command": "...", "overrides": {...}}` |
| POST | `/api/ai/stop` · `/api/ai/clear` · `/api/ai/config` | Dừng · xóa chat · lưu cài đặt (`"test": true` để kiểm tra kết nối) |
| POST | `/api/ai_command` | Chỉ các lệnh `eval`, `extract_text`, `page_text`, `get_selection` (dành cho CLI/script) |
| POST | `/api/click` · `/api/type` · `/api/scroll` · `/api/select_text` | Giữ lại để tương thích với script cũ |

## 6. Vận hành

- Chạy `start.bat` (tự cài thư viện nếu thiếu) hoặc `python server.py`, rồi mở `http://localhost:8000`.
- Biến môi trường: `PORT`, `ALLOW_LAN=1`, `AIB_PROFILE_DIR` (thư mục profile khác), `AIB_AI_CONFIG` (file cài đặt AI khác), `AIB_KILL_STALE=0` (không dọn tiến trình chrome-headless-shell cũ khi khởi động), `GEMINI_API_KEY`.
- Test: `pip install -r requirements-dev.txt`, sau đó `python -m pytest` (nhanh) hoặc `python -m pytest -m llm` (Gemini thật).
- Đăng nhập Cambridge/Rikkei một lần trong màn hình trực tiếp; phiên đăng nhập được lưu ở `~/.ai_browser_profile`.
- Bản đồ mã: repo bật plugin Understand-Anything qua `.claude/settings.json`. Chạy `/understand` sau khi sửa (hoặc `/understand --auto-update` một lần), `/understand-diff` trước khi phát hành, và commit `.ua/knowledge-graph.json`. Quy trình đầy đủ ở mục "Quy trình làm việc với Understand-Anything" trong `CLAUDE.md`.

## 7. Kiểm thử (21/10/2026)

`python -m pytest`: **127 passed** (22/10/2026, khoảng 20 phút trên máy đang bận; trước đó 108 passed trong khoảng 6 phút) (CI chạy trên cả Linux và Windows), không cần mạng hay API key. Gồm `test_long_pages.py` (trang 10 câu trải dài, có hộp cuộn và hộp xác nhận "Còn N câu chưa trả lời": AI thấy đủ 10 câu, nút Nộp sớm bị chặn, chỉ lần nộp đủ câu mới tính, đạt 10/10 và còn 2/3 lượt) và `test_gemini_quota.py` (hết lượt theo ngày thì chuyển model ngay, giới hạn theo phút thì chờ, tự tìm model khác, thông báo khi tất cả đều hết lượt). Gồm `test_claude.py` (11): hình dạng request gửi Claude (structured output, effort, fallbacks, cache, ảnh, không có `temperature`), từ chối an toàn, sai khóa, chuyển model khi bị giới hạn tốc độ, từ chối audio, schema chặt, agent dùng đúng provider, bài nghe với Claude vẫn chép lời bằng Gemini (và cảnh báo khi thiếu khóa Gemini), cài đặt không lộ khóa nào. Gồm `test_fast_input.py` (14): kênh CDP trực tiếp, gõ tiếng Việt / Ctrl+A / Backspace / Tab, kéo bôi đen, người dùng tự kéo thả bằng tay (HTML5, thư viện chuột, SortableJS), phím không hợp lệ, bảng tên phím.

`python tools/test_desktop_exe.py` (bản đã cài bằng bộ cài): server sẵn sàng sau ~6 giây; cửa sổ trình duyệt (1037×852) và bảng AI (403×852) nằm cạnh nhau; trang vẽ ở 60–92 khung/giây; trang tự theo kích thước cửa sổ; đóng trình duyệt hoặc đóng bảng AI đều thoát sau ~3,5 giây, không sót process hay cửa sổ nào; gỡ cài đặt xóa chương trình nhưng giữ cài đặt của bạn.

| File | Nội dung |
|---|---|
| `test_dnd.py` (14) | 11 trang kéo thả (HTML5, chuột + ngưỡng, pointer + isTrusted, jQuery UI, SortableJS native/fallback, interact.js, chỉ touch, bấm-rồi-bấm, nhấn giữ, iframe khác origin) đều đặt đúng từ qua chuỗi tự động; ghi nhớ chiến lược; báo lỗi trung thực khi thả sai chỗ; ma trận 7×11 |
| `test_browser.py` (10) | Gõ phím + tiếng Việt, double-click chọn chữ, trích chữ trong div/iframe, mô tả phần tử bài tập, điền/chọn/click (kể cả trong iframe), id cũ bị báo lỗi, bắt audio từ mạng + tải về, chặn đọc file không phải audio, tab/popup, đổi viewport |
| `test_agent.py` (12) | Vòng lặp agent với Gemini giả lập: giải bài trộn 6 dạng rồi xác minh điểm, dừng khi màn hình không đổi, chế độ Gợi ý không đụng vào bài, override theo lần chạy, báo kéo thả thất bại cho mô hình, lệnh nhanh không gọi LLM, thiếu key, lỗi LLM không bị giấu, parse JSON |
| `test_server.py` (8) | Phục vụ UI/Primer, chặn path traversal, luồng hình JPEG, SSE hello, input, chặn cross-origin + DNS rebinding, danh sách lệnh trắng, không bao giờ trả API key |
| `test_ui.py` (5) | UI thật trong Chromium riêng: không lỗi JS, icon Primer, click + gõ qua canvas, Ctrl+C về clipboard, hộp Cài đặt, đổi theme sáng/tối, ngăn kéo chữ có lọc, cảnh báo khi thiếu key |

`python -m pytest -m llm` (Gemini thật): **2 passed** trong khoảng 5 phút. Bài trộn 6 dạng đạt **6/6**; bài nghe (giọng đọc SAPI, player chỉ tải audio khi bấm Play) đạt **4/4** sau khi tự bấm Play, chép lời và điền.

## 8. Giới hạn đã biết

- **Quota miễn phí của Gemini: 20 lượt/ngày cho mỗi model** (đo ngày 21/10/2026). Một số tên model dùng chung quota: `gemini-3.7-flash` và `gemini-flash-latest` đều báo `model: gemini-3.8-flash`. Mỗi bước của agent tốn 1 lượt, nên một model đủ cho khoảng 3–6 bài mỗi ngày. Cách xử lý trong `llm.py`:
  - Lỗi 429 **theo ngày** (`PerDay`, hoặc "retry in" lâu hơn 90 giây): bỏ qua model đó **ngay lập tức** (Google trả lỗi trong ~0,3 giây), ghi nhớ đến lúc reset và không gọi lại;
  - lỗi 429 **theo phút**: chờ đúng thời gian Google yêu cầu rồi thử lại;
  - khi mọi model đã cấu hình đều hết lượt: tự tìm các model Flash khác của khóa (Flash trước Flash-Lite, bản ổn định trước bản preview);
  - khi tất cả đều hết lượt: báo rõ còn bao lâu thì có lượt lại, và gợi ý chuyển sang Claude.

  Chat ghi rõ model nào đang được dùng và vì sao.
- Bài nghe: audio phát qua MediaSource/`blob:` (stream từng mảnh) không tải lại được nên không chép lời được; file mp3/m4a/wav/ogg thông thường thì được.
- Kéo thả: đã thử 11 cơ chế phổ biến nhưng **chưa thử trên Cambridge One/Rikkei thật** (cần tài khoản của bạn). Nếu không cách nào có tác dụng, agent nhận lỗi kèm danh sách các cách đã thử và được dặn đổi cách tiếp cận.
- `config.DEFAULT_URL` và nút Cambridge chứa đường dẫn lớp học cụ thể của bạn (org/class id). Nếu đặt repo GitHub ở chế độ công khai, hãy cân nhắc thay đường dẫn này.
- Hộp thoại `alert/confirm` của trang được tự động chấp nhận và báo bằng thông báo nổi.
