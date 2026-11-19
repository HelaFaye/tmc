# fx-CG50 upper DRAM check

A standalone fxSDK/gint add-in for validating Picori's proposed game-BSS RAM
range on the target calculator. It is intentionally separate from Picori:
it does not link the game, use `fxcg.ld`, clear `.xbss`, or initialize the ROM
window.

## Range and access method

- Picori's linked BSS range: P1 `0x8c4e0000..0x8c800000` (end exclusive).
- Physical DRAM: `0x0c4e0000..0x0c800000` (3,200 KiB).
- This probe accesses the same DRAM through the uncached P2 alias
  `0xac4e0000..0xac800000`.

The P2 range is used so memory reads and writes are not satisfied by the SH-4
P1 data cache. The code saves each 4 KiB block in ordinary add-in RAM, runs
byte-, halfword- and word-wide patterns plus address and walking-bit tests,
restores the original block, and checks that the restore succeeded before
moving to the next block. The log is written to `\\fls0\RAMCHECK.TXT`.

A successful result validates read/write behavior over the entire candidate
physical range during this add-in session. It does not prove that every future
OS version or other software state will leave the range unused. The hardware
check is still required on the actual fx-CG50 and OS version being targeted.

## Build

From this directory on the Arch Linux development machine:

```sh
fxsdk build-cg -c
fxsdk build-cg
```

Expected output: `RAMCheck.g3a` in this directory.

The project uses the installed fxSDK and gint (minimum gint 2.11). It reuses
Picori's two calculator icon PNGs but links only the probe source and gint.

## Run on hardware

1. Copy `RAMCheck.g3a` to the fx-CG50's storage memory using USB mass storage.
2. Safely eject the calculator's storage volume.
3. Launch `RAMCheck` from the calculator's main menu.
4. Keep the calculator powered throughout the test. Press a key at the prompt.
5. Wait for the final PASS/FAIL screen. Do not interrupt either test phase.
   During the per-block phase, one 4 KiB block is temporarily overwritten.
   During the final cross-block phase, one word in each of the 800 pages is
   temporarily overwritten simultaneously until all saved words are restored.
   A reset, crash or power loss during that final phase can leave those 800
   page-base words modified.
6. Return to the main menu, reconnect USB mass storage, and copy
   `RAMCHECK.TXT` from the storage root back to the development machine.

The probe writes only to the DRAM range under test and the report file through
Gint's BFile API. It does not write raw flash addresses, alter clock registers,
or execute CPU sleep instructions.

## Result interpretation

- `RESULT: PASS`: all 800 blocks passed byte/halfword/word patterns,
  within-block address signatures, walking data-bit tests, the cross-block
  page-base signature test, and restore verification.
- `RESULT: FAIL`: inspect the first failing phase and P1/P2 address in
  `RAMCHECK.TXT`. Do not use the tested range for Picori until the cause is
  understood.
- A bus error, reset, or hang is not a pass. Record the exact observed behavior
  and do not launch the normal Picori build that clears the range until the
  diagnostic completes cleanly.
