// glrender.js -- rasterize projected quads with WebGL2 in headless Chromium.
//
//   node glrender.js JOBS.json
//
// JOBS.json is a list of {dir, w, h, tw, th, out}. Each dir holds pos.bin
// (float32 x,y,z per vertex, already in clip space), uv.bin (float32 texel
// x,y per vertex), shade.bin (float32 per vertex), idx.bin (uint32
// triangles) and tex.rgba (tw x th RGBA). The nearest surface wins
// (depth test GREATER); each face shows the one texel its uv names, times
// its shade, over the background. Writes the w x h image to out (PNG).
// Called by glrender.py, which does the projection exactly as refcheck.py.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const PAGE = `<!doctype html><canvas id=c></canvas><script>
async function draw(w, h, tw, th, bg) {
  const get = async n => new Uint8Array(await (await fetch('/d/' + n)).arrayBuffer());
  const pos = new Float32Array((await get('pos.bin')).buffer);
  const uv = new Float32Array((await get('uv.bin')).buffer);
  const sh = new Float32Array((await get('shade.bin')).buffer);
  const idx = new Uint32Array((await get('idx.bin')).buffer);
  const tex = await get('tex.rgba');
  const c = document.getElementById('c'); c.width = w; c.height = h;
  const gl = c.getContext('webgl2', { antialias: false, preserveDrawingBuffer: true });
  const sh_ = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o);
    if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw gl.getShaderInfoLog(o); return o; };
  const p = gl.createProgram();
  gl.attachShader(p, sh_(gl.VERTEX_SHADER, '#version 300 es\\nin vec3 a;in vec2 t;in float s;flat out vec2 vt;flat out float vs;void main(){gl_Position=vec4(a,1.0);vt=t;vs=s;}'));
  gl.attachShader(p, sh_(gl.FRAGMENT_SHADER, '#version 300 es\\nprecision highp float;uniform highp sampler2D T;flat in vec2 vt;flat in float vs;out vec4 o;void main(){ivec2 q=clamp(ivec2(floor(vt)),ivec2(0),textureSize(T,0)-1);o=vec4(texelFetch(T,q,0).rgb*vs,1.0);}'));
  gl.linkProgram(p); gl.useProgram(p);
  const buf = (data, name, n) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); const l = gl.getAttribLocation(p, name);
    gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, n, gl.FLOAT, false, 0, 0); };
  buf(pos, 'a', 3); buf(uv, 't', 2); buf(sh, 's', 1);
  const ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
  const t = gl.createTexture(); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, t);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, tw, th, 0, gl.RGBA, gl.UNSIGNED_BYTE, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.uniform1i(gl.getUniformLocation(p, 'T'), 0);
  gl.viewport(0, 0, w, h); gl.clearColor(bg[0], bg[1], bg[2], 1); gl.clearDepth(0);
  gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.GREATER); gl.disable(gl.CULL_FACE);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.drawElements(gl.TRIANGLES, idx.length, gl.UNSIGNED_INT, 0);
  gl.finish();
  return c.toDataURL('image/png');
}
</script>`;

(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const b = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const page = await b.newPage();
  let dir = null;
  await page.route('http://glrender.local/**', r => {
    const u = new URL(r.request().url());
    if (u.pathname === '/') return r.fulfill({ status: 200, contentType: 'text/html', body: PAGE });
    return r.fulfill({ status: 200, contentType: 'application/octet-stream',
                       body: fs.readFileSync(path.join(dir, path.basename(u.pathname))) });
  });
  await page.goto('http://glrender.local/');
  for (const j of jobs) {
    dir = j.dir;
    const url = await page.evaluate(a => draw(...a), [j.w, j.h, j.tw, j.th, j.bg]);
    fs.writeFileSync(j.out, Buffer.from(url.split(',')[1], 'base64'));
  }
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(1); });
