#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manga_notify.py
เช็กว่ามังงะที่ตามอยู่มีตอนใหม่ไหม แล้วแจ้งเตือนเข้า Telegram

หลักการ:
- เข้าหน้าเรื่องแต่ละเรื่อง อ่านค่า <meta property="article:modified_time">
  ซึ่งจะเปลี่ยนทุกครั้งที่เว็บอัปเดตตอนใหม่
- เทียบกับค่าที่เก็บไว้ในไฟล์ manga_state.json
- ถ้าเปลี่ยน = มีตอนใหม่ -> ส่งข้อความเข้า Telegram

วิธีใช้:
  python manga_notify.py          # เช็กตามปกติ
  python manga_notify.py --test   # ส่งข้อความทดสอบเข้า Telegram
  python manga_notify.py --reset  # ล้างสถานะ (ครั้งถัดไปจะถือว่าเป็นรอบแรก ไม่แจ้ง)
"""

import json
import os
import re
import sys
import time
import random
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ========================= ตั้งค่าตรงนี้ =========================

# ใส่ลิงก์ "หน้าเรื่อง" ที่อยากตาม (ไม่ใช่หน้าตอน) เริ่ม 2-3 เรื่องก่อน
SERIES_URLS = [
    "https://www.go-manga.com/revenge-iron-blooded/",
    "https://www.go-manga.com/max-level-player-100th/",
    "https://www.go-manga.com/god-level-assassin/",
    "https://www.go-manga.com/dragonslayers-regression/",
]

# --- Telegram ---
# แนะนำตั้งเป็น environment variable (ดูวิธีในไฟล์ README) แต่จะแก้ตรงนี้ก็ได้
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8840975941:AAHkuy55NMpI_ix5LGi25y_0bf29N0BQxvM")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6714721651")

# ===============================================================

STATE_FILE = Path(__file__).resolve().parent / "manga_state.json"

# ทำตัวให้เหมือนเบราว์เซอร์จริง ลดโอกาสโดนระบบกัน bot ปฏิเสธ
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "th,en-US;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

REQUEST_TIMEOUT = 20
DELAY_RANGE = (5, 15)  # หน่วงเวลาสุ่มระหว่างเรื่อง (วินาที) เพื่อความสุภาพกับเว็บ


# ----------------------- จัดการสถานะ -----------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print("[warn] อ่านไฟล์สถานะไม่ได้ เริ่มใหม่")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ----------------------- ดึงและแกะหน้าเว็บ -----------------------

def fetch_page(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.RequestException as e:
        print(f"[error] ดึง {url} ไม่ได้: {e}")
        return None


def parse_series(html: str, url: str) -> dict:
    """แกะข้อมูลที่ต้องใช้จากหน้าเรื่อง"""
    soup = BeautifulSoup(html, "html.parser")

    def meta(prop: str) -> str | None:
        tag = soup.find("meta", attrs={"property": prop})
        return tag["content"].strip() if tag and tag.get("content") else None

    title = meta("og:title") or (soup.title.string.strip() if soup.title else url)
    modified_time = meta("article:modified_time")

    # หาตอนล่าสุด (best-effort) — โครงสร้างธีม Madara
    latest_title = None
    latest_url = None
    chap = soup.select_one("li.wp-manga-chapter a, .version-chap li a, .listing-chapters_wrap a")
    if chap:
        latest_title = chap.get_text(strip=True)
        latest_url = chap.get("href")

    return {
        "url": url,
        "title": title,
        "modified_time": modified_time,
        "latest_chapter_title": latest_title,
        "latest_chapter_url": latest_url,
    }


def build_signal(info: dict) -> str | None:
    """สัญญาณที่ใช้บอกว่ามีของใหม่: ใช้ modified_time เป็นหลัก ถ้าไม่มีก็ใช้ลิงก์ตอนล่าสุด"""
    return info.get("modified_time") or info.get("latest_chapter_url")


# ----------------------- Telegram -----------------------

def send_telegram(text: str) -> bool:
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            api,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "false",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[error] ส่ง Telegram ไม่ได้: {e}")
        return False


def format_message(info: dict) -> str:
    title = info["title"]
    chap = info.get("latest_chapter_title")
    link = info.get("latest_chapter_url") or info["url"]
    head = f"📢 <b>{title}</b> มีอัปเดตใหม่!"
    if chap:
        head += f"\nตอนล่าสุด: {chap}"
    head += f'\n<a href="{link}">เปิดอ่าน</a>'
    return head


# ----------------------- ตัวหลัก -----------------------

def run_check() -> None:
    state = load_state()
    for i, url in enumerate(SERIES_URLS):
        html = fetch_page(url)
        if not html:
            continue

        info = parse_series(html, url)
        signal = build_signal(info)
        if signal is None:
            print(f"[skip] แกะข้อมูลไม่ได้: {url}")
            continue

        prev = state.get(url, {})
        prev_signal = prev.get("signal")

        if prev_signal is None:
            # รอบแรกของเรื่องนี้: บันทึกไว้เฉยๆ ไม่แจ้ง (กันสแปมตอนเริ่มใช้)
            print(f"[init] {info['title']}")
        elif signal != prev_signal:
            if send_telegram(format_message(info)):
                print(f"[NEW]  {info['title']} -> แจ้งแล้ว")
        else:
            print(f"[same] {info['title']}")

        state[url] = {
            "signal": signal,
            "title": info["title"],
            "latest_chapter_title": info.get("latest_chapter_title"),
        }

        # หน่วงก่อนไปเรื่องถัดไป (ยกเว้นเรื่องสุดท้าย)
        if i < len(SERIES_URLS) - 1:
            time.sleep(random.uniform(*DELAY_RANGE))

    save_state(state)
    print("เช็กครบแล้ว")


def main() -> None:
    if "--test" in sys.argv:
        ok = send_telegram("✅ ทดสอบ: บอทแจ้งมังงะพร้อมทำงานแล้ว")
        print("ส่งทดสอบสำเร็จ" if ok else "ส่งทดสอบไม่สำเร็จ ตรวจ TOKEN / CHAT_ID")
        return
    if "--reset" in sys.argv:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        print("ล้างสถานะแล้ว")
        return
    run_check()


if __name__ == "__main__":
    main()
