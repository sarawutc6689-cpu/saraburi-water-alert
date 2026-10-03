#!/usr/bin/env python3
"""รันที่โฟลเดอร์ที่มี saraburi_water_bot.py และ index.html (หรือ site/index.html): python3 patch_trend.py
เพิ่มกราฟแนวโน้ม 3 กราฟ: ระดับน้ำเทียบตลิ่ง (สระบุรี 48 ชม.), อัตราไหลพระราม 6, ต้นน้ำป่าสัก
ต้องรัน patch_bot.py และ patch_bot2.py มาก่อน  สำรองไฟล์เป็น .bak4"""
import os, shutil, sys

def rep(s, old, new):
    if s.count(old) != 1:
        sys.exit(f"ไม่พบข้อความที่จะแก้ (พบ {s.count(old)} ครั้ง):\n{old[:90]}")
    return s.replace(old, new)

P = "saraburi_water_bot.py"
s = open(P, encoding="utf-8").read(); shutil.copy(P, P + ".bak4")
s = rep(s, '''u["rise"] = round(u["wl"] - win[0][1], 2) if u["wl"] is not None and len(win) >= 2 else None''',
        '''u["rise"] = round(u["wl"] - win[0][1], 2) if u["wl"] is not None and len(win) >= 2 else None
        u["hist"] = h''')
s = rep(s, '''    write_site(stations, dam, now, dam_err, r6, r6_err,''', '''    if r6 and r6["ts"] and r6["discharge"] is not None:
        h6 = state["rama6"].get("hist", [])
        if not h6 or h6[-1][0] != r6["ts"]:
            h6 = h6 + [[r6["ts"], r6["discharge"]]]
        r6["hist"] = h6

    write_site(stations, dam, now, dam_err, r6, r6_err,''')
open(P, "w", encoding="utf-8").write(s)
print("แก้บอตเสร็จ (สำรอง", P + ".bak4)")

H = next((p for p in ("index.html", "site/index.html") if os.path.exists(p)), None)
if not H:
    sys.exit("ไม่พบ index.html")
h = open(H, encoding="utf-8").read(); shutil.copy(H, H + ".bak4")
h = rep(h, "async function load(){", r'''function chartCard(title,series,lines,unit,dec){
  const W=600,H=220,ml=46,mr=10,mt=10,mb=26,all=series.flatMap(s=>s.pts);
  const xs=all.map(p=>p[0]),ys=all.map(p=>p[1]).concat(lines.map(l=>l.y));
  const x0=Math.min(...xs),x1=Math.max(...xs);let y0=Math.min(...ys),y1=Math.max(...ys);
  if(y1==y0){y1+=1;y0-=1}const pd=(y1-y0)*.08;y0-=pd;y1+=pd;
  const X=t=>ml+(t-x0)/((x1-x0)||1)*(W-ml-mr),Y=v=>mt+(1-(v-y0)/(y1-y0))*(H-mt-mb);
  const tm=t=>new Date(t*1000).toLocaleString("th-TH",{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"});
  const tx=(x,y,a,t,fill,op)=>`<text x="${x}" y="${y}" text-anchor="${a}" font-size="11" fill="${fill||"currentColor"}" opacity="${op||.7}">${t}</text>`;
  let g="";
  for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4;g+=`<line x1="${ml}" x2="${W-mr}" y1="${Y(v)}" y2="${Y(v)}" style="stroke:var(--bd)"/>`+tx(ml-4,Y(v)+4,"end",f(v,dec));}
  lines.forEach(l=>{g+=`<line x1="${ml}" x2="${W-mr}" y1="${Y(l.y)}" y2="${Y(l.y)}" stroke="${l.color}" stroke-dasharray="5 4"/>`+tx(W-mr,Y(l.y)-3,"end",esc(l.label),l.color,1);});
  series.forEach(s=>{g+=`<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${s.pts.map(p=>X(p[0]).toFixed(1)+","+Y(p[1]).toFixed(1)).join(" ")}"><title>${esc(s.name)}</title></polyline>`;});
  g+=tx(ml,H-6,"start",tm(x0))+tx(W-mr,H-6,"end",tm(x1));
  const lg=series.map(s=>`<span style="margin-right:12px;white-space:nowrap"><i style="display:inline-block;width:10px;height:10px;background:${s.color};border-radius:2px"></i> ${esc(s.name)} <b>${f(s.pts[s.pts.length-1][1],dec)}</b></span>`).join("");
  return`<div class="card" style="grid-column:1/-1;--c:#999"><h2>📈 ${esc(title)}</h2><svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${g}</svg><div class="mut">${lg} ${esc(unit)}</div></div>`;
}
function trends(d){
  try{
    const pal=["#0969da","#2da44e","#bc4c00","#8250df","#cf222e","#6e7781"],ok=a=>a&&a.length>1;let out="";
    const st=d.stations.filter(s=>s.bank&&ok(s.hist));
    if(st.length)out+=chartCard("ระดับน้ำเทียบตลิ่ง จ.สระบุรี (48 ชม.) · 0 = ระดับตลิ่ง, ค่าบวก = ล้นตลิ่ง",st.map((s,i)=>({name:s.name,color:pal[i%pal.length],pts:s.hist.map(p=>[p[0],p[1]-s.bank])})),[{y:0,label:"ตลิ่ง",color:"#cf222e"}],"ม.",2);
    const r=d.rama6;
    if(r&&ok(r.hist))out+=chartCard("อัตราไหลท้ายเขื่อนพระราม 6 (24 ชม.)",[{name:"อัตราไหล",color:"#0969da",pts:r.hist}],[{y:400,label:"400",color:"#d4a72c"},{y:600,label:"600",color:"#bc4c00"},{y:700,label:"700",color:"#cf222e"}],"ลบ.ม./วิ",0);
    const up=(d.upstream||[]).filter(s=>ok(s.hist));
    if(up.length)out+=chartCard("ต้นน้ำป่าสัก: ระดับน้ำที่เปลี่ยนไปจากจุดเริ่มต้น (24 ชม.)",up.map((s,i)=>({name:s.name,color:pal[i%pal.length],pts:s.hist.map(p=>[p[0],p[1]-s.hist[0][1]])})),[{y:0,label:"",color:"#8b949e"}],"ม.",2);
    return out;
  }catch(e){console.warn(e);return"";}
}
async function load(){''')
h = rep(h, 'upd.textContent="อัปเดตล่าสุด "', 'g.insertAdjacentHTML("beforeend",trends(d));\n    upd.textContent="อัปเดตล่าสุด "')
open(H, "w", encoding="utf-8").write(h)
print("แก้หน้าเว็บเสร็จ (สำรอง", H + ".bak4)")
