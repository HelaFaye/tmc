"""vox -- the tile stage remade (docs/vr/10-vox-remake.md).

The game's camera draws a point at height y over depth z on row z - y.
Every drawn pixel is solved for one number, its height; its depth
follows. Built that way, the game's own camera sees exactly the drawing,
and the drawing's shading -- lit tops, shaded fronts, slopes between --
says how the height runs.

  room.py     a room's picture, layers, collision and actors
  regions.py  the picture split into material regions and shading
  scenes.py   the reference scenes every change is checked against
"""
