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


def extract_rows(body):
    """หา list ของ dict (รายการสถานี) ในผลลัพธ์ ไม่ว่าจะซ้อนกี่ชั้น เลือก list ที่ยาวที่สุด"""
    best = []

    def walk(node):
        nonlocal best
        if isinstance(node, list):
            if node and all(isinstance(x, dict) for x in node) and len(node) > len(best):
                best = node
            for x in node[:3]:
                walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(body)
    return best


def http_get_json(params=None):
    for attempt in range(3):
        r = requests.get(API_URL, headers=HEADERS, params=params, timeout=30)
        if r.status_code == 429:  # ต้นทางจำกัดอัตราเรียก ให้รอแล้วลองใหม่
            wait = int(r.headers.get("Retry-After", 30 * (attempt + 1)))
            print(f"429 Too Many Requests, รอ {wait}s", file=sys.stderr)
            time.sleep(min(wait, 120))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("ThaiWater ตอบ 429 ต่อเนื่อง ลองใหม่รอบหน้า")


def province_values(node, out=None):
    """รวบรวมค่าทุกอย่างที่อยู่ใต้ key ที่มีคำว่า province (รหัสหรือชื่อ ไม่ว่าจะซ้อนกี่ชั้น)"""
    out = [] if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            if "province" in str(k).lower():
                if isinstance(v, dict):
                    out.extend(str(x) for x in v.values())
                else:
                    out.append(str(v))
            else:
                province_values(v, out)
    return out


def geocode_values(node, out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            if "geocode" in str(k).lower() and isinstance(v, (str, int)):
                out.append(str(v))
            else:
                geocode_values(v, out)
    return out


def is_saraburi(rec):
    if not isinstance(rec, dict):
        return False
    vals = province_values(rec)
    if PROVINCE_CODE in vals or any(PROVINCE_NAME in v for v in vals):
        return True
    # geocode ขึ้นต้นด้วยรหัสจังหวัด 2 หลัก (เช่น 190101)
    return any(g.startswith(PROVINCE_CODE) and len(g) >= 2 for g in geocode_values(rec))


STATION_ID_KEYS = ("station_id", "tele_station_id", "stationid", "station")


def index_by_id(section):
    rows = extract_rows(section)
    return {str(r["id"]): r for r in rows if isinstance(r, dict) and r.get("id") is not None}


def join_rows(body):
    """ข้อมูลจริงเป็นแบบ relational: waterlevel_data อ้างอิง station ด้วย id จึงต้อง join ก่อน"""
    if not (isinstance(body, dict) and "waterlevel_data" in body):
        return extract_rows(body)
    stations = index_by_id(body.get("station"))
    out = []
    for row in extract_rows(body.get("waterlevel_data")):
        sid = next((str(row[k]) for k in STATION_ID_KEYS
                    if row.get(k) is not None and not isinstance(row[k], dict)), None)
        st = stations.get(sid, {}) if sid else {}
        merged = {**st, **{k: v for k, v in row.items() if k != "id"}}
        if sid:
            merged["id"] = sid
        if st:
            merged["station"] = st
        out.append(merged)
    return out


def fetch_raw():
    """ลองกรองที่ต้นทางด้วย province_code ก่อน ถ้าไม่ได้ผลค่อยดึงทั้งประเทศแล้วกรองเอง"""
    rows, body = [], None
    for params in ({"province_code": PROVINCE_CODE}, None):
        body = http_get_json(params)
        rows = join_rows(body)
        if any(is_saraburi(r) for r in rows):
            return rows, body
    return rows, body


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
    rows, body = fetch_raw()
    return [normalize(r) for r in rows if is_saraburi(r)], rows, body


def main():
    stations, raw, body = get_stations()

    if "--debug" in sys.argv:
        print("โครงสร้างชั้นนอก:")
        for k, v in (body.items() if isinstance(body, dict) else []):
            size = len(v) if hasattr(v, "__len__") else "-"
            print(f"  - {k}: {type(v).__name__} ({size})")
        for name in ("waterlevel_data", "station"):
            rows_ = extract_rows(body.get(name)) if isinstance(body, dict) else []
            print(f"\n[{name}] จำนวนแถว {len(rows_)}")
            if rows_:
                print("keys:", list(rows_[0].keys()))
                print(json.dumps(rows_[0], ensure_ascii=False)[:1500])
        print(f"\nหลัง join: {len(raw)} แถว | ของสระบุรี: {len(stations)}")
        sar = next((r for r in raw if is_saraburi(r)), None)
        if sar:
            print(json.dumps(sar, ensure_ascii=False, indent=1)[:2500])
        print("\nหลัง normalize (10 สถานีแรก):")
        for s_ in stations[:10]:
            print(s_)
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
