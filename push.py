#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推送脚本 —— 给 Hermes 定时任务用。
只在出现【7 天内的官方交通罢工】或【6 小时内发生的高危事件】时输出文字；
没有值得提醒的内容就不输出（定时任务不会发消息）。
"""
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import monitor as M  # noqa: E402

MAX_LINES = 5


def fresh(iso: str, hours: int) -> bool:
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() <= hours * 3600
    except Exception:
        return False


def main():
    events, status, new_ids = M.run_once(quiet=True)
    now = datetime.now()
    picks = []
    for e in events:
        if e["id"] not in new_ids:
            continue
        # 1) 官方交通罢工: 7 天内开始
        if e["source_id"] == "mit" and e.get("start"):
            try:
                start = datetime.fromisoformat(e["start"])
            except Exception:
                continue
            if -1 <= (start - now).days <= 7:
                picks.append(e)
                continue
        # 2) 刚发生的高危事件: 6 小时内
        if e["severity"] == "high" and e["kind"] in ("protest", "strike") and fresh(e["published"], 6):
            picks.append(e)

    if not picks:
        return

    picks.sort(key=lambda e: (0 if e["source_id"] == "mit" else 1,
                              M.ZONE_ORDER.index(e["zone"]),
                              e.get("start") or e.get("published", "")))
    lines = [f"⚠️ 罢工 / 游行提醒 · {now:%m-%d %H:%M}"]
    for e in picks[:MAX_LINES]:
        z = M.ZONES[e["zone"]]
        when = f"（{e['dates']}）" if e.get("dates") else ""
        mark = "🔴" if e["severity"] == "high" else "🟠"
        lines.append(f"{mark} {z['name']}｜{e['title'][:80]}{when}")
    if len(picks) > MAX_LINES:
        lines.append(f"…另有 {len(picks) - MAX_LINES} 条，看本机看板")
    ok = sum(1 for s in status if s["ok"])
    lines.append(f"来源 {ok}/{len(status)} 在线")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
