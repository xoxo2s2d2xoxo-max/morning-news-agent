#!/usr/bin/env python3
"""
Japan major news briefing agent.

- Collects major Japanese headlines from RSS feeds
- Creates a concise Japanese morning briefing with Claude AI summary
- Sends the briefing via Gmail
- Runs once or every day at 05:00 (Asia/Tokyo)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import smtplib
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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


# ──────────────────────────────────────────────
# RSS 収集
# ──────────────────────────────────────────────

def fetch_url(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MorningBusinessNewsAgent/1.0"},
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
        items.append(NewsItem(title=title, link=link, summary=desc, published=pub, source=source_url))
    return items


def score_item(item: NewsItem) -> int:
    haystack = f"{item.title} {item.summary}".lower()
    score = 0
    for words in CATEGORY_HINTS.values():
        for kw in words:
            if kw.lower() in haystack:
                score += 1
    if len(item.title) <= 60:
        score += 1
    return score


def similar(a: str, b: str) -> bool:
    a = re.sub(r"\W+", "", a.lower())
    b = re.sub(r"\W+", "", b.lower())
    if a == b:
        return True
    if len(a) >= 20 and len(b) >= 20 and a[:20] == b[:20]:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 15 and shorter in longer:
        return True
    return False


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    result: list[NewsItem] = []
    for item in items:
        if any(similar(item.title, seen.title) for seen in result):
            continue
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


# ──────────────────────────────────────────────
# カテゴリ判定
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# Claude API で要約生成
# ──────────────────────────────────────────────

def generate_ai_summary(items: list[NewsItem], now_jst: dt.datetime) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "（ANTHROPIC_API_KEY が未設定のため要約をスキップしました）"

    date_str = now_jst.strftime("%Y年%m月%d日（%a）")
    news_text = "\n\n".join(
        f"{i+1}. [{detect_category(item)}] {item.title}\n{item.summary or '（詳細なし）'}"
        for i, item in enumerate(items)
    )
    prompt = (
        f"あなたはビジネスパーソン向けのニュース編集者です。"
        f"以下の日本語ニュース一覧を読み、{date_str}の朝刊として押さえておくべきポイントを"
        f"200〜300字で簡潔にまとめてください。箇条書きや改行を使い読みやすくしてください。\n\n"
        f"---\n{news_text}\n---\n\nまとめ："
    )

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return "".join(block.get("text", "") for block in data.get("content", []))
    except Exception as e:
        return f"（AI要約の生成に失敗しました: {e}）"


# ──────────────────────────────────────────────
# ブリーフィング作成
# ──────────────────────────────────────────────

CATEGORY_BADGE = {
    "政治": "🏛",
    "経済": "📈",
    "社会": "🏙",
    "国際": "🌏",
    "テクノロジー": "💻",
    "その他": "📰",
}


def make_briefing_text(items: list[NewsItem], ai_summary: str, now_jst: dt.datetime) -> str:
    date_str = now_jst.strftime("%Y-%m-%d (%a)")
    lines = [
        f"【朝5時 日本の主要ニュース要約】{date_str}",
        "",
        "■ AI要約",
        ai_summary,
        "",
        "■ 今日押さえるニュース",
    ]
    if not items:
        lines.append("- ニュースを取得できませんでした。")
    else:
        for item in items:
            cat = detect_category(item)
            badge = CATEGORY_BADGE.get(cat, "📰")
            snippet = item.summary or "詳細はリンク先を確認してください。"
            if len(snippet) > 90:
                snippet = snippet[:87] + "..."
            lines.append(f"- {badge}[{cat}] {item.title} / {snippet}")

    lines += ["", "■ 参照リンク"]
    for item in items:
        lines.append(f"- {item.link}")
    return "\n".join(lines)


def make_briefing_html(items: list[NewsItem], ai_summary: str, now_jst: dt.datetime) -> str:
    date_str = now_jst.strftime("%Y年%m月%d日（%A）")
    rows = ""
    for item in items:
        cat = detect_category(item)
        badge = CATEGORY_BADGE.get(cat, "📰")
        snippet = item.summary or ""
        if len(snippet) > 100:
            snippet = snippet[:97] + "..."
        rows += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #f0f0f0;vertical-align:top;">
            <span style="display:inline-block;background:#f0f4ff;color:#3b5bdb;font-size:11px;padding:2px 8px;border-radius:12px;margin-bottom:4px;">{badge} {cat}</span><br>
            <a href="{item.link}" style="color:#1a1a2e;font-size:14px;font-weight:600;text-decoration:none;">{item.title}</a><br>
            <span style="color:#666;font-size:12px;">{snippet}</span>
          </td>
        </tr>"""

    summary_html = ai_summary.replace("\n", "<br>")

    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;">
  <div style="max-width:600px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
    <div style="background:#1a1a2e;padding:24px 28px;">
      <p style="color:#8b9cf4;font-size:12px;margin:0 0 4px;">朝刊ニュースブリーフィング</p>
      <h1 style="color:#fff;font-size:20px;margin:0;">{date_str}</h1>
    </div>
    <div style="padding:20px 28px;background:#f8f9ff;border-bottom:1px solid #eee;">
      <p style="font-size:12px;color:#666;margin:0 0 8px;font-weight:600;">AI 要約</p>
      <p style="font-size:14px;color:#333;line-height:1.7;margin:0;">{summary_html}</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">
      {rows}
    </table>
    <div style="padding:16px 28px;background:#f5f5f7;text-align:center;">
      <p style="color:#999;font-size:11px;margin:0;">このメールは自動送信されています</p>
    </div>
  </div>
