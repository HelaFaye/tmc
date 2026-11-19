# fx-CG50 upper-DRAM validation

Picori's linker places `.xbss` at P1 `0x8c4e0000` and reserves the half-open
region through `0x8c800000`. The linked game BSS is 1,539,760 bytes, leaving
1,737,040 bytes unused inside the 3,200 KiB region. This is a static link
result, not a device test.

The SH-4 address map exposes physical DRAM at `0x0c000000` through P1
`0x8c000000` (cached) and P2 `0xac000000` (uncached). Independent fx-CG50
hardware research identifies physical `0x0c4e0000..0x0c800000` as a 3,200 KiB
range that the OS, applications, add-in launch and boot did not access during
its measurement; a real-device DRAM dump also showed this range had not been
written. See the external [CG50 memory map](https://hexbinoct.github.io/casio-cg50/memory-map/).

Because the project's actual target is OS 3.20.0202, validate the range on
that device before relying on it. Build the isolated probe in
`tools/fxcg50-ramcheck/` and run `RAMCheck.g3a` on the calculator. The probe
uses the uncached P2 alias, saves each 4 KiB block in ordinary add-in RAM,
tests byte/halfword/word patterns, within-block address signatures and
walking data bits, then restores and verifies the block. A final cross-block
pass writes distinct signatures to the first word of all 800 pages at once,
verifies them, and restores/verifies those original words. This detects page
address-line aliasing that isolated per-block tests cannot detect. It saves a
report to
`\\fls0\RAMCHECK.TXT`.

A PASS establishes that the tested range supported these reads and writes on
the target device during that session, including distinct address signatures
at all 800 page bases, and that the saved contents were restored. It does not
prove the OS will never use it in another lifecycle or OS version. Do not
interrupt either test phase: during the final cross-page test, one word in all
800 pages is temporarily changed at once. A reset or power loss in that phase
can leave those page-base words modified. If the probe faults, resets or
reports any mismatch, do not launch Picori's normal build, which clears its
`.xbss` range during startup.

The probe intentionally avoids raw flash writes, clock-generator writes and
CPU sleep instructions. These restrictions follow the community's published
[probe safety guidance](https://hexbinoct.github.io/casio-cg50/guides/probe-addins/).

## Hardware result: OS 3.20.0202

The v2 add-in was run on the target fx-CG50. Its saved report was:

```text
PASS 800 / 800
Checks passed: 12001
Failed checks: 0
Restore failures: 0
RESULT: PASS
```

The 800 blocks cover 3,276,800 bytes (3,200 KiB). Testing was performed
through the uncached P2 alias `0xac4e0000..0xac800000`, corresponding to the
same physical DRAM used by Picori through P1 `0x8c4e0000..0x8c800000`. The
per-block tests and final cross-block page-signature test completed, and the
probe verified restoration. This is a successful read/write and restoration
test on this device and OS version during that session, not a universal
claim about other OS versions or all future OS activity.
