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
DAM_LEVELS = [                # (เกณฑ์ % ของความจุเก็บกัก, ไอคอน, ป้ายระดับ) เรียงน้อย→มาก
    (90, "🟠", "เฝ้าระวัง"),
    (100, "🔴", "วิกฤต: เกินความจุเก็บกัก"),
    (110, "🚨", "วิกฤตมาก"),
]
DAM_REARM = 2                 # ต้องลดต่ำกว่าเกณฑ์เดิมเกิน 2 จุด % จึงนับว่าลดระดับ (กันเตือนซ้ำเวลาค่าแกว่ง)
DAM_OUT_RISE_PCT = 25         # เตือนเมื่อ "ระบาย" เพิ่ม >= 25% จากข้อมูลวันก่อนหน้า (ไม่ผูกกับหน่วย)
DAM_OUT_MIN = 1.0             # ไม่เตือนถ้าอัตราระบายปัจจุบันต่ำกว่านี้ (กันค่าเล็กๆ แกว่ง)
RAMA6_ID = "2624"             # ThaiWater: ท้ายเขื่อนพระรามหก (S.26) กรมชลประทาน
RAMA6_PROVINCE = "14"         # พระนครศรีอยุธยา
RAMA6_Q_LEVELS = [            # เกณฑ์อัตราไหล ลบ.ม./วินาที  (ค่าตั้งต้นของผม ปรับได้ ไม่ใช่เกณฑ์ทางการ)
    (400, "🟡", "ระบายน้ำสูง"),
    (600, "🟠", "ระบายน้ำมาก (ระดับที่ ปภ. เคยออกประกาศ 600-700)"),
    (700, "🔴", "ระบายน้ำสูงมาก"),
]
RAMA6_REARM = 20              # ลบ.ม./วิ ที่ต้องลดต่ำกว่าเกณฑ์จึงนับว่าลดระดับ
RAMA6_Q_RISE, RAMA6_RISE_H = 100, 6   # เตือนถ้าอัตราไหลเพิ่ม >= 100 ลบ.ม./วิ ภายใน 6 ชม.
UP_PROVINCE = "67"            # เพชรบูรณ์
UPSTREAM = [                  # (id สถานีใน ThaiWater, ชื่อ, ที่ตั้ง, เวลาน้ำถึงเขื่อนป่าสักฯ)  เวลาเป็นค่าประมาณเบื้องต้น ปรับได้
    ("2799", "บ้านบ่อวัง (S.42)", "อ.วิเชียรบุรี จ.เพชรบูรณ์", "ราว 1-2 วัน"),
    ("700", "หนองไผ่ (PAS003)", "อ.หนองไผ่ จ.เพชรบูรณ์", "ราว 2-3 วัน"),
]
UP_RISE_M, UP_RISE_H = 0.30, 6   # เตือนถ้าสถานีต้นน้ำเพิ่ม >= 0.30 ม. ภายใน 6 ชม.
RAIN_URL = "https://api.open-meteo.com/v1/forecast"
RAIN_POINTS = [("หล่มสัก", 16.78, 101.24), ("เมืองเพชรบูรณ์", 16.42, 101.16), ("หนองไผ่", 16.11, 101.10),
               ("วิเชียรบุรี", 15.65, 101.11), ("แก่งคอย", 14.58, 101.00)]   # จุดในลุ่มน้ำป่าสัก
RAIN_LEVELS = [(35, "🟠", "ฝนหนัก"), (90, "🔴", "ฝนหนักมาก")]   # มม./วัน ตามเกณฑ์ของกรมอุตุฯ
RAIN_REARM = 10
LOCAL_POINTS = [("เมืองสระบุรี", 14.53, 100.91), ("มวกเหล็ก", 14.65, 101.20)]   # จุดในสระบุรี ใช้ดูฝนช่วงสั้น (ฝนตกหนักฉับพลัน)
RAIN3_HOURS = 3
RAIN3_LEVELS = [(30, "🟠", "ฝนตกหนักใน 3 ชม.ข้างหน้า"), (60, "🔴", "ฝนตกหนักมากใน 3 ชม.ข้างหน้า")]   # มม. สะสมใน 3 ชม. (เกณฑ์ประมาณการ ปรับได้)
RAIN3_REARM = 10
TMD_URL = "https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/at"   # API พยากรณ์รายชั่วโมงตามพิกัดของกรมอุตุนิยมวิทยา (ต้องมี token)
TMD_FIELD = "rain"            # ชื่อตัวแปรปริมาณฝน (มม./ชม.) ตามเอกสาร TMD "ตัวแปรพยากรณ์อากาศรายชั่วโมง" ตรวจด้วย --debug
TMD_POINTS = [("เมืองสระบุรี", 14.53, 100.91), ("มวกเหล็ก", 14.65, 101.20),
              ("แก่งคอย", 14.58, 101.00), ("หนองไผ่", 16.11, 101.10)]   # จำกัดจำนวนจุด เพื่อไม่ชน rate limit
