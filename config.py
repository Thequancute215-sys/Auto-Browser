import os

# Server configuration
# 127.0.0.1 = chỉ máy này truy cập được. Đặt ALLOW_LAN=1 nếu thật sự muốn mở ra mạng LAN
# (cảnh báo: ai trong mạng cũng điều khiển được trình duyệt đã đăng nhập của bạn).
ALLOW_LAN = os.environ.get("ALLOW_LAN", "0") == "1"
HOST = "0.0.0.0" if ALLOW_LAN else "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))

# Browser configuration
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800
VIEWPORT = {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
VIEWPORT_PRESETS = [(1280, 800), (1366, 768), (1440, 900), (1024, 768), (1600, 900)]

# Screencast (CDP Page.startScreencast): chỉ phát khung hình khi màn hình thay đổi
# "stream": Chromium chạy nền, hình được truyền vào giao diện web (dùng từ trình duyệt bất kỳ).
# "window": cửa sổ Chromium thật (mượt như trình duyệt thường, có âm thanh) + bảng AI riêng; app desktop dùng chế độ này.
BROWSER_MODE = os.environ.get("AIB_BROWSER_MODE", "stream")
# Vị trí/kích thước cửa sổ Chromium ở chế độ window: "x,y,w,h" (do desktop_app.py tính theo màn hình)
WINDOW_RECT = os.environ.get("AIB_WINDOW_RECT", "")
STREAM_QUALITY = 72          # JPEG quality của luồng trực tiếp
FAST_CDP = os.environ.get("AIB_FAST_CDP", "1") == "1"  # kênh CDP trực tiếp cho hình + input (tắt: AIB_FAST_CDP=0)
AI_SCREENSHOT_QUALITY = 80   # JPEG quality của ảnh gửi cho Gemini

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PROFILE_DIR = os.environ.get("AIB_PROFILE_DIR") or os.path.join(os.path.expanduser("~"), ".ai_browser_profile")
# Dọn các tiến trình chrome-headless-shell còn sót từ lần chạy trước (tắt bằng AIB_KILL_STALE=0)
KILL_STALE_BROWSERS = os.environ.get("AIB_KILL_STALE", "1") == "1"
AI_CONFIG_FILE = os.environ.get("AIB_AI_CONFIG") or os.path.join(BASE_DIR, "ai_config.json")

# Default URLs
DEFAULT_URL = (
    "https://www.cambridgeone.org/nlp/learner/org_cup_P5P89k5Xlm9dZYgWcNtWU/class/"
    "0dbf774e-59f2-4477-bc4e-336e02cddf5b/product/empbe2ednl3dw/assignment/"
    "64fffda9-288b-4dc4-81a4-a2e2333d07f6/view/item/1626243307374%2F1626245741894%2F1626258839518"
)
RIKKEI_URL = "https://lms-student.rikkei.edu.vn"

# User-Agent for realistic browsing
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",                          # Prevent SwiftShader 100% CPU runaway on Windows
    "--disable-software-rasterizer",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-extensions",
    "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--disable-sync",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--no-first-run",
    "--mute-audio",
    "--no-sandbox",
    "--password-store=basic",
    "--use-mock-keychain",
]
