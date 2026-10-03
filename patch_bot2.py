#!/usr/bin/env python3
"""รันที่โฟลเดอร์ที่มี saraburi_water_bot.py และ index.html (หรือ site/index.html): python3 patch_bot2.py
เพิ่ม 1) เตือนต้นน้ำป่าสัก (เพชรบูรณ์) เมื่อน้ำเพิ่มเร็ว  2) พยากรณ์ฝนลุ่มน้ำป่าสักตอนบน (Open-Meteo)
ต้องรัน patch_bot.py (รอบแรก) มาแล้ว  สำรองไฟล์เป็น .bak2"""
import os, shutil, sys

def load(p):
    return open(p, encoding="utf-8").read()

def rep(s, old, new):
    if s.count(old) != 1:
        sys.exit(f"ไม่พบข้อความที่จะแก้ (พบ {s.count(old)} ครั้ง):\n{old[:90]}")
    return s.replace(old, new)

# ---------------- บอต ----------------
P = "saraburi_water_bot.py"
s = load(P); shutil.copy(P, P + ".bak2")

s = rep(s, r'''RAMA6_Q_RISE, RAMA6_RISE_H = 100, 6   # เตือนถ้าอัตราไหลเพิ่ม >= 100 ลบ.ม./วิ ภายใน 6 ชม.
''', r'''RAMA6_Q_RISE, RAMA6_RISE_H = 100, 6   # เตือนถ้าอัตราไหลเพิ่ม >= 100 ลบ.ม./วิ ภายใน 6 ชม.
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
''')

s = rep(s, r'''def write_site(stations, dam, now, dam_err=None, rama6=None, rama6_err=None):''', r'''def fetch_upstream():
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
    """พยากรณ์ฝนรายวันจาก Open-Meteo (ฟรี ไม่ต้องใช้คีย์) เป็นค่าจากแบบจำลอง ไม่ใช่ข้อมูลเครื่องวัดฝน"""
    try:
        r = requests.get(RAIN_URL, timeout=30, params={
            "latitude": ",".join(str(p[1]) for p in RAIN_POINTS),
            "longitude": ",".join(str(p[2]) for p in RAIN_POINTS),
            "daily": "precipitation_sum", "timezone": "Asia/Bangkok", "past_days": 1, "forecast_days": 3})
        r.raise_for_status()
        body = r.json()
        body = body if isinstance(body, list) else [body]
        days = []
        for i, d in enumerate(body[0]["daily"]["time"]):
            vals = [(RAIN_POINTS[k][0], to_float(b["daily"]["precipitation_sum"][i])) for k, b in enumerate(body)]
            vals = [v for v in vals if v[1] is not None]
            if vals:
                top = max(vals, key=lambda v: v[1])
                days.append({"date": d, "max": top[1], "where": top[0], "avg": sum(v[1] for v in vals) / len(vals)})
        return {"days": days, "today": datetime.now(TZ).strftime("%Y-%m-%d")}, None
    except Exception as e:
        print(f"ดึงพยากรณ์ฝนไม่สำเร็จ: {e}", file=sys.stderr)
        return None, f"{type(e).__name__}: {str(e)[:150]}"


def fmt_upstream(up, err=None):
    if not up:
        return f"⛰ <b>ต้นน้ำป่าสัก</b>\n⚠️ ดึงข้อมูลไม่สำเร็จ: {html.escape(str(err or ''))}"
    lines = ["⛰ <b>ต้นน้ำป่าสัก (เพชรบูรณ์)</b>"]
    for u in up:
        em, lab = LEVELS.get(u["level"], ("⚪", "ไม่ทราบสถานะ"))
        rise = "" if u.get("rise") is None else f" | {u['rise']:+.2f} ม. ใน {UP_RISE_H} ชม."
        lines.append(f"{em} {html.escape(u['name'])}: {_num(u['wl'])} ม.รทก. ({lab}){rise}")
    return "\n".join(lines)


def fmt_rain(r, err=None):
    if not r:
        return f"🌧 <b>พยากรณ์ฝน</b>\n⚠️ ดึงข้อมูลไม่สำเร็จ: {html.escape(str(err or ''))}"
    lines = ["🌧 <b>พยากรณ์ฝนลุ่มน้ำป่าสักตอนบน</b> (แบบจำลอง Open-Meteo)"]
    for d in r["days"]:
        if d["date"] >= r["today"]:
            lines.append(f"{d['date']}: สูงสุด {d['max']:.0f} มม. ({html.escape(d['where'])}) เฉลี่ย {d['avg']:.0f}")
    return "\n".join(lines)


def write_site(stations, dam, now, dam_err=None, rama6=None, rama6_err=None, extra=None):''')

