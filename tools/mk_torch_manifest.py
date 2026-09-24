import sys, numpy as np, collections
from pathlib import Path
sys.path.insert(0,'/home/claude/kit/tools')
import shapefit as SF, room_explore as RE, shading as SH
DUMP=Path('/home/claude/v4/vrdump')
SP=Path('/tmp/claude-0/-home-claude/e8ef37b8-7c2a-5d9b-93d5-4efb346de00b/scratchpad')
def hot_frac(a):
    a=a[:,:,:3].astype(float); mx=a.max(2); mn=a.min(2)
    sat=np.where(mx>0,(mx-mn)/np.maximum(mx,1),0)
    return float(((sat>0.45)&(mx>110)).mean())
out=[]; skipped=collections.Counter(); buttons=0
for p in sorted(DUMP.glob('room_*.tmcr')):
    try: r=RE.load_room(p)
    except Exception: continue
    for li,L in enumerate(r.layers):
        if not L.get('present') or L.get('tile') is None: continue
        tile,tt=L['tile'],L['tiletype']
        tmap=tt[np.clip(tile,0,len(tt)-1)]
        ys,xs=np.where(np.isin(tmap,[0x76,0x77]))
        if not len(ys): continue
        d=SH.decompose_room(r,li)
        if d is None: continue
        mat=d[0]; art=RE.room_art_rgb(r,li); Hm,Wm=mat.shape
        for cy,cx in zip(ys,xs):
            y0,x0=int(cy)*16,int(cx)*16
            if y0+16>Hm or x0+16>Wm: skipped['outside room']+=1; continue
            ca=art[y0:y0+16, x0:x0+16]
            hf=hot_frac(ca)
            if hf<=0.0:
                buttons+=1; continue          # a button, not a torch
            cell=mat[y0:y0+16, x0:x0+16]
            ry0,ry1=max(0,y0-16),min(Hm,y0+32); rx0,rx1=max(0,x0-16),min(Wm,x0+32)
            ring=mat[ry0:ry1,rx0:rx1].copy().astype(int)
            ring[y0-ry0:y0-ry0+16, x0-rx0:x0-rx0+16]=-1
            for ncy,ncx in zip(ys,xs):
                if (ncx,ncy)==(cx,cy): continue
                ay,ax=int(ncy)*16-ry0, int(ncx)*16-rx0
                if -16<ay<ring.shape[0] and -16<ax<ring.shape[1]:
                    ring[max(0,ay):ay+16, max(0,ax):ax+16]=-1
            rc=collections.Counter(ring[ring>=0].ravel().tolist()); rn=max(1,sum(rc.values()))
            bg={m for m,c in rc.items() if c/rn>=0.15}
            want=sorted(int(m) for m,c in collections.Counter(cell.ravel().tolist()).items()
                        if int(m) not in bg and c>=6 and int(m)>=0)
            if not want: skipped['no materials']+=1; continue
            m=SF.largest_blob(np.isin(cell,want))
            if m.sum()<60: skipped[f'blob<60']+=1; continue
            yy,xx=np.where(m)
            if (yy.max()-yy.min()+1)<8 or (xx.max()-xx.min()+1)<8:
                skipped['not torch shaped']+=1; continue
            out.append(f"{p.name}  {cx},{cy},{cx+1},{cy+1}  torch  "
                       f"materials={','.join(map(str,want))} layer={li}"
                       f"   # TORCH hot={hf:.2f}")
hdr=["# Torch manifest. Tile type 0x76 and 0x77 are named TORCH and",
     "# TORCH_LIT, but the names describe behaviour, not looks: 0x77 is a",
     "# floor BUTTON. The separator is the art -- a torch has saturated,",
     "# bright pixels (fire, any hue: orange, green) and a button has none.",
     "# Measured over 31 distinct variants: torches 0.17..0.57, buttons",
     "# exactly 0.00. Layer is recorded per cell because some torches are",
     "# on layer 1 and fitting them from layer 0 builds the floor.",
     f"# {len(out)} torches, {buttons} buttons excluded, {sum(skipped.values())} skipped.",
     "# room              cells        mode     options"]
(SP/'torches.scene').write_text("\n".join(hdr+out)+"\n")
print(f"torches: {len(out)}   buttons excluded: {buttons}   skipped: {dict(skipped)}")
