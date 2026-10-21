# Hướng dẫn cài đặt Auto Browser

Có 3 cách cài. Chọn **một** cách phù hợp với bạn:

| Cách | Dành cho | Ưu điểm |
|---|---|---|
| [A. Bộ cài Windows](#a-bộ-cài-windows-khuyên-dùng) | Hầu hết mọi người dùng Windows 10/11 | Cửa sổ trình duyệt thật, mượt nhất, có âm thanh; tự cập nhật |
| [B. Docker](#b-docker-mọi-hệ-điều-hành) | Windows / macOS / Linux, máy chủ, máy cấu hình khác nhau | Chạy giống hệt nhau trên mọi máy; không cài Python hay Chromium vào máy |
| [C. Từ mã nguồn](#c-từ-mã-nguồn-dành-cho-người-phát-triển) | Người muốn sửa code | Toàn quyền chỉnh sửa |

**Mọi bản cập nhật đều không cần gỡ app hay tải lại từ đầu.** API key, cài đặt và các phiên đăng nhập (Cambridge, Rikkei...) luôn được giữ nguyên. Xem [Cập nhật](#cập-nhật).

---

## A. Bộ cài Windows (khuyên dùng)

1. Vào trang [Releases](https://github.com/Thequancute215-sys/Auto-Browser/releases/latest), tải file **`AutoBrowser-Setup-<phiên bản>.exe`**.
2. Chạy file vừa tải. Không cần quyền quản trị (admin).
   - Bộ cài chưa được ký số, nên Windows có thể hiện *"Windows protected your PC"*. Bấm **More info → Run anyway**.
3. Mở **Auto Browser** bằng biểu tượng trên Desktop hoặc trong Start menu.
   - Lần đầu chạy, app tự tải Chromium (khoảng 150 MB) và hiện một cửa sổ tải xuống. Chờ đến khi xong.
4. Màn hình chia hai: **bên trái** là cửa sổ trình duyệt bạn dùng bình thường (đăng nhập Cambridge/Rikkei ở đây, chỉ cần một lần), **bên phải** là bảng **Trợ lý AI**.
5. Chọn AI: xem [Chọn AI](#chọn-ai).

Gỡ cài đặt: *Settings → Apps → Auto Browser → Uninstall*. Cài đặt của bạn ở `%LOCALAPPDATA%\AutoBrowser` không bị xóa, nên cài lại sẽ có sẵn.

## B. Docker (mọi hệ điều hành)

Cần cài [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) hoặc Docker Engine (Linux).

1. Tạo một thư mục trống, tải file [`docker-compose.yml`](docker-compose.yml) vào đó.
2. Mở Terminal / PowerShell tại thư mục đó và chạy:
   ```bash
   docker compose up -d
   ```
3. Mở trình duyệt vào **http://localhost:8000**.

Ở chế độ Docker, trình duyệt chạy ẩn trong container và hình ảnh được truyền vào trang web (click → hình cập nhật khoảng 40–60 ms). Vì lý do bảo mật, chỉ máy đang chạy Docker mới mở được giao diện.

| Việc cần làm | Lệnh |
|---|---|
| Dừng | `docker compose down` (dữ liệu vẫn còn) |
| Xem nhật ký | `docker compose logs -f` |
| Chạy kèm AI trên máy trong Docker | `docker compose --profile local-ai up -d`, rồi đặt `OLLAMA_HOST=http://ollama:11434` trong file `.env` |
| Tự động cập nhật | `docker compose --profile autoupdate up -d` |
| Tự build từ mã nguồn | `git clone` kho này rồi `docker compose up -d --build` |

> **Kho GitHub đang ở chế độ riêng tư?** Trước khi tải image, đăng nhập bằng một [Personal access token](https://github.com/settings/tokens) có quyền `read:packages`:
> `docker login ghcr.io -u <tên GitHub> -p <token>`

## C. Từ mã nguồn (dành cho người phát triển)

Cần Python 3.13 và Git.
```powershell
git clone https://github.com/Thequancute215-sys/Auto-Browser.git
cd Auto-Browser
.\start.bat          # Windows: tự cài thư viện, tải Chromium và mở http://localhost:8000
```
Trên macOS/Linux:
```bash
python3 -m pip install -r requirements.txt && python3 -m playwright install --with-deps chromium
python3 server.py    # rồi mở http://localhost:8000
```

---

## Chọn AI

Mở **⚙️ Cài đặt** trong bảng AI và chọn một trong ba:

| AI | Chi phí | Giới hạn | Ghi chú |
|---|---|---|---|
| **Google Gemini / Gemma** (mặc định) | Miễn phí | Mỗi mô hình có hạn mức theo ngày (Gemini Flash khoảng 20 lượt/ngày) | App tự xoay vòng giữa các mô hình Gemini Flash, Flash-Lite và Gemma: khi một mô hình hết lượt thì chuyển sang mô hình khác, nên tổng số lượt mỗi ngày lớn hơn nhiều. Khóa miễn phí tại [Google AI Studio](https://aistudio.google.com/app/apikey). Bắt buộc cho bài nghe. |
| **Claude (Anthropic)** | Trả phí (khoảng 0,05–0,1 USD mỗi bước) | Không giới hạn theo ngày | Suy luận kỹ. Khóa tại [console.anthropic.com](https://console.anthropic.com/settings/keys). |
| **Trên máy (Ollama)** | Miễn phí | **Không giới hạn** | Chạy hoàn toàn trên máy bạn, dữ liệu không rời máy. Chậm hơn đám mây, cần máy tương đối khỏe (khuyên dùng 16 GB RAM). |

### Cài AI chạy trên máy (Ollama)

1. Cài [Ollama](https://ollama.com/download).
2. Mở Terminal / PowerShell, tải mô hình (khoảng 6,6 GB, đọc được cả hình ảnh):
   ```bash
   ollama pull gemma4:e4b
   ```
   Máy yếu (8 GB RAM) có thể dùng `gemma4:e2b` (nhỏ hơn, kém chính xác hơn).
3. Trong ⚙️ Cài đặt chọn **Trên máy (Ollama)**, hoặc giữ Gemini và bật **"Khi Gemini/Gemma đám mây hết lượt miễn phí, tự chuyển sang AI trên máy"**. Với lựa chọn sau, các tác vụ dài không bị dừng giữa chừng khi hết lượt.

## Cách dùng nhanh

- **Giải bài:** mở bài tập trong trình duyệt rồi bấm **Chạy**, hoặc gõ *"giải bài này"*.
- **Đi tới một mục ở bất kỳ đâu trên trang:** gõ *"vào mục Lesson 5"*, *"mở phần Bài tập"*, *"vao muc buoi 2"* (gõ không dấu cũng được). App tìm trên toàn trang, kể cả phần chưa cuộn tới, rồi mở mục đó mà **không tốn lượt gọi AI**.
- **Gợi ý thay vì tự làm:** chọn **Gợi ý** để AI chỉ hiện đáp án kèm giải thích.
- **An toàn khi nộp bài:** AI không được bấm *Nộp / Submit / Check* khi trang còn câu chưa trả lời.

## Cập nhật

| Bạn cài bằng | Cách cập nhật | Có cần gỡ / tải lại? |
|---|---|---|
| Bộ cài Windows | **Tự động**: khi mở app, nếu có bản mới thì app tự tải, kiểm tra mã SHA-256, cài đè và mở lại. Hoặc bấm **Cập nhật ngay** trên thông báo trong bảng AI. Tắt được trong ⚙️ Cài đặt → Cập nhật. | Không |
| Docker | `docker compose pull && docker compose up -d`, hoặc bật profile `autoupdate` để tự cập nhật mỗi 6 giờ | Không |
| Mã nguồn | Chạy lại `start.bat` (tự `git pull`), hoặc `git pull` | Không |

Dữ liệu được giữ qua mọi bản cập nhật:

| Dữ liệu | Bộ cài Windows / mã nguồn | Docker |
|---|---|---|
| API key và cài đặt AI | `%LOCALAPPDATA%\AutoBrowser\ai_config.json` | volume `autobrowser-data` |
| Phiên đăng nhập các trang web | `~\.ai_browser_profile` | volume `autobrowser-data` |

> Nếu kho GitHub đang để riêng tư, app cần một GitHub token chỉ đọc (⚙️ Cài đặt → Cập nhật) để kiểm tra bản mới.

## Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| "Tất cả mô hình Google đã hết lượt miễn phí hôm nay" | Đợi đến khi có lượt lại (app báo thời gian), bật AI trên máy (Ollama), hoặc chuyển sang Claude. |
| "Chưa thấy Ollama" | Mở ứng dụng Ollama (biểu tượng ở khay hệ thống), rồi mở lại ⚙️ Cài đặt. Trong Docker, kiểm tra biến `OLLAMA_HOST`. |
| Cửa sổ app không hiện | Xem nhật ký tại `%LOCALAPPDATA%\AutoBrowser\logs\app.log`. |
| Cổng 8000 đã bị chiếm | App desktop tự chọn cổng trống trong khoảng 8000–8010. Với Docker, đổi `"127.0.0.1:8000:8000"` thành `"127.0.0.1:8080:8000"` rồi mở http://localhost:8080. |
| Docker báo `denied` khi tải image | Kho đang riêng tư: xem hộp lưu ý ở mục B. |