SUMMARY_HOURS = (7, 13)       # ส่งสรุปอัตโนมัติ 07:00 และ 13:00 เวลาไทย (รอบแรกที่รันหลังเวลานั้น)
SUMMARY_GRACE_H = 3           # ถ้ารอบตั้งเวลาดีเลย์/ถูกข้าม ยังส่งให้ภายใน 3 ชม. หลังเวลานั้น
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


def next_level(value, levels, prev, rearm):
    """ระดับเตือนปัจจุบัน (0 = ไม่เตือน) ขึ้นได้ทันที แต่ลดต้องต่ำกว่าเกณฑ์เดิมเกิน rearm"""
    n = 0
    for i, (th_, _, _) in enumerate(levels, 1):
        if value is not None and value >= th_:
            n = i
    cur = prev
    while cur > 0 and value < levels[cur - 1][0] - rearm:
        cur -= 1
    return max(cur, n)


def normalize_rama6(rec):
    s = normalize(rec)
    s["discharge"] = to_float(rec.get("discharge"))
    s["qmax"] = to_float(dig(rec, "station.qmax"))
    s["crit"] = to_float(dig(rec, "station.critical_level_msl"))
    s["ts"] = parse_ts(s["time"])
    return s


def fetch_rama6():
    """สถานีท้ายเขื่อนพระรามหก จากฟีด ThaiWater เดียวกับสถานีสระบุรี คืนค่า (ข้อมูล, error)"""
    err = f"ไม่พบสถานี id {RAMA6_ID} (ท้ายเขื่อนพระรามหก) ในข้อมูล ThaiWater"
    try:
        for params in ({"province_code": RAMA6_PROVINCE}, None):
            for r in join_rows(http_get_json(params)):
                if str(dig(r, "station.id", "station_id", "id", default="")) == RAMA6_ID:
                    return normalize_rama6(r), None
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:150]}"
        print(f"ดึงข้อมูลพระราม 6 ไม่สำเร็จ: {err}", file=sys.stderr)
    return None, err


def fmt_rama6(r, err=None):
    if not r:
        return f"🚧 <b>เขื่อนพระราม 6</b>\n⚠️ ดึงข้อมูลไม่สำเร็จ: {html.escape(str(err or ''))}"
    q = _num(r["discharge"], 0)
    cap = f" (ความจุลำน้ำ ~{r['qmax']:.0f})" if r["qmax"] else ""
    lines = ["🚧 <b>เขื่อนพระราม 6 (ท้ายเขื่อน S.26)</b>", "แม่น้ำป่าสัก อ.ท่าเรือ จ.พระนครศรีอยุธยา",
             f"อัตราไหล {q} ลบ.ม./วินาที{cap}"]
    if r["wl"] is not None:
        extra = []
        if r["bank"]:
            extra.append(f"ตลิ่ง {r['bank']:.2f}")
        if r["crit"]:
            extra.append(f"วิกฤต {r['crit']:.2f}")
        lines.append(f"ระดับน้ำ {r['wl']:.2f} ม.รทก." + (f" ({' | '.join(extra)})" if extra else ""))
    lines.append(f"ข้อมูลเมื่อ {html.escape(str(r['time']))}")
    if r.get("stale"):
        lines.append(f"⏱ ข้อมูลไม่อัปเดตเกิน {STALE_H} ชม. ตัวเลขอาจไม่ใช่สถานการณ์ปัจจุบัน")
    return "\n".join(lines)


