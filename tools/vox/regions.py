"""A room's picture split into regions, and each pixel's shading.

A region is a connected run of one material -- one colour family -- with
the dark outline drawn round things as its border. Within a region a
pixel's shading says which way its surface faces the light the game
draws with (from above, a little behind):

  LIT     a top: level ground, a roof's flat, a step's tread
  MID     a slope between
  SHADED  a front, facing the camera: a wall, a step's riser

Shading is relative to the region: its lightest third is lit, its
darkest third shaded. A region all one tone (grass, a path) is LIT.
"""
import numpy as np

from .room import RE

OUTLINE = 56            # luminance: darker is the drawn outline
GREY_SAT = 0.16         # saturation: below, a grey (stone, metal)
HUE_STEP = 30           # degrees: one colour family
FLAT_SPREAD = 28        # luminance: a region this even is one tone
MIN_REGION = 6          # px: smaller is detail, merged into a neighbour

LIT, MID, SHADED, OUTLINE_C = 1, 2, 3, 4


def hsv(px):
    rgb = px[..., :3].astype(float) / 255.0
    mx, mn = rgb.max(-1), rgb.min(-1)
    d = np.where(mx - mn > 1e-9, mx - mn, 1.0)
    r_, g_, b_ = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hue = np.where(mx == r_, ((g_ - b_) / d) % 6,
                   np.where(mx == g_, (b_ - r_) / d + 2, (r_ - g_) / d + 4)) * 60.0
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    return hue, sat


def luminance(px):
    return px[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114])


def material(px):
    """Each pixel's colour family: 0 outline, 1 grey, 2.. a hue bucket."""
    hue, sat = hsv(px)
    lum = luminance(px)
    fam = 2 + (np.floor(((hue + HUE_STEP / 2) % 360) / HUE_STEP)).astype(int)
    fam = np.where(sat < GREY_SAT, 1, fam)
    return np.where(lum < OUTLINE, 0, fam)


def split(px):
    """(labels, n, shade): labels[y, x] the region of each pixel (0 the
    outline), shade[y, x] LIT / MID / SHADED / OUTLINE_C."""
    fam = material(px)
    h, w = fam.shape
    labels = np.zeros((h, w), np.int32)
    n = 0
    for f in np.unique(fam):
        if f == 0:
            continue
        lab, k = RE._label(fam == f)
        labels[lab > 0] = lab[lab > 0] + n
        n += k
    # specks join the region they sit in
    sizes = np.bincount(labels.ravel(), minlength=n + 1)
    small = (sizes < MIN_REGION) & (np.arange(n + 1) > 0)
    if small.any():
        for _ in range(4):
            m = small[labels]
            if not m.any():
                break
            P = np.pad(labels, 1)
            for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                nb = P[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx]
                take = m & (nb > 0) & ~small[nb]
                labels = np.where(take, nb, labels)
                m = small[labels]
    lum = luminance(px)
    shade = np.full((h, w), OUTLINE_C, np.int8)
    for j in np.unique(labels):
        if j == 0:
            continue
        sel = labels == j
        v = lum[sel]
        lo, hi = np.percentile(v, 33), np.percentile(v, 67)
        if v.max() - v.min() <= FLAT_SPREAD:
            shade[sel] = LIT
            continue
        s = np.where(lum[sel] >= hi, LIT, np.where(lum[sel] <= lo, SHADED, MID))
        shade[sel] = s
    shade = smooth(shade, labels)
    return labels, int(labels.max()), shade


SMOOTH_PASSES = 3


def smooth(shade, labels):
    """Texture inside a region (shingles, brick lines, leaves) is not
    shape: each pixel takes the shading most of its 3x3 neighbours in the
    same region have."""
    h, w = shade.shape
    for _ in range(SMOOTH_PASSES):
        P = np.pad(shade, 1)
        L = np.pad(labels, 1, constant_values=-1)
        votes = np.zeros((4, h, w), np.int16)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nb = P[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx]
                same = L[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx] == labels
                for k, c in enumerate((LIT, MID, SHADED)):
                    votes[k] += (same & (nb == c))
        best = np.array([LIT, MID, SHADED])[votes[:3].argmax(0)]
        shade = np.where((labels > 0) & (votes[:3].max(0) > 0), best, shade).astype(np.int8)
    return shade


def overlay(px, labels, shade):
    """A picture of the split: each region its own colour, lit pale,
    shaded dark, mid between, outline black."""
    rng = np.random.default_rng(7)
    pal = rng.integers(40, 255, size=(labels.max() + 1, 3))
    out = pal[labels].astype(float)
    k = np.select([shade == LIT, shade == MID, shade == SHADED], [1.0, 0.7, 0.4], 0.0)
    out = out * k[..., None]
    return np.clip(0.55 * out + 0.45 * px[..., :3], 0, 255).astype(np.uint8)
