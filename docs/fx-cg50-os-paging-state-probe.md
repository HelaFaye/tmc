# fx-CG50 OS paging-state probe

This read-only diagnostic is the next evidence-gathering step for the D1 ROM
window. It records the live MMU/UTLB state together with candidate OS paging
metadata, without modifying the OS data, MMU registers, or TLB entries.

## Candidate OS locations

The probe reads three locations described by the public CG50 Internals
reverse-engineering notes for OS 3.60. It captures 512 page-table words
because the candidate table occupies the 2 KiB span immediately before the
counter; the OS 3.60 notes also state that launch clears only the first 256
words. Thus nonzero words later in the 512-word dump must not automatically be
classified as live mappings: the end of the table may contain entries beyond
the running add-in's code or stale values.

| Address | Captured value | OS 3.60 interpretation |
|---|---|---|
| `0x8C04CF0C` | 512 consecutive 32-bit words | Add-in code-page table; one word per 4 KiB page starting at VA `0x00300000` |
| `0x8C04D70C` | 32-bit word, read before and after capture | Software round-robin page-mapping counter |
| `0x8C04CEE1` | 8-bit value | Add-in-mapped flag |

**These offsets are not yet verified for the target OS 3.20.0202.** The
probe explicitly labels the layout as unverified and preserves raw values so
they can be compared with the live TLB data. It does not call the OS mapping
syscalls, infer that a value is trustworthy, or change any of the locations.

## Output

`OS-PAGING-PROBE.g3a` writes `/OS-PAGING.TXT` to calculator storage memory.
The file contains:

- `MMUCR`, `PTEH`, `PTEL`, `VBR`, and all 64 raw UTLB address/data pairs;
- candidate page-table words 0–511, with their corresponding virtual page
  and low-29-bit physical value;
- the candidate page-table counter before and after the read;
- the candidate add-in-mapped byte;
- consistency counts and comparisons between valid 4 KiB add-in UTLB mappings
  and corresponding candidate page-table words. The probe separately reports
  whether the first seven words are nonzero and page-aligned, the counter is
  within 0–54, and the mapped flag equals one; these are diagnostics, not a
  substitute for verifying the OS 3.20.0202 layout.

The UTLB is read sequentially, not atomically. The counter is bracketed because
a natural code-page miss can advance it while the probe executes. The probe
never writes `MMUCR`, `PTEH`, `PTEL`, UTLB entries, the page table, the counter,
or the mapped flag. Gint's normal TLB-miss handler remains active.

## Applying and building

Run the overlay applier from the repository root on branch `fx-cg50`:

```sh
python3 /path/to/apply_overlay.py .
git diff --check
```

Build the diagnostic together with the existing probes:

```sh
cd port/fxcg
fxsdk build-cg -c \
  -DTMC_FXCG_EMULATOR_TEST=ON \
  -DTMC_FXCG_TLBH_LINK_PROBE=ON \
  -DTMC_FXCG_TLB_STATE_PROBE=ON \
  -DTMC_FXCG_OS_PAGING_PROBE=ON
fxsdk build-cg
```

The CI workflow is updated to build and upload the separate
`fxcg50-os-paging-state-probe` artifact. CI only builds it; the target's RAM
contents still must be collected on the physical calculator.

After running `OS-PAGING-PROBE.g3a` on the physical fx-CG50, copy
`OS-PAGING.TXT` from storage memory over USB and attach it for analysis.

## Interpretation gate

Do not use these candidate offsets to implement D1 until the target capture
shows coherent evidence. In particular, compare candidate table words 0–6
against valid 4 KiB UTLB translations for virtual pages `0x00300000` through
`0x00306000`. A coherent set of nonzero, page-aligned table words matching the
live physical translations, together with a plausible mapped flag and a
counter in the expected allocator range, supports the offsets but does not by
itself prove the OS 3.20.0202 allocation routine is identical to OS 3.60.

No TLB slot is reserved, no replacement policy is changed, and
`Fxcg_RomInit()` remains fail-closed.
