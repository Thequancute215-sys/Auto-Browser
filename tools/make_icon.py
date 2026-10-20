"""
Renders assets/icon.svg into assets/icon.ico (16–256 px, PNG-compressed entries) using headless Chromium,
and copies the SVG to static/favicon.svg. No image library needed.

Run: python tools/make_icon.py
"""
import pathlib
import shutil
import struct

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
SVG = ROOT / "assets" / "icon.svg"
ICO = ROOT / "assets" / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def render_pngs():
    svg = SVG.read_text(encoding="utf-8")
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for s in SIZES:
            page = b.new_page(viewport={"width": s, "height": s})
            page.set_content(f"<html><body style='margin:0;background:transparent'>"
                             f"<div style='width:{s}px;height:{s}px'>{svg.replace('<svg ', f'<svg width=\"{s}\" height=\"{s}\" ', 1)}</div></body></html>")
            out[s] = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": s, "height": s})
            page.close()
        b.close()
    return out


def write_ico(pngs):
    # ICONDIR + ICONDIRENTRY[n] + PNG blobs (Vista+ supports PNG-compressed icon images).
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries, blobs = b"", b""
    offset = 6 + 16 * len(pngs)
    for size, data in sorted(pngs.items()):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    ICO.write_bytes(header + entries + blobs)


def main():
    pngs = render_pngs()
    write_ico(pngs)
    (ROOT / "assets" / "icon-256.png").write_bytes(pngs[256])
    shutil.copyfile(SVG, ROOT / "static" / "favicon.svg")
    print(f"wrote {ICO} ({ICO.stat().st_size // 1024} KB, sizes {SIZES})")


if __name__ == "__main__":
    main()
