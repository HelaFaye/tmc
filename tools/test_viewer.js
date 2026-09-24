// Node test for the viewer's parser and camera. Extracts the marked block from
// world-viewer.html so the tested code IS the shipped code, not a copy that can
// drift away from it.
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "world-viewer.html"), "utf8");
const m = html.match(/\/\* TESTABLE BEGIN \*\/([\s\S]*?)\/\* TESTABLE END \*\//);
if (!m) { console.error("FAIL: testable block not found in world-viewer.html"); process.exit(1); }
eval(m[1]);

let fails = 0;
function check(name, cond, detail) {
  console.log(`  ${cond ? "ok  " : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!cond) fails++;
}

// ---- parser, against a real worldgen file -------------------------------
const objPath = process.argv[2];
if (objPath && fs.existsSync(objPath)) {
  const txt = fs.readFileSync(objPath, "utf8");
  const r = parseOBJ(txt);
  const vLines = (txt.match(/^v /gm) || []).length;
  const fLines = (txt.match(/^f /gm) || []).length;
  const tLines = (txt.match(/^vt /gm) || []).length;

  // The parser emits one vertex per triangle CORNER rather than reusing the
  // file's vertices. That is required once faces carry texture coords: a
  // vertex shared by two faces with different UVs cannot occupy one buffer
  // slot. So the file's vertex count is no longer the expected output -- the
  // invariant is that every corner is accounted for.
  check("one vertex per triangle corner", r.nVerts === r.nTris * 3,
        `${r.nVerts} verts for ${r.nTris} tris`);
  check("quads triangulate 2:1", r.nTris === fLines * 2, `${r.nTris} tris from ${fLines} quads`);
  check("every index is in range", r.idx.every(i => i >= 0 && i < r.nVerts));
  check("colours are 0..1", r.col.every(c => c >= 0 && c <= 1));
  check("positions parsed", r.pos.length === r.nVerts * 3);

  // Every emitted position must be one the file actually contains -- catches an
  // off-by-one in the 1-indexed face parsing, which would otherwise just look
  // like slightly wrong geometry.
  const src = new Set();
  for (const L of txt.split("\n")) {
    const p = L.split(/\s+/);
    if (p[0] === "v") src.add(`${+p[1]},${+p[2]},${+p[3]}`);
  }
  let bad = 0;
  for (let i = 0; i < r.nVerts; i++) {
    if (!src.has(`${r.pos[i*3]},${r.pos[i*3+1]},${r.pos[i*3+2]}`)) bad++;
  }
  check("every emitted vertex exists in the file", bad === 0, `${bad} strays`);

  if (tLines) {
    check("UV per vertex", r.uv.length === r.nVerts * 2,
          `${r.uv.length / 2} uvs for ${r.nVerts} verts`);
    check("UVs inside [0,1]", r.uv.every(u => u >= -1e-6 && u <= 1 + 1e-6));
    check("hasUV reported", r.hasUV === true);
  }
} else {
  console.log("  (no .obj passed; skipping parser test against real data)");
}

// ---- parser edge cases ---------------------------------------------------
{
  const r = parseOBJ("# c\nv 0 0 0 1 0 0\nv 1 0 0 1 0 0\nv 1 0 1 1 0 0\nv 0 0 1 1 0 0\nf 1 2 3 4\n");
  check("one quad -> 2 triangles", r.nTris === 2);
  const g = parseOBJ("v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n");
  check("vertices without colour default to grey",
        g.col.slice(0, 3).every(c => Math.abs(c - 0.6) < 1e-9));
  check("a triangle stays 1 triangle", g.nTris === 1);
}

// ---- the camera invariant that actually matters --------------------------
// Static and Blink must not inherit continuous player motion. Walk the player
// in a straight line and look at what the camera does.
function walk(mode) {
  const cam = { x: 0, y: 0, z: 0 };
  const pl = { x: 0, y: 0, z: 0 };
  const seen = [];
  let jumps = 0;
  for (let i = 0; i < 400; i++) {
    pl.x += 3;
    const r = stepCamera(mode, cam, pl, { threshold: 240 });
    if (r.jumped) jumps++;
    cam.x = r.x; cam.z = r.z;
    seen.push(cam.x);
  }
  const distinct = new Set(seen.map(v => v.toFixed(4))).size;
  return { distinct, jumps, final: cam.x, playerFinal: pl.x };
}

const st = walk("static");
check("STATIC never moves", st.distinct === 1 && st.final === 0,
      `${st.distinct} distinct positions`);

const bl = walk("blink");
check("BLINK moves only in discrete jumps",
      bl.distinct === bl.jumps + 1 && bl.jumps > 0,
      `${bl.jumps} jumps, ${bl.distinct} distinct positions`);
check("BLINK stays within its threshold of the player",
      Math.abs(bl.playerFinal - bl.final) <= 240 + 3,
      `gap ${(bl.playerFinal - bl.final).toFixed(1)}`);

const at = walk("attached");
check("ATTACHED moves continuously", at.distinct > 300,
      `${at.distinct} distinct positions`);

// ATTACHED must match the C policy in port/vr/vr_anchor.c, which is the whole
// point of mirroring it here: dead zone, speed cap, frame-rate independence.
{
  let cam = { x: 0, y: 0, z: 0 };
  for (let i = 0; i < 600; i++) {
    const pl = { x: (i % 2) ? 8 : -8, y: 0, z: 0 };
    const r = stepCamera("attached", cam, pl, {});
    cam = { x: r.x, y: 0, z: r.z };
  }
  check("ATTACHED ignores jitter inside the dead zone",
        Math.abs(cam.x) < 1e-9, `drifted ${cam.x.toFixed(6)}`);

  cam = { x: 0, y: 0, z: 0 };
  let worst = 0;
  for (let i = 0; i < 300; i++) {
    const before = cam.x;
    const r = stepCamera("attached", cam, { x: 5000, y: 0, z: 0 }, {});
    cam = { x: r.x, y: 0, z: r.z };
    worst = Math.max(worst, Math.abs(cam.x - before) * 60);
  }
  check("ATTACHED respects the 420 px/s cap on a teleport",
        worst <= 420.5, `peak ${worst.toFixed(1)} px/s`);

  const run = (dt, n) => {
    let c = { x: 0, y: 0, z: 0 };
    for (let i = 0; i < n; i++) {
      const r = stepCamera("attached", c, { x: 1000, y: 0, z: 0 }, { dt });
      c = { x: r.x, y: 0, z: r.z };
    }
    return c.x;
  };
  const a60 = run(1 / 60, 120), a90 = run(1 / 90, 180);
  check("ATTACHED is frame-rate independent (60Hz == 90Hz)",
        Math.abs(a60 - a90) < 1.0, `${a60.toFixed(2)} vs ${a90.toFixed(2)}`);
}

// A camera that only ever teleports cannot produce the slow drift that causes
// sim sickness; this is the property the mode exists to guarantee.
check("BLINK is at least 10x coarser than ATTACHED",
      bl.distinct * 10 < at.distinct,
      `${bl.distinct} vs ${at.distinct}`);

console.log(fails ? `\n  ${fails} check(s) FAILED` : "\n  all checks passed");
process.exit(fails ? 1 : 0);
