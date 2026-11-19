# fx-CG50 read-only UTLB state probe

This diagnostic is the next prerequisite for the D1 runtime paging spike. The
Gint TLB-handler link override has passed CI, but that proves only that Picori
can supply `.gint.tlbh`; it does not identify which UTLB entries the installed
OS 3.20.0202/Gint runtime is using or reserving.

## What it records

`TLB-STATE-PROBE.g3a` snapshots the following when its `main()` starts:

- SH-4A `MMUCR`, `PTEH`, and `PTEL`;
- Gint's current VBR;
- all 64 UTLB address words and data words, read from the P4 UTLB arrays.

The program captures one point-in-time snapshot before drawing. The screen
shows the raw address/data words in four pages of 16 entries. Green rows have
both address and data valid bits set. Use LEFT/RIGHT to change pages and EXIT
to quit. Photograph all four pages, including the register summary, when
running on the target calculator.

## Safety boundary

The probe itself does not write `MMUCR`, `PTEH`, `PTEL`, or UTLB array entries.
It installs no custom TLB-miss handler and does not access the D1 ROM window.
Gint's normal handler remains active and may naturally refill add-in code pages
as the program executes; the displayed data is the captured snapshot, not a
promise that the live TLB remains frozen. The separate
`TLBH-LINK-PROBE-DO-NOT-RUN.g3a` remains build-only and must never be executed.

The read-only probe is intended for the physical fx-CG50 running OS 3.20.0202.
The CI emulator builds the binary but does not run it; emulator TLB behavior is
not a substitute for the target hardware snapshot.

## Why this precedes the refill handler

Do not assume that the OS 3.60 slot allocation documented by reverse
engineering is identical on OS 3.20.0202. In particular, do not pick a UTLB
slot or change `URB`/`URC` until the target's actual state and replacement
policy have been established. The raw snapshot will inform a narrowly scoped
handler design that preserves Gint's existing add-in-code syscall-12 path and
forwards all non-test-window misses to Gint's normal exception path.

This diagnostic does not enable paging and does not change
`Fxcg_RomInit()`, which remains fail-closed.
