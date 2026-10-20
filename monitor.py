#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
罢工 / 游行 实时监测 v2
范围:
  本地 —— 蒙彼利埃(法国·埃罗省) · 米兰 · 佛罗伦萨 · 里米尼 · 圣马力诺
  全国 —— 法国全国 · 意大利全国

数据源(公开, 无需密钥):
  意大利交通部 MIT 官方罢工日历(权威) / ANSA 全国+3 大区 / Il Resto del Carlino 里米尼
  La Nazione 佛罗伦萨 / San Marino RTV / Libertas.sm
  franceinfo / Le Monde / ICI 全国+埃罗省 / Midi Libre / CGT 全国工会

用法:
  python monitor.py                 抓一次, 更新 data/events.json + dashboard.html
  python monitor.py --open          抓完打开看板
  python monitor.py --watch 300 --serve 8765 --open   常驻(推荐)
  python monitor.py --new-only      只列本轮新增
  python monitor.py --json          JSON 输出本轮新增(给定时推送用)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html as htmlmod
import json
import re
import sys
import threading
import time
import unicodedata
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
EVENTS_FILE = DATA_DIR / "events.json"
STATUS_FILE = DATA_DIR / "sources.json"
DASHBOARD = BASE / "dashboard.html"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NEWS_MAX_AGE_DAYS = 14      # 新闻最多回溯天数
HISTORY_KEEP_DAYS = 21      # 本地归档保留天数
HIGH_FRESH_HOURS = 72       # 超过该小时数的旧闻不再算“高危”

# ---------------------------------------------------------------- 监测区域
ZONES = {
    "montpellier": {"name": "蒙彼利埃", "latin": "Montpellier", "flag": "🇫🇷", "group": "local"},
    "milano": {"name": "米兰", "latin": "Milano", "flag": "🇮🇹", "group": "local"},
    "firenze": {"name": "佛罗伦萨", "latin": "Firenze", "flag": "🇮🇹", "group": "local"},
    "rimini": {"name": "里米尼", "latin": "Rimini", "flag": "🇮🇹", "group": "local"},
    "sanmarino": {"name": "圣马力诺", "latin": "San Marino", "flag": "🇸🇲", "group": "local"},
    "fr_national": {"name": "法国全国", "latin": "France", "flag": "🇫🇷", "group": "national"},
    "it_national": {"name": "意大利全国", "latin": "Italia", "flag": "🇮🇹", "group": "national"},
}
ZONE_ORDER = ["montpellier", "milano", "firenze", "rimini", "sanmarino", "fr_national", "it_national"]
ITALIAN_CITIES = ["milano", "firenze", "rimini"]

CITY_WORDS = {
    "montpellier": ["montpellier", "herault", "occitanie", "sete", "beziers", "lunel",
                    "palavas", "lattes", "castelnau", "juvignac", "mauguio", "clapiers",
                    "montferrier", "saint-jean-de-vedas", "castries", "villeneuve-les-maguelone"],
    "milano": ["milano", "milan", "lombardia", "malpensa", "linate", "trenord",
               "atm milano", "sesto san giovanni", "san donato milanese", "rogoredo",
               "lambrate", "cadorna", "porta garibaldi", "bicocca", "rho fiera"],
    "firenze": ["firenze", "florence", "toscana", "tuscany", "santa maria novella",
                "rifredi", "autolinee toscane", "tramvia", "sesto fiorentino",
                "campo di marte", "scandicci", "campi bisenzio", "bagno a ripoli", "fiesole"],
    "rimini": ["rimini", "romagna", "start romagna", "riccione", "cattolica", "bellaria",
               "santarcangelo", "misano", "coriano", "verucchio", "torriana"],
    "sanmarino": ["san marino", "serravalle", "borgo maggiore", "faetano",
                  "montegiardino", "chiesanuova", "domagnano", "sammarinese",
                  "repubblica di san marino", "republic of san marino"],
}

# 提到这些地名、又没有命中监测城市时 → 判定为“别处新闻”, 丢弃
OTHER_PLACES = [
    "paris", "lyon", "marseille", "toulouse", "nice", "nimes", "avignon", "ales",
    "perpignan", "bordeaux", "lille", "rennes", "nantes", "strasbourg", "grenoble",
    "saint-etienne", "toulon", "aix-en-provence", "montauban", "carcassonne", "narbonne",
    "bologna", "roma", "torino", "napoli", "genova", "palermo", "bari", "venezia",
    "verona", "padova", "modena", "parma", "reggio emilia", "guastalla", "ravenna",
    "forli", "cesena", "pesaro", "ancona", "perugia", "trieste", "livorno", "lucca",
    "arezzo", "siena", "grosseto", "pistoia", "carrara", "empoli", "prato", "pisa",
    "bergamo", "brescia", "como", "varese", "monza", "pavia", "mantova", "cremona",
    "udine", "trento", "bolzano", "cagliari", "catania", "messina", "foggia", "lecce",
]

