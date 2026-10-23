"""
In-place updates: users never uninstall and re-download the app.

- Desktop (installed exe): download the new installer from GitHub Releases, check its SHA-256 against the
  digest GitHub reports, run it silently over the current install (same Inno Setup AppId → upgrade in
  place; settings in %LOCALAPPDATA%\\AutoBrowser and logins in ~/.ai_browser_profile are untouched), then
  the installer reopens the app.
- Docker: the image is replaced by `docker compose pull && docker compose up -d` (or Watchtower); data
  lives in the /data volume.
- Source checkout: start.bat runs `git pull` before starting.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import config
from version import REPO, __version__

CHECK_TTL_S = 3600


def runtime_mode() -> str:
    if config.IN_DOCKER:
        return "docker"
    if getattr(sys, "frozen", False):
        return "desktop"
    return "source"


def _vtuple(v: str):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(latest: str, current: str = None) -> bool:
    return _vtuple(latest) > _vtuple(current or __version__)


class Updater:
    def __init__(self, token_getter=None):
        self._token_getter = token_getter or (lambda: os.environ.get("AIB_UPDATE_TOKEN", ""))
        self._cache = None
        self._lock = threading.Lock()
        self.state = {"phase": "idle", "message": ""}

    def _get(self, url, accept="application/vnd.github+json", timeout=10):
        headers = {"Accept": accept, "User-Agent": f"AutoBrowser/{__version__}"}
        token = (self._token_getter() or "").strip()
        if token:  # only needed while the repository is private
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(req, timeout=timeout)

    def check(self, force=False) -> dict:
        """Latest release info, cached for an hour. Never raises (offline → update_available False)."""
        with self._lock:
            if self._cache and not force and time.time() - self._cache[0] < CHECK_TTL_S:
                return self._cache[1]
        info = {"current": __version__, "mode": runtime_mode(), "latest": None, "update_available": False,
                "notes": "", "url": f"https://github.com/{REPO}/releases", "error": None}
        try:
            with self._get(f"https://api.github.com/repos/{REPO}/releases/latest") as r:
                rel = json.loads(r.read().decode())
            tag = rel.get("tag_name", "")
            info.update(latest=tag.lstrip("v"), notes=(rel.get("body") or "")[:4000], url=rel.get("html_url", info["url"]))
            info["update_available"] = is_newer(tag)
            setup = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(".exe")), None)
            if setup:
                info["asset"] = {"name": setup["name"], "url": setup["url"], "size": setup["size"],
                                 "sha256": (setup.get("digest") or "").replace("sha256:", "")}
        except Exception as e:
            code = getattr(e, "code", None)
            info["error"] = ("Kho GitHub đang ở chế độ riêng tư: cần token để kiểm tra cập nhật."
                             if code in (401, 403, 404) else f"Không kiểm tra được cập nhật: {str(e)[:120]}")
        with self._lock:
            self._cache = (time.time(), info)
        return info

    def apply(self, on_quit=None) -> dict:
        """Desktop only: download, verify and launch the installer, then ask the app to quit."""
        info = self.check(force=True)
        if info["mode"] != "desktop":
            return {"status": "error", "message": self.instructions(info["mode"])}
        if not info["update_available"]:
            return {"status": "error", "message": info.get("error") or f"Đang dùng bản mới nhất ({__version__})."}
        asset = info.get("asset")
        if not asset:
            return {"status": "error", "message": "Bản phát hành chưa có bộ cài .exe."}
        self.state = {"phase": "downloading", "message": f"Đang tải {asset['name']}..."}
        path = os.path.join(tempfile.gettempdir(), asset["name"])
        sha = hashlib.sha256()
        with self._get(asset["url"], accept="application/octet-stream", timeout=60) as r, open(path, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                sha.update(chunk)
                f.write(chunk)
        # Fail closed: never run the installer without a matching SHA-256. GitHub reports a
        # digest for release assets; if it is missing we refuse rather than install blindly.
        expected = (asset.get("sha256") or "").lower()
        if not expected:
            os.remove(path)
            self.state = {"phase": "error", "message": "Bản phát hành thiếu mã kiểm tra (digest), đã hủy để an toàn."}
            return {"status": "error", "message": self.state["message"]}
        if sha.hexdigest().lower() != expected:
            os.remove(path)
            self.state = {"phase": "error", "message": "File tải về không khớp mã kiểm tra, đã hủy."}
            return {"status": "error", "message": self.state["message"]}
        self.state = {"phase": "installing", "message": f"Đang cài bản {info['latest']}, app sẽ tự mở lại."}
        flags = 0x00000008 | 0x01000000 | 0x00000200  # DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB | NEW_PROCESS_GROUP
        # The installer must outlive this process (our Job Object kills children on exit), hence breakaway.
        subprocess.Popen([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH"],
                         creationflags=flags, close_fds=True)
        if on_quit:
            threading.Timer(1.5, on_quit).start()
        return {"status": "ok", "message": self.state["message"], "version": info["latest"]}

    @staticmethod
    def instructions(mode: str) -> str:
        if mode == "docker":
            return "Bản Docker: chạy  docker compose pull && docker compose up -d  (dữ liệu được giữ nguyên)."
        if mode == "source":
            return "Bản mã nguồn: chạy start.bat (tự git pull) hoặc  git pull  rồi mở lại."
        return ""
