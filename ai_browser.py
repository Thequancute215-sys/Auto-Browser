"""
AI Browser CLI Controller
Enables AI agents and command-line scripts to programmatically automate the browser workspace.
"""

import sys
import json
import urllib.request
import config

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

API_BASE = f"http://localhost:{config.PORT}/api"

def call_api(endpoint: str, payload: dict) -> dict:
    url = f"{API_BASE}/{endpoint}"
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

def get_state() -> dict:
    url = f"{API_BASE}/state"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

def print_help():
    print("""
Cách sử dụng ai_browser.py:
  python ai_browser.py goto <url>         - Chuyển hướng tới URL
  python ai_browser.py click <x> <y>      - Click chuột tại tọa độ (x, y)
  python ai_browser.py type <văn bản>     - Gõ văn bản vào vị trí đang focus
  python ai_browser.py key <tên phím>     - Nhấn phím (Enter, Tab, Backspace, v.v.)
  python ai_browser.py scroll <deltaY>    - Cuộn trang (+250 xuống, -250 lên)
  python ai_browser.py extract            - Đọc toàn bộ nội dung chữ / câu hỏi trên trang
  python ai_browser.py eval <code JS>     - Chạy script JavaScript trên trang
  python ai_browser.py state              - Xem danh sách tab, URL và tọa độ chuột hiện tại
""")

def main():
    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "goto" and len(sys.argv) > 2:
        print(call_api("goto", {"url": sys.argv[2]}))

    elif cmd == "click" and len(sys.argv) >= 4:
        x, y = int(sys.argv[2]), int(sys.argv[3])
        btn = sys.argv[4] if len(sys.argv) > 4 else "left"
        print(call_api("click", {"x": x, "y": y, "button": btn}))

    elif cmd == "type" and len(sys.argv) > 2:
        text = " ".join(sys.argv[2:])
        print(call_api("type", {"text": text}))

    elif cmd == "key" and len(sys.argv) > 2:
        print(call_api("type", {"key": sys.argv[2]}))

    elif cmd == "scroll" and len(sys.argv) > 2:
        print(call_api("scroll", {"deltaY": int(sys.argv[2])}))

    elif cmd == "extract":
        res = call_api("ai_command", {"cmd": "extract_text", "payload": {}})
        print(res.get("text", "")[:4000])

    elif cmd == "eval" and len(sys.argv) > 2:
        script = " ".join(sys.argv[2:])
        res = call_api("ai_command", {"cmd": "eval", "payload": {"script": script}})
        print(res.get("result"))

    elif cmd == "state":
        print(json.dumps(get_state(), indent=2, ensure_ascii=False))

    else:
        print_help()

if __name__ == "__main__":
    main()
