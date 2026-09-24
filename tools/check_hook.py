#!/usr/bin/env python3
"""Syntax-check the roomcap hook before anything tries to build it.

A hook is inserted into port_repro_roomcap.c as text, and the failure mode
that actually bites is STRUCTURAL: an inserted block landing inside an
existing comment, so its own '*/' closes that comment early and the rest of
the comment's prose becomes code. The build then fails with things like
"'the' undeclared" -- an English word from a sentence -- and a cascade of
undeclared identifiers further down the function.

Balance alone is not enough to catch that (the counts stay equal), so this
compiles the hook against a stub harness and reports only the structural
error classes. Stub field-name mismatches are expected and ignored: the
question is whether the hook's own syntax holds together.

Exit status 0 = safe to build.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

STRUCTURAL = ("undeclared", "expected ';'", "expected declaration",
              "unterminated comment", "expected expression",
              "at end of input", "expected identifier")

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32;
typedef signed int s32;
typedef struct { int kind,id,type,spriteIndex,paletteBank,vramSlot,palette,
                     spriteVramOffset,animIndex,frameIndex,raw;
                 struct { u16 WORD; int raw; } x, y, z; } Entity;
typedef struct { Entity base; } PlayerEntity;
typedef struct { Entity base; } GenericEntity;
#define MAX_ENTITIES 128
#define TMC_VR 1
GenericEntity gEntities[MAX_ENTITIES]; PlayerEntity gPlayerEntity;
int global_progress;
struct { int active, mask, raw; } gFadeControl;
struct { int area, room, origin_x, origin_y, width, height; } gRoomControls;
int main(void) {
  const char* out = getenv("TMC_ROOMCAP_OUT");
  int a = 0, r = 0; (void)a; (void)r;
'''


def check(path):
    spec = importlib.util.spec_from_file_location("hookmod", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    code = getattr(mod, "HOOK", None)
    if code is None:
        print("!! no HOOK template in", path)
        return 1

    opens, closes = code.count("/*"), code.count("*/")
    bo, bc = code.count("{"), code.count("}")
    print(f"  comments {opens} open / {closes} close   braces {bo} / {bc}")
    bad = opens != closes or bo != bc

    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as f:
        f.write(HARNESS + code + "\n  return 0;\n}\n")
        tmp = f.name
    res = subprocess.run(["gcc", "-fsyntax-only", "-w", tmp],
                         capture_output=True, text=True)
    errs = [l for l in res.stderr.splitlines() if " error:" in l]
    structural = [l for l in errs
                  if any(k in l for k in STRUCTURAL)]
    os.unlink(tmp)

    print(f"  gcc: {len(errs)} error(s), {len(structural)} structural")
    for l in structural[:10]:
        print("   ", l.replace(tmp, "hook.c"))
    if structural or bad:
        print("!! the hook would not compile -- refusing to build")
        return 1
    print("  hook syntax OK")
    return 0


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else \
        str(Path(__file__).with_name("apply_roomcap_hook.py"))
    sys.exit(check(p))
