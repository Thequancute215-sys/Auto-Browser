import os
import re

# Server configuration
# 127.0.0.1 = chỉ máy này truy cập được. Đặt ALLOW_LAN=1 nếu thật sự muốn mở ra mạng LAN
# (cảnh báo: ai trong mạng cũng điều khiển được trình duyệt đã đăng nhập của bạn).
ALLOW_LAN = os.environ.get("ALLOW_LAN", "0") == "1"
# AIB_BIND only changes which network interface the server listens on (Docker needs 0.0.0.0 inside the
# container); requests must still come from localhost unless ALLOW_LAN=1. Publish the port as
# 127.0.0.1:8000:8000 so only the host machine can reach it.
HOST = os.environ.get("AIB_BIND") or ("0.0.0.0" if ALLOW_LAN else "127.0.0.1")
IN_DOCKER = os.environ.get("AIB_IN_DOCKER") == "1"
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
# Chromium extension that adds "Phân tích AI" to the right-click menu (window mode only)
EXTENSION_DIR = os.path.join(BASE_DIR, "browser_ext")
PROFILE_DIR = os.environ.get("AIB_PROFILE_DIR") or os.path.join(os.path.expanduser("~"), ".ai_browser_profile")
# Dọn các tiến trình chrome-headless-shell còn sót từ lần chạy trước (tắt bằng AIB_KILL_STALE=0)
KILL_STALE_BROWSERS = os.environ.get("AIB_KILL_STALE", "1") == "1"
AI_CONFIG_FILE = os.environ.get("AIB_AI_CONFIG") or os.path.join(BASE_DIR, "ai_config.json")

# Default URLs
# Page opened at start. Personal class/assignment links belong in the user's own settings
# ("start_url" in ai_config.json, passed in as AIB_START_URL), never in the repository.
CAMBRIDGE_URL = "https://www.cambridgeone.org/home"


def _saved(key: str, default=None):
    """A value from the user's saved settings (ai_config.json), read once at startup."""
    try:
        import json
        with open(AI_CONFIG_FILE, "r", encoding="utf-8") as f:
            value = json.load(f).get(key)
        return default if value is None else value
    except Exception:
        return default


def _saved_start_url() -> str:
    return str(_saved("start_url", "") or "").strip()


DEFAULT_URL = os.environ.get("AIB_START_URL") or _saved_start_url() or CAMBRIDGE_URL

# Ad servers are blocked inside the automated browser: Google "vignette" interstitials pop up on the first
# click and turn the agent's click into an ad visit (seen on englishpage.com), and ads clutter the screenshots.
# Only ad-serving hosts are listed (no analytics/CDNs), so sites keep working. Off: AIB_BLOCK_ADS=0 or
# "block_ads": false in ai_config.json.
BLOCK_ADS = os.environ.get("AIB_BLOCK_ADS", "1" if _saved("block_ads", True) else "0") == "1"
AD_HOSTS = (
    "doubleclick.net", "googlesyndication.com", "googleadservices.com", "googletagservices.com",
    "adservice.google.com", "fundingchoicesmessages.google.com", "amazon-adsystem.com", "adnxs.com",
    "taboola.com", "outbrain.com", "criteo.com", "criteo.net", "pubmatic.com", "rubiconproject.com",
    "openx.net", "casalemedia.com", "moatads.com", "adsrvr.org", "smartadserver.com", "teads.tv",
    "media.net", "popads.net", "propellerads.com", "adform.net", "yieldmo.com", "sharethrough.com",
)
AD_URL_RE = re.compile(r"^https?://([^/?#]*\.)?(" + "|".join(re.escape(h) for h in AD_HOSTS) + r")(:\d+)?([/?#]|$)")
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
    "--password-store=basic",
    "--use-mock-keychain",
]

# Keep Chromium's sandbox ON for the desktop/stream build: the agent browses arbitrary,
# possibly hostile sites with the user's logins, so the renderer sandbox is the main
# containment for a malicious page. In Docker (non-root pwuser, no user namespaces) the
# sandbox cannot start, so it is disabled only there.
if IN_DOCKER:
    CHROMIUM_ARGS.append("--no-sandbox")
