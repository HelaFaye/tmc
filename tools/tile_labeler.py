#!/usr/bin/env python3
"""tile_labeler.py -- label and relabel tiles in a browser.

Runs a small web server on this machine (127.0.0.1 only) over your own
room dumps and opens a page where you:

  * pick a room (listed by Picori's area and room names),
  * click a cell, or drag a rectangle, to select cells,
  * see what the game and Picori say about them: tile type and its name,
    the special (object) tile and its note, the surface action, the
    collision, the game code that refers to the tile, the terrain class,
    the height, and the family tileid gives it (and whether a person set it),
  * set a family, a height, and/or a label, for the selected cells, or for
    every cell of that tile type in the room, the area or the whole game,
  * see and delete the rules already written for the room,
  * pick a tile type from the room's list to highlight all its cells.

Every change is a line appended to vr/tiles/overrides.txt (see tileid.py
for the format): room numbers, coordinates and tile type numbers, nothing
from the ROM, so the file is committed and shared. The room art is served
to your browser from your dumps and never written anywhere.

Starting point: the families tileid gives before any override are seeded
from Picori's own names (picori_labels.py) -- CUT_BUSH, ROCK, PERMA_ROCK,
SIGNPOST, Pots, Boulder -- and from the game's collision and actions.

Usage:
  python3 tools/tile_labeler.py states/p0            # then open the URL
  python3 tools/tile_labeler.py states/p0 --port 8800 --room 03_01
"""
import argparse
import io
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import picori_labels as PL  # noqa: E402
import room_explore as RE  # noqa: E402
import tileid as TI  # noqa: E402
import tilevox as TV  # noqa: E402

FAMILIES = sorted(set(TI.PROP_FAMILIES) | set(TI.UPRIGHT_FAMILIES) | {"foliage", "flowers"})
CLASS_CODE = {RE.CLASS_VOID: "void", RE.CLASS_GROUND: "ground", RE.CLASS_WATER: "water",
              RE.CLASS_WALL: "wall", RE.CLASS_LEDGE: "ledge", RE.CLASS_HOLE: "hole"}


