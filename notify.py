# -*- coding: utf-8 -*-
"""推送通道: ntfy / Telegram / Server酱(微信) / 自定义 webhook，由环境变量决定，可同时开多个。"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

UA = "strike-watch/1.0"


def _post(url: str, data: bytes, headers: dict) -> bool:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] {url.split('?')[0]} 失败: {type(exc).__name__}: {exc}")
        return False


def ntfy(topic: str, title: str, body: str, click: str = "") -> bool:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    # HTTP 头只能放 ASCII，中文标题退回英文，正文里仍是中文
    safe_title = title if all(ord(c) < 128 for c in title) else "Strike / protest alert"
    headers = {
        "Title": safe_title,
        "Priority": "high",
        "Tags": "warning",
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click:
        headers["Click"] = click
    return _post(f"{server}/{topic}", body.encode("utf-8"), headers)


def telegram(token: str, chat_id: str, text: str) -> bool:
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                      "disable_web_page_preview": "true"}).encode()
    return _post(f"https://api.telegram.org/bot{token}/sendMessage", payload,
                 {"Content-Type": "application/x-www-form-urlencoded"})


def serverchan(key: str, title: str, body: str) -> bool:
    payload = urllib.parse.urlencode({"title": title, "desp": body}).encode()
    req = urllib.request.Request(
        f"https://sctapi.ftqq.com/{key}.send", data=payload,
        headers={"User-Agent": UA,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
            if data.get("code") != 0:
                print(f"[notify] serverchan 返回错误: {data}")
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] serverchan 失败: {type(exc).__name__}: {exc}")
        return False


def webhook(url: str, title: str, body: str) -> bool:
    data = json.dumps({"title": title, "text": body}, ensure_ascii=False).encode("utf-8")
    return _post(url, data, {"Content-Type": "application/json"})


def send(title: str, body: str, click: str = "") -> list[str]:
    """按环境变量把消息推到所有已配置的通道, 返回成功的通道名。"""
    sent = []
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic and ntfy(topic, title, body, click):
        sent.append("ntfy")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat and telegram(token, chat, f"{title}\n{body}"):
        sent.append("telegram")
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if key and serverchan(key, title, body):
        sent.append("serverchan")
    hook = os.environ.get("WEBHOOK_URL", "").strip()
    if hook and webhook(hook, title, body):
        sent.append("webhook")
    return sent
