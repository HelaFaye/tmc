# fx-CG50 D1 OS mapper proof probe

This is the next hardware diagnostic after the OS paging-state capture. It
checks whether the running calculator's OS can create D1 virtual aliases using
its own 4 KiB round-robin mapping syscall.

## Why this test exists

The OS paging-state capture on OS 3.20.0202 established that:

- `0x8C04CF0C` is the active add-in physical-page table;
- `0x8C04D70C` behaves as the OS page-mapping counter (stable at 7 during the
  previous capture);
- `0x8C04CEE1` reports the add-in-mapped state;
- all seven currently resident add-in 4 KiB translations matched their
  corresponding page-table entries.

CG50 Internals describes syscall `0x0009` as mapping a 4 KiB page using
`r4=virtual`, `r5=physical`, `r6=flags`, allocating the next slot from the
OS-managed 0..54 round-robin pool. The handler in OS 3.60 uses flags `0x6C`.
The syscall number is the interface; code addresses are version-specific.
However, the detailed routine description is from OS 3.60. This probe therefore
validates the mapping call on OS 3.20.0202 instead of assuming the behavior.

## What it does

`D1-MAP-PROBE.g3a` is an **active hardware diagnostic**, not a read-only probe.
It:

1. Checks MMU translation, SV, URB=63, the add-in-mapped flag, and that the
   active add-in page table begins with at least one nonzero, aligned flash page.
2. Checks that no currently valid UTLB entry overlaps the maximum D1 test span
   `0x20000000`–`0x20007FFF`.
3. Walks the first 256 active page-table words, touches every contiguous
   nonzero source page, and CRC32-checks up to the first eight pages. This
   primes source translations and code pages before allocating D1 aliases.
4. Calls OS syscall `0x0009` once per source page to map its corresponding
   physical page at `0x20000000` onward, with flags `0x6C`.
5. Checks the syscall dispatcher signature, resolves the syscall table at
   runtime, and confirms syscall `0x0009` points into the OS flash window.
6. Checks the OS counter advance, each allocated UTLB slot, the virtual and
   physical page fields, valid bits, and 4 KiB page-size encoding.
7. Reads the D1 aliases and compares their CRC32 values with the source pages.
8. Saves `/D1-MAP-PROBE.TXT` through `gint_world_switch()` using stdio.

The probe does not write the OS page table, mapped flag, MMUCR, PTEH, PTEL, or
UTLB registers directly. The OS mapper does update its normal allocator state
and creates temporary UTLB entries. Those entries remain subject to ordinary
OS round-robin replacement; the probe does not reserve slots.

## Safety boundaries

- Hardware-only: the lightweight `calcemu` does not emulate the Casio OS
  syscall environment needed to validate this routine.
- Run only on the target fx-CG50 OS 3.20.0202 after verifying the build.
- The probe refuses to call the mapper if its preflight checks fail, including
  a dispatcher signature or syscall-table target that does not match the
  expected ROM-resident form.
- If the syscall mapping does not match the expected UTLB entry, it does not
  dereference that alias.
- Do not combine this with any experimental custom TLB-miss handler.
- `Fxcg_RomInit()` remains fail-closed. No D1 runtime mapping is enabled.

## P2 UTLB snapshot implementation

The mapper proof probe obtains UTLB observations through the existing
position-independent `P2TlbSnapshotTemplate` in
`tests/fxcg50-tlb-probe/tlb_state_probe.S`. It copies that template to
`0xac7ff000`, writes the 64 UTLB and four ITLB pairs into the P2 buffer at
`0xac7fe000`, and executes `SYNCO; ICBI` before returning to P0. C code reads
only the copied result buffer after the helper returns; it must not dereference
`0xf6000000` or `0xf7000000` directly.

The initial D1-overlap check, per-mapping verification, and final stability
check all use this P2 snapshot path. A snapshot installation/readback failure
is a distinct preflight failure (`P2_UTLB_SNAPSHOT_FAILED`); a runtime snapshot
failure is reported as map error 3. The test still uses the OS-managed
round-robin slots and does not reserve them. A passing result is evidence for
the tested mapper behavior on that exact OS/device only; it does not validate
a custom TLB-miss handler or guarantee that D1 aliases survive unrelated OS
page faults.

## Build

```sh
cd port/fxcg
fxsdk build-cg -c \
  -DTMC_FXCG_EMULATOR_TEST=ON \
  -DTMC_FXCG_TLBH_LINK_PROBE=ON \
  -DTMC_FXCG_TLB_STATE_PROBE=ON \
  -DTMC_FXCG_OS_PAGING_PROBE=ON \
  -DTMC_FXCG_D1_MAP_PROBE=ON
fxsdk build-cg
```

The target produces `D1-MAP-PROBE.g3a`. CI builds and uploads it as
`fxcg50-d1-os-mapper-proof-probe`; CI does not execute it.

## Success criteria

The report should show one to eight page mappings, matching UTLB entries,
matching source/alias CRC32 values for every mapped page, and an allocator
counter that advanced by the mapped-page count modulo 55. A passing run validates the OS syscall mapping primitive for the test aliases. It does **not** yet validate calling that
syscall from inside the TLB-miss exception handler, nor does it validate the
physical backing strategy for the complete game ROM.

## Next step after a passing hardware run

Implement a separately gated D1 TLB-miss handler that resolves the requested
D1 page to the add-in's actual ROM asset backing page and cooperates with the
OS mapper. First prove its register/exception ABI with an isolated test; only
then connect it to `Fxcg_RomInit()`.
