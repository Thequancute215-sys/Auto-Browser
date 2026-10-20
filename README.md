# Auto Browser

Trình duyệt có trợ lý AI (Google Gemini hoặc Claude) **tự đọc đề, làm bài, kể cả bài nghe và kéo thả, rồi kiểm tra lại kết quả** trên Cambridge One, Rikkei LMS và các trang khác. Giao diện dùng GitHub Primer, có sáng/tối.

## Cài đặt (Windows)

1. Chạy `AutoBrowser-Setup-6.0.0.exe` (không cần quyền admin). Bộ cài tạo biểu tượng **Auto Browser** trên Desktop và trong Start menu, kèm trình gỡ cài đặt.
2. Mở **Auto Browser**. Màn hình chia hai:
   - **bên trái**: cửa sổ Chromium thật, mượt như trình duyệt thường và có âm thanh. Đăng nhập Cambridge/Rikkei ở đây (chỉ cần một lần);
   - **bên phải**: bảng Trợ lý AI.
3. Trong bảng AI, bấm **Cài đặt** (bánh răng), chọn AI rồi dán khóa và bấm **Lưu & kiểm tra kết nối**:
   - **Google Gemini** (mặc định): miễn phí, có giới hạn lượt mỗi ngày. Khóa lấy tại https://aistudio.google.com/app/apikey.
   - **Claude (Anthropic)**: trả phí theo lượt dùng (khoảng 0,05–0,1 USD mỗi bước với Claude Opus 5.5), suy luận kỹ, không bị giới hạn lượt miễn phí. Khóa lấy tại https://console.anthropic.com/settings/keys. Claude không nghe được audio, nên **bài nghe vẫn cần khóa Gemini** (chỉ dùng để chép lời).
4. Mở bài tập trong cửa sổ trình duyệt, rồi bấm **Chạy**.

Đóng một trong hai cửa sổ là thoát hẳn ứng dụng. Cài đặt AI và nhật ký nằm ở `%LOCALAPPDATA%\AutoBrowser`. Phiên đăng nhập các trang web lưu ở `~\.ai_browser_profile`. Lần đầu chạy trên máy mới, ứng dụng tự tải Chromium (~150 MB).

## Trợ lý AI

- **Tự làm**: điền từ, chọn đáp án, chọn trong danh sách, kéo thả (7 cách, tự chọn cách phù hợp), sau đó nhìn lại màn hình để kiểm tra. Tùy chọn **Tự nộp** và **Tự qua bài**. Vòng tròn xanh trong cửa sổ trình duyệt cho thấy AI đang thao tác ở đâu.
- **Bài nghe**: AI tự bấm Play, tải audio, chép lời rồi làm bài.
- **Gợi ý**: chỉ hiện thẻ đáp án kèm giải thích tiếng Việt, bạn tự làm.
- Ra lệnh tự nhiên: *"giải bài này"*, *"bấm nút Login"*, *"tóm tắt trang"*, *"mở youtube"*. Nút **Dừng** có tác dụng ngay.

## Chế độ web (không cần cài)

`python server.py` rồi mở `http://localhost:8000`. Trình duyệt chạy nền và hình được truyền vào trang web (click → hình cập nhật khoảng 42 ms, cuộn khoảng 50 khung/giây). Gõ phím thật, tiếng Việt, Ctrl+C/Ctrl+V, kéo thả và bôi đen đều dùng được.

## Phát triển

```powershell
pip install -r requirements-dev.txt; python -m playwright install chromium
python -m pytest                     # 74 test (~5,5 phút): kéo thả, trình duyệt, input nhanh, agent, Claude, server, UI
python -m pytest -m llm              # agent end-to-end với Gemini thật
python tools/bench_smoothness.py     # đo độ mượt chế độ web → tests/reports/smoothness.md
python tools/build_exe.py            # dist/AutoBrowser/AutoBrowser.exe + dist/AutoBrowser-Setup-x.y.z.exe
python tools/test_desktop_exe.py     # kiểm tra app desktop đã đóng gói
```

Kiến trúc, API, kết quả kiểm thử và giới hạn: [HANDOVER.md](HANDOVER.md). Quy tắc cho AI coding agent: [CLAUDE.md](CLAUDE.md).
