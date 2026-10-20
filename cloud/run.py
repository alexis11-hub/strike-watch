# -*- coding: utf-8 -*-
"""云端入口: 抓取 → 更新 docs/ 页面 → 有值得提醒的内容就推送到手机。"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import monitor as M  # noqa: E402
from notify import send  # noqa: E402

DOCS = ROOT / "docs"
DATA_PUBLIC = DOCS / "data"
MAX_LINES = 6


def fresh(iso: str, hours: int) -> bool:
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() <= hours * 3600
    except Exception:
        return False


def pick(events, new_ids):
    now = datetime.now()
    out = []
    for e in events:
        if e["id"] not in new_ids:
            continue
        if e["source_id"] == "mit" and e.get("start"):
            try:
                start = datetime.fromisoformat(e["start"])
            except Exception:
                continue
            if -1 <= (start - now).days <= 7:
                out.append(e)
                continue
        if e["severity"] == "high" and e["kind"] in ("protest", "strike") and fresh(e["published"], 6):
            out.append(e)
    out.sort(key=lambda e: (0 if e["source_id"] == "mit" else 1,
                            M.ZONE_ORDER.index(e["zone"]),
                            e.get("start") or e.get("published", "")))
    return out


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    DATA_PUBLIC.mkdir(parents=True, exist_ok=True)
    M.DASHBOARD = DOCS / "index.html"

    events, status, new_ids = M.run_once(quiet=True)
    shutil.copy(M.EVENTS_FILE, DATA_PUBLIC / "events.json")

    ok = sum(1 for s in status if s["ok"])
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] 来源 {ok}/{len(status)} · 事件 {len(events)} · 新增 {len(new_ids)}")
    for s in status:
        if not s["ok"]:
            print(f"  源失败 {s['name']}: {s['error'][:120]}")

    picks = pick(events, new_ids)
    if not picks:
        print("无需要推送的内容")
        return

    lines = []
    for e in picks[:MAX_LINES]:
        z = M.ZONES[e["zone"]]
        when = f"（{e['dates']}）" if e.get("dates") else ""
        mark = "🔴" if e["severity"] == "high" else "🟠"
        lines.append(f"{mark} {z['name']}｜{e['title'][:80]}{when}")
    if len(picks) > MAX_LINES:
        lines.append(f"…另有 {len(picks) - MAX_LINES} 条")

    title = f"罢工/游行提醒 · {len(picks)} 条"
    body = "\n".join(lines)
    page = os.environ.get("PAGES_URL", "").strip()
    if page:
        body += f"\n\n看板: {page}"
    sent = send(title, body, click=page)
    print(f"推送 {len(picks)} 条 → {', '.join(sent) if sent else '未配置任何通道'}")
    print(body)


if __name__ == "__main__":
    main()
