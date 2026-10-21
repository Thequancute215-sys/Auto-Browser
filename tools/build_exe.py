"""
Builds the Windows app and its installer.

  python tools/build_exe.py            -> dist/AutoBrowser/AutoBrowser.exe  (+ installer if Inno Setup is installed)

Output:
  dist/AutoBrowser/                   portable app folder (AutoBrowser.exe + _internal/)
  dist/AutoBrowser-Setup-<ver>.exe    per-user installer: Start menu + desktop shortcut + uninstaller

Chromium itself is not bundled (it would add ~300 MB): the app uses Playwright's shared browser folder
(%LOCALAPPDATA%\\ms-playwright) and downloads it on first run if missing.
"""
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from version import __version__ as VERSION  # noqa: E402  (single source: version.py)
NAME = "AutoBrowser"


def build_app():
    import PyInstaller.__main__
    PyInstaller.__main__.run([
        str(ROOT / "desktop_app.py"),
        "--name", NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", str(ROOT / "assets" / "icon.ico"),
        "--add-data", f"{ROOT / 'static'}{os.pathsep}static",
        "--collect-all", "playwright",      # Python package + bundled Node driver
        "--hidden-import", "websocket",
        "--collect-submodules", "anthropic",   # Claude provider (imported lazily)
        "--collect-submodules", "google.genai",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--version-file", str(write_version_file()),
    ])
    exe = ROOT / "dist" / NAME / f"{NAME}.exe"
    print(f"app: {exe} ({exe.stat().st_size // 1024} KB)")
    return exe


def write_version_file():
    major, minor, patch = (int(x) for x in VERSION.split("."))
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({major}, {minor}, {patch}, 0), prodvers=({major}, {minor}, {patch}, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Auto Browser'),
    StringStruct('FileDescription', 'Auto Browser - trình duyệt có trợ lý AI'),
    StringStruct('FileVersion', '{VERSION}'),
    StringStruct('ProductName', 'Auto Browser'),
    StringStruct('ProductVersion', '{VERSION}'),
    StringStruct('OriginalFilename', '{NAME}.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")
    return path


def find_iscc():
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", "")):
        for sub in ("Programs\\Inno Setup 6", "Inno Setup 6"):
            p = pathlib.Path(base) / sub / "ISCC.exe"
            if p.is_file():
                return p
    return shutil.which("ISCC")


def build_installer():
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup not found: skipping the installer (winget install JRSoftware.InnoSetup)")
        return None
    subprocess.run([str(iscc), f"/DAppVersion={VERSION}", str(ROOT / "installer" / "AutoBrowser.iss")], check=True)
    setup = ROOT / "dist" / f"{NAME}-Setup-{VERSION}.exe"
    print(f"installer: {setup} ({setup.stat().st_size // 1024 // 1024} MB)")
    return setup


if __name__ == "__main__":
    build_app()
    if "--no-installer" not in sys.argv:
        build_installer()