def fetch_upstream():
    """สถานีต้นน้ำป่าสักในเพชรบูรณ์จากฟีด ThaiWater คืนค่า (รายการสถานี, error)"""
    want, found = {u[0] for u in UPSTREAM}, {}
    try:
        for params in ({"province_code": UP_PROVINCE}, None):
            for r in join_rows(http_get_json(params)):
                sid = str(dig(r, "station.id", "station_id", "id", default=""))
                if sid in want and sid not in found:
                    found[sid] = normalize(r)
            if len(found) == len(want):
                break
    except Exception as e:
        print(f"ดึงข้อมูลต้นน้ำไม่สำเร็จ: {e}", file=sys.stderr)
        return [], f"{type(e).__name__}: {str(e)[:150]}"
    out = []
    for sid, name, loc, lag in UPSTREAM:
        if sid in found:
            u = dict(found[sid])
            u.update(name=name, loc=loc, lag=lag, ts=parse_ts(u["time"]))
            out.append(u)
    return out, (None if len(out) == len(UPSTREAM) else "ไม่พบสถานีต้นน้ำบางแห่งในข้อมูล")


def fetch_rain():
    """พยากรณ์ฝนจาก Open-Meteo (ฟรี ไม่ต้องใช้คีย์): รายวัน (ลุ่มน้ำตอนบน) + 3 ชม.ข้างหน้า (ทุกจุดรวมในสระบุรี)
    เป็นค่าจากแบบจำลอง ไม่ใช่ข้อมูลเครื่องวัดฝน"""
    try:
        pts = RAIN_POINTS + LOCAL_POINTS
        r = requests.get(RAIN_URL, timeout=30, params={
            "latitude": ",".join(str(p[1]) for p in pts),
            "longitude": ",".join(str(p[2]) for p in pts),
            "daily": "precipitation_sum,precipitation_probability_max", "hourly": "precipitation",
            "timezone": "Asia/Bangkok", "past_days": 1, "forecast_days": 3})
        r.raise_for_status()
        body = r.json()
        body = body if isinstance(body, list) else [body]
        n_up = len(RAIN_POINTS)
        days = []
        for i, d in enumerate(body[0]["daily"]["time"]):
            vals = []
            for k, b in enumerate(body[:n_up]):
                mm = to_float(b["daily"]["precipitation_sum"][i])
                pr = to_float((b["daily"].get("precipitation_probability_max") or [None] * 99)[i])
                if mm is not None:
                    vals.append((RAIN_POINTS[k][0], mm, pr))
            if vals:
                top = max(vals, key=lambda v: v[1])
                days.append({"date": d, "max": top[1], "where": top[0], "prob": top[2],
                             "avg": sum(v[1] for v in vals) / len(vals)})
        # ฝนสะสมใน N ชม.ข้างหน้า (ค่า hourly ของ Open-Meteo = ปริมาณฝนของชั่วโมงก่อนหน้าเวลานั้น)
        next3 = None
        times = body[0]["hourly"]["time"]
        cur = datetime.now(TZ).strftime("%Y-%m-%dT%H:00")
        if cur in times:
            i0 = times.index(cur)
            best = None
            for k, b in enumerate(body):
                seg = [to_float(x) for x in b["hourly"]["precipitation"][i0 + 1:i0 + 1 + RAIN3_HOURS]]
                if len(seg) == RAIN3_HOURS and all(x is not None for x in seg):
                    tot = sum(seg)
                    if best is None or tot > best[0]:
                        best = (tot, pts[k][0])
            if best:
                next3 = {"mm": best[0], "where": best[1], "hours": RAIN3_HOURS}
        return {"days": days, "next3": next3, "today": datetime.now(TZ).strftime("%Y-%m-%d")}, None
    except Exception as e:
        print(f"ดึงพยากรณ์ฝนไม่สำเร็จ: {e}", file=sys.stderr)
        return None, f"{type(e).__name__}: {str(e)[:150]}"


