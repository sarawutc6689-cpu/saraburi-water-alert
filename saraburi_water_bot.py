#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
saraburi_water_bot.py (v2)
- ระดับน้ำสถานีโทรมาตร จ.สระบุรี
- เขื่อนป่าสักชลสิทธิ์
- เตือนน้ำขึ้นเร็วผิดปกติ
- เตือนข้อมูลสถานีค้าง
- เขียน docs/data.json สำหรับแดชบอร์ด
โหมด: summary | alert | debug
"""

import os
import sys
import json
import html
from datetime import datetime, timedelta, timezone

import requests

TH = timezone(timedelta(hours=7))

API_WL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
API_DAM = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/dam_detail"
TIMEOUT = 30

PROVINCE = "สระบุรี"
DAM_KEYWORDS = ["ป่าสักชลสิทธิ์"]        # เพิ่มชื่อเขื่อนอื่นได้ เช่น "พระราม 6"

RISE_1H_M = 0.30                          # ม./ชม.
RISE_3H_M = 0.60                          # ม./3 ชม.
RISE_COOLDOWN_H = 3
STALE_HOURS = 3
STALE_COOLDOWN_H = 12
HISTORY_HOURS = 24
DAM_WARN_PCT = 80.0
DAM_CRIT_PCT = 95.0

STATE_FILE = "state.json"
DASH_FILE = "docs/data.json"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# ---------------- utils ----------------

def now_th():
    return datetime.now(TH)


def dig(obj, *keys, default=None):
    cur = obj
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur if cur is not None else default


def th_text(v, default="-"):
    if isinstance(v, dict):
        return v.get("th") or v.get("en") or default
    return v or default


def to_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_dt(s):
    if not s:
        return None
    s = str(s).replace("T", " ").split(".")[0].split("+")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TH)
        except ValueError:
            continue
    return None


def fmt(v, nd=2, unit=""):
    if v is None:
        return "-"
    return f"{v:,.{nd}f}{unit}"


# ---------------- state ----------------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    st.setdefault("status", {})       # สถานะล่าสุดต่อสถานี
    st.setdefault("history", {})      # ประวัติระดับน้ำ [[epoch, level], ...]
    st.setdefault("last_rise", {})    # epoch ของการเตือนน้ำขึ้นเร็วครั้งล่าสุด
    st.setdefault("stale", {})        # {station: epoch ที่เตือนค้างครั้งล่าสุด}
    st.setdefault("dam_status", {})
    return st


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ---------------- telegram ----------------

def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("ไม่พบ TELEGRAM_BOT_TOKEN หรือ TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    ok = True
    for i in range(0, len(text), 3800):
        chunk = text[i:i + 3800]
        try:
            r = requests.post(
                url,
                json={
                    "chat_id": CHAT_ID,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=TIMEOUT,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"ส่ง Telegram ไม่สำเร็จ: {e}")
            ok = False
    return ok


# ---------------- fetch ----------------

def fetch_stations():
    r = requests.get(API_WL, timeout=TIMEOUT, headers={"User-Agent": "saraburi-water-bot/2.0"})
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("data")
    if isinstance(rows, dict):
        rows = rows.get("data", [])
    out = []
    for row in rows or []:
        prov = th_text(dig(row, "geocode", "province_name"), "")
        if PROVINCE not in str(prov):
            continue
        st = row.get("station", {}) or {}
        name = th_text(st.get("tele_station_name"), th_text(row.get("station_name"), "ไม่ทราบชื่อ"))
        wl = to_float(row.get("waterlevel_msl"))
        bank = to_float(st.get("min_bank")) or to_float(row.get("min_bank"))
        ground = to_float(st.get("ground_level")) or to_float(row.get("ground_level"))
        dt = parse_dt(row.get("waterlevel_datetime") or row.get("datetime"))
        pct = None
        if wl is not None and bank is not None and ground is not None and bank > ground:
            pct = (wl - ground) / (bank - ground) * 100.0
        out.append({
            "key": str(st.get("id") or row.get("id") or name),
            "name": name,
            "amphoe": th_text(dig(row, "geocode", "amphoe_name"), "-"),
            "level": wl,
            "bank": bank,
            "ground": ground,
            "percent": pct,
            "status": classify(pct),
            "datetime": dt.strftime("%Y-%m-%d %H:%M") if dt else None,
            "epoch": int(dt.timestamp()) if dt else None,
        })
    out.sort(key=lambda s: s["name"])
    return out


def classify(pct):
    if pct is None:
        return "ไม่มีข้อมูล"
    if pct >= 100:
        return "ล้นตลิ่ง"
    if pct >= 70:
        return "น้ำมาก"
    if pct >= 30:
        return "ปกติ"
    return "น้ำน้อย"


def icon(status):
    return {
        "ล้นตลิ่ง": "🔴",
        "น้ำมาก": "🟠",
        "ปกติ": "🟢",
        "น้ำน้อย": "🔵",
    }.get(status, "⚪")


def fetch_dams():
    r = requests.get(API_DAM, timeout=TIMEOUT, headers={"User-Agent": "saraburi-water-bot/2.0"})
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("data")
    if isinstance(rows, dict):
        rows = rows.get("data", [])
    out = []
    for row in rows or []:
        name = th_text(dig(row, "dam", "dam_name"), "")
        if not any(k in str(name) for k in DAM_KEYWORDS):
            continue
        dt = parse_dt(row.get("dam_date"))
        out.append({
            "name": name,
            "storage": to_float(row.get("dam_storage")),
            "percent": to_float(row.get("dam_storage_percent")),
            "inflow": to_float(row.get("dam_inflow")),
            "released": to_float(row.get("dam_released")),
            "uses_water": to_float(row.get("dam_uses_water")),
            "normal_storage": to_float(dig(row, "dam", "normal_storage")),
            "datetime": dt.strftime("%Y-%m-%d %H:%M") if dt else None,
        })
    return out


# ---------------- rise detection ----------------

def update_history(state, stations, now_epoch):
    cutoff = now_epoch - HISTORY_HOURS * 3600
    for s in stations:
        if s["level"] is None:
            continue
        hist = state["history"].get(s["key"], [])
        stamp = s["epoch"] or now_epoch
        if not hist or hist[-1][0] != stamp:
            hist.append([stamp, s["level"]])
        hist = [h for h in hist if h[0] >= cutoff]
        state["history"][s["key"]] = hist[-200:]


def rise_over(hist, level, now_epoch, hours):
    """คืนค่า (ระดับที่เพิ่ม, จำนวนชั่วโมงจริง) เทียบกับค่าที่ใกล้ now-hours ที่สุด"""
    target = now_epoch - hours * 3600
    best = None
    for ts, lv in hist:
        if ts >= now_epoch:
            continue
        gap = abs(ts - target)
        if best is None or gap < best[0]:
            best = (gap, ts, lv)
    if not best:
        return None
    _, ts, lv = best
    dt_h = (now_epoch - ts) / 3600.0
    if dt_h < hours * 0.6 or dt_h > hours * 1.8:
        return None
    return (level - lv, dt_h)


def check_rise(state, stations, now_epoch):
    msgs = []
    for s in stations:
        if s["level"] is None:
            continue
        last = state["last_rise"].get(s["key"], 0)
        if now_epoch - last < RISE_COOLDOWN_H * 3600:
            continue
        hist = state["history"].get(s["key"], [])
        base = s["epoch"] or now_epoch
        hit = None
        r1 = rise_over(hist, s["level"], base, 1)
        if r1 and r1[0] >= RISE_1H_M:
            hit = ("1 ชม.", r1[0], r1[1])
        if not hit:
            r3 = rise_over(hist, s["level"], base, 3)
            if r3 and r3[0] >= RISE_3H_M:
                hit = ("3 ชม.", r3[0], r3[1])
        if hit:
            win, delta, hours = hit
            msgs.append(
                f"⚡ <b>น้ำขึ้นเร็วผิดปกติ</b>\n"
                f"สถานี: {html.escape(s['name'])} (อ.{html.escape(s['amphoe'])})\n"
                f"เพิ่มขึ้น <b>{fmt(delta, 2, ' ม.')}</b> ในช่วง {win} (วัดจริง {hours:.1f} ชม.)\n"
                f"ระดับปัจจุบัน: {fmt(s['level'], 2, ' ม.รทก.')} | ตลิ่ง {fmt(s['bank'], 2, ' ม.')}\n"
                f"สถานะ: {icon(s['status'])} {s['status']} ({fmt(s['percent'], 0, '%')} ของตลิ่ง)\n"
                f"ข้อมูลเวลา {s['datetime'] or '-'}"
            )
            state["last_rise"][s["key"]] = now_epoch
    return msgs


# ---------------- stale detection ----------------

def check_stale(state, stations, now_epoch):
    msgs = []
    for s in stations:
        key = s["key"]
        age_h = None
        if s["epoch"]:
            age_h = (now_epoch - s["epoch"]) / 3600.0
        is_stale = (age_h is None) or (age_h > STALE_HOURS)
        prev = state["stale"].get(key)
        if is_stale:
            if prev is None or now_epoch - prev >= STALE_COOLDOWN_H * 3600:
                age_txt = f"{age_h:.1f} ชม." if age_h is not None else "ไม่ทราบ"
                msgs.append(
                    f"⏳ <b>ข้อมูลสถานีไม่อัปเดต</b>\n"
                    f"สถานี: {html.escape(s['name'])} (อ.{html.escape(s['amphoe'])})\n"
                    f"ข้อมูลล่าสุด: {s['datetime'] or 'ไม่มี'} (ค้างมาแล้ว {age_txt})\n"
                    f"ค่าที่แสดงบนแดชบอร์ดอาจไม่ตรงกับความจริง"
                )
                state["stale"][key] = now_epoch
        else:
            if prev is not None:
                msgs.append(
                    f"✅ <b>ข้อมูลสถานีกลับมาปกติ</b>\n"
                    f"สถานี: {html.escape(s['name'])}\n"
                    f"ข้อมูลล่าสุด: {s['datetime']}"
                )
                state["stale"].pop(key, None)
    return msgs


# ---------------- dam alert ----------------

def check_dam(state, dams):
    msgs = []
    for d in dams:
        pct = d["percent"]
        if pct is None:
            continue
        level = "crit" if pct >= DAM_CRIT_PCT else ("warn" if pct >= DAM_WARN_PCT else "normal")
        prev = state["dam_status"].get(d["name"])
        if level != prev and level != "normal":
            mark = "🔴" if level == "crit" else "🟠"
            msgs.append(
                f"{mark} <b>{html.escape(d['name'])}</b>\n"
                f"ปริมาตรเก็บกัก: {fmt(d['storage'], 2)} ล้าน ลบ.ม. ({fmt(pct, 1, '%')})\n"
                f"น้ำไหลเข้า: {fmt(d['inflow'], 2)} | ระบายออก: {fmt(d['released'], 2)} ล้าน ลบ.ม./วัน\n"
                f"ข้อมูลเวลา {d['datetime'] or '-'}"
            )
        if level != prev and level == "normal" and prev is not None:
            msgs.append(
                f"🟢 <b>{html.escape(d['name'])}</b> กลับสู่เกณฑ์ปกติ ({fmt(pct, 1, '%')})"
            )
        state["dam_status"][d["name"]] = level
    return msgs


# ---------------- status change alert ----------------

def check_status(state, stations):
    msgs = []
    order = {"น้ำน้อย": 0, "ปกติ": 1, "น้ำมาก": 2, "ล้นตลิ่ง": 3}
    for s in stations:
        if s["status"] == "ไม่มีข้อมูล":
            continue
        prev = state["status"].get(s["key"])
        if prev == s["status"]:
            continue
        state["status"][s["key"]] = s["status"]
        if prev is None:
            continue
        if order.get(s["status"], 0) >= 2 or order.get(prev, 0) >= 2:
            arrow = "เพิ่มขึ้น" if order.get(s["status"], 0) > order.get(prev, 0) else "ลดลง"
            msgs.append(
                f"{icon(s['status'])} <b>สถานะเปลี่ยน ({arrow})</b>\n"
                f"สถานี: {html.escape(s['name'])} (อ.{html.escape(s['amphoe'])})\n"
                f"{prev} → <b>{s['status']}</b>\n"
                f"ระดับน้ำ {fmt(s['level'], 2, ' ม.รทก.')} | ตลิ่ง {fmt(s['bank'], 2, ' ม.')} "
                f"({fmt(s['percent'], 0, '%')})\n"
                f"ข้อมูลเวลา {s['datetime'] or '-'}"
            )
    return msgs


# ---------------- dashboard ----------------

def write_dashboard(stations, dams, now_epoch):
    os.makedirs(os.path.dirname(DASH_FILE), exist_ok=True)
    data = {
        "updated": now_th().strftime("%Y-%m-%d %H:%M:%S"),
        "stations": [
            {
                "name": s["name"],
                "amphoe": s["amphoe"],
                "level": s["level"],
                "bank": s["bank"],
                "percent": s["percent"],
                "status": s["status"],
                "datetime": s["datetime"],
                "stale": bool(s["epoch"] is None or (now_epoch - s["epoch"]) / 3600.0 > STALE_HOURS),
            }
            for s in stations
        ],
        "dams": dams,
    }
    with open(DASH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


# ---------------- messages ----------------

def build_summary(stations, dams):
    lines = [f"📊 <b>สรุปสถานการณ์น้ำ จ.สระบุรี</b>",
             f"ณ {now_th().strftime('%d/%m/%Y %H:%M')} น.", ""]
    for s in stations:
        lines.append(
            f"{icon(s['status'])} <b>{html.escape(s['name'])}</b> (อ.{html.escape(s['amphoe'])})\n"
            f"   ระดับ {fmt(s['level'], 2, ' ม.รทก.')} / ตลิ่ง {fmt(s['bank'], 2, ' ม.')} "
            f"= {fmt(s['percent'], 0, '%')} — {s['status']}\n"
            f"   ข้อมูล {s['datetime'] or 'ไม่มี'}"
        )
    if dams:
        lines.append("\n🏞 <b>เขื่อน</b>")
        for d in dams:
            lines.append(
                f"• <b>{html.escape(d['name'])}</b>\n"
                f"   เก็บกัก {fmt(d['storage'], 2)} ล้าน ลบ.ม. ({fmt(d['percent'], 1, '%')})\n"
                f"   เข้า {fmt(d['inflow'], 2)} | ระบาย {fmt(d['released'], 2)} ล้าน ลบ.ม./วัน\n"
                f"   ข้อมูล {d['datetime'] or '-'}"
            )
    lines.append("\nที่มา: คลังข้อมูลน้ำแห่งชาติ (ThaiWater)")
    return "\n".join(lines)


# ---------------- main ----------------

def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE", "alert")).strip().lower()
    print(f"โหมด: {mode}")

    stations = fetch_stations()
    print(f"พบสถานี จ.{PROVINCE}: {len(stations)} สถานี")
    try:
        dams = fetch_dams()
    except requests.RequestException as e:
        print(f"ดึงข้อมูลเขื่อนไม่สำเร็จ: {e}")
        dams = []
    print(f"พบเขื่อน: {len(dams)} แห่ง")

    state = load_state()
    now_epoch = int(now_th().timestamp())

    alerts = []
    alerts += check_status(state, stations)
    alerts += check_rise(state, stations, now_epoch)
    alerts += check_stale(state, stations, now_epoch)
    alerts += check_dam(state, dams)

    update_history(state, stations, now_epoch)
    write_dashboard(stations, dams, now_epoch)

    if mode == "debug":
        print(build_summary(stations, dams))
        print("\n--- การแจ้งเตือนที่จะส่ง ---")
        print("\n\n".join(alerts) if alerts else "ไม่มี")
        save_state(state)
        return

    if mode == "summary":
        send_telegram(build_summary(stations, dams))

    if alerts:
        send_telegram("\n\n".join(alerts))
    else:
        print("ไม่มีการแจ้งเตือนรอบนี้")

    save_state(state)
    print("เสร็จสิ้น")


if __name__ == "__main__":
    main()
