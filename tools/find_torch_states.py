import sys, numpy as np, collections
from pathlib import Path
sys.path.insert(0,'/home/claude/kit/tools')
import room_explore as RE
DUMP=Path('/home/claude/v4/vrdump')
# tile INDEX is what indexes the tileset art; group by (area, layer) and
# collect which tile indices carry 0x76 (unlit) vs 0x77 (lit).
byarea=collections.defaultdict(lambda: {'unlit':set(),'lit':set(),'rooms':set()})
for p in sorted(DUMP.glob('room_*.tmcr')):
    try: r=RE.load_room(p)
    except Exception: continue
    for li,L in enumerate(r.layers):
        if not L.get('present') or L.get('tile') is None: continue
        tile,tt=L['tile'],L['tiletype']
        tmap=tt[np.clip(tile,0,len(tt)-1)]
        for code,key in ((0x76,'unlit'),(0x77,'lit')):
            ys,xs=np.where(tmap==code)
            for y,x in zip(ys,xs):
                byarea[(int(r.area),li)][key].add(int(tile[y,x]))
                byarea[(int(r.area),li)]['rooms'].add((p.name,int(x),int(y),key))
both=[(k,v) for k,v in byarea.items() if v['unlit'] and v['lit']]
print(f"areas/layers with BOTH unlit(0x76) and lit(0x77): {len(both)}")
for (a,li),v in both[:12]:
    print(f"  area {a:3d} L{li}: unlit tiles {sorted(v['unlit'])[:4]} lit tiles {sorted(v['lit'])[:4]}")
    ex=collections.defaultdict(list)
    for rm,x,y,key in v['rooms']: ex[key].append((rm,x,y))
    for key in ('unlit','lit'):
        if ex[key]: print(f"      {key:5s} e.g. {ex[key][0]}")
