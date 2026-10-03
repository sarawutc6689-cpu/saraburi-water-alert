#!/usr/bin/env python3
"""รันที่โฟลเดอร์ที่มี saraburi_water_bot.py: python3 patch_bot.py
แก้ไฟล์เดิมในที่ (สำรองเป็น saraburi_water_bot.py.bak) เพื่อเพิ่ม
1) เตือนเขื่อนป่าสักฯ หลายระดับ (90/100/110%) + เตือนเมื่ออัตราระบายเพิ่มขึ้น
2) สถานีท้ายเขื่อนพระราม 6 (ThaiWater id 2624): ระดับเตือนตามอัตราไหล/ระดับน้ำ + เตือนเมื่อไหลเพิ่มเร็ว
"""
import shutil, sys

P = "saraburi_water_bot.py"
s = open(P, encoding="utf-8").read()
shutil.copy(P, P + ".bak")

def rep(old, new, count=1):
    global s
    if s.count(old) != count:
        sys.exit(f"ไม่พบข้อความที่จะแก้ (พบ {s.count(old)} ครั้ง):\n{old[:80]}")
    s = s.replace(old, new)

# 1) ค่าคงที่
rep('''DAM_PCT = 90                  # เตือนเมื่อเขื่อนป่าสักฯ >= 90% ของความจุ
''', '''DAM_LEVELS = [                # (เกณฑ์ % ของความจุเก็บกัก, ไอคอน, ป้ายระดับ) เรียงน้อย→มาก
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
''')

# 2) ฟังก์ชันช่วย + ดึง/จัดรูปแบบพระราม 6 (วางก่อน write_site)
rep('''def write_site(stations, dam, now, dam_err=None):''', '''def next_level(value, levels, prev, rearm):
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
        return f"🚧 <b>เขื่อนพระราม 6</b>\\n⚠️ ดึงข้อมูลไม่สำเร็จ: {html.escape(str(err or ''))}"
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
    return "\\n".join(lines)


def write_site(stations, dam, now, dam_err=None, rama6=None, rama6_err=None):''')

rep('''"dam": dam, "dam_error": dam_err, "stations": stations},''',
    '''"dam": dam, "dam_error": dam_err,
                   "rama6": rama6, "rama6_error": rama6_err, "stations": stations},''')

# 3) debug
rep('''        print("\\nเขื่อนป่าสักฯ:", fetch_dam())''',
    '''        print("\\nเขื่อนป่าสักฯ:", fetch_dam())
        print("\\nพระราม 6:", fetch_rama6())''')

# 4) main: ดึงข้อมูล + state
rep('''    (dam, dam_err), now = fetch_dam(), time.time()''',
    '''    (dam, dam_err), now = fetch_dam(), time.time()
    r6, r6_err = fetch_rama6()''')
rep('''for k in ("hist", "flags", "dam"):''', '''for k in ("hist", "flags", "dam", "rama6"):''')
rep('''    write_site(stations, dam, now, dam_err)''', '''    write_site(stations, dam, now, dam_err, r6, r6_err)''')

# 5) สรุปประจำวัน
rep('''                      + "\\n\\n" + fmt_dam(dam, dam_err))''',
    '''                      + "\\n\\n" + fmt_dam(dam, dam_err) + "\\n\\n" + fmt_rama6(r6, r6_err))''')

# 6) บล็อกเตือนเขื่อนเดิม -> หลายระดับ + อัตราระบาย + พระราม 6
old_start = '''    if dam and dam["pct"] is not None:
        hi = dam["pct"] >= DAM_PCT'''
i = s.index(old_start)
j = s.index("    save_state(state)\n    print(f\"ตรวจ", i)
new_block = '''    if dam and dam["pct"] is not None:
        ds = state["dam"]
        prev = ds.get("lvl", 1 if ds.get("hi") else 0)   # รองรับ state เก่าที่เก็บแค่ hi
        cur = next_level(dam["pct"], DAM_LEVELS, prev, DAM_REARM)
        if cur > prev:
            th_, em, label = DAM_LEVELS[cur - 1]
            send_telegram(f"{em} <b>เขื่อนป่าสักฯ ระดับเตือน: {label}</b> (≥{th_}% ของความจุ)\\n\\n"
                          + fmt_dam(dam) + foot)
        elif cur < prev:
            low = DAM_LEVELS[cur - 1][2] if cur else "ต่ำกว่าเกณฑ์เฝ้าระวัง"
            send_telegram(f"✅ <b>เขื่อนป่าสักฯ ลดลงสู่ระดับ: {low}</b>\\n\\n" + fmt_dam(dam))
        ds["lvl"] = cur
        ds.pop("hi", None)
    if dam and dam["outflow"] is not None and dam["date"]:
        h = state["dam"].setdefault("hist", [])      # [[วันที่ข้อมูล, เวลาที่บันทึก, ระบาย]]
        if not h or h[-1][0] != dam["date"]:         # RID อัปเดตเป็นรอบ จึงเทียบเฉพาะเมื่อวันที่ข้อมูลเปลี่ยน
            if h and h[-1][2] and h[-1][2] > 0 and dam["outflow"] >= DAM_OUT_MIN:
                pct = (dam["outflow"] - h[-1][2]) / h[-1][2] * 100
                if pct >= DAM_OUT_RISE_PCT:
                    send_telegram(f"📈 <b>เขื่อนป่าสักฯ เพิ่มการระบายน้ำ</b> (+{pct:.0f}% จาก {h[-1][2]:.2f} → {dam['outflow']:.2f}) "
                                  "ท้ายน้ำ (ท่าเรือ/นครหลวง/อยุธยา) อาจได้รับผลกระทบ\\n\\n" + fmt_dam(dam) + foot)
            h.append([dam["date"], now, dam["outflow"]])
            state["dam"]["hist"] = h[-10:]
    if r6 and r6["discharge"] is not None:
        rs = state["rama6"]
        wl_lvl = 3 if (r6["crit"] is not None and r6["wl"] is not None and r6["wl"] >= r6["crit"]) else 0
        prev = rs.get("lvl", 0)
        cur = max(next_level(r6["discharge"], RAMA6_Q_LEVELS, prev, RAMA6_REARM), wl_lvl)
        if cur > prev:
            em, label = RAMA6_Q_LEVELS[cur - 1][1:]
            note = "\\n⚠️ ระดับน้ำถึงเกณฑ์วิกฤตของสถานี" if wl_lvl else ""
            send_telegram(f"{em} <b>เขื่อนพระราม 6: {label}</b>\\n\\n" + fmt_rama6(r6) + note + foot)
        elif cur < prev:
            low = RAMA6_Q_LEVELS[cur - 1][2] if cur else "ต่ำกว่าเกณฑ์เฝ้าระวัง"
            send_telegram(f"✅ <b>เขื่อนพระราม 6 ลดลงสู่ระดับ: {low}</b>\\n\\n" + fmt_rama6(r6))
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
                send_telegram(f"📈 <b>เขื่อนพระราม 6 เพิ่มการระบายน้ำเร็ว</b> (+{rise:.0f} ลบ.ม./วิ ใน {RAMA6_RISE_H} ชม.)\\n\\n"
                              + fmt_rama6(r6) + foot)
            elif rise < RAMA6_Q_RISE / 2:
                rs["rise"] = False
'''
s = s[:i] + new_block + s[j:]
open(P, "w", encoding="utf-8").write(s)
print("แก้ไขเสร็จ (สำรองไว้ที่", P + ".bak)")
