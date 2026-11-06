# B-F3: small, evidence-based board matching repair

## Measured facts

B is PCAM00 / OP46B1 / F.55. Its complete ABL SHA256 still equals the reviewed
sample. Later read-only socinfo returned SoC360/rev1.1, foundry3, subtype0,
platform version0x10000, first PMIC model0x1001b and die revision0x20000.
No environment hiding was removed. Bound revid device names are pm660@0 and
pm660l@2. That kernel exposes no numeric revid attributes on those devices; a
narrow PMIC log search returned no lines. A node name is not a direct read of
ABL's numeric PMIC1 model. No raw PMIC registers were read.

## Exact change

```dts
qcom,msm-id = <0x30168 0 0x30189 0>;
oppo,ramdiag-board-variant = "B-F3";
```

The low16 SoC IDs remain 360 and 393. The upper foundry field is3 rather than0.
The two revision cells remain0. Board-id `<8 0>`, all three PMIC candidate tuples,
reserved memory, USB supplies/tuning, kernel configuration and initramfs runtime
are unchanged. The extra DT marker distinguishes this board variant from old
v4 Run7; kernel and userland retain the honest v4 revision.

## Reviewed ABL instructions

These are RVAs in the analyzed LinuxLoader; not measured phone addresses:

- 0x1bba0–0x1bbb0: compare low16 SoC ID.
- 0x1bd14: extract `msm-id & 0xff0000`.
- 0x1bd54–0x1bd68: compare against foundry<<16; exact sets0x10000.
- 0x1bd98–0x1bd9c: foundry0 fallback sets0x8000, not the required exact bit.
- 0x1b6ac–0x1b6b4: require mask0x34150000, which includes0x10000.
- 0x1be94–0x1bed8 and0x1c0bc–0x1c0d8: platform8 and subtype0 equality
  contribute their exact bits. A lower-compatible board version uses separate
  ranking bits; it does not require changing `<8 0>` solely for this mask.
- 0x1c494–0x1c4f4: PMIC model comparisons use each slot's low byte. Existing
  tuples require PMIC0=27 and PMIC1=26; live0x1001b supports the first slot.
  The @2 node address is not tuple slot2 and die revision is not a model ID.

This supports the small MSM-only repair. It does not prove all ABL-visible
values, hypervisor overlay behavior, actual boot success or a recovery test.
The validator continues to report DTBO skip as NOT_PROVEN_ON_TARGET.

## Verification and delivery boundaries

Tests accept both corrected IDs and reject old foundry0, half-corrected pairs,
wrong-foundry IDs and missing/incorrect B-F3 markers. Existing boot/config/cpio,
thermal, USB, source-snapshot and checksum tests remain mandatory.

The uploaded artifact name includes F3. Final delivery is a separate IMG with a
new measured hash, not a renamed old image. No flash installer or automatic
recovery is added. Writing boot via TWRP would be persistent, and ordinary reboot
would not restore Android. Actual device reboot/write still needs confirmation
of the exact procedure and the phone's own original boot recovery route.
