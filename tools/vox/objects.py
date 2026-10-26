"""A room's things -- each tree, house, hedge, pillar -- as one label each.

Two sources, each strong where the other is weak:

  binding   The game builds things from tiles it reuses. Two neighbouring
            tiles belong to one thing when, across the game, each is
            usually found next to the other there (BIND). A house's tiles
            always travel together; two houses stacked in a row do not, so
            they part. Statistics are over every dumped room.

  the art   Trees overlap and share edge tiles, so binding cannot part
            them. A watershed of the drawn picture can: seeds at the
            deepest points away from the long dark outlines, grown out
            over everything that is not floor.

Watershed pieces lying mostly in one bound group join, unless the group is
mostly leaf green (a stand of trees); then pieces join only where the
boundary between them is not a drawn outline (a shading step inside one
crown). Plain even fills (a hall's surround) are dropped.

  python -m vox.objects --dumps states/p0 03_01 02_00 --show out/
"""
import heapq
import json
from collections import Counter
from pathlib import Path

import numpy as np

from .room import RE, load as load_room
from . import regions as RG
from . import tiles as TL

BIND = 0.5              # share of each tile's uses with the other beside it
LEAF = 0.5              # share of a bound group's pixels in leaf green
LEAF_DIFF = 20          # green over red and blue: leaf green
UNDRAWN = 24            # brightest channel below this: an undrawn cell

RIDGE_STEP = 10         # luminance: an outline is this much darker than both sides
RIDGE_MIN = 14          # px: shorter dark runs are texture
CORE = 3                # px: a seed is at least this far from an outline
FILL_STD = 4.0          # luminance spread: below, a plain fill
FILL_MIN = 2000         # px: a plain fill is at least this large
LINE_SHARE = 0.4        # of a boundary on an outline: two things, not one
DETAIL_PX = 96          # px: a piece this small, inside one neighbour, is part of it

OFFS = ((0, 1), (1, 0))


# -- binding ----------------------------------------------------------------

def cell_keys(r, cls=None):
    """Each cell's tile key, both layers ('ground|overhead'); '' undrawn."""
    if cls is None:
        cls = RE.classify_room(r, 0)
    K = np.full((r.cells_h, r.cells_w), "", object)
    over = {}
    for (li, cy, cx), (key, _tt, _c) in TL.room_tiles(r, cls).items():
        if li:
            over[cy, cx] = key
        else:
            K[cy, cx] = key
    for (cy, cx), key in over.items():
        K[cy, cx] = f"{K[cy, cx]}|{key}"
    a = RE.room_art_rgb(r, 0)
    if a is None:
        return np.full_like(K, "")
    a = a[:r.cells_h * 16, :r.cells_w * 16, :3]
    lit = a.reshape(r.cells_h, 16, r.cells_w, 16, 3).max((1, 3, 4))
    K[lit < UNDRAWN] = ""
    return K


def solid(K, cls):
    """Cells a thing stands on: blocked, or drawn over."""
    over = np.vectorize(lambda s: "|" in s, otypes=[bool])(K)
    drawn = K != ""
    return drawn & (np.isin(cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]) | over)


def statistics(dumps, cache=None):
    """(uses, pairs): how often each key is used, and each (a, b, dy, dx)
    neighbour pair. Cached as JSON (tile indices only, no art)."""
    if cache and Path(cache).exists():
        d = json.loads(Path(cache).read_text())
        return Counter(d["uses"]), Counter({tuple(k.split("\t")[:2]) + tuple(int(v) for v in k.split("\t")[2:]): c
                                            for k, c in d["pairs"].items()})
    uses, pairs = Counter(), Counter()
    for p in sorted(Path(dumps).glob("room_*.tmcr")):
        r = RE.load_room(p)
        if not r.cells_w:
            continue
        K = cell_keys(r)
        h, w = K.shape
        for y, x in zip(*np.nonzero(K != "")):
            a = K[y, x]
            uses[a] += 1
            for dy, dx in OFFS:
                yy, xx = y + dy, x + dx
                if yy < h and xx < w and K[yy, xx]:
                    pairs[(a, K[yy, xx], dy, dx)] += 1
    if cache:
        Path(cache).write_text(json.dumps({
            "uses": uses, "pairs": {"\t".join(map(str, k)): c for k, c in pairs.items()}}))
    return uses, pairs


def bound(K, cls, uses, pairs, bind=BIND):
    """Cell labels: neighbouring solid cells whose tiles are bound share
    one. Cells bound to nothing get 0."""
    h, w = K.shape
    S = solid(K, cls)
    parent = np.arange(h * w)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for y, x in zip(*np.nonzero(S)):
        for dy, dx in OFFS:
            yy, xx = y + dy, x + dx
            if yy < h and xx < w and S[yy, xx]:
                a, b = K[y, x], K[yy, xx]
                c = pairs[(a, b, dy, dx)]
                if c >= bind * uses[a] and c >= bind * uses[b]:
                    parent[find(y * w + x)] = find(yy * w + xx)
    lab = np.array([find(i) for i in range(h * w)]).reshape(h, w) + 1
    lab = np.where(S, lab, 0)
    size = np.bincount(lab.ravel())
    return np.where(size[lab] > 1, lab, 0)


