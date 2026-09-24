import sys, numpy as np, collections
from pathlib import Path
sys.path.insert(0,'/home/claude/kit/tools')
import room_explore as RE, shading as SH
DUMP=Path('/home/claude/v4/vrdump')
rooms=sorted(DUMP.glob('room_*.tmcr'))[:120]
# Q1: for one material in one room, does shade vary at all?
varies=0; flat=0; spread=collections.Counter()
# Q2: does shade differ between layer 0 and layer 1 for the SAME material?
bylayer=collections.defaultdict(lambda: collections.defaultdict(list))
for p in rooms:
    try: r=RE.load_room(p)
    except Exception: continue
    for li,L in enumerate(r.layers):
        if not L.get('present') or L.get('tile') is None: continue
        d=SH.decompose_room(r,li)
        if d is None: continue
        mat,shd=d[0],d[1]
        for m in np.unique(mat):
            if m<0: continue
            sel=mat==m
            if sel.sum()<64: continue
            sh=shd[sel]
            n=len(np.unique(sh))
            spread[n]+=1
            if n>1: varies+=1
            else: flat+=1
            bylayer[(p.name,int(m))][li].append(float(sh.mean()))
print(f"material instances examined: {varies+flat}")
print(f"  shade VARIES within the material: {varies}  ({100*varies/max(1,varies+flat):.1f}%)")
print(f"  single shade only:               {flat}")
print("  distinct shades per material:", dict(sorted(spread.items())[:8]))
# layer comparison
diffs=[]
for key,ls in bylayer.items():
    if 0 in ls and 1 in ls:
        diffs.append(np.mean(ls[1])-np.mean(ls[0]))
if diffs:
    diffs=np.array(diffs)
    print(f"\nsame material on BOTH layers: {len(diffs)} cases")
    print(f"  mean(shade on layer1 - layer0) = {diffs.mean():+.3f}")
    print(f"  median                         = {np.median(diffs):+.3f}")
    print(f"  fraction where layer1 brighter = {100*(diffs>0).mean():.1f}%")
    print(f"  std                            = {diffs.std():.3f}")