# ---------------------------------------------------------------- 数据源
# mode: city=本市报纸(宽松, 用地名黑名单排除外地)
#       region=大区通讯社(严格, 必须出现本市关键词)
#       national=全国媒体(只在出现罢工/游行关键词时收录)
SOURCES = [
    {"id": "mit", "name": "意大利交通部 罢工日历", "mode": "mit",
     "url": "https://scioperi.mit.gov.it/mit2/public/scioperi/rss", "zone": None},
    # --- 意大利本地
    {"id": "ansa_lom", "name": "ANSA 伦巴第", "mode": "region",
     "url": "https://www.ansa.it/lombardia/notizie/lombardia_rss.xml", "zone": "milano"},
    {"id": "ansa_tos", "name": "ANSA 托斯卡纳", "mode": "region",
     "url": "https://www.ansa.it/toscana/notizie/toscana_rss.xml", "zone": "firenze"},
    {"id": "ansa_er", "name": "ANSA 艾米利亚-罗马涅", "mode": "region",
     "url": "https://www.ansa.it/emiliaromagna/notizie/emiliaromagna_rss.xml", "zone": "rimini"},
    {"id": "carlino_rimini", "name": "Il Resto del Carlino · 里米尼", "mode": "city",
     "url": "https://www.ilrestodelcarlino.it/rss/rimini", "zone": "rimini"},
    {"id": "nazione_fi", "name": "La Nazione · 佛罗伦萨", "mode": "city",
     "url": "https://www.lanazione.it/rss/firenze", "zone": "firenze"},
    # --- 圣马力诺
    {"id": "smrtv", "name": "San Marino RTV", "mode": "city",
     "url": "https://www.sanmarinortv.sm/rss.xml", "zone": "sanmarino"},
    {"id": "libertas", "name": "Libertas.sm", "mode": "city",
     "url": "https://www.libertas.sm/feed/", "zone": "sanmarino"},
    # --- 法国本地
    {"id": "ici_herault", "name": "ICI / France Bleu 埃罗省", "mode": "city",
     "url": "https://www.ici.fr/rss/herault/a-la-une.xml", "zone": "montpellier"},
    {"id": "midilibre", "name": "Midi Libre", "mode": "city",
     "url": "https://www.midilibre.fr/rss.xml", "zone": "montpellier"},
    # --- 全国
    {"id": "cgt", "name": "CGT 全国工会", "mode": "national",
     "url": "https://www.cgt.fr/rss.xml", "zone": "fr_national"},
    {"id": "franceinfo", "name": "franceinfo", "mode": "national",
     "url": "https://www.francetvinfo.fr/titres.rss", "zone": "fr_national"},
    {"id": "lemonde", "name": "Le Monde", "mode": "national",
     "url": "https://www.lemonde.fr/rss/une.xml", "zone": "fr_national"},
    {"id": "ici_nat", "name": "ICI 全国", "mode": "national",
     "url": "https://www.ici.fr/rss/a-la-une.xml", "zone": "fr_national"},
    {"id": "ansa_top", "name": "ANSA 头条", "mode": "national",
     "url": "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml", "zone": "it_national"},
    {"id": "ansa_cron", "name": "ANSA 全国新闻", "mode": "national",
     "url": "https://www.ansa.it/sito/notizie/cronaca/cronaca_rss.xml", "zone": "it_national"},
    {"id": "ilfatto", "name": "Il Fatto Quotidiano", "mode": "national",
     "url": "https://www.ilfattoquotidiano.it/feed/", "zone": "it_national"},
]

# ---------------------------------------------------------------- 关键词
STRIKE_WORDS = [
    "sciopero", "scioperi", "scioper", "astensione dal lavoro", "sciopero generale",
    "greve", "preavis de greve", "preavis", "debrayage", "greve generale",
    "strike", "mouvement social", "conflit social", "arret de travail",
]
PROTEST_WORDS = [
    "manifestanti", "corteo", "cortei", "presidio", "sit-in", "sit in",
    "blocco", "blocchi", "blocage", "barrage", "protesta", "proteste", "protest",
    "agitazione", "picchetto", "piquet", "occupazione", "occupazion", "occupation",
    "rassemblement", "manifestation", "manifestants", "cortege", "defile",
    "in piazza", "sfilata", "mobilisation", "mobilitazione",
    "journee d'action", "journee de mobilisation", "colere", "assembla",
]
# 意大利语 "manifestazione" 既指示威也指赛事/活动, 单独出现不算
WEAK_IT_WORDS = ["manifestazione", "manifestazion"]
WEAK_CONTEXT = ["studenti", "lavorator", "sindacat", "antifascis", "pro-pal",
                "pro palestina", "palestina", "per la pace", "presidio", "corteo",
                "sciopero", "protesta", "occupazion", "no green pass", "no vax",
                "carovita", "salario", "precari", "sfila", "bandiere"]
VIOLENT_WORDS = [
    "scontri", "scontro", "cariche", "manganelli", "manganello", "feriti",
    "violenze", "violenza", "violence", "violences", "affrontement", "affrontements",
    "emeute", "emeutes", "incendie", "incendi", "lacrymogene", "lacrimogeni",
    "assalto", "devastat", "danneggiament", "saccage", "casseur", "disordini",
    "rivolta", "pillage", "blindati", "idranti",
]
GENERAL_WORDS = ["sciopero generale", "greve generale", "general strike", "plurisettoriale",
                 "sciopero nazionale", "manifestation nationale", "toute la france",
                 "in tutta italia", "journee de mobilisation nationale"]

SEV_RANK = {"high": 3, "medium": 2, "low": 1}
SEV_LABEL = {"high": "高危", "medium": "关注", "low": "信息"}
KIND_LABEL = {"strike": "罢工", "protest": "游行/集会", "news": "相关动态"}

SECTOR_CN = {
    "aereo": "✈️ 航空", "ferroviario": "🚆 铁路", "trasporto pubblico locale": "🚌 公交",
    "marittimo": "⛴️ 海运", "trasporto merci": "🚚 货运", "taxi": "🚕 出租车",
    "ncc": "🚕 网约车", "elicotteri": "🚁 直升机", "generale": "⚠️ 全国总罢工",
    "plurisettoriale": "⚠️ 多行业总罢工", "appalti ferroviari": "🚆 铁路外包",
    "circolazione e sicurezza stradale": "🛣️ 道路",
}


