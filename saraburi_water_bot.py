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


from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=7))
RISE_M, RISE_H = 0.30, 3      # เตือนถ้าน้ำเพิ่ม >= 0.30 ม. ภายใน 3 ชม.
STALE_H = 3                   # เตือนถ้าข้อมูลสถานีเก่ากว่า 3 ชม.
KEEP_H = 48                   # เก็บประวัติระดับน้ำย้อนหลัง 48 ชม. (ใช้คำนวณ + วาดกราฟ)
DAM_PCT = 90                  # เตือนเมื่อเขื่อนป่าสักฯ >= 90% ของความจุ
DAM_URL = "https://app.rid.go.th/reservoir/api/dam/public"
SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")


def parse_ts(v):
    try:
        return datetime.strptime(str(v)[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()
    except ValueError:
        return None


DAM_HEADERS = {"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"}


def fetch_dam():
    """เขื่อนป่าสักชลสิทธิ์ จาก API กรมชลประทาน คืนค่า (ข้อมูล, ข้อความ error)
    โครงสร้างจริง: {date, data:[{region, dam:[{name, volume, percent_storage, ...}]}]}"""
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

    err = "ไม่ทราบสาเหตุ"
    for attempt in range(3):
        try:
            r = requests.get(DAM_URL, headers=DAM_HEADERS, timeout=30)
            r.raise_for_status()
            body = r.json()
            d = walk(body)
            if not d:
                return None, "ไม่พบเขื่อนป่าสักชลสิทธิ์ในข้อมูลของกรมชลประทาน"
            return {"name": str(d.get("name")), "pct": to_float(d.get("percent_storage")),
                    "volume": to_float(d.get("volume")), "capacity": to_float(d.get("storage") or d.get("capacity")),
                    "inflow": to_float(d.get("inflow")), "outflow": to_float(d.get("outflow")),
                    "date": str(body.get("date", "") if isinstance(body, dict) else "")}, None
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:150]}"
            print(f"ดึงข้อมูลเขื่อนไม่สำเร็จ (ครั้งที่ {attempt + 1}): {err}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return None, err


def _num(v, nd=2):
    return "ไม่มีข้อมูล" if v is None else f"{v:.{nd}f}"


def fmt_dam(d, err=None):
    if not d:
        return f"🏞 <b>เขื่อนป่าสักชลสิทธิ์</b>\n⚠️ ดึงข้อมูลไม่สำเร็จ: {html.escape(str(err or ''))}"
    pct = f" ({d['pct']:.0f}% ของความจุเก็บกัก)" if d["pct"] is not None else ""
    cap = f" / {d['capacity']:.0f}" if d["capacity"] is not None else ""
    return (f"🏞 <b>{html.escape(d['name'])}</b>\nน้ำในเขื่อน {_num(d['volume'])}{cap} ล้าน ลบ.ม.{pct}\n"
            f"ไหลเข้า {_num(d['inflow'])} | ระบาย {_num(d['outflow'])} (หน่วยตามกรมชลประทาน)\n"
            f"ข้อมูลวันที่ {html.escape(d['date'])}")


def write_site(stations, dam, now, dam_err=None):
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now, "province": PROVINCE_NAME, "dam": dam, "dam_error": dam_err, "stations": stations},
                  f, ensure_ascii=False)


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
        "pct": to_float(dig(rec, "storage_percent")),
        "prev": to_float(dig(rec, "waterlevel_msl_previous")),
        "time": dig(rec, "waterlevel_datetime", "datetime", "station.waterlevel_datetime", default=""),
    }


