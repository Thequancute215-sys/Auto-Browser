import urllib.request
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')
p2 = json.dumps({'cmd': 'eval', 'payload': {'script': """(() => {
    const el = Array.from(document.querySelectorAll('*')).find(e => ['auto', 'scroll'].includes(window.getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight);
    return el ? { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight } : null;
})()"""}}).encode('utf-8')
r2 = urllib.request.Request('http://127.0.0.1:8000/api/ai_command', data=p2, headers={'Content-Type': 'application/json'})
print('Current scroll state:', urllib.request.urlopen(r2).read().decode('utf-8'))
