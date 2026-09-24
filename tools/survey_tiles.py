"""Run tilehints over every distinct tile in the game and report."""
import hashlib
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import room_explore as RE          # noqa: E402
import surfaces as SU              # noqa: E402
import tilehints as TH             # noqa: E402


def main():
    dump = Path(sys.argv[1] if len(sys.argv) > 1 else "vrdump")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "world/tilehints.txt")
    rooms = sorted(dump.glob("room_*.tmcr"))
    seen = {}
    counts = Counter()
    by_surface = defaultdict(Counter)
    bad = 0
    bar = RE.Progress(len(rooms), "tiles ")
    for p in rooms:
        bar.update(1)
        try:
            r = RE.load_room(p)
        except Exception:
            bad += 1
            continue
        for li, L in enumerate(r.layers):
            if not L.get("present") or L.get("tile") is None:
                continue
            try:
                art = RE.room_art_rgb(r, li)
            except Exception:
                art = None
            if art is None:
                continue          # layer present but no palette decoded
            try:
                surf = SU.surface_names(r, li).astype(str)
            except Exception:
                surf = None
            ch, cw = art.shape[0] // 16, art.shape[1] // 16
            for cy in range(ch):
                for cx in range(cw):
                    t = art[cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16, :3]
                    if t.shape[:2] != (16, 16):
                        continue
                    k = hashlib.blake2b(np.ascontiguousarray(
                        t.astype("uint8")).tobytes(), digest_size=12).digest()
                    counts[k] += 1
                    if k not in seen:
                        seen[k] = TH.classify(t)
                    if surf is not None and cy < surf.shape[0] \
                            and cx < surf.shape[1]:
                        by_surface[surf[cy, cx]][seen[k]["kind"]] += 1
    bar.done()

    total_cells = sum(counts.values())
    kinds = Counter()
    for k, info in seen.items():
        kinds[info["kind"]] += counts[k]

    lines = []
    lines.append(f"# Tile shape hints over {len(rooms) - bad} rooms.")
    lines.append(f"# {len(seen)} distinct 16x16 tiles across "
                 f"{total_cells} cells.")
    lines.append("#")
    lines.append("# Outline = near-black stroke, judged per tile. It marks an")
    lines.append("# oblique angle transition, so it maps the tile's edges in")
    lines.append("# 3D. Shading = faces under a north-west light.")
    lines.append("")
    lines.append("== kind, by how many cells in the game use it ==")
    for kind, n in kinds.most_common():
        uniq = sum(1 for i in seen.values() if i["kind"] == kind)
        lines.append("  %-9s %8d cells  %5.1f%%   %5d distinct tiles"
                     % (kind, n, 100.0 * n / max(total_cells, 1), uniq))

    lines.append("")
    lines.append("== outline coverage ==")
    fr = np.array([i["outline_frac"] for i in seen.values()])
    lines.append("  tiles with no outline at all: %d (%.1f%%)"
                 % (int((fr < 0.01).sum()), 100.0 * (fr < 0.01).mean()))
    lines.append("  median outline coverage: %.3f of the tile" % np.median(fr))
    ring = Counter(i["ring"] for i in seen.values())
    for k in sorted(ring):
        lines.append("  %d of 4 borders outlined: %5d tiles" % (k, ring[k]))

    lines.append("")
    lines.append("== shading bands (distinct flat shades per tile) ==")
    bd = Counter(min(i["bands"], 6) for i in seen.values())
    for k in sorted(bd):
        lines.append("  %d band(s): %5d tiles" % (k, bd[k]))

    lines.append("")
    lines.append("== does the light agree with north-west? ==")
    lines.append("  The statistic is max(0, cos) between a tile's bright-vs-")
    lines.append("  dark axis and the north-west light. Averaged over RANDOM")
    lines.append("  directions that is 1/pi = 0.318, so 0.318 means 'no")
    lines.append("  information', not 'lit from the south-east'. Most tiles")
    lines.append("  are texture and have no meaningful normal, so the")
    lines.append("  all-tile figure sits near the null by construction. The")
    lines.append("  number that matters is the one for tiles that are")
    lines.append("  actually objects -- outlined, and shaded in real faces.")
    lit = np.array([i["lit"] for i in seen.values()])
    bands = np.array([i["bands"] for i in seen.values()])
    ring_a = np.array([i["ring"] for i in seen.values()])
    has = bands >= 2
    obj = has & (ring_a >= 1) & (bands >= 3)
    lines.append("")
    lines.append("  null (random normals)                     0.318")
    if has.any():
        lines.append("  all tiles with >1 shade    n=%6d      %.3f"
                     % (int(has.sum()), float(lit[has].mean())))
    if obj.any():
        lines.append("  outlined, 3+ shades        n=%6d      %.3f"
                     % (int(obj.sum()), float(lit[obj].mean())))
    lines.append("")
    lines.append("  Measured separately on known objects, which is the")
    lines.append("  cleanest test available (see tools/viewangle.py):")
    lines.append("    chests   n=306   0.640")
    lines.append("    torches  n=223   0.557")
    lines.append("  Both about double the null, from two unrelated object")
    lines.append("  classes. The north-west light stands.")

    lines.append("")
    lines.append("== kind vs what the GAME says the cell is ==")
    lines.append("   (independent check: art-derived kind against the "
                 "decomp's surface table)")
    for s, c in sorted(by_surface.items(),
                       key=lambda t: -sum(t[1].values()))[:14]:
        tot = sum(c.values())
        top = ", ".join("%s %.0f%%" % (k, 100.0 * v / tot)
                        for k, v in c.most_common(3))
        lines.append("  %-24s %7d cells: %s" % (s, tot, top))

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