def fmt_station(s, with_time=True):
    emoji, label = LEVELS.get(s["level"], ("⚪", "ไม่ทราบสถานะ"))
    lines = [f"{emoji} <b>{html.escape(s['name'])}</b>"]
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
        if s["prev"] is not None:
            d = s["wl"] - s["prev"]
            trend = "⬆️ เพิ่มขึ้น" if d > 0.005 else "⬇️ ลดลง" if d < -0.005 else "➡️ คงที่"
            lines.append(f"แนวโน้ม: {trend} ({d:+.2f} ม.จากค่าก่อนหน้า)")
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
    out = [normalize(r) for r in rows if is_saraburi(r)]
    for x in out:
        x["ts"] = parse_ts(x["time"])
    return out, rows, body


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
        print("\nเขื่อนป่าสักฯ:", fetch_dam())
        print("\nหลัง normalize (10 สถานีแรก):")
        for s_ in stations[:10]:
            print(s_)
        return

    if not stations:
        print("ไม่พบสถานีของสระบุรี ตรวจ field ด้วย --debug", file=sys.stderr)
        return

    (dam, dam_err), now = fetch_dam(), time.time()
    state = load_state()
    if "levels" not in state:  # รองรับ state รูปแบบเก่า
        state = {"levels": state}
    for k in ("hist", "flags", "dam"):
        state.setdefault(k, {})
    alerts, cleared, rises, stales, fresh = [], [], [], [], []
    for s_ in stations:
        sid = s_["id"]
        h = state["hist"].get(sid, [])
        if s_["ts"] and s_["wl"] is not None and (not h or h[-1][0] != s_["ts"]):
            h.append([s_["ts"], s_["wl"]])
        h = [p for p in h if p[0] >= now - KEEP_H * 3600]
        state["hist"][sid] = s_["hist"] = h
        win = [p for p in h if p[0] >= now - RISE_H * 3600]
        s_["rise"] = round(s_["wl"] - win[0][1], 2) if s_["wl"] is not None and len(win) >= 2 else None
        s_["stale"] = bool(s_["ts"]) and now - s_["ts"] > STALE_H * 3600
        f = state["flags"].setdefault(sid, {})
        prev, cur = state["levels"].get(sid), s_["level"]
        if cur is not None:
            if cur >= ALERT_FROM_LEVEL and cur != prev:
                alerts.append(s_)
            elif prev is not None and prev >= ALERT_FROM_LEVEL > cur:
                cleared.append(s_)
            state["levels"][sid] = cur
        if s_["rise"] is not None:
            if s_["rise"] >= RISE_M and not f.get("rise"):
                rises.append(s_)
                f["rise"] = True
            elif s_["rise"] < RISE_M / 2:
                f["rise"] = False
        if s_["stale"] and not f.get("stale"):
            stales.append(s_)
            f["stale"] = True
        elif not s_["stale"] and f.get("stale"):
            fresh.append(s_)
            f["stale"] = False

    write_site(stations, dam, now, dam_err)

    if "--summary" in sys.argv:
        stations.sort(key=lambda x: -(x["level"] or 0))
        send_telegram(f"📊 <b>สรุประดับน้ำ จ.{PROVINCE_NAME}</b> ({len(stations)} สถานี)\n\n"
                      + "\n\n".join(fmt_station(x) for x in stations)
                      + "\n\n" + fmt_dam(dam, dam_err))
        save_state(state)
        return

    foot = "\n\n⚠️ ข้อมูลอัตโนมัติ ไม่ใช่ประกาศทางการ โปรดติดตาม ปภ./กรมชลประทาน/อบจ.-ท้องถิ่น"
    join = lambda xs, fn=fmt_station: "\n\n".join(fn(x) for x in xs)
    if alerts:
        send_telegram(f"🚨 <b>แจ้งเตือนระดับน้ำ จ.{PROVINCE_NAME}</b>\n\n"
                      + join(sorted(alerts, key=lambda x: -x["level"])) + foot)
    if rises:
        send_telegram(f"📈 <b>น้ำเพิ่มเร็วผิดปกติ</b> (≥{RISE_M} ม. ใน {RISE_H} ชม.)\n\n"
                      + join(rises, lambda x: f"{fmt_station(x)}\nเพิ่มขึ้น {x['rise']:+.2f} ม. ใน {RISE_H} ชม.") + foot)
    if stales:
        send_telegram(f"⏱ <b>ข้อมูลสถานีไม่อัปเดตเกิน {STALE_H} ชม.</b>\n\n" + "\n".join(
            f"• {html.escape(x['name'])} (ล่าสุด {html.escape(str(x['time']))})" for x in stales))
    if fresh:
        send_telegram("✅ <b>สถานีกลับมาอัปเดตข้อมูลแล้ว</b>\n\n" + "\n".join(
            f"• {html.escape(x['name'])}" for x in fresh))
    if cleared:
        send_telegram("✅ <b>ระดับน้ำลดลงต่ำกว่าเกณฑ์เฝ้าระวัง</b>\n\n" + join(cleared))
    if dam and dam["pct"] is not None:
        hi = dam["pct"] >= DAM_PCT
        if hi and not state["dam"].get("hi"):
            send_telegram(f"🌊 <b>เขื่อนป่าสักฯ น้ำสูงกว่า {DAM_PCT}% ของความจุ</b>\n\n" + fmt_dam(dam) + foot)
        state["dam"]["hi"] = hi
    save_state(state)
    print(f"ตรวจ {len(stations)} สถานี | น้ำมาก {len(alerts)} | ขึ้นเร็ว {len(rises)} | ข้อมูลค้าง {len(stales)} | คลี่คลาย {len(cleared)}")


if __name__ == "__main__":
    main()