def fetch_tmd(debug=False):
    """พยากรณ์ฝน 3 ชม.ข้างหน้าจาก TMD (ต้องตั้ง TMD_TOKEN) ไม่มี token = ข้ามเงียบ ๆ คืน (None, None)"""
    token = os.environ.get("TMD_TOKEN", "").strip()
    if not token:
        return None, None
    try:
        start = datetime.now(TZ).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        pts = []
        for name, lat, lon in TMD_POINTS:
            r = requests.get(TMD_URL, timeout=30, headers={"accept": "application/json", "authorization": f"Bearer {token}"},
                             params={"lat": lat, "lon": lon, "date": start.strftime("%Y-%m-%d"), "hour": start.hour,
                                     "duration": RAIN3_HOURS, "fields": TMD_FIELD})
            if r.status_code == 429:
                raise RuntimeError("เรียก TMD ถี่เกิน rate limit (429)")
            if r.status_code in (401, 403):
                raise RuntimeError(f"token TMD ใช้ไม่ได้หรือหมดอายุ ({r.status_code})")
            if r.status_code == 422:
                raise RuntimeError(f"TMD ปฏิเสธ request (422) ตรวจชื่อ field '{TMD_FIELD}': {r.text[:120]}")
            r.raise_for_status()
            body = r.json()
            if debug:
                print(f"TMD {name}:", json.dumps(body, ensure_ascii=False)[:600])
            fc = body["WeatherForecasts"][0]["forecasts"]
            vals = [to_float((f.get("data") or {}).get(TMD_FIELD)) for f in fc]
            if len(vals) < RAIN3_HOURS or any(v is None for v in vals):
                raise RuntimeError(f"ข้อมูล TMD ไม่ครบหรือไม่มี field '{TMD_FIELD}'")
            pts.append((name, sum(vals[:RAIN3_HOURS])))
        top = max(pts, key=lambda v: v[1])
        return {"next3": {"mm": top[1], "where": top[0], "hours": RAIN3_HOURS},
                "points": [{"name": n, "mm": m} for n, m in pts]}, None
    except Exception as e:
        print(f"ดึงพยากรณ์ TMD ไม่สำเร็จ: {e}", file=sys.stderr)
        return None, f"{type(e).__name__}: {str(e)[:150]}"


def fmt_tmd(t, err=None):
    if err:
        return f"🌦 <b>พยากรณ์ฝน กรมอุตุฯ (TMD)</b>\n⚠️ ดึงข้อมูลไม่สำเร็จ: {html.escape(str(err))}"
    if not t:
        return ""
    n = t["next3"]
    pts = " · ".join(f"{html.escape(p['name'])} {p['mm']:.0f}" for p in t["points"])
    return (f"🌦 <b>พยากรณ์ฝน กรมอุตุฯ (TMD)</b>\n⏱ {n['hours']} ชม.ข้างหน้า: สูงสุด {n['mm']:.0f} มม. "
            f"({html.escape(n['where'])})\n{pts} (มม.)")


def fmt_upstream(up, err=None):
    if not up:
        return f"⛰ <b>ต้นน้ำป่าสัก</b>\n⚠️ ดึงข้อมูลไม่สำเร็จ: {html.escape(str(err or ''))}"
    lines = ["⛰ <b>ต้นน้ำป่าสัก (เพชรบูรณ์)</b>"]
    for u in up:
        em, lab = LEVELS.get(u["level"], ("⚪", "ไม่ทราบสถานะ"))
        rise = "" if u.get("rise") is None else f" | {u['rise']:+.2f} ม. ใน {UP_RISE_H} ชม."
        lines.append(f"{em} {html.escape(u['name'])}: {_num(u['wl'])} ม.รทก. ({lab}){rise}")
    return "\n".join(lines)


def _rain_day_line(d):
    pr = "" if d.get("prob") is None else f" โอกาสฝน {d['prob']:.0f}%"
    return f"{d['date']}: สูงสุด {d['max']:.0f} มม. ({html.escape(d['where'])}) เฉลี่ย {d['avg']:.0f}{pr}"


def fmt_rain(r, err=None):
    if not r:
        return f"🌧 <b>พยากรณ์ฝน</b>\n⚠️ ดึงข้อมูลไม่สำเร็จ: {html.escape(str(err or ''))}"
    lines = ["🌧 <b>พยากรณ์ฝนลุ่มน้ำป่าสักตอนบน</b> (แบบจำลอง Open-Meteo)"]
    n3 = r.get("next3")
    if n3:
        lines.append(f"⏱ {n3['hours']} ชม.ข้างหน้า: สูงสุด {n3['mm']:.0f} มม. ({html.escape(n3['where'])})")
    for d in r["days"]:
        if d["date"] >= r["today"]:
            lines.append(_rain_day_line(d))
    return "\n".join(lines)