s = rep(s, '"stations": stations},', '"stations": stations, **(extra or {})},')
s = rep(s, r'''print("\nพระราม 6:", fetch_rama6())''', r'''print("\nพระราม 6:", fetch_rama6())
        print("\nต้นน้ำ:", fetch_upstream())
        print("\nฝน:", fetch_rain())''')
s = rep(s, r'''    r6, r6_err = fetch_rama6()''', r'''    r6, r6_err = fetch_rama6()
    up, up_err = fetch_upstream()
    rain, rain_err = fetch_rain()''')
s = rep(s, r'''for k in ("hist", "flags", "dam", "rama6"):''', r'''for k in ("hist", "flags", "dam", "rama6", "up", "rain"):''')
s = rep(s, r'''    write_site(stations, dam, now, dam_err, r6, r6_err)''', r'''    for u in up:
        us = state["up"].setdefault(u["id"], {})
        h = us.get("hist", [])
        if u["ts"] and u["wl"] is not None and (not h or h[-1][0] != u["ts"]):
            h.append([u["ts"], u["wl"]])
        h = [p for p in h if p[0] >= now - 24 * 3600]
        us["hist"] = h
        win = [p for p in h if p[0] >= now - UP_RISE_H * 3600]
        u["rise"] = round(u["wl"] - win[0][1], 2) if u["wl"] is not None and len(win) >= 2 else None

    write_site(stations, dam, now, dam_err, r6, r6_err,
               extra={"upstream": up, "upstream_error": up_err, "rain": rain, "rain_error": rain_err})''')
s = rep(s, r'''+ "\n\n" + fmt_dam(dam, dam_err) + "\n\n" + fmt_rama6(r6, r6_err))''',
        r'''+ "\n\n" + fmt_dam(dam, dam_err) + "\n\n" + fmt_rama6(r6, r6_err)
                      + "\n\n" + fmt_upstream(up, up_err) + "\n\n" + fmt_rain(rain, rain_err))''')

tail = '    save_state(state)\n    print(f"ตรวจ'
i = s.rindex(tail)
block = r'''    for u in up:
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
                f"{d['date']}: สูงสุด {d['max']:.0f} มม. ({html.escape(d['where'])}) เฉลี่ย {d['avg']:.0f}" for d, _ in hot)
                + "\n\nเป็นค่าจากแบบจำลองพยากรณ์ ผลต่อระดับน้ำขึ้นกับความชื้นดินและการบริหารเขื่อน" + foot)
'''
s = s[:i] + block + s[i:]
open(P, "w", encoding="utf-8").write(s)
print("แก้บอตเสร็จ (สำรอง", P + ".bak2)")

# ---------------- หน้าเว็บ ----------------
H = next((p for p in ("index.html", "site/index.html") if os.path.exists(p)), None)
if not H:
    print("ไม่พบ index.html (ข้ามส่วนหน้าเว็บ)")
else:
    h = load(H); shutil.copy(H, H + ".bak2")
    h = rep(h, "async function load(){", r'''function upCard(us){
  const rows=us.map(s=>{const c=C[s.level]||"#999";return`<div style="margin:6px 0"><b>${esc(s.name)}</b> <span class="tag" style="--c:${c}">${L[s.level]||"?"}</span><div class="mut">${esc(s.loc)} · ${f(s.wl)} ม.รทก. · ${s.rise==null?"-":(s.rise>0?"+":"")+f(s.rise)} ม./6 ชม. · ถึงเขื่อนป่าสักฯ ${esc(s.lag)} (ประมาณ)</div></div>`}).join("");
  return`<div class="card" style="--c:${C[4]}"><h2>⛰ ต้นน้ำป่าสัก (เพชรบูรณ์)</h2>${rows}</div>`;
}
function rainCard(r){
  const days=(r.days||[]).filter(x=>x.date>=r.today),mx=Math.max(0,...days.map(x=>x.max));
  const c=mx>=90?C[5]:mx>=35?"#bc4c00":mx>=10?C[4]:C[3];
  return`<div class="card" style="--c:${c}"><h2>🌧 พยากรณ์ฝน ลุ่มน้ำป่าสักตอนบน</h2>${days.map(x=>`<div class="mut">${esc(x.date)}: <b>${f(x.max,0)}</b> มม. (${esc(x.where)}) · เฉลี่ย ${f(x.avg,0)}</div>`).join("")}<div class="mut">จากแบบจำลอง Open-Meteo ไม่ใช่ประกาศกรมอุตุฯ</div></div>`;
}
async function load(){''')
    h = rep(h, '(has6?rama6Card(d.rama6,d.rama6_error):"");',
            '(has6?rama6Card(d.rama6,d.rama6_error):"")+(d.upstream&&d.upstream.length?upCard(d.upstream):"")+(d.rain?rainCard(d.rain):"");')
    open(H, "w", encoding="utf-8").write(h)
    print("แก้หน้าเว็บเสร็จ (สำรอง", H + ".bak2)")