# ---------------------------------------------------------------- 工具
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(s or "").lower()).strip()


def clean_text(raw: str, limit: int = 320) -> str:
    if not raw:
        return ""
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    t = re.sub(r"(?i)<br\s*/?>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmlmod.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t


def fetch(url: str, timeout: int = 25, tries: int = 3) -> str:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/rss+xml, application/xml, text/xml, "
                          "text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.9,fr-FR;q=0.8,en;q=0.6",
                "Accept-Encoding": "gzip",
                "Cache-Control": "no-cache",
                "Connection": "close",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                blob = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    try:
                        blob = gzip.decompress(blob)
                    except OSError:
                        pass
                charset = resp.headers.get_content_charset() or "utf-8"
                text = blob.decode(charset, "replace")
                if not text.strip():
                    raise ValueError("empty body")
                return text
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise last  # type: ignore[misc]


def parse_date(value: str):
    if not value:
        return None
    value = value.strip()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.replace(".000", ""), fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def parse_feed(xml_text: str):
    payload = xml_text.lstrip("\ufeff \t\r\n")
    idx = payload.find("<")
    if idx > 0:
        payload = payload[idx:]
    root = ET.fromstring(payload)
    out = []
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        title = link = desc = pub = ""
        for child in list(node):
            ctag = child.tag.split("}")[-1].lower()
            text = (child.text or "").strip()
            if ctag == "title":
                title = clean_text(text, 300)
            elif ctag == "link":
                href = child.attrib.get("href")
                if href:
                    link = href
                elif text:
                    link = text
            elif ctag in ("description", "summary", "content", "encoded"):
                if len(clean_text(text, 2000)) > len(desc):
                    desc = text
            elif ctag in ("pubdate", "published", "updated", "date"):
                if not pub:
                    pub = text
        out.append({"title": title or "(无标题)", "link": link.strip(),
                    "desc": desc, "published": pub})
    return out


def hash_id(*parts: str) -> str:
    raw = "||".join(norm(p) for p in parts if p)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def classify(text: str):
    blob = norm(text)
    strike = any(w in blob for w in STRIKE_WORDS)
    protest = any(w in blob for w in PROTEST_WORDS)
    if not protest and any(w in blob for w in WEAK_IT_WORDS):
        protest = any(c in blob for c in WEAK_CONTEXT)
    violent = any(w in blob for w in VIOLENT_WORDS)
    general = any(w in blob for w in GENERAL_WORDS)
    if not (strike or protest):
        return None, None
    kind = "strike" if strike else "protest"
    sev = "high" if (violent or general) else "medium"
    return kind, sev


def zones_in(text: str):
    blob = norm(text)
    return [z for z in ZONE_ORDER
            if ZONES[z]["group"] == "local"
            and any(norm(w) in blob for w in CITY_WORDS.get(z, []))]


def mentions_other_place(text: str) -> bool:
    blob = norm(text)
    return any(norm(p) in blob for p in OTHER_PLACES)


# ---------------------------------------------------------------- MIT 官方日历
def parse_ddmmyyyy(value: str):
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y")
    except Exception:
        return None


def mit_zones(meta: dict, blob: str):
    """返回 (zones, affects, national)"""
    region = norm(meta.get("Regione", ""))
    prov = norm(meta.get("Provincia", ""))
    rel = norm(meta.get("Rilevanza", ""))
    sector = norm(meta.get("Settore", ""))
    national = region in ("italia", "tutte", "") or "nazionale" in rel

    if national:
        return ["it_national"], list(ITALIAN_CITIES), True

    zones, affects = [], []
    if "lombardia" in region and ("milano" in prov or "tutte" in prov
                                  or any(w in blob for w in ("milano", "malpensa", "linate", "trenord"))):
        zones.append("milano")
    if "toscana" in region and ("firenze" in prov or "tutte" in prov
                                or any(w in blob for w in ("firenze", "autolinee toscane"))):
        zones.append("firenze")
    if "emilia" in region and ("rimini" in prov
                               or any(w in blob for w in ("rimini", "romagna", "riccione"))):
        zones.append("rimini")

    # 飞机 / 火车这类跨城交通的罢工, 即使在外地也会影响行程
    if sector in ("aereo", "ferroviario") and "aziendale" not in rel and not zones:
        zones = ["it_national"]
    return zones, affects, False


def build_mit_events(items, today: datetime):
    events = []
    for it in items:
        desc = it["desc"]
        meta = {}
        for chunk in re.split(r"(?i)<br\s*/?>", desc):
            txt = htmlmod.unescape(re.sub(r"<[^>]+>", "", chunk)).strip()
            if ":" in txt:
                key, val = txt.split(":", 1)
                key = key.strip()
                if key and key not in meta:
                    meta[key] = val.strip()
        m = re.search(r"Data inizio:\s*(\d{2}/\d{2}/\d{4})", it["title"])
        start = parse_ddmmyyyy(m.group(1)) if m else None
        end = parse_ddmmyyyy(meta.get("Data fine", ""))
        if end is None:
            m2 = re.search(r"Data fine:\s*(\d{2}/\d{2}/\d{4})", desc)
            end = parse_ddmmyyyy(m2.group(1)) if m2 else start
        if end and end < today - timedelta(days=1):
            continue
        if start and start > today + timedelta(days=30):
            continue  # 只看一个月内, 远期罢工等临近再报

        blob = norm(" ".join([it["title"], desc]))
        if any(w in blob for w in ("revoca", "sospes", "differit")):
            continue

        zones, affects, national = mit_zones(meta, blob)
        if not zones:
            continue

        sector = meta.get("Settore", "")
        cat = meta.get("Categoria interessata", "")
        modal = meta.get("Modalità", "") or meta.get("Modalita", "")
        if not modal:
            mm = re.search(r"modalit[aà]:\s*([^<]+)", desc, re.I)
            modal = mm.group(1).strip() if mm else ""
        unions = meta.get("Sindacati", "")
        note = meta.get("Note", "")
        region = meta.get("Regione", "")
        prov = meta.get("Provincia", "").strip()

        sev = "high" if (national or "generale" in blob or "plurisettorial" in blob) else "medium"
        date_txt = ""
        if start and end:
            date_txt = f"{start:%m/%d} → {end:%m/%d}" if start.date() != end.date() \
                else f"{start:%m/%d}"
        where = " / ".join([x for x in [region, prov] if x and x.lower() != "tutte"])
        cat_short = re.sub(r"^(PERSONALE|LAVORATORI|OPERATORI|ADDETTI)\s+(SOC\.?|SOCC\.?)?\s*",
                           "", cat, flags=re.I).strip()[:90]
        cn = SECTOR_CN.get(norm(sector), sector or "公共交通")
        title_txt = cn if cn.startswith("⚠️") else f"{cn}罢工"
        if where:
            title_txt += f" · {where}"
        summary_bits = [x for x in [cat_short, modal, unions] if x]
        summary = " · ".join(summary_bits)[:300]
        if note:
            summary += f" ｜ {note[:120]}"

        for z in zones:
            events.append({
                "id": hash_id("mit", it["title"], str(start), str(end), z),
                "zone": z,
                "affects": affects if z == "it_national" else [],
                "source_id": "mit",
                "source": "MIT 罢工日历",
                "kind": "strike",
                "severity": sev,
                "title": title_txt,
                "summary": summary,
                "link": "https://scioperi.mit.gov.it/mit2/public/scioperi",
                "published": (end or start or today).isoformat(timespec="minutes"),
                "start": start.isoformat(timespec="minutes") if start else "",
                "dates": date_txt,
                "national": national,
                "soon": bool(start and 0 <= (start - today).days <= 3),
            })
    return events


# ---------------------------------------------------------------- 新闻源
def build_news_events(src, items):
    events = []
    now = datetime.now()
    cutoff = now - timedelta(days=NEWS_MAX_AGE_DAYS)
    for it in items:
        raw = " ".join([it["title"], clean_text(it["desc"], 600)])
        kind, sev = classify(raw)
        if kind is None:
            continue
        if re.search(r"(rassegna stampa|press review|rassegna web|il punto)", norm(it["title"])):
            continue
        pub = parse_date(it["published"])
        if pub and pub < cutoff:
            continue
        if not pub:
            pub = now

        found = zones_in(raw)
        mode = src["mode"]
        zone = None
        affects = []

        if mode == "city":
            if found:
                zone, affects = found[0], found[1:]
            elif mentions_other_place(raw):
                continue
            else:
                zone = src["zone"]
        elif mode == "region":
            if not found:
                continue
            zone, affects = found[0], found[1:]
        else:  # national
            # 全国媒体只看标题: 否则正文里一个“manifestation”会把无关新闻也拉进来
            if classify(it["title"])[0] is None:
                continue
            if found:
                zone, affects = found[0], found[1:]
            else:
                zone = src["zone"]

        # 旧闻降级: 超过 72 小时的“高危”降为关注
        if sev == "high" and (now - pub).total_seconds() > HIGH_FRESH_HOURS * 3600:
            sev = "medium"

        title = it["title"]
        if len(title) > 160:
            title = title[:159].rstrip() + "…"

        events.append({
            "id": hash_id(src["id"], it["title"]),
            "zone": zone,
            "affects": affects,
            "source_id": src["id"],
            "source": src["name"],
            "kind": kind,
            "severity": sev or "low",
            "title": title,
            "summary": clean_text(it["desc"], 300),
            "link": it["link"],
            "published": pub.isoformat(timespec="minutes"),
            "dates": "",
            "national": mode == "national",
            "soon": False,
        })
    return events


# ---------------------------------------------------------------- 抓取
def collect():
    today = datetime.now()
    results, status = [], []
    lock = threading.Lock()

    def work(src):
        t0 = time.time()
        try:
            items = parse_feed(fetch(src["url"]))
            evs = build_mit_events(items, today) if src["mode"] == "mit" else build_news_events(src, items)
            with lock:
                results.extend(evs)
                status.append({"id": src["id"], "name": src["name"], "ok": True,
                               "items": len(items), "kept": len(evs),
                               "ms": int((time.time() - t0) * 1000), "error": ""})
        except Exception as exc:  # noqa: BLE001
            with lock:
                status.append({"id": src["id"], "name": src["name"], "ok": False,
                               "items": 0, "kept": 0, "ms": int((time.time() - t0) * 1000),
                               "error": f"{type(exc).__name__}: {exc}"[:160]})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, SOURCES))

    return dedupe(results), status