</body>
</html>"""


# ──────────────────────────────────────────────
# Gmail 送信
# ──────────────────────────────────────────────

def send_gmail(subject: str, text_body: str, html_body: str) -> None:
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to_address = os.environ.get("NOTIFY_EMAIL", gmail_user)

    print(f"[DEBUG] GMAIL_USER: {'設定あり' if gmail_user else '未設定'}")
    print(f"[DEBUG] GMAIL_APP_PASSWORD: {'設定あり' if gmail_password else '未設定'}")
    print(f"[DEBUG] NOTIFY_EMAIL: {'設定あり' if to_address else '未設定'}")

    if not gmail_user or not gmail_password:
        print("エラー: GMAIL_USER / GMAIL_APP_PASSWORD が未設定のためメール送信をスキップします")
        return

    print(f"メール送信開始 → {to_address}")
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = gmail_user
        msg["To"] = to_address
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_address, msg.as_string())
        print(f"メール送信完了 → {to_address}")
    except Exception as e:
        print(f"メール送信エラー: {e}")


# ──────────────────────────────────────────────
# 保存
# ──────────────────────────────────────────────

def save_briefing(text: str, now_jst: dt.datetime) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"briefing_{now_jst.strftime('%Y%m%d')}.txt"
    path.write_text(text, encoding="utf-8")
    return path


# ──────────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────────

def run_once() -> None:
    now_jst = dt.datetime.now(JST)
    print(f"[{now_jst.isoformat()}] ニュース収集を開始します...")

    items = collect_news()
    print(f"  {len(items)} 件のニュースを取得しました")

    ai_summary = generate_ai_summary(items, now_jst)

    text_body = make_briefing_text(items, ai_summary, now_jst)
    html_body = make_briefing_html(items, ai_summary, now_jst)

    path = save_briefing(text_body, now_jst)
    print(text_body)
    print(f"\nSaved: {path}")

    date_str = now_jst.strftime("%Y/%m/%d")
    send_gmail(
        subject=f"【朝刊ニュース】{date_str} の主要ニュース",
        text_body=text_body,
        html_body=html_body,
    )


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
    parser.add_argument("--once", action="store_true", help="Run now once and exit")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.once:
        run_once()
    else:
        run_daemon()
