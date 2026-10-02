#!/usr/bin/env python3
"""
บอทแจ้งเตือนระดับน้ำจังหวัดสระบุรี (รวมชลประทาน/สสน. + เขื่อนป่าสักฯ + เตือนน้ำขึ้นเร็ว + แดชบอร์ด)
"""
import html
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

API_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
PROVINCE_CODE = "19"
PROVINCE_NAME = "สระบุรี"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
ALERT_FROM_LEVEL = 4

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://www.thaiwater.net/",
    "Accept": "application/json",
}

LEVELS = {
    1: ("🟤", "น้ำน้อยวิกฤติ"),
    2: ("🟡", "น้ำน้อย"),
    3: ("🟢", "ปกติ"),
    4: ("🔵", "น้ำมาก (เฝ้าระวัง)"),
    5: ("🔴", "ล้นตลิ่ง (อันตราย)"),
}

TZ = timezone(timedelta(hours=7))
RISE_M, RISE_H = 0.30, 3      # เตือนถ้าน้ำเพิ่ม >= 0.30 ม. ภายใน 3 ชม.
STALE_H = 3                   # เตือนถ้าข้อมูลสถานีเก่ากว่า 3 ชม.
KEEP_H = 48                   # เก็บประวัติระดับน้ำย้อนหลัง 48 ชม.
DAM_PCT = 90                  # เตือนเมื่อเขื่อนป่าสักฯ >= 90%
DAM_URL = "https://app.rid.go.th/reservoir/api/dam/public"


def parse_ts(v):
    try:
        return datetime.strptime(str(v)[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()
    except ValueError:
        return None


def fetch_dam():
    def walk(n):
        if isinstance(n, dict):
            if "ป่าสัก" in str(n.get("name", "")):
                return n
            n = list(n.values())
        if isinstance(n, list):
            for v in n:
                f = walk(v)
                if f:
                    return f
    try:
        r = requests.get(DAM_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        d = walk(r.json())
        if not d:
            return None
        return {
            "name": str(d.get("name")), 
            "pct": to_float(d.get("percent_storage")),
            "volume": to_float(d.get("volume")), 
            "capacity": to_float(d.get("capacity")),
            "inflow": to_float(d.get("inflow")), 
            "outflow": to_float(d.get("outflow")),
            "date": str(d.get("date", ""))
        }
    except Exception as e:
        print("ดึงข้อมูลเขื่อนไม่สำเร็จ:", e, file=sys.stderr)
        return None


def fmt_dam(d):
    pct = f" ({d['pct']:.0f}% ของความจุ)" if d["pct"] is not None else ""
    return (f"🏞 <b>{html.escape(d['name'])}</b>\nน้ำในเขื่อน {d['volume']} ล้าน ลบ.ม.{pct}\n"
            f"ไหลเข้า {d['inflow']} | ระบาย {d['outflow']} (ล้าน ลบ.ม./วัน)")


def write_site(stations, dam, now):
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now, "province": PROVINCE_NAME, "dam": dam, "stations": stations},
                  f, ensure_ascii=False)


def dig(d, *paths, default=None):
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
    if isinstance(v, dict):
        return v.get("th") or v.get("en") or ""
    return v or ""


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_rows(body):
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
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 30 * (attempt + 1)))
            time.sleep(min(wait, 120))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("ThaiWater ตอบ 429 ต่อเนื่อง")


def province_values(node, out=None):
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
    geos = geocode_values(rec)
    if PROVINCE_CODE in vals or any(PROVINCE_NAME in v for v in vals):
        return True
    return any(g.startswith(PROVINCE_CODE) and len(g) >= 2 for g in geos)


STATION_ID_KEYS = ("station_id", "tele_station_id", "stationid", "station")


def index_by_id(section):
    rows = extract_rows(section)
    return {str(r["id"]): r for r in rows if isinstance(r, dict) and r.get("id") is not None}


def join_rows(body):
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
    body = http_get_json(None)
    rows = join_rows(body)
    return rows, body