# -- the art ----------------------------------------------------------------

def ridges(L):
    """(outlines, all dark lines): pixels darker than both sides across,
    one or two pixels wide; outlines are the long runs."""
    t = RIDGE_STEP
    P = np.pad(L, 1, mode="edge")
    l, r, u, d = P[1:-1, :-2], P[1:-1, 2:], P[:-2, 1:-1], P[2:, 1:-1]
    P2 = np.pad(L, 2, mode="edge")
    l2, r2, u2, d2 = P2[2:-2, :-4], P2[2:-2, 4:], P2[:-4, 2:-2], P2[4:, 2:-2]
    rg = ((L < l - t) & (L < r - t)) | ((L < u - t) & (L < d - t))
    rg |= (((L < l2 - t) & (L < r2 - t) & (np.minimum(l, r) < L + t)) |
           ((L < u2 - t) & (L < d2 - t) & (np.minimum(u, d) < L + t)))
    lab, _ = RE._label(rg)
    size = np.bincount(lab.ravel())
    return rg & (size[lab] >= RIDGE_MIN), rg


def mask(rm):
    """Pixels of things: what the overhead layer draws, and blocked cells
    and sprites unless in one of the room's floor colours."""
    h, w = rm.size
    alpha = (rm.overhead[:h, :w, 3] > 0) if rm.overhead is not None else np.zeros((h, w), bool)
    cell = np.ones((16, 16), bool)
    blocked = np.kron(np.isin(rm.cls, [RE.CLASS_WALL, RE.CLASS_LEDGE]), cell)[:h, :w]
    walk = np.kron(rm.cls == RE.CLASS_GROUND, cell)[:h, :w] & ~alpha & ~rm.sprite
    pic = rm.picture
    q = (pic[..., 0].astype(np.int32) << 16) | (pic[..., 1].astype(np.int32) << 8) | pic[..., 2]
    vals, cnt = np.unique(q[walk], return_counts=True)
    floor = np.isin(q, vals[cnt >= max(20, int(0.0005 * walk.sum()))])
    return alpha | ((blocked | rm.sprite) & ~floor)


