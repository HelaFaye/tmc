"""A room as the remade stage reads it: the picture the game shows (no
actors in it), the ground and overhead layers, collision classes, and the
actors, all in drawn pixels."""
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import room_explore as RE  # noqa: E402
import room_capture as CAP  # noqa: E402

ACTOR_KINDS = (3, 7)        # enemy, NPC: built by the entity stage
ACTOR_PAD = 4               # px


@dataclass
class Room:
    rid: str
    raw: object                     # room_explore's room
    picture: np.ndarray             # H x W x 3, as the game shows it
    ground: np.ndarray              # the ground layer's art
    overhead: np.ndarray | None     # H x W x 4, the overhead layer
    sprite: np.ndarray              # where sprites draw (actors removed)
    cls: np.ndarray                 # cell classes (room_explore)
    meta: dict = field(default_factory=dict)

    @property
    def size(self):
        return self.picture.shape[:2]


def load(dump, captures=CAP.OUT):
    import extract_art
    dump = Path(dump)
    r = RE.load_room(dump)
    rid = dump.stem[5:]
    h, w = r.cells_h * 16, r.cells_w * 16
    ground = np.ascontiguousarray(RE.room_art_rgb(r, 0)[:h, :w, :3].astype(np.uint8))
    over = None
    if len(r.layers) > 1 and r.layers[1]["present"]:
        o = extract_art.room_art(r, 1)
        if o is not None:
            over = np.asarray(o)[:h, :w]
    pic, spr = ground.copy(), np.zeros((h, w), bool)
    got = CAP.load_shown(rid, captures)
    if got is not None and got[0].shape[0] >= h and got[0].shape[1] >= w:
        pic = np.array(got[0][:h, :w, :3])
        spr = np.array(got[1][:h, :w])
        # actors move and are built on their own: their sprites are not
        # the room's surface
        actors = [(e["x"] - r.origin_x, e["y"] - r.origin_y) for e in r.entities
                  if e["kind"] in ACTOR_KINDS and not e.get("is_player")]
        if actors:
            lab, n = RE._label(spr)
            for j in range(1, n + 1):
                ys, xs = np.nonzero(lab == j)
                if any(xs.min() - ACTOR_PAD <= ax <= xs.max() + ACTOR_PAD and
                       ys.min() - ACTOR_PAD <= ay <= ys.max() + ACTOR_PAD for ax, ay in actors):
                    pic[ys, xs] = ground[ys, xs]
                    spr[ys, xs] = False
    cls = RE.classify_room(r, 0)
    return Room(rid, r, pic, ground, over, spr, cls, dict(path=str(dump)))
