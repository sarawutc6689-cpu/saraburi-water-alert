#!/usr/bin/env python3
"""
บอทแจ้งเตือนระดับน้ำจังหวัดสระบุรี ผ่าน Telegram
แหล่งข้อมูล: ThaiWater (สสน./HII) public endpoint waterlevel_load

วิธีใช้
  export TELEGRAM_BOT_TOKEN="123456:ABC..."
  export TELEGRAM_CHAT_ID="-100xxxxxxxxxx"   # ห้อง/กลุ่ม/แชตส่วนตัว
  python3 saraburi_water_bot.py --debug      # ดูโครงสร้างข้อมูลจริง 1 สถานี (ตรวจชื่อ field)
  python3 saraburi_water_bot.py --summary    # ส่งสรุปทุกสถานีเข้า Telegram
  python3 saraburi_water_bot.py              # ตรวจ + แจ้งเตือนเมื่อสถานะเปลี่ยน (ใช้กับ cron)

cron ตัวอย่าง (ทุก 15 นาที):
  */15 * * * * cd /path/to && /usr/bin/python3 saraburi_water_bot.py >> bot.log 2>&1
  0 7 * * *    cd /path/to && /usr/bin/python3 saraburi_water_bot.py --summary
"""
import html
import json
import os
import sys
import time

import requests

API_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
PROVINCE_CODE = "19"          # สระบุรี
PROVINCE_NAME = "สระบุรี"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
ALERT_FROM_LEVEL = 4          # แจ้งเตือนเมื่อ >= น้ำมาก

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://www.thaiwater.net/",
    "Accept": "application/json",
}

# ตรวจสอบกับ --debug ว่าตรงกับข้อมูลจริงหรือไม่
LEVELS = {
    1: ("🟤", "น้ำน้อยวิกฤติ"),
    2: ("🟡", "น้ำน้อย"),
    3: ("🟢", "ปกติ"),
    4: ("🔵", "น้ำมาก (เฝ้าระวัง)"),
    5: ("🔴", "ล้นตลิ่ง (อันตราย)"),
}


def dig(d, *paths, default=None):
    """ดึงค่าจาก dict ซ้อนหลายชั้น ลองหลาย path ตามลำดับ เช่น 'geocode.province_code'"""
    for path in paths:
        cur = d
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                cur = None
                break
        if cur not in (None, ""):
            return cur
    return default


def th(v):
    """field ชื่อมักเป็น {'th': ..., 'en': ...}"""
    if isinstance(v, dict):
        return v.get("th") or v.get("en") or ""
    return v or ""


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_raw():
    for attempt in range(3):
        r = requests.get(API_URL, headers=HEADERS, timeout=30)
        if r.status_code == 429:  # ต้นทางจำกัดอัตราเรียก ให้รอแล้วลองใหม่
            wait = int(r.headers.get("Retry-After", 30 * (attempt + 1)))
            print(f"429 Too Many Requests, รอ {wait}s", file=sys.stderr)
            time.sleep(min(wait, 120))
            continue
        r.raise_for_status()
        body = r.json()
        return body.get("data", body) if isinstance(body, dict) else body
    raise RuntimeError("ThaiWater ตอบ 429 ต่อเนื่อง ลองใหม่รอบหน้า")


def is_saraburi(rec):
    code = str(dig(rec, "geocode.province_code", "province_code", default=""))
    name = th(dig(rec, "geocode.province_name", "province_name", default=""))
    return code == PROVINCE_CODE or PROVINCE_NAME in str(name)


def normalize(rec):
    station = rec.get("station", {}) if isinstance(rec.get("station"), dict) else {}
    sid = str(dig(rec, "station.id", "station_id", "id", default=""))
    name = th(dig(rec, "station.tele_station_name", "tele_station_name", "station_name", default=sid))
    river = th(dig(rec, "river_name", "station.river_name", default=""))
    amphoe = th(dig(rec, "geocode.amphoe_name", "amphoe_name", default=""))
    wl = to_float(dig(rec, "waterlevel_msl", "waterlevel_value", "water_level"))
    bank = to_float(dig(rec, "station.min_bank", "min_bank", "bank_level", "bank"))
    level = dig(rec, "situation_level", "station.situation_level")
    level = int(level) if str(level).isdigit() else None
    # ถ้าไม่มี situation_level ให้ประเมินเองจากตลิ่ง
    if level is None and wl is not None and bank:
        level = 5 if wl >= bank else 4 if wl >= bank - 1.0 else 3
    return {
        "id": sid,
        "name": name or sid,
        "river": river,
        "amphoe": amphoe,
        "wl": wl,
        "bank": bank,
        "level": level,
        "time": dig(rec, "waterlevel_datetime", "datetime", "station.waterlevel_datetime", default=""),
    }


