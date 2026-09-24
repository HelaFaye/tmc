import sys, numpy as np, collections
from pathlib import Path
sys.path.insert(0,'/home/claude/kit/tools')
import room_explore as RE, shading as SH
DUMP=Path('/home/claude/v4/vrdump')
rows=[]
for p in sorted(DUMP.glob('room_*.tmcr'))[:80]:
    try: r=RE.load_room(p)
    except Exception: continue
    L=r.layers[0]
    if not L.get('present') or L.get('collision') is None: continue
    d=SH.decompose_room(r,0)
    if d is None: continue
    mat,shd=d[0],d[1]
    coll=np.asarray(L['collision'])
    H,W=shd.shape
    ch,cw=coll.shape if coll.ndim==2 else (0,0)
    if ch==0: continue
    # collision is per CELL; average shade per cell and compare
    cy=min(ch,H//16); cx=min(cw,W//16)
    if cy<4 or cx<4: continue
    blocked=[]; open_=[]
    for y in range(cy):
        for x in range(cx):
            sh=shd[y*16:(y+1)*16, x*16:(x+1)*16]
            mm=mat[y*16:(y+1)*16, x*16:(x+1)*16]
            v=sh[mm>=0]
            if len(v)<64: continue
            (blocked if coll[y,x]!=0 else open_).append(float(v.mean()))
    if len(blocked)>20 and len(open_)>20:
        rows.append((p.name, np.mean(blocked), np.mean(open_), len(blocked), len(open_)))
print(f"rooms with both blocked and open cells: {len(rows)}")
if rows:
    b=np.array([r[1] for r in rows]); o=np.array([r[2] for r in rows])
    d=b-o
    print(f"  mean shade, BLOCKED cells (walls/solid): {b.mean():.3f}")
    print(f"  mean shade, OPEN cells (walkable floor): {o.mean():.3f}")
    print(f"  blocked - open: mean {d.mean():+.3f}  median {np.median(d):+.3f}  std {d.std():.3f}")
    print(f"  fraction of rooms where BLOCKED is darker: {100*(d<0).mean():.1f}%")
    print("\n  sample:")
    for n,bb,oo,cb,co in rows[:8]:
        print(f"    {n:22s} blocked {bb:.2f} ({cb:4d} cells)  open {oo:.2f} ({co:4d})  diff {bb-oo:+.2f}")
