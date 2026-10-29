"""
Renders site/og.png (1200 x 627), the image LinkedIn and other sites show when the project page is shared.
It is a screenshot of the top of site/index.html, so run tools/build_site.py first.

  pip install playwright && playwright install chromium
  python tools/make_og_image.py
"""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PAGE = os.path.join(ROOT, "site", "index.html")
OUT = os.path.join(ROOT, "site", "og.png")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1200, "height": 627}, device_scale_factor=1, color_scheme="light")
    page.goto("file://" + PAGE, wait_until="load")
    page.wait_for_timeout(800)
    page.screenshot(path=OUT, clip={"x": 0, "y": 0, "width": 1200, "height": 627})
    browser.close()
print(f"wrote {OUT}")
