import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import config
from llm import GeminiClient, LLMError, parse_json_response

DEFAULT_SETTINGS = {
    "api_key": "",
    "model": "gemini-flash-latest",
    # Free-tier quota is per model, so a chain keeps the agent working when one model is exhausted.
    "fallback_models": ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-lite-latest"],
    "mode": "auto",            # "auto" = tự điền đáp án, "suggest" = chỉ gợi ý đáp án trong chat
    "auto_submit": True,       # bấm Check/Submit sau khi điền xong
    "auto_next": False,        # tự chuyển sang bài tiếp theo
    "max_steps": 30,
    "action_delay_ms": 150,
    "listen_audio": True,      # tải audio của bài nghe, nhờ Gemini chép lời rồi dùng để làm bài
}

TRANSCRIBE_PROMPT = """Transcribe this audio from an English-learning exercise verbatim.
- Keep the original language; do not translate or summarise.
- If there are several speakers, prefix lines with "A:", "B:" (or names if they are said).
- Mark unclear words as [unclear]. Output plain text only."""

SITE_SHORTCUTS = {
    "cambridge": config.DEFAULT_URL,
    "rikkei": config.RIKKEI_URL,
    "lms": config.RIKKEI_URL,
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
}

SYSTEM_PROMPT = """You are an autonomous browser agent driving a real Chromium browser for a Vietnamese student on e-learning sites (Cambridge One, Rikkei LMS and others).

Every turn you receive: the user's task, run settings, a screenshot of the current viewport in which every interactive element is outlined by a colored box with a numeric ID label, the list of those elements, the visible page text, and the history of your previous steps.

Reply with ONLY one JSON object:
{
  "observation": "what the screen shows now (1-2 short sentences)",
  "plan": "what you do in this step and why (short)",
  "answers": [{"q": "question or blank, short", "a": "the answer", "why": "short reason, in Vietnamese"}],
  "actions": [ ...actions... ],
  "status": "continue" | "done" | "need_user",
  "message": "short message to the user, in Vietnamese"
}

Actions (only use element IDs from the CURRENT element list):
- {"action":"click","id":N}
- {"action":"type","id":N,"text":"...","enter":false}      replaces the field's content
- {"action":"select","id":N,"option":"visible option text"}  native <select> only
- {"action":"drag","id":N,"target_id":M}                    drag-and-drop exercises
- {"action":"hover","id":N}
- {"action":"scroll","direction":"down"|"up","amount":600}   add "id":N to scroll inside that container
- {"action":"press","key":"Enter"}                          Playwright key names, e.g. Tab, Escape, Control+A
- {"action":"navigate","url":"https://..."}
- {"action":"wait","ms":1500}

Rules:
1. Solve exercises for real. Read the instructions, the example, the questions, any reading passage and every option before answering. Accuracy matters more than speed: check grammar, tense, collocations, spelling and the meaning of the context.
2. Batch work: do every answer action visible on this screen in ONE step, in reading order. Actions run sequentially; you will see the resulting screen next turn.
3. IDs are renumbered every turn. Never reuse IDs from the history.
4. A field's current content is in `value`. Its blank is located by the `before`/`after` text. Leave fields that are already correct alone.
5. `checked` marks a selected radio/checkbox. Do not click an option that is already checked (it may toggle off).
5b. Drag-and-drop exercises: use "drag" from the `draggable` word/item to the `dropzone` gap (its `context` shows where [HERE] sits in the sentence). Clicking a draggable item does not place it.
6. If more questions are below, scroll. Put the scroll LAST in the actions list (IDs change after scrolling).
7. Use status "done" only when the screen proves the task is finished and no action is left. If you still have actions, use "continue" and verify next turn.
8. Login, CAPTCHA, payments, or a choice only the user can make: status "need_user" and explain in "message".
9. If an earlier action failed or changed nothing, try a different approach (another element, keyboard, scrolling, waiting for loading) instead of repeating it.
10. "answers" lists only answers decided in THIS step. Keep every text field short.
11. Listening exercises: answer from the AUDIO TRANSCRIPTS section. If the activity has audio but no transcript is listed yet, click its play button once and add a "wait" of 3000 ms; the audio is captured and transcribed automatically for the next turn."""