def write_site(stations, dam, now, dam_err=None, rama6=None, rama6_err=None, extra=None):
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now, "province": PROVINCE_NAME, "dam": dam, "dam_error": dam_err,
                   "rama6": rama6, "rama6_error": rama6_err, "stations": stations, **(extra or {})},
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
        "lat": to_float(dig(rec, "station.tele_station_lat")),
        "lon": to_float(dig(rec, "station.tele_station_long")),
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


def summary_text(stations, dam, dam_err, r6, r6_err, up, up_err, rain, rain_err, title="สรุประดับน้ำ", tmd=None, tmd_err=None):
    st = sorted(stations, key=lambda x: -(x["level"] or 0))
    return (f"📊 <b>{title} จ.{PROVINCE_NAME}</b> ({len(st)} สถานี)\n\n"
            + "\n\n".join(fmt_station(x) for x in st)
            + "\n\n" + fmt_dam(dam, dam_err) + "\n\n" + fmt_rama6(r6, r6_err)
            + "\n\n" + fmt_upstream(up, up_err) + "\n\n" + fmt_rain(rain, rain_err)
            + (("\n\n" + fmt_tmd(tmd, tmd_err)) if (tmd or tmd_err) else ""))


def due_summary_slot(state, now_ts):
    """คืน key ของรอบสรุปที่ถึงเวลาแต่ยังไม่ส่ง (เช่น '2026-10-04-07') ไม่งั้นคืน None
    ใช้เวลาไทยของบอตเอง จึงไม่ขึ้นกับว่า GitHub ดีเลย์หรือข้ามรอบตั้งเวลา"""
    t = datetime.fromtimestamp(now_ts, TZ)
    sent = state.setdefault("summary_sent", {})
    for h in sorted(SUMMARY_HOURS, reverse=True):
        key = f"{t:%Y-%m-%d}-{h:02d}"
        if 0 <= t.hour - h < SUMMARY_GRACE_H and key not in sent:
            return key
    return None


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
        print("\nพระราม 6:", fetch_rama6())
        print("\nต้นน้ำ:", fetch_upstream())
        print("\nฝน:", fetch_rain())
        print("\nTMD:", fetch_tmd(debug=True) if os.environ.get("TMD_TOKEN") else "ไม่ได้ตั้ง TMD_TOKEN")
        print("\nหลัง normalize (10 สถานีแรก):")
        for s_ in stations[:10]:
            print(s_)
        return

    if not stations:
        print("ไม่พบสถานีของสระบุรี ตรวจ field ด้วย --debug", file=sys.stderr)
        return

    (dam, dam_err), now = fetch_dam(), time.time()
    r6, r6_err = fetch_rama6()
    if r6:
        r6["stale"] = bool(r6["ts"]) and now - r6["ts"] > STALE_H * 3600
    up, up_err = fetch_upstream()
    rain, rain_err = fetch_rain()
    tmd, tmd_err = fetch_tmd()
    state = load_state()
    if "levels" not in state:  # รองรับ state รูปแบบเก่า
        state = {"levels": state}
    for k in ("hist", "flags", "dam", "rama6", "up", "rain", "rain3"):
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

    for u in up:
        us = state["up"].setdefault(u["id"], {})
        h = us.get("hist", [])
        if u["ts"] and u["wl"] is not None and (not h or h[-1][0] != u["ts"]):
            h.append([u["ts"], u["wl"]])
        h = [p for p in h if p[0] >= now - 24 * 3600]
        us["hist"] = h
        win = [p for p in h if p[0] >= now - UP_RISE_H * 3600]
        u["rise"] = round(u["wl"] - win[0][1], 2) if u["wl"] is not None and len(win) >= 2 else None
        u["hist"] = h

    if r6 and r6["ts"] and r6["discharge"] is not None:
        h6 = state["rama6"].get("hist", [])
        if not h6 or h6[-1][0] != r6["ts"]:
            h6 = h6 + [[r6["ts"], r6["discharge"]]]
        r6["hist"] = h6

    write_site(stations, dam, now, dam_err, r6, r6_err,
               extra={"upstream": up, "upstream_error": up_err, "rain": rain, "rain_error": rain_err, "tmd": tmd, "tmd_error": tmd_err})

    if "--summary" in sys.argv:
        send_telegram(summary_text(stations, dam, dam_err, r6, r6_err, up, up_err, rain, rain_err, tmd=tmd, tmd_err=tmd_err))
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
        ds = state["dam"]
        prev = ds.get("lvl", 1 if ds.get("hi") else 0)   # รองรับ state เก่าที่เก็บแค่ hi
        cur = next_level(dam["pct"], DAM_LEVELS, prev, DAM_REARM)
        if cur > prev:
            th_, em, label = DAM_LEVELS[cur - 1]
            send_telegram(f"{em} <b>เขื่อนป่าสักฯ ระดับเตือน: {label}</b> (≥{th_}% ของความจุ)\n\n"
                          + fmt_dam(dam) + foot)
        elif cur < prev:
            low = DAM_LEVELS[cur - 1][2] if cur else "ต่ำกว่าเกณฑ์เฝ้าระวัง"
            send_telegram(f"✅ <b>เขื่อนป่าสักฯ ลดลงสู่ระดับ: {low}</b>\n\n" + fmt_dam(dam))
        ds["lvl"] = cur
        ds.pop("hi", None)
    if dam and dam["outflow"] is not None and dam["date"]:
        h = state["dam"].setdefault("hist", [])      # [[วันที่ข้อมูล, เวลาที่บันทึก, ระบาย]]
        if not h or h[-1][0] != dam["date"]:         # RID อัปเดตเป็นรอบ จึงเทียบเฉพาะเมื่อวันที่ข้อมูลเปลี่ยน
            if h and h[-1][2] and h[-1][2] > 0 and dam["outflow"] >= DAM_OUT_MIN:
                pct = (dam["outflow"] - h[-1][2]) / h[-1][2] * 100
                if pct >= DAM_OUT_RISE_PCT:
                    send_telegram(f"📈 <b>เขื่อนป่าสักฯ เพิ่มการระบายน้ำ</b> (+{pct:.0f}% จาก {h[-1][2]:.2f} → {dam['outflow']:.2f}) "
                                  "ท้ายน้ำ (ท่าเรือ/นครหลวง/อยุธยา) อาจได้รับผลกระทบ\n\n" + fmt_dam(dam) + foot)
            h.append([dam["date"], now, dam["outflow"]])
            state["dam"]["hist"] = h[-10:]
    if r6 and r6["discharge"] is not None:
        rs = state["rama6"]
        wl_lvl = 3 if (r6["crit"] is not None and r6["wl"] is not None and r6["wl"] >= r6["crit"]) else 0
        prev = rs.get("lvl", 0)
        cur = max(next_level(r6["discharge"], RAMA6_Q_LEVELS, prev, RAMA6_REARM), wl_lvl)
        if cur > prev:
            em, label = RAMA6_Q_LEVELS[cur - 1][1:]
            note = "\n⚠️ ระดับน้ำถึงเกณฑ์วิกฤตของสถานี" if wl_lvl else ""
            send_telegram(f"{em} <b>เขื่อนพระราม 6: {label}</b>\n\n" + fmt_rama6(r6) + note + foot)
        elif cur < prev:
            low = RAMA6_Q_LEVELS[cur - 1][2] if cur else "ต่ำกว่าเกณฑ์เฝ้าระวัง"
            send_telegram(f"✅ <b>เขื่อนพระราม 6 ลดลงสู่ระดับ: {low}</b>\n\n" + fmt_rama6(r6))
        rs["lvl"] = cur
        h = rs.get("hist", [])
        if r6["ts"] and (not h or h[-1][0] != r6["ts"]):
            h.append([r6["ts"], r6["discharge"]])
        h = [p for p in h if p[0] >= now - 24 * 3600]
        rs["hist"] = h
        win = [p for p in h if p[0] >= now - RAMA6_RISE_H * 3600]
        rise = r6["discharge"] - win[0][1] if len(win) >= 2 else None
        if rise is not None:
            if rise >= RAMA6_Q_RISE and not rs.get("rise"):
                rs["rise"] = True
                send_telegram(f"📈 <b>เขื่อนพระราม 6 เพิ่มการระบายน้ำเร็ว</b> (+{rise:.0f} ลบ.ม./วิ ใน {RAMA6_RISE_H} ชม.)\n\n"
                              + fmt_rama6(r6) + foot)
            elif rise < RAMA6_Q_RISE / 2:
                rs["rise"] = False
    for u in up:
        us = state["up"][u["id"]]
        em, lab = LEVELS.get(u["level"], ("⚪", "ไม่ทราบสถานะ"))
        note = (f"\nน้ำจากจุดนี้จะไหลลงเขื่อนป่าสักฯ {u['lag']} (ค่าประมาณเบื้องต้น) "
                "แล้วท้ายน้ำขึ้นกับการระบายของเขื่อนอีกชั้นหนึ่ง")
        if u["rise"] is not None:
            if u["rise"] >= UP_RISE_M and not us.get("rise"):
                us["rise"] = True
                send_telegram(f"📈 <b>ต้นน้ำป่าสักเพิ่มขึ้นเร็ว: {html.escape(u['name'])}</b>\n{html.escape(u['loc'])}\n"
                              f"ระดับ {_num(u['wl'])} ม.รทก. ({u['rise']:+.2f} ม. ใน {UP_RISE_H} ชม.) สถานะ {lab}{note}" + foot)
            elif u["rise"] < UP_RISE_M / 2:
                us["rise"] = False
        if u["level"] is not None:
            prevl = us.get("lvl")
            if prevl is not None and u["level"] > prevl and u["level"] >= 4:
                send_telegram(f"{em} <b>ต้นน้ำป่าสักเข้าสู่ระดับ: {lab}</b> ({html.escape(u['name'])})\n"
                              f"ระดับ {_num(u['wl'])} ม.รทก.{note}" + foot)
            us["lvl"] = u["level"]
    if rain:
        rs, hot = state["rain"], []
        for k in [k for k in rs if k < rain["today"]]:
            rs.pop(k)
        for d in rain["days"]:
            if d["date"] < rain["today"]:
                continue
            prevr = rs.get(d["date"], 0)
            curr = next_level(d["max"], RAIN_LEVELS, prevr, RAIN_REARM)
            if curr > prevr:
                hot.append((d, curr))
            rs[d["date"]] = curr
        if hot:
            top = max(c for _, c in hot)
            em, lab = RAIN_LEVELS[top - 1][1:]
            send_telegram(f"{em} <b>พยากรณ์: {lab} ในลุ่มน้ำป่าสักตอนบน</b>\n\n" + "\n".join(
                _rain_day_line(d) for d, _ in hot)
                + "\n\nเป็นค่าจากแบบจำลองพยากรณ์ ผลต่อระดับน้ำขึ้นกับความชื้นดินและการบริหารเขื่อน" + foot)
    cands = []
    if tmd:
        cands.append(("กรมอุตุฯ (TMD)", tmd["next3"]))
    if rain and rain.get("next3"):
        cands.append(("Open-Meteo", rain["next3"]))
    src, n3 = max(cands, key=lambda c: c[1]["mm"]) if cands else (None, None)
    if n3:
        prev3 = state["rain3"].get("lvl", 0)
        curr3 = next_level(n3["mm"], RAIN3_LEVELS, prev3, RAIN3_REARM)
        if curr3 > prev3:
            em, lab = RAIN3_LEVELS[curr3 - 1][1:]
            others = "".join(f"\n{html.escape(nm)}: {c['mm']:.0f} มม." for nm, c in cands if nm != src)
            send_telegram(f"{em} <b>พยากรณ์: {lab}</b>\n\nสะสมสูงสุด {n3['mm']:.0f} มม. ใน {n3['hours']} ชม. "
                          f"({html.escape(n3['where'])}) จาก {src}{others}\nเสี่ยงน้ำท่วมฉับพลัน/น้ำหลากในพื้นที่ลาดชันและริมลำน้ำ"
                          "\n\nเป็นค่าพยากรณ์ คลาดเคลื่อนได้ ควรดูประกาศกรมอุตุนิยมวิทยาประกอบ" + foot)
        state["rain3"]["lvl"] = curr3
    slot = due_summary_slot(state, now)
    if slot:
        send_telegram(summary_text(stations, dam, dam_err, r6, r6_err, up, up_err, rain, rain_err, "สรุปประจำเวลา", tmd=tmd, tmd_err=tmd_err))
        state["summary_sent"][slot] = int(now)
        for k in sorted(state["summary_sent"])[:-6]:   # เก็บแค่ 6 รายการล่าสุด
            state["summary_sent"].pop(k, None)
    save_state(state)
    print(f"ตรวจ {len(stations)} สถานี | น้ำมาก {len(alerts)} | ขึ้นเร็ว {len(rises)} | ข้อมูลค้าง {len(stales)} | คลี่คลาย {len(cleared)}")


if __name__ == "__main__":
    main()
