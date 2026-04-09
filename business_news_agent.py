#!/usr/bin/env python3
"""
Japan major news briefing agent.

- Collects major Japanese headlines from RSS feeds
- Creates a concise Japanese morning briefing
- Runs once or every day at 05:00 (Asia/Tokyo)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
OUTPUT_DIR = Path("daily_reports")
MAX_ITEMS = 15

# Mainstream/public RSS feeds (Japan major topics)
RSS_FEEDS = [
    "https://www3.nhk.or.jp/rss/news/cat0.xml",  # top
    "https://www3.nhk.or.jp/rss/news/cat1.xml",  # social
    "https://www3.nhk.or.jp/rss/news/cat4.xml",  # politics
    "https://www3.nhk.or.jp/rss/news/cat5.xml",  # international
    "https://www3.nhk.or.jp/rss/news/cat6.xml",  # economy
    "https://www3.nhk.or.jp/rss/news/cat7.xml",  # science/culture
    "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
    "https://news.yahoo.co.jp/rss/topics/domestic.xml",
    "https://news.yahoo.co.jp/rss/topics/world.xml",
    "https://news.yahoo.co.jp/rss/topics/business.xml",
    "https://news.yahoo.co.jp/rss/topics/it.xml",
]

CATEGORY_HINTS = {
    "政治": ["政治", "国会", "選挙", "首相", "政府", "政策", "外務省"],
    "経済": ["経済", "市場", "株", "金利", "為替", "決算", "企業", "物価", "インフレ"],
    "社会": ["事故", "災害", "事件", "医療", "教育", "地域", "社会"],
    "国際": ["国際", "米国", "中国", "欧州", "ウクライナ", "中東", "外交"],
    "テクノロジー": ["ai", "生成ai", "半導体", "it", "テック", "宇宙", "科学"],
}


@dataclass
class NewsItem:
    title: str
    link: str
    summary: str
    published: str
    source: str


def fetch_url(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MorningBusinessNewsAgent/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_rss(xml_text: str, source_url: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title", default=""))
        link = clean_text(item.findtext("link", default=""))
        desc = clean_text(item.findtext("description", default=""))
        pub = clean_text(item.findtext("pubDate", default=""))
        if not title or not link:
            continue
        items.append(
            NewsItem(
                title=title,
                link=link,
                summary=desc,
                published=pub,
                source=source_url,
            )
        )
    return items


def score_item(item: NewsItem) -> int:
    haystack = f"{item.title} {item.summary}".lower()
    score = 0
    for words in CATEGORY_HINTS.values():
        for kw in words:
            if kw.lower() in haystack:
                score += 1
    # Prefer shorter/clear titles a bit
    if len(item.title) <= 60:
        score += 1
    return score


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        key = re.sub(r"\W+", "", item.title.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def collect_news() -> list[NewsItem]:
    all_items: list[NewsItem] = []
    for url in RSS_FEEDS:
        try:
            xml_text = fetch_url(url)
        except (urllib.error.URLError, TimeoutError):
            continue
        all_items.extend(parse_rss(xml_text, source_url=url))

    all_items = dedupe_items(all_items)
    all_items.sort(key=score_item, reverse=True)
    return all_items[:MAX_ITEMS]


def detect_category(item: NewsItem) -> str:
    haystack = f"{item.title} {item.summary}".lower()
    best_name = "その他"
    best_score = 0
    for category, words in CATEGORY_HINTS.items():
        score = sum(1 for w in words if w.lower() in haystack)
        if score > best_score:
            best_score = score
            best_name = category
    return best_name


def one_line_summary(item: NewsItem) -> str:
    snippet = item.summary
    if not snippet:
        snippet = "詳細はリンク先を確認してください。"
    if len(snippet) > 90:
        snippet = snippet[:87] + "..."
    category = detect_category(item)
    return f"- [{category}] {item.title} / {snippet}"


def make_briefing(items: list[NewsItem], now_jst: dt.datetime) -> str:
    date_str = now_jst.strftime("%Y-%m-%d (%a)")
    lines = [
        f"【朝5時 日本の主要ニュース要約】{date_str}",
        "",
        "■ 今日押さえるニュース",
    ]

    if not items:
        lines.append("- ニュースを取得できませんでした。ネットワークやRSS URLを確認してください。")
        return "\n".join(lines)

    for item in items:
        lines.append(one_line_summary(item))

    lines += [
        "",
        "■ 参照リンク",
    ]
    for item in items:
        lines.append(f"- {item.link}")

    return "\n".join(lines)


def save_briefing(text: str, now_jst: dt.datetime) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"briefing_{now_jst.strftime('%Y%m%d')}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def run_once() -> None:
    now_jst = dt.datetime.now(JST)
    items = collect_news()
    briefing = make_briefing(items, now_jst)
    path = save_briefing(briefing, now_jst)
    print(briefing)
    print(f"\nSaved: {path}")


def seconds_until_next_5am(now_jst: dt.datetime) -> int:
    target = now_jst.replace(hour=5, minute=0, second=0, microsecond=0)
    if now_jst >= target:
        target = target + dt.timedelta(days=1)
    return int((target - now_jst).total_seconds())


def run_daemon() -> None:
    while True:
        now_jst = dt.datetime.now(JST)
        wait_sec = seconds_until_next_5am(now_jst)
        print(f"[{now_jst.isoformat()}] Next run in {wait_sec} sec")
        time.sleep(max(wait_sec, 1))
        run_once()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Morning Japan major news agent")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run now once and exit",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.once:
        run_once()
    else:
        run_daemon()