def _now() -> str:
    return time.strftime("%H:%M:%S")


class AIController:
    """
    Observe → Think → Act → Verify agent loop on top of BrowserManager.

    Each step: annotate interactive elements across all frames (Set-of-Marks screenshot + element list
    + visible text), ask Gemini for a JSON plan, execute the actions with real mouse/keyboard input,
    then look again. The loop ends only when the model confirms completion from the screen, the
    user stops it, the step budget runs out, or no progress is detected.
    """

    def __init__(self, browser_manager, bus):
        self.browser = browser_manager
        self.bus = bus
        self.llm = GeminiClient()
        self.settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._load_config()
        self._run_settings: Dict[str, Any] = dict(self.settings)
        self._transcripts: Dict[str, str] = {}  # audio URL -> transcript (kept for the whole session)
        self._media_failed: set = set()

        self.lock = threading.Lock()
        self.messages: List[Dict[str, Any]] = []
        self._msg_seq = 0
        self.is_running = False
        self._stop_event = threading.Event()
        self.current_task_thread: Optional[threading.Thread] = None
        self.run_state = {"step": 0, "max_steps": 0, "phase": "idle", "task": "", "detail": ""}

        self.add_message(
            "ai",
            "Xin chào! Tôi là trợ lý AI điều khiển trình duyệt.\n"
            "- Giải bài: tôi đọc màn hình (cả nội dung trong iframe), suy luận đáp án rồi **tự điền / chọn / kéo thả** bằng chuột & bàn phím thật, sau đó **kiểm tra lại** kết quả.\n"
            "- Ra lệnh tự nhiên: *\"giải bài này\"*, *\"bấm nút Login\"*, *\"mở youtube\"*, *\"cuộn xuống 600\"*...\n"
            "- Chế độ **Gợi ý** chỉ hiện đáp án kèm giải thích, không tự điền.\n\n"
            "Mở ⚙️ **Cài đặt** để nhập Gemini API Key, sau đó bấm **Chạy**.",
            kind="intro",
        )

    # ------------------------------------------------------------------ config

    def _load_config(self):
        if os.path.exists(config.AI_CONFIG_FILE):
            try:
                with open(config.AI_CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                for k in DEFAULT_SETTINGS:
                    if k in saved:
                        self.settings[k] = saved[k]
            except Exception:
                pass
        if not self.settings.get("api_key"):
            self.settings["api_key"] = os.environ.get("GEMINI_API_KEY", "").strip()

    def _persist(self):
        try:
            with open(config.AI_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    @property
    def api_key(self) -> str:
        return (self.settings.get("api_key") or "").strip()

    @property
    def model(self) -> str:
        return (self.settings.get("model") or DEFAULT_SETTINGS["model"]).strip()

    def save_config(self, payload: Dict[str, Any]):
        key = (payload.get("api_key") or "").strip()
        if key:
            self.settings["api_key"] = key
        if payload.get("clear_key"):
            self.settings["api_key"] = ""
        if payload.get("model"):
            self.settings["model"] = str(payload["model"]).strip()
        if payload.get("mode") in ("auto", "suggest"):
            self.settings["mode"] = payload["mode"]
        for flag in ("auto_submit", "auto_next", "listen_audio"):
            if flag in payload:
                self.settings[flag] = bool(payload[flag])
        if "max_steps" in payload:
            try:
                self.settings["max_steps"] = max(1, min(100, int(payload["max_steps"])))
            except (TypeError, ValueError):
                pass
        if "fallback_models" in payload and isinstance(payload["fallback_models"], list):
            self.settings["fallback_models"] = [str(m).strip() for m in payload["fallback_models"] if str(m).strip()][:6]
        self._persist()
        self.bus.publish("config", self.get_config())

    def get_config(self) -> Dict[str, Any]:
        key = self.api_key
        masked = (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("***" if key else "")
        cfg = {k: v for k, v in self.settings.items() if k != "api_key"}
        cfg.update({"has_key": bool(key), "masked_key": masked, "usage": dict(self.llm.usage)})
        return cfg

    def list_models(self) -> List[dict]:
        return self.llm.list_models(self.api_key)

    def test_connection(self) -> str:
        text = self.llm.generate(self.api_key, self.model, "Trả lời đúng 3 từ: 'Kết nối thành công!'",
                                 fallbacks=self.settings.get("fallback_models"), max_output_tokens=64)
        used = self.llm.last_model_used or self.model
        return f"{text.strip()[:60]} (mô hình: {used})"

    # ------------------------------------------------------------------ messages & status

    def add_message(self, role: str, text: str, kind: str = "text", data: Any = None, status: str = "ok"):
        with self.lock:
            self._msg_seq += 1
            msg = {"id": self._msg_seq, "role": role, "kind": kind, "text": text, "data": data,
                   "status": status, "timestamp": _now()}
            self.messages.append(msg)
            if len(self.messages) > 300:
                self.messages = self.messages[-300:]
        self.bus.publish("message", msg)
        return msg

    def _set_phase(self, phase: str, **extra):
        self.run_state.update({"phase": phase, **extra})
        self.bus.publish("ai_status", self._status_payload())

    def _status_payload(self):
        return {"is_running": self.is_running, **self.run_state}

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            msgs = list(self.messages)
        return {**self._status_payload(), "messages": msgs, "config": self.get_config()}

    def clear_messages(self):
        with self.lock:
            self.messages = []
        self.bus.publish("messages_cleared", {})

    # ------------------------------------------------------------------ run / stop

    def stop(self):
        if not self.is_running:
            return
        self._stop_event.set()
        self._set_phase("stopping")
        self.add_message("ai", "🛑 Đang dừng... (thao tác hiện tại sẽ kết thúc ngay sau bước này)", kind="status", status="stopped")

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def run_command(self, user_command: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cmd = (user_command or "").strip() or "Tiếp tục giải bài tập trên trang hiện tại"
        if self.is_running:
            return {"status": "busy", "message": "AI đang chạy. Bấm Dừng trước khi ra lệnh mới."}
        # Per-run overrides (e.g. "Làm liên tục" chip) never touch the saved settings.
        run_settings = dict(self.settings)
        for k in ("mode", "auto_submit", "auto_next", "max_steps"):
            if isinstance(overrides, dict) and k in overrides:
                run_settings[k] = overrides[k]
        self._run_settings = run_settings
        self.add_message("user", cmd)
        self._stop_event.clear()
        self.is_running = True
        self.run_state = {"step": 0, "max_steps": 0, "phase": "starting", "task": cmd, "detail": "Đang bắt đầu"}
        self.bus.publish("ai_status", self._status_payload())
        self.current_task_thread = threading.Thread(target=self._execute_pipeline, args=(cmd,), daemon=True)
        self.current_task_thread.start()
        return {"status": "ok"}

    def _execute_pipeline(self, prompt: str):
        try:
            if not self._try_local_command(prompt):
                if not self.api_key:
                    self.add_message(
                        "ai",
                        "⚠️ **Chưa có Gemini API Key** nên tôi chưa thể đọc và giải bài.\n"
                        "Bấm ⚙️ **Cài đặt** → dán khóa (lấy miễn phí tại aistudio.google.com) → Lưu.\n"
                        "Các lệnh đơn giản vẫn dùng được không cần khóa: *mở <trang>*, *tìm kiếm <từ khóa>*, *cuộn xuống*, *tab mới*.",
                        kind="warning", status="warning")
                else:
                    self._agent_loop(prompt)
        except Exception as e:
            self.add_message("ai", f"❌ Lỗi xử lý: {e}", kind="error", status="error")
        finally:
            self.is_running = False
            self._set_phase("idle", detail="")

    # ------------------------------------------------------------------ deterministic commands

    def _try_local_command(self, prompt: str) -> bool:
        """Handles simple, unambiguous commands instantly without spending an LLM call."""
        text = prompt.strip()
        low = text.lower()

        if re.fullmatch(r"(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?", text):
            self.browser.execute("goto", {"url": text})
            self.add_message("ai", f"🌐 Đã mở {text}", kind="action")
            return True

        m = re.fullmatch(r"(mở|vào|truy cập)( trang| web)? (\S+)", low)
        if m:
            target = m.group(3)
            url = SITE_SHORTCUTS.get(target) or (target if "." in target else None)
            if url:
                self.browser.execute("goto", {"url": url})
                self.add_message("ai", f"🌐 Đã mở {target}", kind="action")
                return True

        m = re.fullmatch(r"(cuộn|kéo|scroll) ?(xuống|lên|down|up)( \d+)?( ?px)?", low)
        if m:
            dist = int(m.group(3)) if m.group(3) else 500
            up = m.group(2) in ("lên", "up")
            self.browser.execute("scroll", {"deltaY": -dist if up else dist})
            self.add_message("ai", f"📜 Đã cuộn {'lên' if up else 'xuống'} {dist}px", kind="action")
            return True

        m = re.match(r"^(tìm kiếm|search|tra cứu|google) (.+)$", text, re.I)
        if m:
            q = m.group(2).strip()
            self.browser.execute("goto", {"url": f"https://www.google.com/search?q={urllib.parse.quote(q)}"})
            self.add_message("ai", f"🔍 Đã tìm kiếm: \"{q}\"", kind="action")
            return True

        simple = {
            "tab mới": ("new_tab", {"url": "https://www.google.com"}, "📑 Đã mở tab mới"),
            "đóng tab": ("close_tab", {}, "📑 Đã đóng tab hiện tại"),
            "quay lại": ("back", {}, "⬅️ Đã quay lại trang trước"),
            "tải lại": ("reload", {}, "🔄 Đã tải lại trang"),
        }
        if low in simple:
            action, payload, msg = simple[low]
            if action == "close_tab":
                payload = {"index": self.browser.active_index}
            self.browser.execute(action, payload)
            self.add_message("ai", msg, kind="action")
            return True
        return False

    # ------------------------------------------------------------------ agent loop

    def _rules_for_settings(self) -> str:
        s = self._run_settings
        lines = []
        if s.get("mode") == "suggest":
            lines.append("SUGGEST-ONLY MODE: do NOT type answers, pick options, drag items or press Check/Submit. "
                         "Only 'scroll' and 'wait' actions are allowed (to read the rest of the exercise). "
                         "Put every answer with a short Vietnamese explanation into `answers`, then set status \"done\".")
        else:
            if s.get("auto_submit"):
                lines.append("When every answer on the activity is filled, press its Check/Submit/Nộp bài button once, "
                             "then read the feedback. If some answers are marked wrong and the page allows changing them, fix them.")
            else:
                lines.append("Do NOT press Check/Submit/Nộp bài buttons. Finish (status \"done\") after filling every answer.")
            if s.get("auto_next"):
                lines.append("After the current activity is complete, go to the next activity (Next / Tiếp theo / Bài tiếp theo) and keep solving.")
            else:
                lines.append("Do NOT move on to another activity or lesson; stop when the current one is complete.")
        return "\n".join(f"- {l}" for l in lines)

    @staticmethod
    def _format_element(e: dict) -> str:
        tag = e.get("tag", "")
        kind = tag
        if e.get("type") and tag == "input":
            kind = f"input:{e['type']}"
        if e.get("editable") == "content":
            kind = "editable"
        if e.get("toggle"):
            kind = f"{tag}/{e['toggle']}"
        if e.get("role"):
            kind += f"[{e['role']}]"
        parts = [f"[{e['id']}] {kind}"]
        if e.get("text"):
            parts.append(json.dumps(e["text"][:100], ensure_ascii=False))
        if e.get("label") and e.get("label") != e.get("text"):
            parts.append(f"label={json.dumps(e['label'][:60], ensure_ascii=False)}")
        if e.get("editable"):
            parts.append(f"value={json.dumps(e.get('value', ''), ensure_ascii=False)}")
            if e.get("before") or e.get("after"):
                parts.append(f"context: …{e.get('before', '')} ___ {e.get('after', '')}…")
        if e.get("dropzone") and not e.get("editable") and (e.get("before") or e.get("after")):
            parts.append(f"context: …{e.get('before', '')} [HERE] {e.get('after', '')}…")
        if e.get("options"):
            parts.append(f"options={json.dumps(e['options'][:15], ensure_ascii=False)}")
        flags = [f for f in ("checked", "selected", "disabled", "draggable", "dropzone") if e.get(f)]
        if flags:
            parts.append("(" + ", ".join(flags) + ")")
        parts.append(f"@{e['x']},{e['y']}")
        return " ".join(parts)

    def _build_prompt(self, task: str, obs: dict, history: List[str], step: int, max_steps: int) -> str:
        elements = obs.get("elements", [])
        el_lines = "\n".join(self._format_element(e) for e in elements) or "(no interactive elements detected)"
        texts, budget = [], 14000
        for t in obs.get("texts", []):
            chunk = t["text"][:budget]
            budget -= len(chunk)
            label = "main page" if t["frame"] == 0 else f"iframe {t['frame']} ({t['url'][:60]})"
            texts.append(f"--- {label} ---\n{chunk}")
            if budget <= 0:
                break
        scroll = obs.get("scroll") or {}
        scroll_note = ""
        if scroll.get("maxY"):
            scroll_note = f"Scroll position: {int(scroll.get('ratio', 0) * 100)}% (can scroll down: {scroll.get('canScrollDown')})"
        hist = "\n".join(history[-10:]) or "(first step)"
        audio_lines = []
        for i, m in enumerate(obs.get("media", []), 1):
            name = m["src"].split("?")[0].rsplit("/", 1)[-1][:60]
            tr = self._transcripts.get(m["src"])
            if tr:
                audio_lines.append(f"--- audio {i} ({name}) ---\n{tr[:6000]}")
            else:
                audio_lines.append(f"--- audio {i} ({name}) --- (not transcribed yet)")
        audio_block = "\n".join(audio_lines) or "(no audio on this page)"
        return f"""TASK FROM USER: {task}

RUN SETTINGS:
{self._rules_for_settings()}

STEP {step} of at most {max_steps}.
PAGE: {obs.get('title', '')} — {obs.get('url', '')}
Viewport: {obs.get('viewport', {}).get('width')}x{obs.get('viewport', {}).get('height')}. {scroll_note}

HISTORY OF YOUR PREVIOUS STEPS:
{hist}

INTERACTIVE ELEMENTS (id, kind, text, state, center position):
{el_lines}

VISIBLE PAGE TEXT:
{chr(10).join(texts) or '(empty)'}

AUDIO TRANSCRIPTS:
{audio_block}

Reply with the JSON object only."""

    @staticmethod
    def _signature(obs: dict) -> str:
        """Id-independent fingerprint of what is on screen, used to detect actions that change nothing."""
        items = [(e.get("tag"), e.get("text"), e.get("value"), e.get("checked"), e.get("selected"), e.get("disabled"))
                 for e in obs.get("elements", [])]
        raw = json.dumps([obs.get("url"), items, (obs.get("scroll") or {}).get("currentY"),
                          sum(len(t["text"]) for t in obs.get("texts", []))], ensure_ascii=False)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _describe_action(self, act: dict, elements: Dict[int, dict]) -> str:
        kind = act.get("action")
        el = elements.get(act.get("id")) if isinstance(act.get("id"), int) else None
        name = ""
        if el:
            name = (el.get("text") or el.get("label") or el.get("before") or "")[:40]
            name = f" «{name}»" if name else ""
        if kind == "click":
            return f"🖱️ Click #{act.get('id')}{name}"
        if kind == "type":
            return f"✍️ Điền #{act.get('id')}: **{act.get('text', '')}**"
        if kind == "select":
            return f"🔽 Chọn #{act.get('id')}: **{act.get('option', '')}**"
        if kind == "drag":
            tgt = elements.get(act.get("target_id")) or {}
            tname = (tgt.get("text") or tgt.get("label") or "")[:30]
            return f"✋ Kéo #{act.get('id')}{name} → #{act.get('target_id')}" + (f" «{tname}»" if tname else "")
        if kind == "scroll":
            return f"📜 Cuộn {'lên' if act.get('direction') == 'up' else 'xuống'}"
        if kind == "press":
            return f"⌨️ Bấm phím {act.get('key')}"
        if kind == "navigate":
            return f"🌐 Mở {act.get('url', '')[:60]}"
        if kind == "wait":
            return f"⏳ Chờ {act.get('ms', 1000)}ms"
        if kind == "hover":
            return f"👆 Rê chuột #{act.get('id')}{name}"
        return f"• {kind}"

    def _ensure_transcripts(self, obs: dict, s: dict):
        """Downloads and transcribes each new audio source once; transcripts persist for the session."""
        for m in obs.get("media", [])[:3]:
            url = m["src"]
            if url in self._transcripts or url in self._media_failed or self.stop_requested:
                continue
            name = url.split("?")[0].rsplit("/", 1)[-1][:50]
            self._set_phase("listening", detail=f"🎧 Đang tải audio {name}")
            res = self.browser.execute("fetch_media", {"url": url}, timeout=60)
            if res.get("status") != "ok":
                self._media_failed.add(url)
                self.add_message("ai", f"🎧 Không tải được audio «{name}»: {res.get('message')}", kind="action", status="warning")
                continue
            self._set_phase("listening", detail=f"🎧 Gemini đang nghe {name} ({res['size'] // 1024} KB)")
            try:
                text = self.llm.generate(self.api_key, self.model, TRANSCRIBE_PROMPT,
                                         attachments=[(res["data"], res["mime"])],
                                         fallbacks=s.get("fallback_models"), max_output_tokens=4096,
                                         should_stop=lambda: self.stop_requested,
                                         on_retry=lambda msg: self._set_phase("listening", detail=f"⚠️ {msg}"))
            except LLMError as e:
                self._media_failed.add(url)
                self.add_message("ai", f"🎧 Không chép lời được audio «{name}»: {e}", kind="action", status="warning")
                continue
            self._transcripts[url] = text.strip()
            self.add_message("ai", text.strip(), kind="transcript", data={"name": name, "size": res["size"]})

    def _agent_loop(self, task: str):
        s = self._run_settings
        max_steps = int(s.get("max_steps", 30))
        suggest = s.get("mode") == "suggest"
        history: List[str] = []
        last_sig, no_change = None, 0
        total_actions = 0
        started = time.time()
        calls_before = self.llm.usage["calls"]
        finished_ok = False

        for step in range(1, max_steps + 1):
            if self.stop_requested:
                break

            # 1. Observe
            self._set_phase("observing", step=step, max_steps=max_steps, detail="Đang quan sát màn hình")
            obs = self.browser.execute("ai_observe", {}, timeout=40)
            if obs.get("status") != "ok":
                self.add_message("ai", f"❌ Không quan sát được trang: {obs.get('message')}", kind="error", status="error")
                break
            if "login" in (obs.get("url") or "").lower() and step == 1 and not obs.get("elements"):
                self.add_message("ai", "⚠️ Trang đăng nhập — hãy đăng nhập trước rồi bấm Chạy.", kind="warning", status="warning")
                break

            if s.get("listen_audio", True) and obs.get("media"):
                self._ensure_transcripts(obs, s)
                if self.stop_requested:
                    break

            sig = self._signature(obs)
            if last_sig is not None and sig == last_sig and history:
                no_change += 1
                history.append(f"WARNING: the screen did not change after step {step - 1}'s actions.")
                if no_change >= 3:
                    self.add_message("ai", "⚠️ Màn hình không thay đổi sau nhiều lần thử — tôi dừng lại để tránh lặp vô ích. "
                                           "Bạn xem giúp màn hình hoặc ra lệnh cụ thể hơn nhé.", kind="warning", status="warning")
                    break
            else:
                no_change = 0
            last_sig = sig

            # 2. Think
            if self.stop_requested:
                break
            self._set_phase("thinking", detail=f"{len(obs.get('elements', []))} phần tử · Gemini đang suy luận")
            prompt = self._build_prompt(task, obs, history, step, max_steps)
            t_think = time.time()
            try:
                raw = self.llm.generate(self.api_key, self.model, prompt, image=obs.get("screenshot"),
                                        system=SYSTEM_PROMPT, json_mode=True,
                                        fallbacks=s.get("fallback_models"), should_stop=lambda: self.stop_requested,
                                        on_retry=lambda m: self._set_phase("thinking", detail=f"⚠️ {m}"))
                plan = parse_json_response(raw)
            except LLMError as e:
                if self.stop_requested:
                    break
                self.add_message("ai", f"❌ Gemini: {e}", kind="error", status="error")
                break
            if self.stop_requested:
                break

            actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict) and a.get("action")]
            status = str(plan.get("status") or "continue").lower()
            if suggest:
                actions = [a for a in actions if a.get("action") in ("scroll", "wait")]

            thought = " ".join(x for x in [plan.get("observation"), plan.get("plan")] if x)
            if thought:
                self.add_message("ai", thought, kind="thought",
                                 data={"step": step, "model": self.llm.last_model_used or self.model,
                                       "secs": round(time.time() - t_think, 1)})
            answers = [a for a in (plan.get("answers") or []) if isinstance(a, dict) and a.get("a")]
            if answers:
                self.add_message("ai", "", kind="answers", data={"answers": answers[:40], "step": step})

            if status in ("done", "need_user") and not actions:
                final = plan.get("message") or ("Đã hoàn thành." if status == "done" else "Cần bạn thao tác.")
                if status == "done":
                    finished_ok = True
                    self.add_message("ai", f"✅ {final}", kind="success", status="success")
                else:
                    self.add_message("ai", f"🙋 {final}", kind="warning", status="warning")
                break
            if not actions:
                history.append(f"Step {step}: no actions returned. Plan was: {plan.get('plan', '')[:160]}")
                if plan.get("message"):
                    self.add_message("ai", plan["message"], kind="text")
                continue

            # 3. Act
            self._set_phase("acting")
            el_map = {e["id"]: e for e in obs.get("elements", [])}
            results = []
            for i, act in enumerate(actions[:40]):
                if self.stop_requested:
                    break
                desc = self._describe_action(act, el_map)
                self._set_phase("acting", detail=f"{i + 1}/{len(actions)} · " + desc.replace("**", "")[:60])
                act = dict(act)
                act["_settle_ms"] = 600 if i == len(actions) - 1 else 120
                res = self.browser.execute("ai_act", act, timeout=30)
                ok = res.get("status") == "ok"
                total_actions += ok
                note = ""
                if not ok:
                    note = f" — ⚠️ {res.get('message')}"
                elif res.get("note"):
                    note = f" — {res['note']}"
                self.add_message("ai", desc + note, kind="action", status="ok" if ok else "warning")
                compact = {k: v for k, v in act.items() if not k.startswith("_")}
                results.append(f"{json.dumps(compact, ensure_ascii=False)} -> {'ok' if ok else 'FAILED: ' + str(res.get('message'))}"
                               + (f" (field now: {res['value']!r})" if 'value' in res else ""))
                delay = int(s.get("action_delay_ms", 150))
                if delay > 0:
                    time.sleep(delay / 1000)

            history.append(f"Step {step}: {plan.get('plan', '')[:200]}\n  actions: " + "; ".join(results))
            if status == "need_user":
                self.add_message("ai", f"🙋 {plan.get('message') or 'Cần bạn thao tác tiếp.'}", kind="warning", status="warning")
                break
        else:
            self.add_message("ai", f"⏱️ Đã dùng hết {max_steps} bước cho phép. Bấm **Chạy** để tiếp tục, hoặc tăng số bước trong Cài đặt.",
                             kind="warning", status="warning")

        elapsed = time.time() - started
        calls = self.llm.usage["calls"] - calls_before
        if self.stop_requested:
            self.add_message("ai", "🛑 Đã dừng theo yêu cầu.", kind="status", status="stopped")
        summary = f"{total_actions} thao tác · {calls} lượt gọi Gemini · {elapsed:.0f}s"
        self.add_message("ai", summary, kind="summary", status="success" if finished_ok else "ok")
