# fx-CG50 Gint TLB-handler link probe

This diagnostic checks one narrow prerequisite for D1: whether a Picori-owned
object can supply Gint's `.gint.tlbh` entry point when linked against the exact
Gint package found by fxSDK.

## Why a link probe is needed

Gint 2.11's kernel startup copies the linker-provided TLB-miss handler to
`VBR + 0x400` using the `gint_tlbh` entry point and its linker-defined size.
Picori's `fxcg.ld` already keeps `.gint.tlbh` and exports the section size.
The probe therefore supplies the ABI-level symbol `_gint_tlbh` from an
application object and checks the final linked ELF.

The proposed integration relies on the normal static-link rule that a strong
application definition resolves the kernel's reference before the linker has
to extract Gint's archive member containing the default handler. The probe
must prove this for the actual fxSDK/Gint link, rather than assume it from
link-order reasoning.

## What CI does

When configured with `-DTMC_FXCG_TLBH_LINK_PROBE=ON`, the `port/fxcg` project
also builds `fxcg_tlbh_link_probe`. It uses the same Gint target and the same
`port/fxcg/fxcg.ld` linker script as Picori. CI inspects the resulting ELF and
requires:

- `_gint_tlbh` and `_gint_tlbh_start` to have the same address;
- `_gint_tlbh_size` to equal exactly four bytes;
- the link to complete without duplicate `_gint_tlbh` definitions.

A four-byte section proves that the app-provided stub, rather than Gint's
larger default section, is the section retained for startup copying.

## Safety boundary

**The generated `TLBH-LINK-PROBE-DO-NOT-RUN.g3a` must never be executed.** Its
handler is deliberately only `rte; nop`; it does not install a TLB translation
and would retry a faulting instruction. CI builds and inspects the ELF but
never boots this add-in. The normal Picori add-in continues using Gint's
unmodified handler and is the only add-in run in the headless emulator.

This check does not demonstrate that a custom handler can map pages, survive
all exception conditions, or work on OS 3.20.0202 hardware. Those are separate
runtime and physical-calculator tests. `Fxcg_RomInit()` remains fail-closed.

## Next D1 step after this passes

Replace the link-only stub with a minimal, audited SH-4A handler that preserves
Gint's existing add-in-code mapping behavior and adds only a narrowly bounded
test window. Then test two resident pages, adjacent-page reads, repeated
refills and UTLB replacement on the physical fx-CG50 before integrating PCPK
or any game ROM call sites.