class Labeler:
    """The dumps, the overrides file, and a cache of computed rooms."""

    def __init__(self, dumps, overrides):
        self.dumps = Path(dumps)
        self.overrides = Path(overrides)
        self.lock = threading.Lock()
        self.cache = {}
        self.art = {}

    # ------------------------------------------------------------ rooms --
    def rooms(self):
        out = []
        for f in sorted(self.dumps.glob("room_*.tmcr")):
            try:
                a, rm = (int(x) for x in f.stem.split("_")[1:3])
            except ValueError:
                continue
            out.append(dict(id=f"{a:02d}_{rm:02d}", area=a, room=rm,
                            area_name=PL.areas().get(a, f"AREA_{a}"),
                            name=PL.room_name(a, rm)))
        return out

    def _load(self, rid):
        r = RE.load_room(self.dumps / f"room_{rid}.tmcr")
        art = RE.room_art_rgb(r, 0)
        if art is None or not r.cells_w:
            raise ValueError(f"room {rid} has no art")
        return r, np.ascontiguousarray(art[:, :, :3].astype(np.uint8))

    def room(self, rid):
        with self.lock:
            if rid in self.cache:
                return self.cache[rid]
            path = str(self.overrides)
            TI._overrides_cache.pop(path, None)
            f = self.dumps / f"room_{rid}.tmcr"
            r, art = self._load(rid)
            cls, H, bl, _fl, _doors = TV.room_heights(r, 0, path=str(f))
            fam, _floor = TI.families(r, cls, H, art)
            role = TV.room_roles(r, cls, H, bl, art)
            # what tileid says without anybody's overrides
            saved = TI._overrides_cache.get(path)
            TI._overrides_cache[path] = []
            try:
                cls0, H0, *_ = TV.room_heights(r, 0, path=str(f))
                fam0, _ = TI.families(r, cls0, H0, art)
            finally:
                if saved is None:
                    TI._overrides_cache.pop(path, None)
                else:
                    TI._overrides_cache[path] = saved
            L = r.layers[0]
            h, w = r.cells_h, r.cells_w
            t = L["tile"][:h, :w].astype(int)
            tt = L["tiletype"][np.clip(t, 0, len(L["tiletype"]) - 1)].astype(int)
            tt = np.where(t >= 0x4000, -1, tt)
            coll = L["collision"][:h, :w].astype(int)
            act = L["act"][:h, :w].astype(int)
            rules = []
            touched = np.zeros((h, w), bool)
            labels = np.full((h, w), "", dtype=object)
            for rule in TI.load_overrides(path):
                m = TI.override_mask(r, rule)
                if m.any():
                    touched |= m
                    if "label" in rule:
                        labels[m] = rule["label"]
                    rules.append(dict(line=rule["line"], text=self._line(rule["line"]),
                                      cells=int(m.sum())))
            # everything Picori says, per distinct value in the room
            tt_refs, sp_refs = PL.code_refs()
            types = {}
            for v in np.unique(tt[tt >= 0]).tolist():
                types[f"t{v}"] = dict(label=f"type 0x{v:x}", name=PL.tile_types().get(v, ""),
                                      code=", ".join(tt_refs.get(v, [])),
                                      count=int((tt == v).sum()))
            for v in np.unique(t[t >= 0x4000]).tolist():
                types[f"s{v}"] = dict(label=f"special 0x{v:x}", name=PL.special_tiles().get(v, ""),
                                      code=", ".join(sp_refs.get(v, [])),
                                      count=int((t == v).sum()))
            acts = {int(v): (PL.act_short(int(v)) or "") for v in np.unique(act).tolist()}
            data = dict(
                id=rid, w=w, h=h, name=PL.room_name(r.area, r.room),
                area=r.area, room=r.room,
                area_name=PL.areas().get(r.area, ""),
                tile=t.tolist(), tt=tt.tolist(), coll=coll.tolist(), act=act.tolist(),
                cls=[[CLASS_CODE.get(c, str(c)) for c in row] for row in cls[:h, :w]],
                height=np.asarray(H)[:h, :w].astype(int).tolist(),
                height_auto=np.asarray(H0)[:h, :w].astype(int).tolist(),
                family=[[x or "" for x in row] for row in fam[:h, :w]],
                family_auto=[[x or "" for x in row] for row in fam0[:h, :w]],
                role=[[x or "" for x in row] for row in role[:h, :w]],
                touched=touched.tolist(), labels=labels.tolist(), rules=rules, types=types,
                acts={str(k): v for k, v in acts.items()}, families=FAMILIES)
            self.cache[rid] = data
            return data

    def art_png(self, rid):
        with self.lock:
            if rid not in self.art:
                from PIL import Image
                r, art = self._load(rid)
                buf = io.BytesIO()
                Image.fromarray(art[:r.cells_h * 16, :r.cells_w * 16]).save(buf, "PNG")
                self.art[rid] = buf.getvalue()
            return self.art[rid]

    # -------------------------------------------------------- overrides --
    def _lines(self):
        return self.overrides.read_text().splitlines() if self.overrides.is_file() else []

    def _line(self, n):
        lines = self._lines()
        return lines[n - 1] if 0 < n <= len(lines) else ""

    def _changed(self):
        self.cache.clear()
        TI._overrides_cache.clear()

    def add(self, req):
        """Append one rule, from the page's form; returns its text."""
        rid = req["room"]
        a, rm = (int(x) for x in rid.split("_"))
        scope = req.get("scope", "cells")
        if scope == "cells":
            x0, y0, x1, y1 = (int(v) for v in req["rect"])
            where = f"{x0},{y0}" if (x0, y0) == (x1, y1) else f"{x0},{y0}-{x1},{y1}"
            who = rid
        else:
            key = "special" if req.get("special") else "type"
            where = f"{key}=0x{int(req['value']):x}"
            who = {"room": rid, "area": f"a{a}", "game": "*"}[scope]
        what = []
        fam = req.get("family", "")
        if fam:
            if fam != "-" and fam not in FAMILIES:
                raise ValueError(f"unknown family {fam}")
            what.append(f"family={fam}")
        if str(req.get("height", "")).strip() != "":
            what.append(f"height={int(req['height'])}")
        label = "_".join(str(req.get("label", "")).split())
        if label:
            what.append(f"label={label}")
        if not what:
            raise ValueError("nothing to set: pick a family, a height or a label")
        line = f"{who:<6} {where:<22} {' '.join(what)}"
        note = str(req.get("note", "")).strip().replace("\n", " ")
        with self.lock:
            lines = self._lines()
            if note:
                lines.append(f"# {note}")
            lines.append(line)
            self.overrides.parent.mkdir(parents=True, exist_ok=True)
            self.overrides.write_text("\n".join(lines) + "\n")
            self._changed()
        return line

    def delete(self, n, text):
        with self.lock:
            lines = self._lines()
            if not (0 < n <= len(lines)) or lines[n - 1] != text:
                raise ValueError("the file changed; reload the room")
            del lines[n - 1]
            self.overrides.write_text("\n".join(lines) + "\n")
            self._changed()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tile Labeler</title>
