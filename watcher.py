#!/usr/bin/env python3
"""Kleinanzeigen araba takipçisi — yeni ilan ve fiyat düşüşlerini Telegram'a bildirir.

Çalışma mantığı:
  1. config.SEARCHES içindeki arama sayfalarını çeker (sadece 1. sayfa).
  2. Daha önce görülmemiş ilanların detay sayfasını açar.
  3. Metin filtreleri (kaza, defekt, kiralık batarya...) + mesafe kuralını uygular.
  4. Geçenleri Telegram'a yollar; fiyat düşüşlerini de bildirir.
  5. state.json'a yazar (GitHub Actions workflow'u bunu repoya commit eder).
"""

import html
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

import config

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
PRICE_DROP_THRESHOLD = 100  # € — bu kadar düşerse tekrar bildir

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
}

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


# --------------------------------------------------------------------------- util
def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"ads": {}, "blocked_runs": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID eksik — mesaj atlanıyor:")
        log(text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
        timeout=20,
    )
    if r.status_code != 200:
        log(f"Telegram hatası {r.status_code}: {r.text[:200]}")


class Blocked(Exception):
    pass


def fetch(session: requests.Session, url: str) -> str:
    r = session.get(url, headers=HEADERS, timeout=30)
    if r.status_code in (403, 429) or "captcha" in r.text[:3000].lower():
        raise Blocked(f"{r.status_code} @ {url}")
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------------- geocoding
_geo = None


def dist_from_home(plz: str) -> float | None:
    """PLZ -> Monheim'dan kuş uçuşu km. Bilinmiyorsa None."""
    global _geo
    if _geo is None:
        import pgeocode

        _geo = pgeocode.Nominatim("de")
    try:
        home = _geo.query_postal_code(config.HOME_PLZ)
        there = _geo.query_postal_code(plz)
        if math.isnan(there.latitude) or math.isnan(home.latitude):
            return None
        lat1, lon1 = math.radians(home.latitude), math.radians(home.longitude)
        lat2, lon2 = math.radians(there.latitude), math.radians(there.longitude)
        a = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
        return 6371 * 2 * math.asin(math.sqrt(a))
    except Exception:
        return None