def dedupe(events):
    """同一条新闻被多家转载时只留一条（同区域、标题前缀相同）。"""
    ordered = sorted(events, key=lambda e: e["published"])
    kept = []
    for ev in ordered:
        key = norm(ev["title"])[:42]
        if any(old["zone"] == ev["zone"] and norm(old["title"])[:42] == key for old in kept):
            continue
        kept.append(ev)
    return kept


def load_previous():
    if not EVENTS_FILE.exists():
        return {}
    try:
        data = json.loads(EVENTS_FILE.read_text("utf-8"))
        return {e["id"]: e for e in data.get("events", [])}
    except Exception:
        return {}


def merge_history(events, previous, now: datetime):
    merged = []
    for ev in events:
        old = previous.get(ev["id"])
        ev["first_seen"] = old.get("first_seen") if old else now.isoformat(timespec="minutes")
        ev["last_seen"] = now.isoformat(timespec="minutes")
        merged.append(ev)
    cutoff = now - timedelta(days=HISTORY_KEEP_DAYS)
    current = {e["id"] for e in merged}
    for eid, old in previous.items():
        if eid in current:
            continue
        try:
            seen = datetime.fromisoformat(old.get("last_seen", "2000-01-01"))
        except Exception:
            continue
        if seen >= cutoff:
            merged.append(old)
    merged.sort(key=lambda e: (SEV_RANK.get(e["severity"], 0), e.get("published", "")),
                reverse=True)
    return merged


