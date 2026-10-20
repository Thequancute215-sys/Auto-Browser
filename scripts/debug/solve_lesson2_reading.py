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

ans1 = """Nhà quản trị là người chịu trách nhiệm điều hành, định hướng, chỉ huy và phối hợp các nguồn lực (nhân lực, tài lực, vật lực, thông tin) thông qua việc thực thi chu trình quản trị nhằm hoàn thành mục tiêu của tổ chức một cách hiệu quả và hiệu suất.
3 cấp bậc quản trị trong doanh nghiệp gồm:
1. Quản trị cấp cao (Top Management): Như Chủ tịch, CEO - chịu trách nhiệm hoạch định chiến lược dài hạn cho toàn bộ tổ chức.
2. Quản trị cấp trung (Middle Management): Như Trưởng phòng ban, Giám đốc khối - cụ thể hóa chiến lược thành kế hoạch hành động và điều phối cấp cơ sở.
3. Quản trị cấp cơ sở (First-line Management): Như Tổ trưởng, Trưởng nhóm - trực tiếp giám sát và phân công công việc hàng ngày cho nhân viên trực tiếp."""

ans2 = """Theo mô hình của Robert L. Katz, 3 nhóm kỹ năng quản trị cốt lõi gồm:
1. Kỹ năng tư duy (Conceptual skills): Khả năng tư duy chiến lược, nhìn nhận toàn cảnh tổ chức và dự báo xu hướng tương lai.
2. Kỹ năng nhân sự (Human skills): Khả năng giao tiếp, tạo động lực, truyền cảm hứng và làm việc hiệu quả với con người.
3. Kỹ năng chuyên môn / kỹ thuật (Technical skills): Sự am hiểu và thành thạo các nghiệp vụ, phương pháp, công cụ kỹ thuật cụ thể."""

ans3 = """- Cấp bậc đòi hỏi kỹ năng tư duy (Conceptual skills) cao nhất là Quản trị cấp cao (Top Management), vì họ phải đưa ra các quyết định chiến lược vĩ mô cho doanh nghiệp.
- Cấp bậc đòi hỏi kỹ năng chuyên môn (Technical skills) cao nhất là Quản trị cấp cơ sở (First-line Management), vì họ trực tiếp hướng dẫn nghiệp vụ và giám sát quy trình thực thi kỹ thuật hàng ngày của nhân viên."""

fill_script = f"""
(() => {{
    const boxes = Array.from(document.querySelectorAll('.ProseMirror[contenteditable="true"]'));
    if (boxes.length < 3) return {{ error: "Found " + boxes.length + " boxes" }};

    const answers = [{json.dumps(ans1)}, {json.dumps(ans2)}, {json.dumps(ans3)}];

    for (let i = 0; i < 3; i++) {{
        boxes[i].innerHTML = '<p>' + answers[i].replace(/\\n/g, '</p><p>') + '</p>';
        boxes[i].dispatchEvent(new Event('input', {{ bubbles: true }}));
        boxes[i].dispatchEvent(new Event('change', {{ bubbles: true }}));
    }}

    return {{ success: true }};
}})()
"""

print("Filling Lesson 2 answers...")
res = eval_js(fill_script)
print("Fill result:", res)
time.sleep(1)

print("Clicking 'Nộp lại để chấm'...")
submit_res = eval_js("""
(() => {
    const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Nộp lại để chấm') || b.innerText.includes('Nộp bài đọc'));
    if (btn) {
        btn.click();
        return { clicked: true, text: btn.innerText };
    }
    return { clicked: false };
})()
""")
print("Submit result:", submit_res)
time.sleep(4)

# Extract grading result
result_text = eval_js("""
(() => {
    const h3 = Array.from(document.querySelectorAll('h3')).find(h => h.innerText.includes('Câu hỏi kiểm tra'));
    return h3 ? h3.parentElement.innerText : 'Not found';
})()
""")
print("=== KẾT QUẢ CHẤM BÀI ĐỌC LESSON 2 ===")
print(result_text[:2000])
