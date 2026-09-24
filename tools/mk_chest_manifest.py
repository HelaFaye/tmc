import sys, numpy as np, collections
from pathlib import Path
sys.path.insert(0,'/home/claude/kit/tools')
import shapefit as SF, room_explore as RE, shading as SH
DUMP=Path('/home/claude/v4/vrdump'); SP=Path('/tmp/claude-0/-home-claude/e8ef37b8-7c2a-5d9b-93d5-4efb346de00b/scratchpad')
out=[]; skipped=[]
for p in sorted(DUMP.glob('room_*.tmcr')):
    try: r=RE.load_room(p)
    except Exception: continue
    for li,L in enumerate(r.layers):
        if not L.get('present') or L.get('tile') is None: continue
        tile,tt=L['tile'],L['tiletype']
        idx=np.where(np.isin(tt[np.clip(tile,0,len(tt)-1)],[0x73,0x74]))
        if not len(idx[0]): continue
        d=SH.decompose_room(r,li)
        if d is None: continue
        mat=d[0]; Hm,Wm=mat.shape
        for cy,cx in zip(*idx):
            y0,x0=int(cy)*16,int(cx)*16
            if y0+16>Hm or x0+16>Wm: continue
            cell=mat[y0:y0+16,x0:x0+16]
            # Background is what SURROUNDS the cell: sample the ring of
            # neighbouring cells and drop any material common out there.
            ry0,ry1=max(0,y0-16),min(Hm,y0+32); rx0,rx1=max(0,x0-16),min(Wm,x0+32)
            ring=mat[ry0:ry1,rx0:rx1].copy().astype(int)
            ring[y0-ry0:y0-ry0+16, x0-rx0:x0-rx0+16]=-1
            # Blank any NEIGHBOURING cell that is itself a chest. Chests come
            # in packed grids, so without this the ring is mostly other
            # chests and the chest's own materials get called background --
            # which is what made 202 real, plainly drawn chests look empty.
            for ncy,ncx in zip(*idx):
                if (ncx,ncy)==(cx,cy): continue
                ay,ax=int(ncy)*16-ry0, int(ncx)*16-rx0
                if -16<ay<ring.shape[0] and -16<ax<ring.shape[1]:
                    ring[max(0,ay):ay+16, max(0,ax):ax+16]=-1
            rc=collections.Counter(ring[ring>=0].ravel().tolist()); rn=max(1,sum(rc.values()))
            bg={m for m,c in rc.items() if c/rn>=0.15}
            want=[int(m) for m,c in collections.Counter(cell.ravel().tolist()).items()
                  if int(m) not in bg and c>=6 and int(m)>=0]
            if not want: skipped.append((p.name,cx,cy,'all materials are background')); continue
            m=SF.largest_blob(np.isin(cell,want))
            if m.sum()<90: skipped.append((p.name,int(cx),int(cy),f'blob {int(m.sum())}px')); continue
            ys,xs=np.where(m)
            if (ys.max()-ys.min()+1)<10 or (xs.max()-xs.min()+1)<10:
                skipped.append((p.name,int(cx),int(cy),'blob not chest-shaped')); continue
            out.append(f"{p.name}  {cx},{cy},{cx+1},{cy+1}  chest  "
                       f"materials={','.join(str(v) for v in sorted(want))} layer={li}"
                       f"   # CHEST cell {cx},{cy}")
hdr=["# Chest manifest, one line per cell whose TILE TYPE is 0x73/0x74 (tiles.h",
     "# calls these CHEST / CHEST_OPEN). One line per CELL, not per clump: the",
     "# old clump rects were padded bounding boxes that often pointed a whole",
     "# cell away from the chest, which is what made 37 lines fit a stripe of",
     "# background. Materials are resolved per cell by dropping whatever also",
     "# surrounds it, because material ids are per-area.",
     f"# {len(out)} fittable, {len(skipped)} skipped (see chests_skipped.txt).",
     "# room              cells        mode     options"]
(SP/'chests_v2.scene').write_text("\n".join(hdr+out)+"\n")
(SP/'chests_skipped.txt').write_text("\n".join(f"{a} cell {b},{c}: {d}" for a,b,c,d in skipped)+"\n")
print(f"fittable {len(out)}, skipped {len(skipped)}")
print("skip reasons:", collections.Counter(s[3].split()[0] for s in skipped).most_common())
