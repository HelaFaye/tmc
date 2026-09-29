# 06 — Camera and depth: reading height out of a flat drawing

The room art is one flat picture. Standing it up means knowing, for every
pixel, how deep and how high the thing drawn there is. This note is what
the drawings and the engine say about that, and how `tools/tilevox.py`
uses it.

## The projection: one row per pixel of depth, one per pixel of height

The engine places every sprite at

```c
/* port/port_draw.c, the entity's OAM position */
s32 y = entity->y.HALF.HI + entity->z.HALF.HI + entity->spriteOffsetY;
```

`y` is depth into the room (south is larger) and `z` is height (negative is
up). So a point `d` deep and `h` high is drawn at screen row

    row = d - h

It is a 1:1 oblique projection, not a true top-down view: one pixel of height
moves a thing up the screen exactly as far as one pixel of depth. Columns are
x, unchanged.

It follows that one drawn row can be depth or height, and the drawing alone
cannot say which. Every rule below is a way of deciding which it is. To un-project
a surface whose height is known, use

    plan depth = row + h

Everything in tilevox that places geometry does this sum: `projective_plan`,
`box_quads` and `bookcase_quads`. The uv of every face is its inverse
(`uv = (x, z - y)`), so a face shows exactly the pixels the game drew for it.

## Interiors: a box seen from above its middle

Indoor rooms (Link's house 34_16, the Minish houses 32_*, dungeon rooms)
draw their walls as a box seen from above its centre, with every inner wall
face folded outward round the floor:

- The corners of the wall band are **diagonal seams** running from the
  floor's corner to the outline's corner. A band drawn like that can only be
  four faces folded out; a heightfield would give square corners.
- The band's **inner edge is the wall's foot** (on the floor); its **outer
  edge (the outline) is the top of the wall**.
- The south band is thinner than the north one because the south wall leans
  toward the camera, but it is the same wall height.

Each band is therefore a vertical face, not a slope and not a flat top. In
`room_shell` and `box_walls`:

- rays from the floor's middle find the band's inner edge (the foot) and
  outer edge (the top);
- a superellipse is fitted to the foot, so oval rooms (32_00) stay oval and
  rectangular ones (34_16, 72_00) stay rectangular;
- the wall's height is the median north band, clamped to `BOX_H`.

`box_quads` stands each face from the foot to that height, textured from the
foot's pixel out to the outline's. The whole shell comes from that; no tile in
the box is placed on its own.

What stands against a wall (plants, stools, dressers) and what is drawn *on*
it (windows, curtains, posters, moulding) look alike in the band. The wall's
own palette and trim tell them apart: a cell drawn in the wall's colours
within the band is relief, and anything else is an object. A person's label in
`vr/tiles/overrides.txt` decides it where the colours cannot (`RELIEF_WORDS`).

## Things on the floor: top above front

A table, a stool or a dresser is drawn as its **lit top** with its **shaded
front** below it. By the projection, the top takes one row per pixel of depth
and the front one row per pixel of height. So:

- the height is the number of front rows;
- the depth is the number of top rows;
- the drawn extent is depth + height, which is why using the whole extent
  for the height (an earlier version) bent the tabletops.

`drawn_front` finds the split in each column: the row with the most light-to-shade
contrast between above and below. It takes the median over the columns. Link's
table has about 22 rows of top and 7 of front, so it is 7 px high: a table at
Link's thigh (Link is about 24 px, `LINK_PX`, roughly 119 cm; Minish Link is
about 5.5 cm).

A piece backed by the back wall is drawn partly over the wall band, so its
front is not all in its cells. It keeps its named height (`FURNITURE_PX`)
or the drawn-extent estimate.

Outlines are not always black. Often they are a darker shade of the thing's
own palette, and `outline_mask` and `wood_mask` count those as outline.

## Stacked things: the bookcase (45_16)

The library bookcase is drawn from the front as boards and rows of books, one
above another up the screen. Read naively, as a heightfield where each drawn
row is somewhere on the floor, it becomes a terrace with each shelf set back
from the one below. That is wrong. It is one upright case:

- Each board has a **front edge one row high** (rows 7, 15 and 23). The edge
  rows are `bookcase_edges`: the first wall row under a board.
- All the edges lie in **one plane**, Zf. The lowest edge stands on the room
  floor, so Zf = (last edge row + 1) × 16 + floor.
- The band above edge row `e` (that board's top and the books standing on it)
  is at level `Zf − 16·e`. In 45_16 that gives 16, 144 and 272 px.
- Each board cell lies at plan depth `row·16 + level`, so each board's top runs
  from its front edge back to the books.
- Each run of books is a vertical face where it meets its board. The books
  come out flush, one plane for every shelf, and the boards protrude in
  front of them.
- A board also hides the top rows of the books below it. Those rows are why
  each band is taller than its visible books (128 px of band for 80 px of
  books).

The game never draws the case's sides, back or top. They wear a strip of the
shelf's own grain (`wood_patch`, `grain_face`), and the inside of the back
wears the dark wood of the board edges, as the game draws it where a book is
missing.

## Summary of the rules

| Drawn as | Means | Built by |
|---|---|---|
| a band round an indoor floor, diagonal corner seams | a vertical wall, foot at the inner edge, top at the outline | `box_walls`, `box_quads` |
| lit top over shaded front | depth = top rows, height = front rows | `drawn_front`, `furniture_prisms` |
| boards with one-row edges stacked up the screen | one upright case, edges in one plane | `bookcase_edges`, `bookcase_quads` |
| a cell drawn as the floor under a blocked tile | a sprite (furniture object) stands there | `drawn_as_floor`; left to the entity stage |
| any surface of known height | plan depth = row + height | everywhere |

Never read a view's depth from the PPU's scroll or affine registers (see the
README). Depth comes only from the room's own cells and the projection above.
