import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import List, Optional

CALL_TIMEOUT_S = 90  # hard ceiling per request; the SDK otherwise waits/retries for minutes
RETRYABLE =("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL", "DEADLINE", "timed out")
MODEL_MISSING = ("404", "NOT_FOUND", "is not found", "not supported")


class LLMError(Exception):
    pass


class QuotaExhausted(LLMError):
    """Every cloud model in the pool is out of free quota (the agent can switch to the local model)."""


def _parse_quota(err: str):
    """Gemini 429 → (seconds until retry, is_daily_quota) or None.
    Example: '... limit: 20, model: gemini-3.8-flash\\nPlease retry in 9h50m5.0s ... GenerateRequestsPerDay...'"""
    if "RESOURCE_EXHAUSTED" not in err and "429" not in err:
        return None
    m = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", err)
    wait = 0.0
    if m and any(m.groups()):
        h, mi, s = m.groups()
        wait = int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    else:
        d = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s", err)
        wait = float(d.group(1)) if d else 60.0
    per_day = "PerDay" in err or "per day" in err.lower()
    return wait, per_day


def _fmt_wait(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, m = seconds // 3600, (seconds % 3600) // 60
    return f"{h} giờ {m} phút" if h else (f"{m} phút" if m else f"{seconds} giây")


def _sleep_unless_stopped(seconds, should_stop):
    end = time.time() + seconds
    while time.time() < end:
        if should_stop and should_stop():
            raise LLMError("Đã dừng theo yêu cầu.")
        time.sleep(max(0.0, min(0.25, end - time.time())))


def parse_json_response(raw: str) -> dict:
    """Extracts the first JSON object from a model reply (tolerates ``` fences and stray prose)."""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception as e:
            raise LLMError(f"Mô hình trả về JSON không hợp lệ: {e}")
    raise LLMError("Mô hình không trả về JSON.")


class GeminiClient:
    """Thin wrapper over google-genai with a cached client, retry/backoff and model fallback."""

    def __init__(self):
        self._client = None
        self._key = None
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini")
        self._sticky = {}  # primary model -> (fallback that worked, expiry timestamp)
        self._exhausted = {}  # model -> (timestamp when the free quota resets, kind)
        self.last_switch_reason = None
        self.last_model_used = None
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def _get_client(self, api_key: str):
        if not api_key:
            raise LLMError("Chưa cấu hình Gemini API Key. Bấm ⚙️ Cài đặt để nhập khóa.")
        with self._lock:
            if self._client is None or self._key != api_key:
                from google import genai
                from google.genai import types
                self._client = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(timeout=CALL_TIMEOUT_S * 1000),
                )
                self._key = api_key
            return self._client

    def _call(self, fn, should_stop=None, timeout: float = CALL_TIMEOUT_S + 5):
        """Runs a blocking SDK call in a helper thread so Stop can abandon it immediately."""
        future = self._pool.submit(fn)
        deadline = time.time() + timeout
        while True:
            if should_stop and should_stop():
                raise LLMError("Đã dừng theo yêu cầu.")
            try:
                return future.result(timeout=0.25)
            except FutureTimeout:
                if time.time() > deadline:
                    raise LLMError(f"Gemini không phản hồi sau {int(timeout)}s (timed out)")

    def list_models(self, api_key: str, include_gemma: bool = True) -> List[dict]:
        client = self._get_client(api_key)
        out = []
        for m in self._call(lambda: list(client.models.list()), timeout=30):
            name = (m.name or "").replace("models/", "")
            actions = m.supported_actions or []
            family_ok = "gemini" in name or (include_gemma and name.startswith("gemma-"))
            if not family_ok or "generateContent" not in actions:
                continue
            if any(bad in name for bad in ("embedding", "tts", "image", "native-audio", "live", "robotics",
                                           "transcribe", "banana", "computer-use", "customtools", "omni")):
                continue
            out.append({"name": name, "display": m.display_name or name,
                        "input_limit": m.input_token_limit or 0})
        out.sort(key=lambda m: m["name"], reverse=True)
        return out

    def generate(self, api_key: str, model: str, prompt: str, image: Optional[bytes] = None,
                 system: Optional[str] = None, json_mode: bool = False, fallbacks: Optional[List[str]] = None,
                 temperature: float = 0.2, max_output_tokens: int = 8192, should_stop=None,
                 on_retry=None, attachments=None, schema=None, effort=None) -> str:
        """`attachments`: optional list of (bytes, mime_type), e.g. audio for listening exercises.
        `schema` / `effort` are Claude-only options and are ignored here (JSON mode covers the format)."""
        from google.genai import types

        client = self._get_client(api_key)
        media = [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in attachments or []]
        if image:
            media.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))

        def payload(mod):
            """Gemini takes a system instruction and JSON mode; Gemma models on this API are more reliable
            with the system prompt inside the message and plain text output (parsed as JSON afterwards)."""
            cfg = {"temperature": temperature, "max_output_tokens": max_output_tokens}
            text = prompt
            if mod.startswith("gemma-"):
                if system:
                    text = f"{system}\n\n{prompt}"
            else:
                if system:
                    cfg["system_instruction"] = system
                if json_mode:
                    cfg["response_mime_type"] = "application/json"
            return media + [text], types.GenerateContentConfig(**cfg)

        candidates = [m for m in [model] + list(fallbacks or []) if m]
        sticky = self._sticky.get(model)
        if sticky and sticky[1] > time.time():
            candidates.insert(0, sticky[0])  # primary failed recently: start from the model that worked
        candidates = list(dict.fromkeys(candidates))
        self.last_switch_reason = None
        last_err = None
        tried = set()
        discovered = False
        i = 0
        while i < len(candidates):
            mod = candidates[i]
            i += 1
            tried.add(mod)
            if self._is_exhausted(mod):
                self._note_switch(mod, on_retry)
                last_err = LLMError(self._exhausted_msg(mod))
            else:
                contents, config = payload(mod)
                text, last_err = self._try_model(client, mod, contents, config, should_stop, on_retry)
                if text is not None:
                    self.last_model_used = mod
                    if mod != model:
                        self._sticky[model] = (mod, time.time() + 300)
                    elif model in self._sticky:
                        del self._sticky[model]
                    return text
            if i == len(candidates) and not discovered and self._all_quota(candidates):
                # Every configured model is out of free quota: other Gemini models have their own buckets.
                discovered = True
                extra = self._discover_alternatives(api_key, tried)
                if extra and on_retry:
                    on_retry(f"Các mô hình đã chọn hết lượt, thử thêm: {', '.join(extra[:3])}...")
                candidates += extra
        if candidates and all(self._is_exhausted(m) for m in candidates):
            soonest = min(self._exhausted.get(m, (0, ""))[0] for m in candidates)
            raise QuotaExhausted("Tất cả mô hình Google (Gemini, Gemma) đã hết lượt miễn phí hôm nay. "
                                 f"Có lượt lại sau khoảng {_fmt_wait(soonest - time.time())}, "
                                 "hoặc dùng mô hình chạy trên máy (Ollama) / Claude trong ⚙️ Cài đặt.")
        raise LLMError(self._friendly(str(last_err)) if last_err else "Không gọi được mô hình.")

    # ---------------------------------------------------------------- quota handling

    def _try_model(self, client, mod, contents, config, should_stop, on_retry):
        """One model: returns (text, None) or (None, error). Daily quota → no retry; per-minute → wait as told."""
        last_err = None
        for attempt in range(3):
            if should_stop and should_stop():
                raise LLMError("Đã dừng theo yêu cầu.")
            try:
                resp = self._call(lambda: client.models.generate_content(model=mod, contents=contents, config=config),
                                  should_stop=should_stop)
            except LLMError:
                raise
            except Exception as e:
                err = str(e)
                last_err = e
                if any(k in err for k in MODEL_MISSING):
                    if on_retry:
                        on_retry(f"Không có mô hình {mod}, thử mô hình dự phòng...")
                    return None, e
                quota = _parse_quota(err)
                if quota:
                    wait_s, per_day = quota
                    if per_day or wait_s > 90:
                        # Out of today's free requests: retrying is pointless until the reset time.
                        self._exhausted[mod] = (time.time() + wait_s, "per_day")
                        self._note_switch(mod, on_retry)
                        return None, e
                    if attempt < 2:  # per-minute limit: wait exactly as long as Google asks
                        if on_retry:
                            on_retry(f"{mod} giới hạn lượt/phút, chờ {wait_s:.0f}s rồi thử lại")
                        _sleep_unless_stopped(wait_s + 0.5, should_stop)
                        continue
                    return None, e
                if any(k in err for k in RETRYABLE):  # overload / server error
                    if attempt < 2:
                        wait = 1.5 * (2 ** attempt)
                        if on_retry:
                            on_retry(f"{mod} đang quá tải, thử lại sau {wait:.0f}s ({attempt + 2}/3)")
                        _sleep_unless_stopped(wait, should_stop)
                        continue
                    if on_retry:
                        on_retry(f"{mod} vẫn quá tải, chuyển sang mô hình dự phòng...")
                    return None, e
                raise LLMError(self._friendly(err))
            text = resp.text if resp else None
            self._record_usage(resp)
            if text:
                return text, None
            reason = ""
            try:
                reason = str(resp.candidates[0].finish_reason)
            except Exception:
                pass
            return None, LLMError(f"Mô hình {mod} trả về rỗng {reason}".strip())
        return None, last_err

    def _is_exhausted(self, mod):
        until = self._exhausted.get(mod, (0, ""))[0]
        return until > time.time()

    def _exhausted_msg(self, mod):
        return f"{mod} hết lượt miễn phí hôm nay (có lại sau {_fmt_wait(self._exhausted[mod][0] - time.time())})"

    def _note_switch(self, mod, on_retry):
        self.last_switch_reason = self._exhausted_msg(mod)
        if on_retry:
            on_retry(f"{self.last_switch_reason}, chuyển mô hình khác...")

    def _all_quota(self, candidates):
        return all(self._is_exhausted(m) for m in candidates)

    def _discover_alternatives(self, api_key, tried):
        """The model pool: other Flash, Flash-Lite and Gemma models of this key, best first.
        Each has its own free-tier bucket, so the pool multiplies the daily number of agent steps."""
        try:
            names = [m["name"] for m in self.list_models(api_key, include_gemma=True)]
        except Exception:
            return []
        pool = [n for n in names if ("flash" in n or n.startswith("gemma-")) and n not in tried and not self._is_exhausted(n)]

        def rank(n):
            family = 2 if n.startswith("gemma-") else (1 if "lite" in n else 0)  # Flash > Flash-Lite > Gemma (slower)
            return (family, "preview" in n or "exp" in n, [-int(x) for x in re.findall(r"\d+", n)])
        pool.sort(key=rank)
        return pool[:10]

    def _record_usage(self, resp):
        self.usage["calls"] += 1
        meta = getattr(resp, "usage_metadata", None)
        if meta:
            self.usage["input_tokens"] += getattr(meta, "prompt_token_count", 0) or 0
            self.usage["output_tokens"] += getattr(meta, "candidates_token_count", 0) or 0

    @staticmethod
    def _friendly(err: str) -> str:
        if "API_KEY_INVALID" in err or "API key not valid" in err:
            return "API Key không hợp lệ. Kiểm tra lại khóa tại aistudio.google.com."
        if "RESOURCE_EXHAUSTED" in err or "429" in err:
            return "Đã hết hạn mức (quota) của API Key cho các mô hình đã thử. Đợi một lúc hoặc đổi mô hình."
        if "PERMISSION_DENIED" in err or "403" in err:
            return "API Key không có quyền dùng mô hình này."
        if any(k in err for k in MODEL_MISSING):
            return "Không tìm thấy mô hình. Bấm 'Tải danh sách' trong Cài đặt để chọn mô hình có sẵn."
        return err[:300]


# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeClient:
    """
    Anthropic Claude through the official SDK, with the same interface as GeminiClient.
    Claude has no audio input, so `attachments` (listening exercises) stay with Gemini.
    """

    _call = GeminiClient._call  # same stoppable helper-thread call

    def __init__(self):
        self._client = None
        self._key = None
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="claude")
        self.last_model_used = None
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def _get_client(self, api_key: str):
        import anthropic
        with self._lock:
            if self._client is None or self._key != api_key:
                # Empty key: the SDK resolves ANTHROPIC_API_KEY / an `ant auth login` profile itself.
                self._client = anthropic.Anthropic(api_key=api_key or None, timeout=CALL_TIMEOUT_S, max_retries=2)
                self._key = api_key
            return self._client

    def list_models(self, api_key: str) -> List[dict]:
        client = self._get_client(api_key)
        models = self._call(lambda: list(client.models.list()), timeout=30)
        return [{"name": m.id, "display": m.display_name or m.id, "input_limit": getattr(m, "max_input_tokens", 0) or 0}
                for m in models]

    def generate(self, api_key: str, model: str, prompt: str, image: Optional[bytes] = None,
                 system: Optional[str] = None, json_mode: bool = False, fallbacks: Optional[List[str]] = None,
                 temperature: float = 0.2, max_output_tokens: int = 16000, should_stop=None,
                 on_retry=None, attachments=None, schema=None, effort="medium") -> str:
        """`temperature` is ignored: current Claude models reject sampling parameters."""
        import anthropic

        if attachments:
            raise LLMError("Claude không nhận audio. Bài nghe cần Gemini API Key (Cài đặt → Gemini).")
        client = self._get_client(api_key)
        content = []
        if image:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.standard_b64encode(image).decode("ascii")}})
        content.append({"type": "text", "text": prompt})
        output_config = {"effort": effort or "medium"}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        params = {"max_tokens": max(max_output_tokens, 4096), "messages": [{"role": "user", "content": content}],
                  "output_config": output_config}
        if system:
            # Stable system prompt first, so the prefix can be cached across agent steps.
            params["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

        last_err = None
        for mod in list(dict.fromkeys([m for m in [model] + list(fallbacks or []) if m])):
            if should_stop and should_stop():
                raise LLMError("Đã dừng theo yêu cầu.")

            def send(mod=mod):
                if mod in CLAUDE_FALLBACK_MODELS:
                    # A safety decline is re-run server-side on Anthropic's recommended model.
                    return client.beta.messages.create(model=mod, betas=[CLAUDE_FALLBACK_BETA], fallbacks="default", **params)
                return client.messages.create(model=mod, **params)

            try:
                resp = self._call(send, should_stop=should_stop)
            except LLMError:
                raise
            except anthropic.AuthenticationError:
                raise LLMError("Claude API Key không hợp lệ. Kiểm tra lại khóa tại console.anthropic.com.")
            except anthropic.PermissionDeniedError:
                raise LLMError(f"API Key không có quyền dùng mô hình {mod}.")
            except anthropic.NotFoundError as e:
                last_err = e
                if on_retry:
                    on_retry(f"Không có mô hình {mod}, thử mô hình dự phòng...")
                continue
            except anthropic.RateLimitError as e:
                last_err = e
                if on_retry:
                    on_retry(f"{mod} đang giới hạn tốc độ, chuyển mô hình dự phòng...")
                continue
            except (anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                last_err = e  # the SDK already retried twice
                if on_retry:
                    on_retry(f"{mod} không phản hồi, thử mô hình dự phòng...")
                continue
            except anthropic.BadRequestError as e:
                raise LLMError(f"Claude từ chối yêu cầu: {e.message[:250]}")

            self._record_usage(resp)
            if resp.stop_reason == "refusal":
                category = getattr(getattr(resp, "stop_details", None), "category", None)
                raise LLMError(f"Claude từ chối thực hiện bước này ({category or 'an toàn'}).")
            text = "".join(b.text for b in resp.content if b.type == "text")
            if resp.stop_reason == "max_tokens" and json_mode:
                raise LLMError("Câu trả lời của Claude bị cắt (max_tokens).")
            if text:
                self.last_model_used = resp.model or mod
                return text
            last_err = LLMError(f"Claude ({mod}) trả về rỗng ({resp.stop_reason})")
        if isinstance(last_err, anthropic.RateLimitError):
            raise LLMError("Claude đang giới hạn tốc độ / hết hạn mức. Đợi một lúc rồi thử lại.")
        raise LLMError(str(last_err)[:300] if last_err else "Không gọi được Claude.")

    def _record_usage(self, resp):
        self.usage["calls"] += 1
        u = getattr(resp, "usage", None)
        if u:
            self.usage["input_tokens"] += (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
            self.usage["output_tokens"] += u.output_tokens or 0


# Local model through Ollama (https://ollama.com): no API key, no quota, runs on the user's own machine.
OLLAMA_DEFAULT_HOST = "http://127.0.0.1:11434"


class OllamaClient:
    """Same interface as GeminiClient / ClaudeClient, over Ollama's REST API (/api/chat, /api/tags)."""

    def __init__(self, host: Optional[str] = None):
        import os
        self.host = (host or os.environ.get("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ollama")
        self.last_model_used = None
        self.last_switch_reason = None
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    _call = GeminiClient._call

    def _request(self, path, body=None, timeout=600):
        import urllib.request
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.host + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def available(self) -> bool:
        try:
            self._request("/api/version", timeout=2)
            return True
        except Exception:
            return False

    def list_models(self, api_key: str = "") -> List[dict]:
        try:
            tags = self._request("/api/tags", timeout=5).get("models", [])
        except Exception as e:
            raise LLMError(f"Không kết nối được Ollama tại {self.host}: {e}")
        return [{"name": m["name"], "display": f"{m['name']} ({m.get('details', {}).get('parameter_size', '?')})",
                 "input_limit": 0} for m in tags]

    def generate(self, api_key: str, model: str, prompt: str, image: Optional[bytes] = None,
                 system: Optional[str] = None, json_mode: bool = False, fallbacks: Optional[List[str]] = None,
                 temperature: float = 0.2, max_output_tokens: int = 4096, should_stop=None,
                 on_retry=None, attachments=None, schema=None, effort=None) -> str:
        if attachments:
            raise LLMError("Mô hình chạy trên máy chưa nghe được audio. Bài nghe cần Gemini API Key.")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        user = {"role": "user", "content": prompt}
        if image:
            user["images"] = [base64.standard_b64encode(image).decode("ascii")]
        messages.append(user)
        body = {"model": model, "messages": messages, "stream": False,
                "options": {"temperature": temperature, "num_predict": max_output_tokens, "num_ctx": 32768},
                "keep_alive": "30m"}
        if schema:
            body["format"] = schema          # Ollama structured outputs: constrained to the JSON schema
        elif json_mode:
            body["format"] = "json"
        try:
            res = self._call(lambda: self._request("/api/chat", body, timeout=900), should_stop=should_stop, timeout=905)
        except LLMError:
            raise
        except Exception as e:
            msg = str(e)
            if "404" in msg or "not found" in msg.lower():
                raise LLMError(f"Chưa có mô hình «{model}» trên máy. Chạy: ollama pull {model}")
            raise LLMError(f"Ollama lỗi ({self.host}): {msg[:200]}")
        self.usage["calls"] += 1
        self.usage["input_tokens"] += res.get("prompt_eval_count", 0) or 0
        self.usage["output_tokens"] += res.get("eval_count", 0) or 0
        text = (res.get("message") or {}).get("content", "")
        if not text:
            raise LLMError(f"Mô hình {model} trả về rỗng")
        self.last_model_used = model
        return text
