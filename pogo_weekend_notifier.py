"""
Pokémon GO 週末活動通知器
每週五執行：檢查這週末（週六、週日）有沒有社群日或其他活動，透過 Discord 通知。

資料來源
  1. LeekDuck 活動行事曆（經 ScrapedDuck 轉成 JSON）：有精確起訖時間，用來判斷「這週末」
  2. Pokémon GO 官網新聞頁：抓最新的活動相關公告標題與連結，附在通知裡

通知方式（可同時設定多種，有設的都會發；多個 ID 用逗號分隔）
  DISCORD_WEBHOOK_URL                         → 用 Webhook 發到某個頻道
  DISCORD_BOT_TOKEN + DISCORD_USER_ID         → 機器人私訊這些人
  DISCORD_BOT_TOKEN + DISCORD_CHANNEL_IDS     → 機器人發到這些伺服器頻道

本機測試：python pogo_weekend_notifier.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ---------- 設定 ----------
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Taipei"))

EVENTS_JSON_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json"

OFFICIAL_NEWS_URLS = [
    "https://pokemongo.com/zh_Hant/news",
    "https://pokemongo.com/news",
]

# 不算「週末活動」的類型：長期掛著的、每週輪替的團體戰頭目、週一極巨戰等
EXCLUDED_TYPES = {
    "season",
    "go-battle-league",
    "research",
    "research-breakthrough",
    "go-pass",
    "pokemon-go-pass",
    "raid-battles",
    "max-mondays",
    "twitch-drops",
}
# 只在特定城市舉辦的實體活動類型：名稱含 LOCAL_KEEP_KEYWORDS 才保留
LOCAL_ONLY_TYPES = {"wild-area", "city-safari", "pokemon-go-tour", "pokemon-go-fest", "live-event"}
LOCAL_KEEP_KEYWORDS = ["Global", "Taiwan", "Taipei", "Kaohsiung", "Tainan", "Taichung", "Taoyuan"]
# 名稱含這些字的是實體聚會，略過
EXCLUDED_NAME_KEYWORDS = ["Night Out"]

# 超過這個天數的活動視為長期活動，不列入（社群日除外）
MAX_EVENT_DAYS = 10

# 官網公告標題的關鍵字
NEWS_KEYWORDS = [
    "社群日", "活動", "聚焦時刻", "團體戰", "孵化日", "極巨", "研究日", "曠野",
    "Community Day", "Event", "Raid Day", "GO Fest", "Spotlight", "Max",
]
NEWS_MAX_AGE_DAYS = 21   # 只列出這幾天內發布的公告
MAX_NEWS_ITEMS = 5

# 沒活動時要不要也通知（"1" = 要）
NOTIFY_WHEN_EMPTY = os.getenv("NOTIFY_WHEN_EMPTY", "1") == "1"

TYPE_LABELS = {
    "community-day": "🌟 社群日",
    "community-day-classic": "🌟 經典社群日",
    "raid-day": "⚔️ 團體戰日",
    "raid-weekend": "⚔️ 團體戰週末",
    "raid-hour": "⚔️ 團體戰時間",
    "raid-battles": "⚔️ 團體戰",
    "max-battles": "💥 極巨戰",
    "max-mondays": "💥 極巨星期一",
    "pokemon-spotlight-hour": "🔦 聚焦時刻",
    "event": "🎉 活動",
    "live-event": "🎪 實體活動",
    "pokemon-go-fest": "🎪 GO Fest",
    "global-challenge": "🌍 全球挑戰",
    "ticketed-event": "🎟️ 付費活動",
    "elite-raids": "⚔️ 精英團體戰",
    "city-safari": "🏙️ 城市野生區",
    "bonus-hour": "⏰ 加成時刻",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (pogo-weekend-notifier; personal use)"}


# ---------- 資料結構 ----------
@dataclass
class Event:
    name: str
    type: str
    heading: str
    link: str
    image: str | None
    start: datetime
    end: datetime

    @property
    def is_community_day(self) -> bool:
        return self.type.startswith("community-day")


# ---------- 時間工具 ----------
def parse_time(value: str | None) -> datetime | None:
    """ScrapedDuck 的時間：結尾有 Z 是 UTC（全球同時開始）；沒有就是「各地當地時間」。"""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(TZ)
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)  # 當地時間 → 以台灣時間解讀
        return dt.astimezone(TZ)
    except ValueError:
        return None


def this_weekend(now: datetime) -> tuple[datetime, datetime]:
    """回傳本週六 00:00 到下週一 00:00。週六、週日執行時回傳當下這個週末。"""
    today = now.date()
    wd = today.weekday()  # 週一=0 … 週日=6
    if wd == 6:
        saturday = today - timedelta(days=1)
    else:
        saturday = today + timedelta(days=(5 - wd) % 7)
    start = datetime.combine(saturday, datetime.min.time(), tzinfo=TZ)
    return start, start + timedelta(days=2)


WEEKDAYS = "一二三四五六日"


def fmt(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}（{WEEKDAYS[dt.weekday()]}）{dt:%H:%M}"


# ---------- 抓資料 ----------
def fetch_events() -> list[Event]:
    resp = requests.get(EVENTS_JSON_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    events = []
    for raw in resp.json():
        start, end = parse_time(raw.get("start")), parse_time(raw.get("end"))
        if not start:
            continue
        events.append(Event(
            name=raw.get("name", "（未命名活動）"),
            type=raw.get("eventType", "event"),
            heading=raw.get("heading", ""),
            link=raw.get("link", ""),
            image=raw.get("image"),
            start=start,
            end=end or start + timedelta(hours=3),
        ))
    return events


def weekend_events(events: list[Event], win_start: datetime, win_end: datetime) -> list[Event]:
    picked = []
    for e in events:
        if e.type in EXCLUDED_TYPES:
            continue
        if e.type in LOCAL_ONLY_TYPES and not any(k.lower() in e.name.lower() for k in LOCAL_KEEP_KEYWORDS):
            continue
        if any(k.lower() in e.name.lower() for k in EXCLUDED_NAME_KEYWORDS):
            continue
        overlaps = e.start < win_end and e.end > win_start
        if not overlaps:
            continue
        too_long = (e.end - e.start) > timedelta(days=MAX_EVENT_DAYS)
        if too_long and not e.is_community_day:
            continue
        picked.append(e)
    # 社群日排最前面，其餘依開始時間
    picked.sort(key=lambda e: (not e.is_community_day, e.start))
    return picked


def parse_news_cards(html: str, base_url: str, now: datetime) -> list[tuple[str, str]]:
    """官網新聞卡片：<a href="/zh_Hant/news/..."> 內含
    <pg-date-format timestamp="毫秒"> 和 class 含 newsCardTitle 的標題。"""
    soup = BeautifulSoup(html, "html.parser")
    oldest = now - timedelta(days=NEWS_MAX_AGE_DAYS)
    seen, items = set(), []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if not re.search(r"/news/[^/?#]+", href) or href in seen:
            continue
        seen.add(href)

        title_el = a.find(class_=re.compile("newsCardTitle"))
        title = (title_el or a).get_text(" ", strip=True)
        title = " ".join(title.split())
        if not title:
            continue

        date_label = ""
        date_el = a.find("pg-date-format")
        if date_el and date_el.get("timestamp", "").isdigit():
            published = datetime.fromtimestamp(int(date_el["timestamp"]) / 1000, TZ)
            if published < oldest:
                continue
            date_label = f"{published.month}/{published.day} "

        if any(k.lower() in title.lower() for k in NEWS_KEYWORDS):
            items.append((f"{date_label}{title}"[:120], href))
        if len(items) >= MAX_NEWS_ITEMS:
            break
    return items


def fetch_official_news(now: datetime) -> tuple[str, list[tuple[str, str]]]:
    """回傳 (成功的官網網址, [(標題, 連結), ...])。抓不到就回傳空清單。"""
    for url in OFFICIAL_NEWS_URLS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[官網] {url} 讀取失敗：{exc}", file=sys.stderr)
            continue
        items = parse_news_cards(resp.text, url, now)
        if items:
            return url, items
        print(f"[官網] {url} 沒有找到符合條件的公告", file=sys.stderr)
    return OFFICIAL_NEWS_URLS[0], []


# ---------- 組訊息 ----------
def build_payload(events: list[Event], news: list[tuple[str, str]], news_url: str,
                  win_start: datetime, win_end: datetime) -> dict | None:
    sat, sun = win_start, win_end - timedelta(days=1)
    title_range = f"{sat.month}/{sat.day}–{sun.month}/{sun.day}"
    has_cd = any(e.is_community_day for e in events)

    if not events and not NOTIFY_WHEN_EMPTY:
        return None

    if has_cd:
        content = f"📣 **這週末（{title_range}）有社群日！**"
    elif events:
        content = f"📣 這週末（{title_range}）有 {len(events)} 個活動"
    else:
        content = f"😴 這週末（{title_range}）沒有排定的活動"

    embeds = []
    for e in events[:9]:  # Discord 一則訊息最多 10 個 embed，留一個給官網公告
        label = TYPE_LABELS.get(e.type, f"📌 {e.heading or e.type}")
        embed = {
            "title": e.name[:256],
            "url": e.link if e.link.startswith("http") else None,
            "color": 0xF5B700 if e.is_community_day else 0x3B82F6,
            "fields": [
                {"name": "類型", "value": label, "inline": True},
                {"name": "開始", "value": fmt(e.start), "inline": True},
                {"name": "結束", "value": fmt(e.end), "inline": True},
            ],
        }
        if e.image and e.image.startswith("http"):
            embed["thumbnail"] = {"url": e.image}
        embeds.append({k: v for k, v in embed.items() if v is not None})

    if news:
        lines = [f"• [{t}]({u})" for t, u in news]
        embeds.append({
            "title": "官網最新活動公告",
            "url": news_url,
            "color": 0x10B981,
            "description": "\n".join(lines)[:4000],
        })

    return {"content": content, "embeds": embeds}


# ---------- 發送 ----------
def send_via_webhook(webhook_url: str, payload: dict) -> None:
    body = {"username": "Pokémon GO 週末通知", **payload}
    resp = requests.post(webhook_url, json=body, timeout=30)
    resp.raise_for_status()


DISCORD_API = "https://discord.com/api/v10"


def _bot_headers(token: str) -> dict:
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


def send_to_channel(token: str, channel_id: str, payload: dict) -> None:
    resp = requests.post(f"{DISCORD_API}/channels/{channel_id}/messages",
                         headers=_bot_headers(token), json=payload, timeout=30)
    resp.raise_for_status()


def send_via_bot_dm(token: str, user_id: str, payload: dict) -> None:
    dm = requests.post(f"{DISCORD_API}/users/@me/channels", headers=_bot_headers(token),
                       json={"recipient_id": user_id}, timeout=30)
    dm.raise_for_status()
    send_to_channel(token, dm.json()["id"], payload)


def _ids(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def send(payload: dict) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    token = os.getenv("DISCORD_BOT_TOKEN")
    targets = []
    if webhook:
        targets.append(("Webhook", lambda: send_via_webhook(webhook, payload)))
    if token:
        for uid in _ids("DISCORD_USER_ID"):
            targets.append((f"私訊 {uid}", lambda uid=uid: send_via_bot_dm(token, uid, payload)))
        for cid in _ids("DISCORD_CHANNEL_IDS"):
            targets.append((f"頻道 {cid}", lambda cid=cid: send_to_channel(token, cid, payload)))
    if not targets:
        raise SystemExit("請設定 DISCORD_WEBHOOK_URL，或 DISCORD_BOT_TOKEN 加上 "
                         "DISCORD_USER_ID / DISCORD_CHANNEL_IDS")

    failed = 0
    for label, do_send in targets:   # 一個失敗不影響其他人
        try:
            do_send()
            print(f"✅ {label}")
        except requests.RequestException as exc:
            failed += 1
            detail = getattr(exc.response, "text", "")[:200] if exc.response is not None else ""
            print(f"❌ {label}：{exc} {detail}", file=sys.stderr)
    if failed:
        raise SystemExit(f"{failed}/{len(targets)} 個對象發送失敗")


# ---------- 主程式 ----------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只印出訊息，不發到 Discord")
    args = parser.parse_args()

    now = datetime.now(TZ)
    win_start, win_end = this_weekend(now)
    print(f"檢查區間：{fmt(win_start)} ～ {fmt(win_end)}")

    try:
        events = weekend_events(fetch_events(), win_start, win_end)
    except requests.RequestException as exc:
        print(f"[活動資料] 讀取失敗：{exc}", file=sys.stderr)
        events = []

    news_url, news = fetch_official_news(now)
    payload = build_payload(events, news, news_url, win_start, win_end)

    if payload is None:
        print("這週末沒有活動，且設定為不通知。")
        return

    if args.dry_run:
        import json
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    send(payload)
    print("已送出 Discord 通知。")


if __name__ == "__main__":
    main()
