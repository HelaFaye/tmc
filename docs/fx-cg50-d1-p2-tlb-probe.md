# fx-CG50 D1 investigation: P2 TLB-array snapshot with file logging

The diagnostic reads the UTLB and ITLB arrays through a small P2 assembly
helper and saves the result as a plain-text file in storage memory.

## Output file

The add-in creates/replaces:

`\\fls0\TLBSNAP.TXT`

The log contains:

- Snapshot status and format version.
- MMUCR, PTEH, PTEL, VBR, URB, URC, AT, SV, and ASID.
- All 64 UTLB entries (index, address word, data word, valid flag).
- All four ITLB entries (index, address word, data word, valid flag).

The file is created at its final even byte length. This matters because
`BFile_Write()` requires an even number of bytes. The add-in removes a previous
`TLBSNAP.TXT` first; if removal or creation cannot be completed, the creation
or write error is shown on the display. A failed P2 snapshot is also written as
a short failure record when storage access is available.

## File-system call context

The P2 helper returns to ordinary add-in code before file access starts. The
`BFile_*` operations are then called using Gint's API and its supported
`FONTCHARACTER` path literal. The diagnostic uses the calculator OS file system;
it does not write directly to flash or raw storage. The file can be copied off
the calculator by enabling USB mass storage and copying `TLBSNAP.TXT` from the
storage root.

## TLB snapshot safety boundary

- Copies a position-independent assembly helper to `0xac7ff000`.
- Reads all 64 UTLB address/data pairs and all four ITLB address/data pairs in
  P2, writing the captured values to `0xac7fe000`.
- Executes `SYNCO` and `ICBI` in P2 before returning to normal add-in code.
- Reads MMUCR/PTEH/PTEL/VBR but does not write MMU control registers or TLB
  entries.
- Does not install a custom TLB miss handler or map the planned ROM window.
- Gint's default handler remains installed and may naturally refill add-in
  code pages while the diagnostic runs.

## Build and use

Build the existing target:

```bash
cmake --build port/fxcg/build-cg --target fxcg_tlb_state_probe
```

Transfer `port/fxcg/TLB-STATE-PROBE.g3a` to the calculator and run it. After
success, connect USB and select mass-storage mode, then copy `TLBSNAP.TXT` from
the storage root to the computer. The display also keeps the five-page view as
a fallback; its footer reports whether the file write succeeded.

Keep D1 ROM initialization fail-closed. This probe does not validate a custom
TLB miss handler, `LDTLB`, OS 3.20.0202 replacement policy, or safe UTLB entry
ownership.
