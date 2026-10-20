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

    def list_models(self, api_key: str) -> List[dict]:
        client = self._get_client(api_key)
        out = []
        for m in self._call(lambda: list(client.models.list()), timeout=30):
            name = (m.name or "").replace("models/", "")
            actions = m.supported_actions or []
            if "gemini" not in name or "generateContent" not in actions:
                continue
            if any(bad in name for bad in ("embedding", "tts", "image", "native-audio", "live", "robotics",
                                           "transcribe", "banana", "computer-use", "customtools")):
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
        contents = []
        for data, mime in attachments or []:
            contents.append(types.Part.from_bytes(data=data, mime_type=mime))
        if image:
            contents.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))
        contents.append(prompt)

        cfg = {"temperature": temperature, "max_output_tokens": max_output_tokens}
        if system:
            cfg["system_instruction"] = system
        if json_mode:
            cfg["response_mime_type"] = "application/json"
        config = types.GenerateContentConfig(**cfg)

        candidates = [m for m in [model] + list(fallbacks or []) if m]
        sticky = self._sticky.get(model)
        if sticky and sticky[1] > time.time():
            candidates.insert(0, sticky[0])  # primary failed recently: start from the model that worked
        candidates = list(dict.fromkeys(candidates))
        last_err = None
        for mod in candidates:
            for attempt in range(3):
                if should_stop and should_stop():
                    raise LLMError("Đã dừng theo yêu cầu.")
                try:
                    resp = self._call(lambda: client.models.generate_content(model=mod, contents=contents, config=config),
                                      should_stop=should_stop)
                    text = resp.text if resp else None
                    self._record_usage(resp)
                    if text:
                        self.last_model_used = mod
                        if mod != model:
                            self._sticky[model] = (mod, time.time() + 300)
                        elif model in self._sticky:
                            del self._sticky[model]
                        return text
                    reason = ""
                    try:
                        reason = str(resp.candidates[0].finish_reason)
                    except Exception:
                        pass
                    last_err = LLMError(f"Mô hình {mod} trả về rỗng {reason}".strip())
                    break
                except Exception as e:
                    err = str(e)
                    last_err = e
                    if "Đã dừng" in err:
                        raise
                    if any(k in err for k in MODEL_MISSING):
                        if on_retry:
                            on_retry(f"Không có mô hình {mod}, thử mô hình dự phòng...")
                        break  # try next model
                    if any(k in err for k in RETRYABLE):
                        if attempt < 2:
                            wait = 1.5 * (2 ** attempt)
                            if on_retry:
                                on_retry(f"{mod} quá tải/hết lượt, thử lại sau {wait:.0f}s ({attempt + 2}/3)")
                            time.sleep(wait)
                            continue
                        if on_retry:
                            on_retry(f"{mod} vẫn lỗi, chuyển sang mô hình dự phòng...")
                        break  # quota/overload persists: try next model
                    raise LLMError(self._friendly(err))
        raise LLMError(self._friendly(str(last_err)) if last_err else "Không gọi được mô hình.")

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
