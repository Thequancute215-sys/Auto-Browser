"""In-place update logic: version comparison, release check, checksum verification, per-mode instructions."""
import hashlib
import io
import json

import pytest

import updater as upd


def test_version_comparison():
    assert upd.is_newer("v6.2.1", "6.2.0")
    assert upd.is_newer("6.10.0", "6.9.9")
    assert not upd.is_newer("v6.2.0", "6.2.0")
    assert not upd.is_newer("6.1.9", "6.2.0")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _release(tag, body=b"installer-bytes", digest=None):
    d = digest if digest is not None else "sha256:" + hashlib.sha256(body).hexdigest()
    return {"tag_name": tag, "body": "notes", "html_url": "https://github.com/x/releases/tag/" + tag,
            "assets": [{"name": f"AutoBrowser-Setup-{tag[1:]}.exe", "url": "https://api.github.com/assets/1",
                        "size": len(body), "digest": d}]}


@pytest.fixture
def fake_net(monkeypatch):
    state = {"release": None, "body": b"installer-bytes", "headers": [], "launched": None}

    def fake_get(self, url, accept="application/vnd.github+json", timeout=10):
        state["headers"].append((url, self._token_getter()))
        if url.endswith("/releases/latest"):
            return FakeResp(json.dumps(state["release"]).encode())
        return FakeResp(state["body"])

    monkeypatch.setattr(upd.Updater, "_get", fake_get)
    monkeypatch.setattr(upd.subprocess, "Popen", lambda args, **kw: state.__setitem__("launched", args))
    return state


def test_check_reports_newer_release(fake_net, monkeypatch):
    monkeypatch.setattr(upd, "__version__", "6.2.0")
    fake_net["release"] = _release("v6.3.0")
    info = upd.Updater().check(force=True)
    assert info["update_available"] and info["latest"] == "6.3.0" and info["asset"]["sha256"]


def test_private_repo_error_is_explained(monkeypatch):
    import urllib.error

    def denied(self, url, **kw):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(upd.Updater, "_get", denied)
    info = upd.Updater().check(force=True)
    assert not info["update_available"] and "riêng tư" in info["error"]


def test_apply_verifies_checksum_and_runs_silent_installer(fake_net, monkeypatch):
    monkeypatch.setattr(upd, "runtime_mode", lambda: "desktop")
    monkeypatch.setattr(upd, "__version__", "6.2.0")
    fake_net["release"] = _release("v6.3.0")
    quit_called = []
    res = upd.Updater().apply(on_quit=lambda: quit_called.append(1))
    assert res["status"] == "ok"
    args = fake_net["launched"]
    assert args[0].endswith("AutoBrowser-Setup-6.3.0.exe")
    assert "/VERYSILENT" in args and "/RELAUNCH" in args and "/CLOSEAPPLICATIONS" in args


def test_apply_refuses_tampered_download(fake_net, monkeypatch):
    monkeypatch.setattr(upd, "runtime_mode", lambda: "desktop")
    monkeypatch.setattr(upd, "__version__", "6.2.0")
    fake_net["release"] = _release("v6.3.0", digest="sha256:" + "0" * 64)
    res = upd.Updater().apply()
    assert res["status"] == "error" and "không khớp" in res["message"]
    assert fake_net["launched"] is None, "a file that fails the checksum must never be executed"


def test_docker_and_source_get_instructions(fake_net, monkeypatch):
    fake_net["release"] = _release("v99.0.0")
    for mode, needle in (("docker", "docker compose pull"), ("source", "git pull")):
        monkeypatch.setattr(upd, "runtime_mode", lambda m=mode: m)
        res = upd.Updater().apply()
        assert res["status"] == "error" and needle in res["message"]
        assert fake_net["launched"] is None


def test_token_is_sent_only_when_configured(fake_net, monkeypatch):
    monkeypatch.setattr(upd, "__version__", "6.2.0")
    fake_net["release"] = _release("v6.2.0")
    upd.Updater(token_getter=lambda: "").check(force=True)
    upd.Updater(token_getter=lambda: "ghp_x").check(force=True)
    assert [t for _, t in fake_net["headers"]] == ["", "ghp_x"]


def test_apply_refuses_download_without_digest(fake_net, monkeypatch):
    monkeypatch.setattr(upd, "runtime_mode", lambda: "desktop")
    monkeypatch.setattr(upd, "__version__", "6.2.0")
    fake_net["release"] = _release("v6.3.0", digest="")
    res = upd.Updater().apply()
    assert res["status"] == "error" and "digest" in res["message"].lower()
    assert fake_net["launched"] is None, "an installer with no digest must never be executed"