def fmt_station(s, with_time=True):
    emoji, label = LEVELS.get(s["level"], ("⚪", "ไม่ทราบสถานะ"))
    lines = [f"{emoji} <b>{html.escape(s['name'])}</b>"]
    loc = " ".join(x for x in [s["river"], f"อ.{s['amphoe']}" if s["amphoe"] else ""] if x)
    if loc:
        lines.append(html.escape(loc))
    if s["wl"] is not None:
        extra = ""
        if s["bank"]:
            extra = f" | ตลิ่ง {s['bank']:.2f} (ต่ำกว่าตลิ่ง {s['bank'] - s['wl']:+.2f} ม.)"
        lines.append(f"ระดับน้ำ {s['wl']:.2f} ม.รทก.{extra}")
    lines.append(f"สถานะ: {label}")
    if with_time and s["time"]:
        lines.append(f"ข้อมูลเมื่อ {html.escape(str(s['time']))}")
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram จำกัด 4096 ตัวอักษรต่อข้อความ
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > 3800:
            chunks.append(cur)
            cur = ""
        cur += block + "\n\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": c.strip(), "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=30,
        )
        r.raise_for_status()


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def get_stations():
    raw = fetch_raw()
    return [normalize(r) for r in raw if is_saraburi(r)], raw


def main():
    stations, raw = get_stations()

    if "--debug" in sys.argv:
        sample = next((r for r in raw if is_saraburi(r)), None)
        print(f"สถานีทั้งประเทศ: {len(raw)} | สระบุรี: {len(stations)}")
        print(json.dumps(sample, ensure_ascii=False, indent=2))
        print("\nหลัง normalize:")
        for s in stations:
            print(s)
        return

    if not stations:
        print("ไม่พบสถานีของสระบุรี ตรวจ field ด้วย --debug", file=sys.stderr)
        return

    if "--summary" in sys.argv:
        stations.sort(key=lambda s: -(s["level"] or 0))
        head = f"📊 <b>สรุประดับน้ำ จ.{PROVINCE_NAME}</b> ({len(stations)} สถานี)\n\n"
        send_telegram(head + "\n\n".join(fmt_station(s) for s in stations))
        return

    # โหมดแจ้งเตือน: ส่งเฉพาะเมื่อสถานะข้ามเกณฑ์ขึ้น/ลง
    state = load_state()
    alerts, cleared = [], []
    for s in stations:
        prev = state.get(s["id"])
        cur = s["level"]
        if cur is not None:
            if cur >= ALERT_FROM_LEVEL and (prev is None or cur != prev):
                alerts.append(s)
            elif prev is not None and prev >= ALERT_FROM_LEVEL and cur < ALERT_FROM_LEVEL:
                cleared.append(s)
            state[s["id"]] = cur

    if alerts:
        alerts.sort(key=lambda s: -s["level"])
        send_telegram(
            f"🚨 <b>แจ้งเตือนระดับน้ำ จ.{PROVINCE_NAME}</b>\n\n"
            + "\n\n".join(fmt_station(s) for s in alerts)
            + "\n\n⚠️ ข้อมูลอัตโนมัติ ไม่ใช่ประกาศทางการ โปรดติดตาม ปภ./กรมชลประทาน/อบจ.-ท้องถิ่น"
        )
    if cleared:
        send_telegram(
            f"✅ <b>ระดับน้ำลดลงต่ำกว่าเกณฑ์เฝ้าระวัง</b>\n\n"
            + "\n\n".join(fmt_station(s) for s in cleared)
        )
    save_state(state)
    print(f"ตรวจแล้ว {len(stations)} สถานี | แจ้งเตือน {len(alerts)} | คลี่คลาย {len(cleared)}")


if __name__ == "__main__":
    main()
