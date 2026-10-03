#!/usr/bin/env python3
"""รันที่โฟลเดอร์ที่มี saraburi_water_bot.py และ index.html (หรือ site/index.html): python3 patch_map.py
เพิ่มแผนที่ (Leaflet + OpenStreetMap) แสดงสถานีสระบุรี ต้นน้ำเพชรบูรณ์ และท้ายเขื่อนพระราม 6
ต้องรัน patch_bot.py และ patch_bot2.py มาก่อน  สำรองไฟล์เป็น .bak3"""
import os, shutil, sys

def rep(s, old, new, n=1):
    if s.count(old) != n:
        sys.exit(f"ไม่พบข้อความที่จะแก้ (พบ {s.count(old)} ครั้ง ต้องการ {n}):\n{old[:90]}")
    return s.replace(old, new)

# --- บอต: เก็บพิกัดสถานีลง data.json ---
P = "saraburi_water_bot.py"
s = open(P, encoding="utf-8").read(); shutil.copy(P, P + ".bak3")
s = rep(s, '''        "prev": to_float(dig(rec, "waterlevel_msl_previous")),
''', '''        "prev": to_float(dig(rec, "waterlevel_msl_previous")),
        "lat": to_float(dig(rec, "station.tele_station_lat")),
        "lon": to_float(dig(rec, "station.tele_station_long")),
''')
open(P, "w", encoding="utf-8").write(s)
print("แก้บอตเสร็จ (สำรอง", P + ".bak3)")

# --- หน้าเว็บ ---
H = next((p for p in ("index.html", "site/index.html") if os.path.exists(p)), None)
if not H:
    sys.exit("ไม่พบ index.html")
h = open(H, encoding="utf-8").read(); shutil.copy(H, H + ".bak3")
h = rep(h, "</style>", '</style>\n<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">')
h = rep(h, '<div class="grid" id="g"></div>',
        '<div id="map" style="height:380px;border:1px solid var(--bd);border-radius:8px;margin-bottom:6px"></div>\n'
        '<div class="mut" style="margin-bottom:12px">สีตามระดับน้ำ: เขียว=ปกติ น้ำเงิน=น้ำมาก แดง=ล้นตลิ่ง · จุดใหญ่=ท้ายเขื่อนพระราม 6 · แผนที่ © OpenStreetMap</div>\n'
        '<div class="grid" id="g"></div>')
h = rep(h, "<script>\nconst C=", '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>\n<script>\nconst C=')
# Leaflet ใช้ตัวแปรสากล L เหมือนหน้านี้ จึงเปลี่ยนชื่อของหน้าเป็น LBL
h = rep(h, "const L={", "const LBL={")
h = rep(h, "L[s.level]", "LBL[s.level]", 2)
h = rep(h, "async function load(){", r'''let MAP,LAYER,FIT=false;
const DAM_POS=null; // ใส่ [ละติจูด, ลองจิจูด] ของเขื่อนป่าสักชลสิทธิ์ เมื่อทราบพิกัดที่ถูกต้อง
function drawMap(d){
  const LF=window.L;if(!LF||!document.getElementById("map"))return;
  if(!MAP){MAP=LF.map("map",{scrollWheelZoom:false});LF.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:17,attribution:"© OpenStreetMap"}).addTo(MAP);LAYER=LF.layerGroup().addTo(MAP);}
  LAYER.clearLayers();const pts=[];
  const add=(lat,lon,c,r,html)=>{if(lat==null||lon==null)return;pts.push([lat,lon]);LF.circleMarker([lat,lon],{radius:r,color:"#fff",weight:2,fillColor:c,fillOpacity:.9}).bindPopup(html).addTo(LAYER);};
  d.stations.forEach(s=>add(s.lat,s.lon,C[s.level]||"#999",8,`<b>${esc(s.name)}</b><br>${f(s.wl)} ม.รทก. · ${LBL[s.level]||"ไม่ทราบ"}`));
  (d.upstream||[]).forEach(s=>add(s.lat,s.lon,C[s.level]||"#999",8,`<b>${esc(s.name)}</b> (ต้นน้ำ)<br>${f(s.wl)} ม.รทก. · ${LBL[s.level]||"ไม่ทราบ"}`));
  const r=d.rama6;if(r)add(r.lat,r.lon,C[r.level]||"#999",13,`<b>ท้ายเขื่อนพระราม 6</b><br>${f(r.discharge,0)} ลบ.ม./วิ · ${f(r.wl)} ม.รทก.`);
  if(DAM_POS&&d.dam)add(DAM_POS[0],DAM_POS[1],d.dam.pct>=100?C[5]:d.dam.pct>=90?"#bc4c00":C[3],13,`<b>${esc(d.dam.name)}</b><br>${f(d.dam.pct,0)}% ของความจุเก็บกัก`);
  if(pts.length&&!FIT){MAP.fitBounds(pts,{padding:[30,30]});FIT=true;}
}
async function load(){''')
h = rep(h, '(d.rain?rainCard(d.rain):"");', '(d.rain?rainCard(d.rain):"");\n    try{drawMap(d)}catch(e){console.warn(e)}')
open(H, "w", encoding="utf-8").write(h)
print("แก้หน้าเว็บเสร็จ (สำรอง", H + ".bak3)")