def normalize(rec):
    sid = str(dig(rec, "station.id", "station_id", "id", default=""))
    name = th(dig(rec, "station.tele_station_name", "tele_station_name", "station_name", default=sid))
    river = th(dig(rec, "river_name", "station.river_name", default=""))
    amphoe = th(dig(rec, "geocode.amphoe_name", "amphoe_name", default=""))
    agency = th(dig(rec, "agency_name", "station.agency_name", default=""))
    wl = to_float(dig(rec, "waterlevel_msl", "waterlevel_value", "water_level"))
    bank = to_float(dig(rec, "station.min_bank", "min_bank", "bank_level", "bank"))
    level = dig(rec, "situation_level", "station.situation_level")
    level = int(level) if str(level).isdigit() else None
    if level is None and wl is not None and bank:
        level = 5 if wl >= bank else 4 if wl >= bank - 1.0 else 3
    return {
        "id": sid,
        "name": name or sid,
        "river": river,
        "amphoe": amphoe,
        "agency": agency,
        "wl": wl,
        "bank": bank,
        "level": level or 3,
        "pct": to_float(dig(rec, "storage_percent")),
        "prev": to_float(dig(rec, "waterlevel_msl_previous")),
        "time": dig(rec, "waterlevel_datetime", "datetime", "station.waterlevel_datetime", default=""),
    }