<style>
:root { --bg:#16181d; --panel:#1f232b; --line:#2e333d; --text:#e6e6e6; --dim:#9aa3ad;
        --accent:#ffcc55; --sel:#4fc3ff; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:13px/1.4 system-ui, sans-serif;
       display:grid; grid-template-columns: 260px 1fr 340px; height:100vh; }
#rooms, #side { background:var(--panel); overflow:auto; padding:10px; }
#rooms { border-right:1px solid var(--line); }
#side { border-left:1px solid var(--line); }
#rooms input { width:100%; padding:6px; background:var(--bg); color:var(--text);
               border:1px solid var(--line); border-radius:4px; }
.area { color:var(--dim); margin:10px 0 2px; font-size:11px; text-transform:uppercase; }
.room { padding:3px 6px; border-radius:4px; cursor:pointer; white-space:nowrap;
        overflow:hidden; text-overflow:ellipsis; }
.room:hover { background:var(--line); } .room.on { background:#394150; color:var(--accent); }
#main { overflow:auto; position:relative; }
#bar { position:sticky; top:0; left:0; background:var(--bg); padding:8px 10px; z-index:2;
       display:flex; gap:12px; align-items:center; border-bottom:1px solid var(--line); flex-wrap:wrap; }
#bar b { color:var(--accent); }
canvas { display:block; image-rendering:pixelated; cursor:crosshair; margin:10px; }
h3 { margin:14px 0 6px; font-size:12px; color:var(--accent); text-transform:uppercase; }
table { border-collapse:collapse; width:100%; }
td { padding:2px 4px; vertical-align:top; border-bottom:1px solid var(--line); }
td:first-child { color:var(--dim); white-space:nowrap; width:1%; }
.code { color:var(--dim); font-size:11px; }
label { display:block; margin:6px 0 2px; color:var(--dim); }
select, input[type=text], input[type=number], textarea { width:100%; padding:5px; background:var(--bg);
       color:var(--text); border:1px solid var(--line); border-radius:4px; font:inherit; }
button { background:#394150; color:var(--text); border:1px solid var(--line); border-radius:4px;
         padding:6px 10px; cursor:pointer; font:inherit; }