def save(events, status):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.write_text(json.dumps(
        {"updated": datetime.now().isoformat(timespec="minutes"), "events": events},
        ensure_ascii=False, indent=1), "utf-8")
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=1), "utf-8")


# ---------------------------------------------------------------- 终端摘要
def human_age(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        return ""
    secs = (datetime.now() - dt).total_seconds()
    if secs < 0:
        return "即将开始"
    if secs < 3600:
        return f"{max(1, int(secs // 60))} 分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小时前"
    return f"{int(secs // 86400)} 天前"


def print_digest(events, status, new_ids):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok = sum(1 for s in status if s["ok"])
    high = [e for e in events if e["severity"] == "high"]
    print(f"[{now}] 来源 {ok}/{len(status)} 正常 · 命中 {len(events)} 条 · 高危 {len(high)} 条 "
          f"· 本轮新增 {len(new_ids)} 条")
    for s in [s for s in status if not s["ok"]]:
        print(f"  ⚠ 源失败 {s['name']}: {s['error'][:90]}")

    shown = [e for e in events if e["id"] in new_ids] or events
    by_zone = {}
    for ev in shown:
        by_zone.setdefault(ev["zone"], []).append(ev)
    for z in ZONE_ORDER:
        arr = by_zone.get(z)
        if not arr:
            continue
        print(f"  {ZONES[z]['flag']} {ZONES[z]['name']}")
        for ev in arr[:6]:
            mark = "🔴" if ev["severity"] == "high" else ("🟠" if ev["severity"] == "medium" else "🔵")
            tag = "新增" if ev["id"] in new_ids else human_age(ev["last_seen"])
            dates = f" {ev['dates']}" if ev.get("dates") else ""
            print(f"    {mark} [{KIND_LABEL.get(ev['kind'], ev['kind'])}]{dates} "
                  f"{ev['title'][:74]}  ({ev['source']}, {tag})")
    print(f"  看板: {DASHBOARD}")


# ---------------------------------------------------------------- 看板
HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>罢工 · 游行 监测</title>
<style>
  :root{
    --bg:#0a0f1c; --panel:#111827; --panel2:#0f172a; --line:#1f2a3d;
    --text:#e8eefc; --muted:#8ea0bd;
    --high:#ef4444; --med:#f59e0b; --low:#38bdf8;
  }
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1200px 600px at 15% -10%,#15213a 0%,var(--bg) 55%) fixed;
       color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",
       "PingFang SC","Microsoft YaHei",Roboto,sans-serif;min-height:100vh}
  .wrap{max-width:1120px;margin:0 auto;padding:22px 20px 60px}
  header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;
         padding-bottom:14px;border-bottom:1px solid var(--line)}
  .brand{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .pulse{width:10px;height:10px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 0 rgba(34,197,94,.6);
         animation:p 2.4s infinite}
  @keyframes p{0%{box-shadow:0 0 0 0 rgba(34,197,94,.5)}70%{box-shadow:0 0 0 10px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
  h1{font-size:19px;margin:0;letter-spacing:.3px;font-weight:650}
  .sub{color:var(--muted);font-size:12.5px;margin-top:3px}
  .stamp{color:var(--muted);font-size:12.5px;text-align:right;line-height:1.7}
  .stamp b{color:var(--text);font-weight:600}
  .alert{margin:18px 0 0;padding:13px 16px;border-radius:12px;border:1px solid rgba(239,68,68,.4);
          background:linear-gradient(90deg,rgba(239,68,68,.16),rgba(239,68,68,.04));display:none}
  .alert.on{display:block}
  .alert h3{margin:0 0 6px;font-size:12.5px;letter-spacing:.8px;color:#fecaca;text-transform:uppercase}
  .alert ul{margin:0;padding-left:18px;font-size:13.5px}
  .alert li{margin:3px 0}
  .cal{display:none;margin-top:18px;padding:14px 16px;border:1px solid var(--line);border-radius:12px;
       background:linear-gradient(180deg,rgba(23,32,51,.75),rgba(15,23,42,.55))}
  .cal.on{display:block}
  .cal h3{margin:0 0 10px;font-size:12px;letter-spacing:.9px;color:#9fb3d1;text-transform:uppercase;font-weight:600}
  .calrow{display:flex;gap:10px;flex-wrap:wrap}
  .calitem{display:flex;flex-direction:column;gap:2px;padding:8px 12px;border-radius:9px;
           background:rgba(10,16,28,.7);border:1px solid var(--line);min-width:140px;max-width:230px}
  .calitem b{font-size:15px;font-variant-numeric:tabular-nums}
  .calitem span{font-size:12px;color:var(--muted);line-height:1.4}
  .calitem.high{border-color:rgba(239,68,68,.45)}
  .calitem.high b{color:#fca5a5}
  .fl{display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:17px;
      padding:0 4px;border-radius:4px;background:#1b2740;color:#9fb3d1;font-size:10px;
      font-weight:700;letter-spacing:.4px;vertical-align:middle}
  .bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:20px 0 6px}
  .tabs{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .tab{border:1px solid var(--line);background:var(--panel2);color:var(--muted);padding:7px 13px;
       border-radius:999px;cursor:pointer;font-size:13px;transition:.16s;white-space:nowrap}
  .tab:hover{border-color:#2e4160;color:var(--text)}
  .tab.on{background:var(--text);color:#0a0f1c;border-color:var(--text);font-weight:600}
  .tab .n{opacity:.55;margin-left:5px;font-variant-numeric:tabular-nums}
  .tabsep{width:1px;height:20px;background:#2a3a55;margin:0 3px}
  .tools{margin-left:auto;display:flex;gap:8px;align-items:center}
  input[type=search]{background:var(--panel2);border:1px solid var(--line);color:var(--text);
       padding:8px 12px;border-radius:9px;font-size:13px;width:180px;outline:none}
  input[type=search]:focus{border-color:#3b5075}
  .chip{border:1px solid var(--line);background:var(--panel2);color:var(--muted);border-radius:9px;
        padding:8px 11px;font-size:12.5px;cursor:pointer;white-space:nowrap}
  .chip.on{border-color:var(--high);color:#fecaca;background:rgba(239,68,68,.12)}
  .city{margin-top:26px}
  .city h2{font-size:15px;margin:0 0 10px;display:flex;align-items:center;gap:9px;font-weight:600}
  .city h2 .cnt{color:var(--muted);font-weight:400;font-size:12.5px}
  .grid{display:grid;gap:10px}
  .card{background:linear-gradient(180deg,rgba(23,32,51,.92),rgba(16,23,38,.92));
        border:1px solid var(--line);border-left:3px solid var(--line);border-radius:11px;
        padding:12px 14px;transition:.15s}
  .card:hover{border-color:#2c3d5c;transform:translateY(-1px)}
  .card.high{border-left-color:var(--high)}
  .card.medium{border-left-color:var(--med)}
  .card.low{border-left-color:var(--low)}
  .row1{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--muted);flex-wrap:wrap}
  .sev{padding:1px 7px;border-radius:5px;font-weight:600;letter-spacing:.3px}
  .sev.high{background:rgba(239,68,68,.16);color:#fca5a5}
  .sev.medium{background:rgba(245,158,11,.14);color:#fcd34d}
  .sev.low{background:rgba(56,189,248,.12);color:#7dd3fc}
  .kind{color:#b7c7e0}
  .new{background:rgba(34,197,94,.14);color:#86efac;padding:1px 7px;border-radius:5px;font-weight:600}
  .nat{background:rgba(168,85,247,.16);color:#d8b4fe;padding:1px 7px;border-radius:5px}
  .dates{background:rgba(56,189,248,.12);color:#7dd3fc;padding:1px 7px;border-radius:5px;
         font-variant-numeric:tabular-nums}
  .soon{background:rgba(245,158,11,.15);color:#fcd34d;padding:1px 7px;border-radius:5px;font-weight:600}
  .card h3{margin:7px 0 4px;font-size:14.5px;line-height:1.45;font-weight:600}
  .ex{margin:0;color:var(--muted);font-size:13px}
  .foot{display:flex;justify-content:space-between;gap:10px;margin-top:9px;font-size:12px;color:#6f819d}
  .foot a{color:#7dd3fc;text-decoration:none}
  .foot a:hover{text-decoration:underline}
  .empty{color:var(--muted);font-size:13.5px;padding:26px;text-align:center;border:1px dashed var(--line);
         border-radius:12px;margin-top:22px}
  .more{justify-self:start;border:1px dashed var(--line);background:transparent;color:var(--muted);
        border-radius:9px;padding:7px 14px;font-size:12.5px;cursor:pointer}
  .more:hover{color:var(--text);border-color:#3b5075}
  footer{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);color:#64748b;font-size:11.5px}
  @media (max-width: 640px){
    .wrap{padding:16px 12px 44px}
    h1{font-size:17px}
    .sub{font-size:11.5px}
    .stamp{text-align:left}
    .tools{margin-left:0;width:100%}
    input[type=search]{flex:1;width:auto;min-width:0}
    .tab{padding:6px 10px;font-size:12px}
    .calitem{min-width:118px;max-width:none;flex:1 1 140px}
    .card{padding:11px 12px}
    .card h3{font-size:14px}
    .ex{font-size:12.5px}
    .foot{flex-direction:column;gap:5px}
  }
  .srcline{line-height:1.9}
  .srcline i{font-style:normal;color:#22c55e}
  .srcline i.bad{color:#ef4444}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <span class="pulse"></span>
      <div>
        <h1>罢工 · 游行 实时监测</h1>
        <div class="sub">法国 · 意大利　—　本地: 蒙彼利埃 / 米兰 / 佛罗伦萨 / 里米尼 / 圣马力诺</div>
      </div>
    </div>
    <div class="stamp">最后更新 <b id="upd">—</b><br><span id="srccount">—</span></div>
  </header>

  <div class="alert" id="alert"><h3>需留意 · 近 72 小时高风险</h3><ul id="alertList"></ul></div>

  <section class="cal" id="cal"></section>

  <div class="bar">
    <div class="tabs" id="tabs"></div>
    <div class="tools">
      <button class="chip" id="hiOnly">只看高危</button>
      <input type="search" id="q" placeholder="搜索…">
    </div>
  </div>

  <main id="list"></main>

  <footer>
    <div class="srcline" id="srcline"></div>
    <div class="srcline" style="margin-top:8px">仅供行程参考。遇到游行: 绕行、不逆行、不停留拍摄, 先进商店/地铁站等封闭空间; 交通罢工前一天确认班次。</div>
  </footer>
</div>

<script>
const DATA = __DATA__;
const ZONES = __ZONES__;
const SEV = {high:"高危", medium:"关注", low:"信息"};
const KIND = {strike:"罢工", protest:"游行/集会", news:"相关动态"};
let activeZone = "all", highOnly = false, query = "";
const LIMIT = 8;
const expanded = {};

function esc(s){ return String(s||"").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
function age(iso){
  if(!iso) return "";
  const t = new Date(iso).getTime();
  if(isNaN(t)) return "";
  const s = (Date.now()-t)/1000;
  if(s < 0) return "即将开始";
  if(s < 3600) return Math.max(1,Math.round(s/60))+" 分钟前";
  if(s < 86400) return Math.round(s/3600)+" 小时前";
  return Math.round(s/86400)+" 天前";
}
function isNew(e){
  const t = new Date(e.first_seen||"").getTime();
  return !isNaN(t) && (Date.now()-t) < 24*3600*1000;
}
function inZone(e, z){
  if(z === "all") return true;
  return e.zone === z || (e.affects||[]).includes(z);
}
function visible(e){
  if(!inZone(e, activeZone)) return false;
  if(highOnly && e.severity !== "high") return false;
  if(query){
    const blob = (e.title+" "+(e.summary||"")+" "+e.source).toLowerCase();
    if(!blob.includes(query.toLowerCase())) return false;
  }
  return true;
}
function renderTabs(){
  const tabs = document.getElementById("tabs");
  const count = z => (DATA.events||[]).filter(e => inZone(e, z)).length;
  let html = `<button class="tab ${activeZone==="all"?"on":""}" data-z="all">全部<span class="n">${(DATA.events||[]).length}</span></button>`;
  let usedSep = false;
  for(const z of Object.keys(ZONES)){
    const Z = ZONES[z];
    if(Z.group === "national" && !usedSep){ html += `<span class="tabsep"></span>`; usedSep = true; }
    html += `<button class="tab ${activeZone===z?"on":""}" data-z="${z}"><span class="fl">${Z.flag}</span> ${Z.name}<span class="n">${count(z)}</span></button>`;
  }
  tabs.innerHTML = html;
  tabs.querySelectorAll("button.tab").forEach(b => b.onclick = () => { activeZone = b.dataset.z; renderTabs(); render(); });
}
function render(){
  const list = document.getElementById("list");
  let events = (DATA.events||[]).filter(visible);
  const seen = new Set();
  events = events.filter(e => { if(seen.has(e.id)) return false; seen.add(e.id); return true; });

  if(!events.length){
    list.innerHTML = '<div class="empty">当前筛选下没有相关动态，监测仍在运行。</div>';
    return;
  }
  const groups = {};
  for(const e of events){
    const z = (activeZone === "all") ? e.zone : activeZone;
    (groups[z] = groups[z]||[]).push(e);
  }
  let html = "";
  for(const z of Object.keys(ZONES)){
    const arr = groups[z]; if(!arr) continue;
    const Z = ZONES[z];
    html += `<section class="city"><h2><span class="fl">${Z.flag}</span>${Z.name}
      <span class="cnt">${Z.latin} · ${arr.length} 条</span></h2><div class="grid">`;
    const shown = expanded[z] ? arr : arr.slice(0, LIMIT);
    for(const e of shown){
      const cls = e.severity||"low";
      html += `<article class="card ${cls}">
        <div class="row1">
          <span class="sev ${cls}">${SEV[cls]||cls}</span>
          <span class="kind">${KIND[e.kind]||e.kind||""}</span>
          ${isNew(e)?'<span class="new">新增</span>':''}
          ${e.national?'<span class="nat">全国</span>':''}
          ${e.dates?`<span class="dates">📅 ${esc(e.dates)}</span>`:''}
          ${e.soon?'<span class="soon">3 天内</span>':''}
          <span style="margin-left:auto">${age(e.published)}</span>
        </div>
        <h3>${esc(e.title)}</h3>
        ${e.summary?`<p class="ex">${esc(e.summary)}</p>`:''}
        <div class="foot"><span>${esc(e.source||"")}${(e.affects&&e.affects.length)?" · 影响 "+e.affects.map(a=>ZONES[a]?ZONES[a].name:a).join("、"):""}</span>
          ${e.link?`<a href="${esc(e.link)}" target="_blank" rel="noreferrer">原文 ↗</a>`:""}</div>
      </article>`;
    }
    if(arr.length > LIMIT){
      html += `<button class="more" data-z="${z}">${expanded[z] ? "收起" : "展开另外 "+(arr.length-LIMIT)+" 条"}</button>`;
    }
    html += "</div></section>";
  }
  list.innerHTML = html;
  list.querySelectorAll("button.more").forEach(b => b.onclick = () => {
    expanded[b.dataset.z] = !expanded[b.dataset.z]; render();
  });
}
function renderCalendar(){
  const box = document.getElementById("cal");
  const now = Date.now();
  const seen = new Set();
  const items = (DATA.events||[]).filter(e => e.source_id==="mit" && e.start)
    .filter(e => { const t = new Date(e.start).getTime(); return t >= now-864e5 && t <= now+14*864e5; })
    .filter(e => { const k = e.title+"|"+e.start; if(seen.has(k)) return false; seen.add(k); return true; })
    .sort((a,b) => a.start.localeCompare(b.start));
  if(!items.length){ box.classList.remove("on"); return; }
  box.classList.add("on");
  box.innerHTML = `<h3>未来两周 · 意大利交通罢工</h3><div class="calrow">` +
    items.map(e => `<div class="calitem ${e.severity}"><b>${fmtMD(e.start)}</b>
      <span>${esc(e.title)}</span></div>`).join("") + `</div>`;
}
function fmtMD(iso){
  const d = new Date(iso);
  return (d.getMonth()+1)+"/"+String(d.getDate()).padStart(2,"0");
}
function renderAlert(){
  const box = document.getElementById("alert"), ul = document.getElementById("alertList");
  const now = Date.now();
  const hot = (DATA.events||[]).filter(e => e.severity==="high" &&
      (now - new Date(e.published).getTime()) < 3*86400*1000)
    .sort((a,b) => {
      const la = (ZONES[a.zone]||{}).group==="local" ? 0 : 1;
      const lb = (ZONES[b.zone]||{}).group==="local" ? 0 : 1;
      if(la !== lb) return la - lb;
      return new Date(b.published) - new Date(a.published);
    }).slice(0,4);
  if(!hot.length){ box.classList.remove("on"); return; }
  box.classList.add("on");
  ul.innerHTML = hot.map(e => {
    const Z = ZONES[e.zone] || {flag:"", name:e.zone};
    return `<li>${Z.flag} <b>${Z.name}</b> · ${esc(e.title)} <span style="color:#fca5a5">${esc(e.dates||"")}</span></li>`;
  }).join("");
}
function renderStatus(){
  const st = DATA.status||[];
  const ok = st.filter(s=>s.ok).length;
  document.getElementById("srccount").innerHTML = `数据源 <b>${ok}/${st.length}</b> 在线`;
  document.getElementById("srcline").innerHTML = "数据源: " + st.map(s =>
    `<i class="${s.ok?"":"bad"}">●</i> ${esc(s.name)}`).join("　");
}
function updateStamp(){ document.getElementById("upd").textContent = (DATA.updated||"—").replace("T"," "); }
function renderAll(){ renderTabs(); render(); renderCalendar(); renderAlert(); renderStatus(); updateStamp(); }
async function tick(){
  try{
    const r = await fetch("data/events.json?t="+Date.now(), {cache:"no-store"});
    if(r.ok){ const j = await r.json(); Object.assign(DATA, j); renderAll(); }
  }catch(e){ /* file:// 打开时退回内嵌数据 */ }
}
document.getElementById("hiOnly").onclick = (e)=>{ highOnly=!highOnly; e.target.classList.toggle("on",highOnly); render(); };
document.getElementById("q").oninput = (e)=>{ query=e.target.value.trim(); render(); };
renderAll();
setInterval(tick, 60000);
</script>
</body>
</html>
"""


def render_html(events, status):
    payload = {"updated": datetime.now().isoformat(timespec="minutes"),
               "events": events, "status": status}
    page = (HTML_TEMPLATE
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
            .replace("__ZONES__", json.dumps(ZONES, ensure_ascii=False)))
    DASHBOARD.write_text(page, "utf-8")


# ---------------------------------------------------------------- 服务
def start_server(port: int):
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(BASE), **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- 主流程
def run_once(quiet: bool = False):
    now = datetime.now()
    prev = load_previous()
    events, status = collect()
    events = merge_history(events, prev, now)
    new_ids = {e["id"] for e in events if e["id"] not in prev}
    save(events, status)
    render_html(events, status)
    if not quiet:
        print_digest(events, status, new_ids)
    return events, status, new_ids


def emit(events, status, new_ids, as_json: bool):
    if as_json:
        payload = [e for e in events if e["id"] in new_ids]
        print(json.dumps({"updated": datetime.now().isoformat(timespec="minutes"),
                          "new": payload, "total": len(events),
                          "sources_ok": sum(1 for s in status if s["ok"]),
                          "sources_total": len(status)}, ensure_ascii=False))
    else:
        for e in events:
            if e["id"] in new_ids:
                print(f"{SEV_LABEL.get(e['severity'], e['severity'])}｜"
                      f"{ZONES[e['zone']]['name']}｜{e['title']}｜{e['source']}｜{e['published']}")


def main():
    ap = argparse.ArgumentParser(description="罢工/游行实时监测(法国+意大利)")
    ap.add_argument("--watch", type=int, default=0, metavar="SEC", help="循环间隔秒数")
    ap.add_argument("--serve", type=int, default=0, metavar="PORT", help="本地 HTTP 端口")
    ap.add_argument("--open", action="store_true", help="打开看板")
    ap.add_argument("--new-only", action="store_true", help="只列本轮新增")
    ap.add_argument("--json", action="store_true", help="JSON 输出本轮新增")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    url = f"http://127.0.0.1:{args.serve}/dashboard.html" if args.serve else ""
    if args.serve:
        start_server(args.serve)
        time.sleep(0.4)

    if args.watch:
        first = True
        while True:
            try:
                events, status, new_ids = run_once(quiet=args.quiet or args.json or args.new_only)
                if args.json or args.new_only:
                    emit(events, status, new_ids, args.json)
                    sys.stdout.flush()
                if first and args.open:
                    webbrowser.open(url or DASHBOARD.as_uri())
                first = False
            except Exception as exc:  # noqa: BLE001
                print(f"[{datetime.now():%H:%M}] 抓取失败: {type(exc).__name__}: {exc}")
            time.sleep(max(30, args.watch))
        return

    events, status, new_ids = run_once(quiet=args.quiet or args.json or args.new_only)
    if args.json or args.new_only:
        emit(events, status, new_ids, args.json)
    if args.open:
        webbrowser.open(url or DASHBOARD.as_uri())
    if args.serve:
        print(f"看板: {url}   (Ctrl+C 退出)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