# ------------------------------------------------------------------------ parsing
def parse_search_page(html_text: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    ads = []
    for art in soup.select("article.aditem[data-adid]"):
        ad_id = art.get("data-adid", "").strip()
        href = art.get("data-href") or ""
        link_el = art.select_one("h2 a") or art.select_one("a")
        if not href and link_el:
            href = link_el.get("href", "")
        title = link_el.get_text(strip=True) if link_el else "?"
        price_el = art.select_one("[class*='price']")
        price = parse_price(price_el.get_text() if price_el else "")
        loc_el = art.select_one(".aditem-main--top--left")
        loc = loc_el.get_text(" ", strip=True) if loc_el else ""
        tags = [t.get_text(strip=True) for t in art.select(".simpletag")]
        if ad_id and href:
            ads.append(
                {
                    "id": ad_id,
                    "url": "https://www.kleinanzeigen.de" + href,
                    "title": title,
                    "price": price,
                    "location": loc,
                    "tags": tags,
                }
            )
    return ads


def parse_price(text: str) -> int | None:
    m = re.search(r"([\d.]+)\s*€", text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", ""))
    except ValueError:
        return None


def parse_detail(html_text: str) -> dict:
    soup = BeautifulSoup(html_text, "html.parser")
    desc_el = soup.select_one("#viewad-description-text")
    desc = desc_el.get_text(" ", strip=True) if desc_el else ""
    # Donanım/detay listeleri de metne dahil (örn. "Fahrzeug mit HV-Leasing-Batterie")
    details = " ".join(
        li.get_text(" ", strip=True) for li in soup.select("#viewad-details li, .addetailslist--detail")
    )
    checktags = " ".join(t.get_text(" ", strip=True) for t in soup.select(".checktag"))
    page_text = soup.get_text(" ", strip=True)

    if "Gewerblicher Nutzer" in page_text:
        seller = "Händler"
    elif "Privater Nutzer" in page_text:
        seller = "Privat"
    else:
        seller = "?"

    plz = None
    loc_el = soup.select_one("#viewad-locality")
    loc_text = loc_el.get_text(" ", strip=True) if loc_el else ""
    m = re.search(r"\b(\d{5})\b", loc_text) or re.search(r"\b(\d{5})\b\s+[A-ZÄÖÜ]", page_text)
    if m:
        plz = m.group(1)

    return {
        "text": f"{desc} {details} {checktags}".lower(),
        "seller": seller,
        "plz": plz,
        "locality": loc_text,
    }


# ----------------------------------------------------------------------- analysis
def evaluate(search: dict, ad: dict, detail: dict) -> tuple[str, list[str]]:
    """returns (verdict, notes); verdict: 'notify' | 'reject'."""
    text = f"{ad['title'].lower()} {detail['text']}"
    for neg in config.REJECT_NEGATIONS:
        text = text.replace(neg, " ")

    notes: list[str] = []

    for term in config.REJECT_TERMS:
        if term in text:
            return "reject", [f"kara liste: '{term}'"]

    rent_hit = [t for t in config.RENT_TERMS if t in text]
    if rent_hit:
        if any(t in text for t in config.RENT_RESOLVED_TERMS):
            notes.append("⚠️ Batarya kirası geçiyor ama 'abgelöst/gekauft' da var — belgeyi iste!")
        else:
            return "reject", [f"kiralık batarya: '{rent_hit[0]}'"]

    req = search.get("require_any")
    if req and not any(t in text for t in req):
        notes.append("⚠️ Satın alınmış batarya ibaresi YOK — Batteriemiete olabilir, mutlaka sor.")

    # Mesafe kuralı
    dist = dist_from_home(detail["plz"]) if detail["plz"] else None
    ad["dist_km"] = round(dist) if dist is not None else None
    if dist is None:
        notes.append("⚠️ Mesafe hesaplanamadı (PLZ bulunamadı).")
    elif detail["seller"] != "Händler":
        if dist > config.MAX_DIST_PRIVATE_KM:
            return "reject", [f"privat + {round(dist)} km (> {config.MAX_DIST_PRIVATE_KM} km)"]
    elif config.MAX_DIST_DEALER_KM and dist > config.MAX_DIST_DEALER_KM:
        return "reject", [f"händler + {round(dist)} km (> {config.MAX_DIST_DEALER_KM} km)"]

    if not detail["text"]:
        notes.append("⚠️ İlan metni okunamadı — filtreler uygulanamadı.")
    return "notify", notes


def format_message(search: dict, ad: dict, detail: dict, notes: list[str]) -> str:
    title = html.escape(ad["title"])
    price = f"{ad['price']:,} €".replace(",", ".") if ad["price"] else "? €"
    tags = " | ".join(ad.get("tags", [])[:3])
    dist = f"~{ad['dist_km']} km" if ad.get("dist_km") is not None else "mesafe ?"
    loc = html.escape(detail.get("locality") or ad.get("location", ""))
    lines = [
        f"🚗 <b>{html.escape(search['name'])}</b> — {title}",
        f"💶 <b>{price}</b>" + (f"  |  {tags}" if tags else ""),
        f"📍 {loc} — {dist}  ({detail['seller']})",
    ]
    lines += notes if notes else ["✅ Metinde kırmızı bayrak yok."]
    lines.append(ad["url"])
    return "\n".join(lines)


# --------------------------------------------------------------------------- main
def main() -> int:
    state = load_state()
    seen = state["ads"]
    session = requests.Session()
    detail_budget = config.MAX_DETAIL_FETCH_PER_RUN
    new_count = 0

    try:
        for search in config.SEARCHES:
            if not search.get("enabled", True):
                continue
            log(f"Arama: {search['name']}")
            ads = parse_search_page(fetch(session, search["url"]))
            log(f"  {len(ads)} ilan bulundu")

            for ad in ads:
                if ad["price"] is not None and ad["price"] < config.MIN_PRICE:
                    continue
                prev = seen.get(ad["id"])

                # Fiyat düşüşü bildirimi (daha önce bildirilenler için)
                if prev:
                    if (
                        prev.get("notified")
                        and ad["price"]
                        and prev.get("price")
                        and prev["price"] - ad["price"] >= PRICE_DROP_THRESHOLD
                    ):
                        tg_send(
                            f"📉 <b>Fiyat düştü:</b> {html.escape(ad['title'])}\n"
                            f"{prev['price']:,} € → <b>{ad['price']:,} €</b>\n{ad['url']}".replace(",", ".")
                        )
                        prev["price"] = ad["price"]
                    continue

                if detail_budget <= 0:
                    continue
                detail_budget -= 1
                time.sleep(random.uniform(3, 6))
                try:
                    detail = parse_detail(fetch(session, ad["url"]))
                except Blocked:
                    raise
                except Exception as e:
                    detail = {"text": "", "seller": "?", "plz": None, "locality": ""}
                    log(f"  detay okunamadı ({ad['id']}): {e}")

                verdict, notes = evaluate(search, ad, detail)
                seen[ad["id"]] = {
                    "price": ad["price"],
                    "title": ad["title"],
                    "notified": verdict == "notify",
                    "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "reason": "; ".join(notes) if verdict == "reject" else "",
                }
                if verdict == "notify":
                    tg_send(format_message(search, ad, detail, notes))
                    new_count += 1
                    log(f"  ✓ bildirildi: {ad['title'][:60]}")
                else:
                    log(f"  ✗ elendi ({notes[0]}): {ad['title'][:60]}")

            time.sleep(random.uniform(3, 6))

        state["blocked_runs"] = 0
    except Blocked as e:
        state["blocked_runs"] = state.get("blocked_runs", 0) + 1
        log(f"ENGELLENDİ: {e} (üst üste {state['blocked_runs']}. koşu)")
        if state["blocked_runs"] == 3:
            tg_send(
                "🛑 Kleinanzeigen GitHub Actions IP'sini üst üste 3 koşudur engelliyor. "
                "Scripti ev/sunucu IP'sinden (cron) çalıştırmayı düşün."
            )

    save_state(state)
    log(f"Bitti — {new_count} yeni bildirim.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