def fmt_station(s):
    emoji, label = LEVELS.get(s["level"], ("🟢", "ปกติ"))
    agency_tag = f"[{s['agency']}] " if s['agency'] else ""
    lines = [f"{emoji} <b>{html.escape(s['name'])}</b> {agency_tag}"]
    loc = " ".join(x for x in [s["river"], f"อ.{s['amphoe']}" if s["amphoe"] else ""] if x)
    if loc:
        lines.append(html.escape(loc))
    if s["wl"] is not None:
        lines.append(f"ระดับน้ำ {s['wl']:.2f} ม.รทก.")
        if s["bank"]:
            gap = s["bank"] - s["wl"]
            where = f"ต่ำกว่าตลิ่ง {gap:.2f} ม." if gap >= 0 else f"สูงกว่าตลิ่ง {-gap:.2f} ม."
            pct = f" ({s['pct']:.0f}% ของตลิ่ง)" if s["pct"] is not None else ""
            lines.append(f"ตลิ่ง {s['bank']:.2f} ม. → {where}{pct}")
    lines.append(f"สถานะ: {label}")
    if s["time"]:
        lines.append(f"ข้อมูลเมื่อ {html.escape(str(s['time']))}")
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > 3800:
            chunks.append(cur)
            cur = ""
        cur += block + "\n\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        requests.post(url, json={"chat_id": chat_id, "text": c.strip(), "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=30).raise_for_status()


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def main():
    rows, _ = fetch_raw()
    stations = [normalize(r) for r in rows if is_saraburi(r)]
    for x in stations:
        x["ts"] = parse_ts(x["time"])

    if "--debug" in sys.argv:
        print(f"พบสถานีในสระบุรีทั้งหมด: {len(stations)} สถานี")
        for s in stations:
            print(f"- {s['name']} ({s['agency']}) ระดับน้ำ: {s['wl']}")
        return

    if not stations:
        return

    dam, now = fetch_dam(), time.time()
    state = load_state()
    for k in ("levels", "hist", "flags", "dam"):
        state.setdefault(k, {})

    alerts, rises, stales, fresh, cleared = [], [], [], [], []

    for s in stations:
        sid = s["id"]
        h = state["hist"].get(sid, [])
        if s["ts"] and s["wl"] is not None and (not h or h[-1][0] != s["ts"]):
            h.append([s["ts"], s["wl"]])
        h = [p for p in h if p[0] >= now - KEEP_H * 3600]
        state["hist"][sid] = h

        # คำนวณน้ำขึ้นเร็ว (เทียบกับ 3 ชม. ที่แล้ว)
        win = [p for p in h if p[0] >= now - RISE_H * 3600]
        rise_val = round(s["wl"] - win[0][1], 2) if s["wl"] is not None and len(win) >= 2 else None
        
        # เช็คข้อมูลค้าง
        is_stale = bool(s["ts"]) and (now - s["ts"] > STALE_H * 3600)
        
        f = state["flags"].setdefault(sid, {})
        prev_lvl = state["levels"].get(sid)
        cur_lvl = s["level"]

        if cur_lvl >= ALERT_FROM_LEVEL and cur_lvl != prev_lvl:
            alerts.append(s)
        elif prev_lvl and prev_lvl >= ALERT_FROM_LEVEL > cur_lvl:
            cleared.append(s)
        state["levels"][sid] = cur_lvl

        # เตือนน้ำขึ้นเร็ว (แม้ยังไม่ถึงเกณฑ์น้ำมาก)
        if rise_val is not None and rise_val >= RISE_M:
            if not f.get("rise_notified"):
                s["rise_val"] = rise_val
                rises.append(s)
                f["rise_notified"] = True
        else:
            if rise_val is not None and rise_val < RISE_M / 2:
                f["rise_notified"] = False

        # เตือนข้อมูลค้าง / กลับมาปกติ
        if is_stale and not f.get("stale_notified"):
            stales.append(s)
            f["stale_notified"] = True
        elif not is_stale and f.get("stale_notified"):
            fresh.append(s)
            f["stale_notified"] = False

    write_site(stations, dam, now)

    if "--summary" in sys.argv:
        stations.sort(key=lambda x: -(x["level"] or 0))
        send_telegram(f"📊 <b>สรุประดับน้ำ จ.{PROVINCE_NAME}</b> ({len(stations)} สถานี)\n\n"
                      + "\n\n".join(fmt_station(x) for x in stations)
                      + ("\n\n" + fmt_dam(dam) if dam else ""))
        save_state(state)
        return

    foot = "\n\n⚠️ ข้อมูลอัตโนมัติจากระบบเฝ้าระวัง"
    if alerts:
        send_telegram("🚨 <b>แจ้งเตือนระดับน้ำสูง (เฝ้าระวัง/ล้นตลิ่ง)</b>\n\n" + "\n\n".join(fmt_station(x) for x in alerts) + foot)
    if rises:
        send_telegram("📈 <b>แจ้งเตือน: น้ำเพิ่มขึ้นเร็วผิดปกติ</b>\n\n" + "\n\n".join(f"{fmt_station(x)}\n⚠️ เพิ่มขึ้น <b>+{x['rise_val']} ม.</b> ในช่วง {RISE_H} ชม.ล่าสุด" for x in rises) + foot)
    if stales:
        send_telegram("⏱ <b>แจ้งเตือน: ข้อมูลสถานีขาดการอัปเดตนานเกิน 3 ชม.</b>\n\n" + "\n".join(f"• {html.escape(x['name'])} (ล่าสุด: {x['time']})" for x in stales))
    if fresh:
        send_telegram("✅ <b>สถานีกลับมาส่งข้อมูลตามปกติแล้ว</b>\n\n" + "\n".join(f"• {html.escape(x['name'])}" for x in fresh))
    if cleared:
        send_telegram("✅ <b>ระดับน้ำลดลงต่ำกว่าเกณฑ์เฝ้าระวังแล้ว</b>\n\n" + "\n\n".join(fmt_station(x) for x in cleared))

    if dam and dam["pct"] is not None:
        hi = dam["pct"] >= DAM_PCT
        if hi and not state["dam"].get("hi"):
            send_telegram(f"🌊 <b>เขื่อนป่าสักฯ น้ำสูงกว่า {DAM_PCT}%</b>\n\n" + fmt_dam(dam) + foot)
        state["dam"]["hi"] = hi

    save_state(state)
    print(f"รันสำเร็จ: ตรวจพบ {len(stations)} สถานี")


if __name__ == "__main__":
    main()
