# Auto Browser

Trình duyệt Chromium chạy nền, điều khiển qua giao diện web (GitHub Primer, sáng/tối), kèm một trợ lý AI (Google Gemini) **tự đọc đề, làm bài, kể cả bài nghe và kéo thả, rồi kiểm tra lại kết quả** trên Cambridge One, Rikkei LMS và các trang khác.

## Bắt đầu nhanh

1. Nhấp đúp `start.bat` (lần đầu tự cài thư viện), hoặc chạy:
   ```powershell
   pip install -r requirements.txt
   python -m playwright install chromium
   python server.py
   ```
2. Mở `http://localhost:8000`.
3. Bấm **Cài đặt**, dán Gemini API Key (miễn phí tại https://aistudio.google.com/app/apikey), rồi bấm **Lưu & kiểm tra kết nối**. Khóa chỉ lưu trong `ai_config.json` trên máy bạn; file này không bao giờ được commit.
4. Đăng nhập vào trang học ngay trên màn hình trực tiếp (chỉ cần một lần), mở bài tập, rồi bấm **Chạy**.

## Màn hình trực tiếp

- **Chuột**: click, kéo thả, bôi đen, double-click, chuột phải, cuộn: như trình duyệt thật, kể cả trong iframe.
- **Bàn phím**: click vào màn hình rồi gõ. Hỗ trợ tiếng Việt (Unikey/IME), tổ hợp phím, **Ctrl+C** (chép chữ của trang về máy bạn), **Ctrl+V** (dán vào trang).
- **Bôi đen & Copy**: bật chế độ này thì kéo chuột qua chữ là tự sao chép.
- **Trích xuất chữ**: xem, lọc và sao chép toàn bộ văn bản trên trang.

## Trợ lý AI

- **Tự làm**: điền từ, chọn đáp án, chọn trong danh sách, kéo thả (7 cách, tự chọn cách phù hợp), sau đó nhìn lại màn hình để kiểm tra. Tùy chọn **Tự nộp** và **Tự qua bài**.
- **Bài nghe**: AI tự bấm Play, tải audio, chép lời rồi làm bài.
- **Gợi ý**: chỉ hiện thẻ đáp án kèm giải thích tiếng Việt, bạn tự làm.
- Ra lệnh tự nhiên: *"giải bài này"*, *"bấm nút Login"*, *"tóm tắt trang"*, *"mở youtube"*, *"cuộn xuống 600"*.
- Nút **Dừng** có tác dụng ngay lập tức.

## Phát triển

```powershell
pip install -r requirements-dev.txt
python -m pytest            # 49 test: kéo thả, trình duyệt, agent, server, UI (~5 phút)
python -m pytest -m llm     # chạy agent end-to-end với Gemini thật
```

Kiến trúc, API, kết quả kiểm thử và giới hạn: [HANDOVER.md](HANDOVER.md). Quy tắc cho AI coding agent: [CLAUDE.md](CLAUDE.md).