def watershed(L, M, line):
    """Pieces of M: one per seed, grown cheapest first, outlines dear."""
    free = M & ~line
    dist = np.zeros(L.shape, np.int32)
    cur, k = free.copy(), 0
    while cur.any() and k < 40:
        k += 1
        dist[cur] = k
        P = np.pad(cur, 1)
        cur = cur & P[:-2, 1:-1] & P[2:, 1:-1] & P[1:-1, :-2] & P[1:-1, 2:]
    # each core is seeded at half its own depth, so touching things whose
    # outline has gaps still get a seed each
    cores, nc = RE._label(dist >= CORE)
    seeds = np.zeros_like(cores)
    n = 0
    for j in range(1, nc + 1):
        mj = cores == j
        sub, ns = RE._label(mj & (dist >= max(CORE, dist[mj].max() // 2)))
        seeds[sub > 0] = sub[sub > 0] + n
        n += ns
    h, w = L.shape
    cost = np.where(line, 255.0, 0.0) + (255 - L) * 0.3
    out = seeds.copy()
    pq = []
    steps = ((1, 0), (-1, 0), (0, 1), (0, -1))
    for y, x in zip(*np.nonzero(seeds)):
        for dy, dx in steps:
            yy, xx = y + dy, x + dx
            if 0 <= yy < h and 0 <= xx < w and M[yy, xx] and not out[yy, xx]:
                heapq.heappush(pq, (cost[yy, xx], yy, xx, out[y, x]))
    while pq:
        c, y, x, k = heapq.heappop(pq)
        if out[y, x]:
            continue
        out[y, x] = k
        for dy, dx in steps:
            yy, xx = y + dy, x + dx
            if 0 <= yy < h and 0 <= xx < w and M[yy, xx] and not out[yy, xx]:
                heapq.heappush(pq, (max(c, cost[yy, xx]), yy, xx, k))
    return out


def drop_plain(lab, L):
    n = lab.max()
    tot = np.bincount(lab.ravel(), minlength=n + 1)
    s1 = np.bincount(lab.ravel(), weights=L.ravel(), minlength=n + 1)
    s2 = np.bincount(lab.ravel(), weights=(L * L).ravel(), minlength=n + 1)
    m = s1 / np.maximum(tot, 1)
    std = np.sqrt(np.maximum(s2 / np.maximum(tot, 1) - m * m, 0))
    plain = (std < FILL_STD) & (tot >= FILL_MIN)
    plain[0] = False
    return np.where(plain[lab], 0, lab)


class _Sets:
    def __init__(self, n):
        self.p = np.arange(n + 1)

    def find(self, i):
        p = self.p
        while p[i] != i:
            p[i] = p[p[i]]
            i = p[i]
        return i

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)

    def apply(self, lab):
        return np.array([self.find(i) for i in range(len(self.p))])[lab]


def join_unlined(lab, line):
    """Join neighbours whose shared boundary is mostly not an outline, and
    small pieces wholly inside one neighbour."""
    n = lab.max()
    sets = _Sets(n)
    size = np.bincount(lab.ravel(), minlength=n + 1)
    pairs, touch = {}, {}
    for a, b, la, lb in ((lab[:, :-1], lab[:, 1:], line[:, :-1], line[:, 1:]),
                         (lab[:-1, :], lab[1:, :], line[:-1, :], line[1:, :])):
        m = a != b
        for x, y, v in zip(a[m].tolist(), b[m].tolist(), (la[m] | lb[m]).tolist()):
            touch.setdefault(x, set()).add(y)
            touch.setdefault(y, set()).add(x)
            if x and y:
                c = pairs.setdefault((min(x, y), max(x, y)), [0, 0])
                c[0] += 1
                c[1] += v
    for (x, y), (cnt, on) in pairs.items():
        if cnt >= 3 and on / cnt < LINE_SHARE:
            sets.join(x, y)
    for j in range(1, n + 1):
        nb = touch.get(j, set())
        if size[j] <= DETAIL_PX and len(nb) == 1 and 0 not in nb:
            sets.join(j, next(iter(nb)))
    return sets.apply(lab)


def leafy(pic):
    p = pic.astype(int)
    return (p[..., 1] > p[..., 0] + LEAF_DIFF) & (p[..., 1] > p[..., 2] + LEAF_DIFF)


def objects(rm, uses, pairs):
    """Pixel labels, one per thing (0 none), numbered 1..n."""
    pic = rm.picture
    h, w = rm.size
    L = RG.luminance(pic)
    outline, dark = ridges(L)
    lab = drop_plain(watershed(L, mask(rm), outline), L)

    B = bound(cell_keys(rm.raw, rm.cls), rm.cls, uses, pairs)
    Bp = np.kron(B, np.ones((16, 16), B.dtype))[:h, :w]
    nb = Bp.max() + 1
    green = np.bincount(Bp.ravel(), weights=leafy(pic).ravel(), minlength=nb) / \
        np.maximum(np.bincount(Bp.ravel(), minlength=nb), 1)
    # each piece's group: the one most of its pixels lie in
    n = lab.max()
    sets = _Sets(n)
    m = (lab > 0) & (Bp > 0)
    key, cnt = np.unique(lab[m].astype(np.int64) * nb + Bp[m], return_counts=True)
    best = {}
    for k, c in zip(key.tolist(), cnt.tolist()):
        p, g = divmod(k, nb)
        if c > best.get(p, (0, 0))[0]:
            best[p] = (c, g)
    first = {}
    for p, (_c, g) in best.items():
        if green[g] < LEAF:
            if g in first:
                sets.join(p, first[g])
            else:
                first[g] = p
    lab = join_unlined(sets.apply(lab), dark)
    _, lab = np.unique(lab, return_inverse=True)
    return lab.reshape(h, w)


def overlay(pic, lab):
    """Each thing tinted its own colour, outlined black."""
    rng = np.random.default_rng(3)
    col = rng.integers(40, 255, (lab.max() + 1, 3))
    v = pic.astype(float)
    m = lab > 0
    v[m] = v[m] * 0.45 + col[lab[m]] * 0.55
    b = np.zeros_like(m)
    b[:, 1:] |= lab[:, 1:] != lab[:, :-1]
    b[1:, :] |= lab[1:, :] != lab[:-1, :]
    v[b & m] = 0
    return v.astype(np.uint8)


def main(argv=None):
    import argparse
    from PIL import Image
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rooms", nargs="+")
    ap.add_argument("--dumps", default="states/p0")
    ap.add_argument("--stats", help="binding statistics cache (default <dumps>/binding.json)")
    ap.add_argument("--show", help="write <dir>/objects_<room>.png (local; it is the game's art)")
    a = ap.parse_args(argv)
    d = Path(a.dumps)
    uses, pairs = statistics(d, a.stats or d / "binding.json")
    for rid in a.rooms:
        rm = load_room(d / f"room_{rid}.tmcr")
        lab = objects(rm, uses, pairs)
        print(rid, lab.max(), "things")
        if a.show:
            Path(a.show).mkdir(parents=True, exist_ok=True)
            Image.fromarray(overlay(rm.picture, lab)).save(Path(a.show) / f"objects_{rid}.png")


if __name__ == "__main__":
    main()
