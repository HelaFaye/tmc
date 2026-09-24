import sys, math
import numpy as np
from PIL import Image

def load(path):
    V=[]; C=[]; F=[]
    for line in open(path):
        p=line.split()
        if not p: continue
        if p[0]=="v":
            V.append([float(p[1]),float(p[2]),float(p[3])])
            C.append([float(p[4]),float(p[5]),float(p[6])] if len(p)>=7 else [.6,.6,.6])
        elif p[0]=="f":
            F.append([int(q.split('/')[0])-1 for q in p[1:]])
    return np.array(V), np.array(C), F

def render(path, out, yaw=35, pitch=32, W=900, H=650):
    V,C,F = load(path)
    ctr = (V.min(0)+V.max(0))/2
    P = V-ctr
    # NOTE: pitch is NEGATED here. The painter's-order test below sorts by
    # -depth, and with a positive rotation the object's TOP gets the larger
    # depth -- i.e. the camera ends up UNDERNEATH. Every pitched render made
    # before this was fixed was a view of the model's base, which is how a
    # correct chest lid read as a ziggurat. Positive pitch now means from
    # above, which is where this game's camera actually is.
    ry, rp = math.radians(yaw), math.radians(-pitch)
    # yaw about Y, then pitch about X
    x = P[:,0]*math.cos(ry) - P[:,2]*math.sin(ry)
    z = P[:,0]*math.sin(ry) + P[:,2]*math.cos(ry)
    y = P[:,1]
    yy = y*math.cos(rp) - z*math.sin(rp)
    zz = y*math.sin(rp) + z*math.cos(rp)
    su, sv, depth = x, -yy, zz
    span = max(su.max()-su.min(), sv.max()-sv.min()) or 1
    k = min(W,H)*0.86/span
    U = (su-su.min())*k + (W-(su.max()-su.min())*k)/2
    Vp= (sv-sv.min())*k + (H-(sv.max()-sv.min())*k)/2

    img = Image.new("RGB",(W,H),(26,28,34))
    px = img.load()
    zbuf = np.full((H,W), 1e18)
    order = sorted(range(len(F)), key=lambda i: -depth[F[i]].mean())
    for fi in order:
        idx=F[fi]
        pts=[(U[i],Vp[i]) for i in idx]
        col=C[idx].mean(0)
        d=depth[idx].mean()
        # flat shade by face normal-ish: use vertical extent to darken sides
        ys=[V[i][1] for i in idx]
        side = (max(ys)-min(ys))>0.5
        shade = 0.62 if side else 1.0
        r,g,b = [int(max(0,min(255, c*255*shade))) for c in col]
        xs=[p[0] for p in pts]; ys2=[p[1] for p in pts]
        x0,x1=int(min(xs)),int(math.ceil(max(xs)))
        y0,y1=int(min(ys2)),int(math.ceil(max(ys2)))
        if x1<0 or y1<0 or x0>=W or y0>=H: continue
        poly=np.array(pts)
        for yy2 in range(max(0,y0),min(H,y1+1)):
            xs_hit=[]
            for i in range(len(poly)):
                a=poly[i]; bnd=poly[(i+1)%len(poly)]
                if (a[1]<=yy2<bnd[1]) or (bnd[1]<=yy2<a[1]):
                    t=(yy2-a[1])/(bnd[1]-a[1])
                    xs_hit.append(a[0]+t*(bnd[0]-a[0]))
            if len(xs_hit)<2: continue
            xs_hit.sort()
            for xa,xb in zip(xs_hit[::2], xs_hit[1::2]):
                for xx in range(max(0,int(xa)), min(W,int(math.ceil(xb)))):
                    if d < zbuf[yy2,xx]:
                        zbuf[yy2,xx]=d; px[xx,yy2]=(r,g,b)
    img.save(out)
    print(f"{path}: {len(V)} verts, {len(F)} faces -> {out}")

if __name__=="__main__":
    render(sys.argv[1], sys.argv[2], yaw=float(sys.argv[3]) if len(sys.argv)>3 else 35, pitch=float(sys.argv[4]) if len(sys.argv)>4 else 32)
