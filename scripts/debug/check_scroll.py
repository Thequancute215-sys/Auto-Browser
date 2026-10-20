import json
import urllib.request
import sys

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def eval_js(script):
    url = "http://localhost:8000/api/ai_command"
    payload = json.dumps({"cmd": "eval", "payload": {"script": script}}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8')).get('result')

scroll_info = eval_js("""
(() => {
    // Find all elements that can scroll
    const scrollables = [];
    const all = document.querySelectorAll('*');
    for (let el of all) {
        if (el.scrollHeight > el.clientHeight + 10) {
            const style = window.getComputedStyle(el);
            if (['auto', 'scroll'].includes(style.overflowY) || el === document.body || el === document.documentElement) {
                scrollables.push({
                    tag: el.tagName,
                    className: el.className.slice(0, 50),
                    scrollHeight: el.scrollHeight,
                    clientHeight: el.clientHeight,
                    scrollTop: el.scrollTop
                });
            }
        }
    }
    return {
        windowScrollY: window.scrollY,
        documentScrollTop: document.documentElement.scrollTop,
        bodyScrollTop: document.body.scrollTop,
        scrollables: scrollables
    };
})()
""")

print("Scroll info:", json.dumps(scroll_info, indent=2, ensure_ascii=False))
