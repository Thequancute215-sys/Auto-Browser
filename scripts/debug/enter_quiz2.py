import json
import urllib.request
import time
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

print("Clicking 'Hoàn thành chặng →'...")
res = eval_js("""
(() => {
    const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Hoàn thành chặng'));
    if (btn) {
        btn.click();
        return { clicked: true, text: btn.innerText };
    }
    return { clicked: false };
})()
""")
print("Result:", res)
time.sleep(3)

content = eval_js("document.body.innerText")
print("=== TRẮC NGHIỆM LESSON 2 ===")
print(content[:3000])
