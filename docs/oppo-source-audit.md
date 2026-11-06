# OPPO official-source audit / 18097 diagnostic revision

> **B-v4 addendum:** the source references below are historical evidence, not a
> claim that old Run3 contains later fixes. The new `pcam00-b-ramdiag-v4` build
> verifies actual Image/config and cpio/helper byte equality. Runtime monitoring
> is now PID1-synchronous, with every enumerated zone validated and previously
> observed identities tracked, plus explicit diagnostic service cleanup before
> requesting reboot. The deadline is cooperative, not an independent watchdog;
> ordinary reboot is NOT restoration of original Android after a boot write.
> Unlock/root/backup status has been confirmed by the user, and root/F.55/ABL
> content identity was subsequently checked read-only on B. This does not prove
> DTBO skip, new-kernel boot, safe charging or recovery. See
> [current B-v4 gate](<B-v4-启动条件与TWRP说明.md>).

**Status: source-grounded RAM-only bring-up, not an installed Debian system or
verified boot. No phone reboot, flashing, formatting or charger programming was
performed for this audit.**

## Immutable sources and evidence ranking

* Official OPPO [Reno source commit](https://github.com/oppo-source/Reno-9.0-kernel-source/tree/61711727601fe83c11947f308e9987aaf2217652):
  Android 9, **Linux 4.9.112**, uploaded 2019-06-22. The complete Git tree inventory
  has 68,520 entries and is not truncated. Relevant blobs were fetched and checked
  against their Git SHA1 identities; this was not merely a repository lookup.
* Mainline reference [pinned commit](https://github.com/57UU/linux-sirius/tree/17601ca5198a736f696447a9b6c568e4be97cc9c): Linux 7.2.3.
* The actual PCAM00 runs Android 11 / F.55 / 4.9.227. Its bootloader-selected
  project 18097, DTB4/DTBO32 and fixed reserved regions were read previously.
  This audit also read its USB tuning module parameters without modifying them.
* Live observations and F.55 bytes take precedence over older Android 9 defaults.
  Neither source correctness nor a passing compile proves electrical correctness
  or successful ABL/DTBO handoff.

## 1. USB: actual vendor code, not just DTS

The official [18097 USB DTS:34–81](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/arch/arm64/boot/dts/18097/sdm670-usb.dtsi#L34-L81)
sets IMP_CTRL1=0x08, BIAS2=0x20 and table TUNE1=0x45, and maps the supplies to
PM660L L1, PM660 L10 and PM660L L7.

But [phy-msm-qusb-v2.c:598–650](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/drivers/usb/phy/phy-msm-qusb-v2.c#L598-L650)
changes the result:

1. The vendor build disables the normal eFuse TUNE1 write under `VENDOR_EDIT`.
2. For project **18097 / 18099 / 18041**, it explicitly writes **TUNE1=0x77** and
   **BIAS2=0x20** after the table and conditional bias override.
3. Read-only F.55 module parameters report `dev_phy_tune1=119` and
   `dev_phy_bias2=32`, exactly 0x77 / 0x20. These are parameter values, not a
   direct MMIO register dump or proof that the new mainline PHY works.

The mainline [QUSB2 driver:530–618](https://github.com/57UU/linux-sirius/blob/17601ca5198a736f696447a9b6c568e4be97cc9c/drivers/phy/qualcomm/phy-qcom-qusb2.c#L530-L618)
provides field-based DT overrides. Its [initialization order:802–809](https://github.com/57UU/linux-sirius/blob/17601ca5198a736f696447a9b6c568e4be97cc9c/drivers/phy/qualcomm/phy-qcom-qusb2.c#L802-L809)
is table → DT overrides → eFuse. Thus setting HSTX alone would be overwritten
by a fused value afterwards.

### Implemented mapping

| Mainline property | Value | Result |
|---|---:|---|
| `qcom,hstx-trim-value` | 7 | 19.8 mA field, high nibble 0x70 |
| `qcom,preemphasis-level` | 3 | low bits 0x03 |
| `qcom,preemphasis-width` | 1 | half-bit flag 0x04 |
| combined TUNE1 | — | **0x77**, not table-only 0x45 or generic 0x30 |
| `qcom,imp-res-offset-value` | 8 | official board IMP_CTRL1 |
| `qcom,bias-ctrl-value` | 32 | official final BIAS2 0x20 |
| `qcom,hsdisc-trim-value` | 1 | TUNE2 low two bits, matching 0x29 |
| `qcom,charge-ctrl-value` | 0 | PHY CHG_CTRL2 field, **not battery charging** |

Only this PHY's `nvmem-cells` consumer reference is removed, preventing the
subsequent eFuse-based override. **No eFuse/NVM bytes are changed.**

USB2-only operation is supported by the official
[PMIC overlay:547–563](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/arch/arm64/boot/dts/18097/sdm670-pmic-overlay.dtsi#L547-L563),
which uses an HS PHY plus nop SS PHY and disables QMP. The new board DTS
explicitly lists only QUSB2, with peripheral/high-speed mode. Legacy
`qcom,dwc3` handling is still built by `CONFIG_USB_DWC3_QCOM`; it is not necessary
to force the newer flat `qcom,snps-dwc3` binding.

## 2. Memory and UFS

The official [skeleton64:1–15](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/arch/arm64/boot/dts/18097/skeleton64.dtsi#L1-L15)
uses `/memory`, with `reg=<0 0 0 0>`, populated by the bootloader. The F.55
base and applied FDT also use `/memory`. The experimental DTS now preserves
that exact path instead of inventing `/memory@80000000`. It still does not
hardcode the observed capacity; ABL replacement must be verified before boot.
This is compatibility alignment, not proof that ABL strictly requires that name.

Official [sdm670.dtsi:2009–2054](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/arch/arm64/boot/dts/18097/sdm670.dtsi#L2009-L2054)
and F.55 describe **one UFS lane**, with 19.2 MHz device refclk. Generic mainline
SDM710 defaults to two. The disabled Reno host node now records one lane and
validation rejects a regression to two.

**UFS and its PHY remain disabled; CONFIG_BLOCK remains n.** Supplies, reset
pinctrl, PHY generation and clock-list completeness are future storage work,
not solved by setting lanes-per-direction. No partition is touched.

Fixed reservations continue to follow F.55. Old source dynamically allocates
12 MiB for `adsp_region`, while F.55 uses 8 MiB. Those dynamic reusable pools
are not automatically imported into the diagnostic tree. Shared AOP/CMD-DB/SMEM
infrastructure must not be removed merely because calls/modem are not needed.

## 3. Power and temperature: do not fake a complete charging port

The stock [thermal DTS:15–28,79 onward](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/arch/arm64/boot/dts/18097/sdm670-thermal.dtsi#L15-L106)
uses hardware limits plus Android userspace thermal control. Its charger also
has [battery-temperature policy](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/drivers/power/oppo/oppo_charger.c#L1666-L1815)
and substantial SMB2/VOOC dependencies. Generic TSENS or PM660 fuel gauge does
not replace that system.

Implemented diagnostic policy (explicitly **not** claimed to be stock behavior):

* Default CPU governor is **powersave**, not the previous performance policy.
* Require the actual CPU cooling, TSENS, PMIC temperature and reset built-ins
  in the resolved configuration.
* Record actual CPU governor and initial thermal readings in USB diagnostics.
* A small read-only monitor stops the diagnostic if no valid readings appear
  within 10 seconds, readings disappear, or any readable zone reaches 70°C.
  70°C is a deliberately conservative **SoC/PMIC diagnostic stop threshold**,
  not an acceptable battery/skin temperature or a charger calibration.
* The 180-second ordinary reboot deadline is checked by PID1 after initial
  pseudo-filesystem setup. It is cooperative, not an independent timer or
  hardware watchdog. Blocking I/O, a hang, wrong readings or failed reset can
  defeat it. After a persistent boot-partition write, reboot starts that same
  candidate again; it does not restore Android.
* Do not turn on `QCOM_LMH` blindly: its current driver programs algorithms and
  temperature thresholds through SCM at probe. That needs separate review/test.
* Configfs `MaxPower=100` matches the requested descriptor draw; this is **USB
  descriptor metadata only**, not an actual charge/input-current limit.

Battery, VOOC, Type-C/PD and charger hardware drivers remain disabled. Their
inherited firmware state is not controlled by this diagnostic. No claim of safe
charging, suspend or unattended operation is made.

## 4. Display and touch were actually inspected, not enabled prematurely

* The [official panel:84–150](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/arch/arm64/boot/dts/18097/dsi-panel_oppo18081samsung_ams641rw01_1080_2340.dtsi#L84-L150)
  and F.55 command payloads match byte-for-byte for ON (438 bytes),
  POST_ON_BACKLIGHT (36), OFF (74), LP1 (283), NOLP (331), and the other 10
  compared byte-stream properties; the TE-DCS scalar also matches.
* `0x29` Display On is in POST_ON_BACKLIGHT, not ON. The actual
  [display function:193–215](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/drivers/gpu/drm/msm/dsi-staging/dsi_display.c#L193-L215)
  sends it on first backlight handling with explicit DSI core-clock control.
  A generator translating only ON would miss required state transitions.
* [S3706 power sequencing:1054–1081](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/drivers/input/touchscreen/oppo_touchscreen/Synaptics/S3706/synaptics_drivers_s3706.c#L1054-L1081)
  enables 2.8-named supply then 1.8, waits, releases reset, waits again;
  power-off order differs. DTS actually requests 2.960 V for the 2.8-named rail.
* **Scope correction from the R17 Pro comparison:** the common parser initially
  reads `reset-gpio`, but the separately audited
  [Reno touch.c:96–122](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/drivers/input/touchscreen/touch.c#L96-L122)
  implements `reconfig_power_control()`. For projects 18097/18099 and
  `PCB == HW_VERSION__10`, it replaces reset with `reset_t0-gpio`; otherwise
  it releases the `vdd_2v8` regulator reference and sets it to NULL. The common
  [driver:3729–3746](https://github.com/oppo-source/Reno-9.0-kernel-source/blob/61711727601fe83c11947f308e9987aaf2217652/drivers/input/touchscreen/oppo_touchscreen/touchpanel_common_driver.c#L3729-L3746)
  calls this hook before power-on. Thus the earlier statement that no T0-selection
  logic had been found was limited to the C files then inspected, not the whole
  repository. The older source enum and F.55 PCB numbering still need mapping;
  **do not infer PCB40 means GPIO99, or change the diagnostic tree on that basis**.
* The S3706 driver contains firmware flashing/recovery operations and OPPO common
  touch dependencies. It cannot be declared a drop-in upstream RMI4 driver.

Therefore display and touch remain disabled in this RAM diagnostic. This audit
provides a credible basis for later drivers, not a fabricated desktop milestone.

## Regression checks and remaining limits

`test_board.py` checks correct board and memory placeholder, disabled single-lane
UFS, exact USB fields and absence of the late nvmem override; deliberately wrong
variants must fail. CI repeats these tests on the actual compiled DTB and resolved
config. `test_initramfs.py` feeds only temporary fake thermal sysfs data to the
side-effect-free monitor helper; it never runs `/init` or tests phone hardware.

The immutable upstream kernel build, independent image validation and boot safety
checks still apply. ABL DTB selection, stock DTBO merge, exact memory handoff,
verified recovery and a complete userdata/calibration backup remain prerequisites
for any real boot attempt. **No automatic flash procedure is added.**
