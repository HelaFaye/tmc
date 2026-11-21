# fx-CG50 P2 SR flag readback probe

This follow-up strengthens the RB-only, BL-only, and combined-state probe. The
wrapper reads SR while the requested state is active and checks the exact
RB|BL field against the requested mask. An unwanted extra flag therefore fails
just as a missing flag does.

The wrapper returns `0x155` when the two-bit readback matches and `0x055` when
it does not. C validates that return independently for RB-only (`0x20000000`),
BL-only (`0x10000000`), and combined (`0x30000000`) calls. The original SR is
retained in R8 and restored before stack accesses. R8-R12 are unbanked; R12
contains the `0x30000000` comparison mask.

The 68-byte template includes a PC-relative mask literal. That literal is
loaded before entering the protected interval, and the instruction plus literal
are copied together, preserving the PC-relative displacement. The C harness
invalidates all three 32-byte instruction-cache lines covering the copied
template.

## Scope

A passing result demonstrates that the requested SR bits were read back during
this synthetic straight-line P2 interval and that the original SR was restored
well enough for the wrapper to return. It does not trigger a real TLB miss,
validate exception-stack behavior, prove Gint handler compatibility, or enable
D1 paging. `Fxcg_RomInit()` must remain fail-closed.