button.go { background:#2f6d3a; } button:hover { filter:brightness(1.2); }
.rule { display:flex; gap:6px; align-items:center; font-family:monospace; font-size:11px;
        padding:3px 0; border-bottom:1px solid var(--line); }
.rule span { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.type { cursor:pointer; padding:2px 4px; border-radius:3px; display:flex; gap:6px; }
.type:hover { background:var(--line); } .type.on { background:#394150; }
.type .n { color:var(--dim); margin-left:auto; }
.chip { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; }
#msg { color:var(--accent); min-height:1.4em; }
.scopes label { display:inline-block; margin-right:10px; color:var(--text); }
</style></head><body>
<div id="rooms"><input id="filter" placeholder="Filter rooms (name or AA_RR)"><div id="roomlist"></div></div>
<div id="main">
  <div id="bar"><span id="title">Pick a room</span>
    <span>zoom <input id="zoom" type="range" min="1" max="6" value="3"></span>
    <span><input type="checkbox" id="showfam" checked> families</span>
    <span><input type="checkbox" id="showgrid" checked> grid</span>
    <span><input type="checkbox" id="showover" checked> overrides</span>
    <span id="hover" class="code"></span></div>
  <canvas id="cv"></canvas>
</div>
<div id="side">
  <div id="msg"></div>
  <h3>Selection</h3><div id="info" class="code">Click a cell, or drag a rectangle. Shift-click extends.</div>
  <h3>Label</h3>
  <div class="scopes" id="scopes">
    <label><input type="radio" name="scope" value="cells" checked> selected cells</label>
    <label><input type="radio" name="scope" value="room"> this tile in the room</label>
    <label><input type="radio" name="scope" value="area"> ... in the area</label>
    <label><input type="radio" name="scope" value="game"> ... in the game</label>
  </div>
  <label>Family</label><select id="family"></select>
  <label>Height (px, blank = leave)</label><input id="height" type="number">
  <label>Label (a name for people)</label><input id="label" type="text" placeholder="e.g. stake_fence">
  <label>Note (written as a comment above the rule)</label><input id="note" type="text">
  <p><button class="go" id="apply">Save rule</button></p>
  <h3>Rules for this room</h3><div id="rules" class="code">none</div>
  <h3>Tiles in this room</h3><div id="types"></div>
</div>
<script>
const COL = {bush:'#3cdc3c', sapling:'#78c828', rock:'#aaaaaa', mushroom:'#e63c3c', stump:'#aa6e32',
  planter:'#c896dc', prop:'#f0f050', signpost:'#ffc878', fence:'#ff8c00', foliage:'#00a05a',
  flowers:'#ff78c8', pot:'#d08050'};
let rooms=[], R=null, art=new Image(), sel=null, drag=null, hi=null;
const $=id=>document.getElementById(id), cv=$('cv'), g=cv.getContext('2d');
const msg=(t)=>{ $('msg').textContent=t||''; };
async function api(path, body){ const r=await fetch(path, body?{method:'POST',
  headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}:{});
  const j=await r.json(); if(!r.ok) throw new Error(j.error||r.status); return j; }
function listRooms(){ const f=$('filter').value.toLowerCase(); let html='', last='';
  for(const r of rooms){ const s=(r.id+' '+r.name+' '+r.area_name).toLowerCase(); if(f && !s.includes(f)) continue;
    if(r.area_name!==last){ html+=`<div class="area">${r.area} ${r.area_name.replace('AREA_','')}</div>`; last=r.area_name; }
    html+=`<div class="room${R&&R.id===r.id?' on':''}" data-id="${r.id}" title="${r.name}">${r.id} ${r.name.replace(/^ROOM_/,'').replace(r.area_name.replace('AREA_','')+'_','')}</div>`; }
  $('roomlist').innerHTML=html; }
$('roomlist').onclick=e=>{ const id=e.target.dataset.id; if(id) openRoom(id); };
$('filter').oninput=listRooms;
async function openRoom(id, keep){ msg('computing '+id+' ...');
  try { R=await api('/api/room/'+id); } catch(err){ msg(err.message); return; }
  if(!keep){ sel=null; hi=null; }
  art=new Image(); art.onload=()=>{ draw(); }; art.src='/api/art/'+id+'?'+Date.now();
  $('title').innerHTML=`<b>${R.id}</b> ${R.name}`; listRooms(); fillSide(); msg('');
  history.replaceState(null,'','#'+id); }
function Z(){ return +$('zoom').value; }
function draw(){ if(!R) return; const z=Z(), S=16*z; cv.width=R.w*S; cv.height=R.h*S;
  g.imageSmoothingEnabled=false; g.drawImage(art,0,0,R.w*S,R.h*S);
  for(let y=0;y<R.h;y++) for(let x=0;x<R.w;x++){ const f=R.family[y][x];
    if($('showfam').checked && f){ g.fillStyle=(COL[f]||'#fff')+'44'; g.fillRect(x*S,y*S,S,S);
      g.strokeStyle=COL[f]||'#fff'; g.lineWidth=Math.max(1,z/2); g.strokeRect(x*S+2,y*S+2,S-4,S-4); }
    if($('showover').checked && R.touched[y][x]){ g.fillStyle='#fff'; g.fillRect(x*S+2,y*S+2,Math.max(3,z*2),Math.max(3,z*2)); }
    if(hi && hi(x,y)){ g.strokeStyle='#ff00ff'; g.lineWidth=2; g.strokeRect(x*S+1,y*S+1,S-2,S-2); } }
  if($('showgrid').checked){ g.strokeStyle='rgba(0,0,0,.35)'; g.lineWidth=1; g.beginPath();
    for(let x=0;x<=R.w;x++){ g.moveTo(x*S+.5,0); g.lineTo(x*S+.5,R.h*S); }
    for(let y=0;y<=R.h;y++){ g.moveTo(0,y*S+.5); g.lineTo(R.w*S,y*S+.5); } g.stroke(); }
  if(sel){ const [x0,y0,x1,y1]=norm(sel); g.strokeStyle='#4fc3ff'; g.lineWidth=3;
    g.strokeRect(x0*S+1.5,y0*S+1.5,(x1-x0+1)*S-3,(y1-y0+1)*S-3); } }
['zoom','showfam','showgrid','showover'].forEach(id=>$(id).oninput=draw);
function norm(s){ return [Math.min(s[0],s[2]),Math.min(s[1],s[3]),Math.max(s[0],s[2]),Math.max(s[1],s[3])]; }
function cellAt(e){ const b=cv.getBoundingClientRect(), S=16*Z();
  return [Math.max(0,Math.min(R.w-1,Math.floor((e.clientX-b.left)/S))), Math.max(0,Math.min(R.h-1,Math.floor((e.clientY-b.top)/S)))]; }
cv.onmousedown=e=>{ if(!R) return; const [x,y]=cellAt(e);
  if(e.shiftKey && sel){ sel=[sel[0],sel[1],x,y]; } else { sel=[x,y,x,y]; } drag=true; draw(); fillSide(); };
cv.onmousemove=e=>{ if(!R) return; const [x,y]=cellAt(e);
  $('hover').textContent=`${x},${y}  ${R.family[y][x]||R.role[y][x]}  h${R.height[y][x]}`;
  if(drag){ sel=[sel[0],sel[1],x,y]; draw(); } };
window.onmouseup=()=>{ if(drag){ drag=false; fillSide(); } };
function tileKey(x,y){ const t=R.tile[y][x]; return t>=0x4000?'s'+t:'t'+R.tt[y][x]; }
function hex(v){ return '0x'+v.toString(16); }
function fillSide(){ if(!R) return;
  const fam=$('family'); if(!fam.dataset.done){ fam.innerHTML='<option value="">(leave)</option><option value="-">- none (terrain)</option>'+
    R.families.map(f=>`<option>${f}</option>`).join(''); fam.dataset.done=1; }
  if(!sel){ $('info').innerHTML='Click a cell, or drag a rectangle. Shift-click extends.'; }
  else { const [x0,y0,x1,y1]=norm(sel), x=sel[0], y=sel[1], k=tileKey(x,y), ty=R.types[k]||{};
    const n=(x1-x0+1)*(y1-y0+1), fa=R.family_auto[y][x], f=R.family[y][x];
    const same=[]; for(let yy=y0;yy<=y1;yy++) for(let xx=x0;xx<=x1;xx++) same.push(R.family[yy][xx]||R.role[yy][xx]);
    const counts={}; same.forEach(s=>counts[s]=(counts[s]||0)+1);
    $('info').innerHTML=`<table>
      <tr><td>cells</td><td>${x0},${y0}${n>1?` – ${x1},${y1} (${n})`:''}</td></tr>
      ${n>1?`<tr><td>made of</td><td>${Object.entries(counts).map(([k,v])=>k+' '+v).join(', ')}</td></tr>`:''}
      <tr><td>first cell</td><td>${x},${y}</td></tr>
      <tr><td>family</td><td>${f||'—'} ${f!==fa?`<span class="code">(tileid: ${fa||'—'}; set by a rule)</span>`:'<span class="code">(tileid)</span>'}</td></tr>
      ${R.labels[y][x]?`<tr><td>label</td><td><b>${R.labels[y][x]}</b></td></tr>`:''}
      <tr><td>role</td><td>${R.role[y][x]||'—'}</td></tr>
      <tr><td>class</td><td>${R.cls[y][x]}</td></tr>
      <tr><td>height</td><td>${R.height[y][x]} px ${R.height[y][x]!==R.height_auto[y][x]?`<span class="code">(was ${R.height_auto[y][x]})</span>`:''}</td></tr>
      <tr><td>tile</td><td>${ty.label||''} <b>${ty.name||''}</b>${ty.code?`<div class="code">code: ${ty.code}</div>`:''}<div class="code">tile index ${hex(R.tile[y][x])}, ${ty.count} in room</div></td></tr>
      <tr><td>action</td><td>${hex(R.act[y][x])} ${R.acts[R.act[y][x]]||''}</td></tr>
      <tr><td>collision</td><td>${hex(R.coll[y][x])}</td></tr></table>`;
    hi=(xx,yy)=>tileKey(xx,yy)===k && $('scopes').querySelector('input:checked').value!=='cells'; }
  $('rules').innerHTML=R.rules.length?R.rules.map(r=>`<div class="rule"><span title="${r.text}">${r.line}: ${r.text}</span>
      <span class="code" style="flex:0">${r.cells}</span><button data-line="${r.line}" data-text="${encodeURIComponent(r.text)}">✕</button></div>`).join(''):'none';
  const keys=Object.keys(R.types).sort((a,b)=>R.types[b].count-R.types[a].count);
  $('types').innerHTML=keys.map(k=>{ const t=R.types[k]; return `<div class="type" data-k="${k}" title="${t.code?'code: '+t.code:''}">
      <span>${t.label}</span><b>${t.name}</b><span class="n">${t.count}</span></div>`; }).join('');
  draw(); }
$('scopes').onchange=()=>{ fillSide(); };
$('types').onclick=e=>{ const d=e.target.closest('.type'); if(!d) return; const k=d.dataset.k;
  for(let y=0;y<R.h;y++) for(let x=0;x<R.w;x++) if(tileKey(x,y)===k){ sel=[x,y,x,y]; break; }
  document.querySelector('input[name=scope][value=room]').checked=true; fillSide(); };
$('rules').onclick=async e=>{ const b=e.target.closest('button'); if(!b) return;
  if(!confirm('Delete rule on line '+b.dataset.line+'?')) return;
  try { await api('/api/delete',{line:+b.dataset.line, text:decodeURIComponent(b.dataset.text)}); msg('deleted');
    await openRoom(R.id,true); } catch(err){ msg(err.message); } };
$('apply').onclick=async()=>{ if(!R||!sel){ msg('select cells first'); return; }
  const scope=document.querySelector('input[name=scope]:checked').value, x=sel[0], y=sel[1], t=R.tile[y][x];
  const body={room:R.id, scope, rect:norm(sel), special:t>=0x4000, value:t>=0x4000?t:R.tt[y][x],
    family:$('family').value, height:$('height').value, label:$('label').value, note:$('note').value};
  try { const r=await api('/api/add', body); msg('saved: '+r.line); $('note').value='';
    await openRoom(R.id,true); } catch(err){ msg(err.message); } };
document.addEventListener('keydown',e=>{ if(!R||!sel||e.target.tagName==='INPUT') return;
  const d={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key]; if(!d) return;
  e.preventDefault(); const x=Math.max(0,Math.min(R.w-1,sel[0]+d[0])), y=Math.max(0,Math.min(R.h-1,sel[1]+d[1]));
  sel=[x,y,x,y]; fillSide(); });
(async()=>{ rooms=await api('/api/rooms'); listRooms(); const h=location.hash.slice(1)||window.START;
  if(h) openRoom(h); })();
</script></body></html>
"""


def make_handler(lab, start):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/":
                    page = PAGE.replace("window.START", json.dumps(start or ""))
                    return self._send(200, page.encode(), "text/html; charset=utf-8")
                if u.path == "/api/rooms":
                    return self._send(200, lab.rooms())
                if u.path.startswith("/api/room/"):
                    return self._send(200, lab.room(u.path.rsplit("/", 1)[1]))
                if u.path.startswith("/api/art/"):
                    return self._send(200, lab.art_png(u.path.rsplit("/", 1)[1]), "image/png")
                return self._send(404, {"error": "not found"})
            except Exception as e:          # noqa: BLE001 -- shown on the page
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_POST(self):
            u = urlparse(self.path)
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                if u.path == "/api/add":
                    return self._send(200, {"line": lab.add(req)})
                if u.path == "/api/delete":
                    lab.delete(int(req["line"]), req["text"])
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "not found"})
            except Exception as e:          # noqa: BLE001
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})
    _ = parse_qs
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps", help="directory of room_AA_RR.tmcr dumps")
    ap.add_argument("--overrides", default=str(TI.OVERRIDES))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--room", default="", help="room to open first, AA_RR")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    import os
    os.environ["TMC_TILE_OVERRIDES"] = a.overrides
    lab = Labeler(a.dumps, a.overrides)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(lab, a.room))
    url = f"http://127.0.0.1:{a.port}/" + (f"#{a.room}" if a.room else "")
    print(f"tile labeler: {url}  (rules -> {a.overrides}; Ctrl-C to stop)")
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
