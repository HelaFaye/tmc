#!/usr/bin/env python3
"""Build the calculator configuration of Picori as a Linux program.

usage: build_host.py <be|le|le64> <out-dir>
  be: powerpc-linux-gnu-gcc, big-endian (TMC_BE), run with qemu-ppc
  le: i686-linux-gnu-gcc, little-endian (runs natively on x86-64 Linux)

The file lists come from port/fxcg/sources.cmake and the
PICORI_PORT_SOURCES list in port/fxcg/CMakeLists.txt, minus the gint-only
files, plus tests/fxcg50-bigendian/host_sys.c.
"""
import concurrent.futures
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FXCG = os.path.join(ROOT, "port", "fxcg")
GINT_ONLY = {"fxcg_sys.c", "fxcg_libc.c", "fxcg_rom.c", "fxcg_ppu_bench.c"}


def sources():
    files = re.findall(r"\$\{TMC_ROOT\}/(\S+\.c)", open(os.path.join(FXCG, "sources.cmake")).read())
    cm = open(os.path.join(FXCG, "CMakeLists.txt")).read()
    block = re.search(r"set\(PICORI_PORT_SOURCES(.*?)\n\)", cm, re.S).group(1)
    for line in block.splitlines():
        line = line.split("#")[0].strip()
        if not line.endswith(".c"):
            continue
        if line.startswith("${TMC_ROOT}/"):
            files.append(line[len("${TMC_ROOT}/"):])
        elif line not in GINT_ONLY:
            files.append("port/fxcg/" + line)
    files.append("tests/fxcg50-bigendian/host_sys.c")
    # The pager's pak reader and cache (FXCG_ROM_HOST), for HOST_PAK runs.
    files.append("port/fxcg/fxcg_rom.c")
    files.append("external/fxLauncher/lib/cglink/src/inflate.c")
    return files


def main(argv):
    mode, out = argv[1], os.path.abspath(argv[2])
    os.makedirs(os.path.join(out, "obj"), exist_ok=True)
    if mode == "be":
        cc, arch, extra = "powerpc-linux-gnu-gcc", [], ["-DTMC_BE=1"]
    elif mode == "le64":
        # Diagnostic: the same configuration with 64-bit pointers (PORT_PTR64).
        cc, arch, extra = "gcc", [], []
    else:
        cc, arch, extra = "i686-linux-gnu-gcc", [], []
    flags = arch + ["-O1", os.environ.get("HOST_G", "-g0"), "-ffunction-sections", "-fdata-sections", "-std=gnu11", "-w", "-fno-strict-aliasing",
                    "-DPC_PORT", "-DNON_MATCHING", "-DUSA", "-DENGLISH", "-DREVISION=0", "-DMULTI_REGION",
                    "-DTMC_EMBEDDED=1", "-DTMC_ROM_WINDOW=1",
                    "-DTMC_FXCG_D1_PAGER=1", "-DFXCG_ROM_HOST=1"] + extra + [
        "-I" + os.path.join(ROOT, d) for d in ("include", "build/USA", "port", ".", "port/ppu/include", "port/fxcg",
                                                   "external/fxLauncher/lib/cglink/include")]
    objs, jobs = [], []
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        for src in sources():
            obj = os.path.join(out, "obj", src.replace("/", "_") + ".o")
            objs.append(obj)
            jobs.append(pool.submit(subprocess.run, [cc] + flags + ["-c", os.path.join(ROOT, src), "-o", obj],
                                    capture_output=True, text=True))
        failed = [(j.result().args[-3], j.result().stderr) for j in jobs if j.result().returncode]
    if failed:
        for src, err in failed[:10]:
            print("FAIL", src, "\n", err[:1500])
        return 1
    # ROM-resident symbols at the window, as on the calculator (no leading _).
    syms = os.path.join(out, "rom_symbols_host.ld")
    with open(syms, "w") as f:
        f.write("FXCG_ROM_WINDOW = 0x20000000;\n")
        for line in open(os.path.join(FXCG, "rom_symbols.ld")):
            if line.startswith("_"):
                f.write(line[1:])
    exe = os.path.join(out, "picori-host")
    # Keep the program out of 0x02000000-0x0A000000: port_resolve_addr() treats
    # values there as GBA addresses (the calculator needs the same; see fxcg.ld).
    link = [cc] + arch + ["-static", "-Wl,--gc-sections", "-Wl,-Ttext-segment=0x40000000", "-o", exe] + objs + [syms, "-Wl,--defsym,sxbss=0", "-lm"]
    r = subprocess.run(link, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-4000:])
        return 1
    print("built", exe, "from", len(objs), "files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
