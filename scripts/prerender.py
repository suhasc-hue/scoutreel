"""Render the heavy, static showcase pages to app/prerendered/*.html.

Run at Docker BUILD time (on Render's fast build CPU). The runtime then serves
these baked-in files from the page cache instantly — the tiny 0.1-vCPU instance
never has to build the multi-shelf pages itself, and it survives spin-downs
(the files live in the image, reloaded into the cache on every startup).

Needs DATABASE_URL pointing at the deploy DB, set before importing the app.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:////app/data/library.db")

from pathlib import Path  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "app" / "prerendered"
OUT.mkdir(exist_ok=True)
PAGES = {"films": "/films", "premium": "/premium", "ai": "/ai", "animation": "/animation"}

with TestClient(app) as client:
    for key, path in PAGES.items():
        r = client.get(path)
        (OUT / f"{key}.html").write_bytes(r.content)
        print(f"prerendered {path} -> {key}.html ({len(r.content)} bytes, HTTP {r.status_code})")
