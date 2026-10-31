# OPPO Reno PCAM00 — experimental native Debian bring-up

**COMPILE-ONLY / UNTESTED ON HARDWARE / DO NOT FLASH.**

This is a first board-specific experiment for OPPO Reno PCAM00 (OP46B1,
project 18097, Snapdragon SDM710). It is not a Xiaomi Mi 8 SE image and not a
finished Debian phone distribution. It combines a custom Reno device tree,
an ARM64 Linux kernel and a tiny RAM-resident diagnostic environment extracted
from authenticated Debian trixie packages (`busybox-static` and `base-files`).
No Android, chroot, proot, LXC or VM is needed for the intended native boot.
However, no successful Reno boot is claimed by a successful CI build.

## Intended first milestone

* Native kernel reaches `/init` in RAM.
* USB NCM enumerates; phone address `192.168.7.2/30`.
* A directly attached host at `192.168.7.1` may read static diagnostic files at
  `http://192.168.7.2:8080/` — no passwordless shell or upload/CGI interface.
* USB ACM enumerates without a command interpreter.
* Default recovery timer requests an ordinary reboot after 180 seconds.
  This is **best effort**, not a guarantee against an earlier kernel hang.

## Safety boundaries of this build

* `CONFIG_BLOCK=n`: no internal block storage APIs. UFS/MMC nodes also disabled.
* No partition names, formatting, filesystem expansion, boot control block,
  AVB edits, modem calibration, Wi-Fi NVM patches or partition-writing commands.
* No loadable modules, `/dev/mem`, EFI variables, remoteproc, display, GPU,
  touch, sound, Wi-Fi, Bluetooth, battery charging policy or Type-C/PD driver.
  The generic power-supply framework is enabled as a USB PHY dependency, but
  no charger/battery hardware driver is enabled.
* Stock F.55 static reserved memory ranges are preserved; assertions check
  coverage and overlap. AOP/CMD-DB/SMEM subranges follow SoC definitions and
  remain a live-hardware verification item.
* USB gadget controller/PHY and three evidenced USB supplies are enabled.
  RPMh/clock/IOMMU/PMIC handling still touches hardware: **RAM-only does not mean
  electrically risk-free, guaranteed recovery, or safe charging**.
* PMIC/SoC temperature support is requested, but battery thermal management
  and runtime functionality are not verified. Short, attended tests only.

## Source and evidence

Kernel reference: https://github.com/57UU/linux-sirius/tree/17601ca5198a736f696447a9b6c568e4be97cc9c

That immutable revision declares **Linux 7.2.3**, despite earlier research
notes calling another binary 7.3. Only common SoC and PMIC include files are
used; the Xiaomi board DTS, boot scripts, rootfs, partition numbers and GPIO
assignments are not used.

Reno data source: local analysis of the PCAM00 F.55 sample, base DTB index 4 and
DTBO entry 32. Sample ZIP SHA256:
`a3a3226634bab03db1537ab10620fe2b57af2df924428a28e36bbf97776c2748`.
The sample contains a modified vbmeta and is not proven to be an untouched
vendor recovery package. No firmware binaries or per-device data are published
in this repository.

Reno evidence was additionally checked read-only on a PCAM00 F.55 phone:
DTB index 4 / DTBO index 32 and fixed reserved-memory ranges match; panel reports
AMS641RW01, touch firmware S3706. These observations still do not validate the
new kernel or its bootloader handoff. No device identifiers or backup data are
included here.

USB supply mappings from static stock DT:
PM660 L10 = 1.8 V; PM660L L1 = 0.88–0.9 V; PM660L L7 = 3.088–3.1 V.
No parent-rail programming or guessed Xiaomi PHY trims are added.

## Build

Use the manually triggered GitHub Actions workflow in this repository.
It pins kernel and Action revisions, cross-compiles on Ubuntu 24.04, verifies
required/prohibited configuration, parses final DTB reservations, obtains
Debian packages with APT signature verification and checks ARM64 BusyBox applets
using QEMU user emulation. QEMU applet checks are **not phone boot tests**.

Outputs on success:

* `Image` and `sdm710-oppo-pcam00-ramdiag.dtb`
* `initramfs-debian-arm64.cpio.gz`
* `boot-pcam00-ramdiag-UNTESTED.img`: unsigned candidate Android boot header v1,
  page size 4096, gzip Image + Reno DTB. Header addresses match the static sample,
  **not measured ABL load locations**.
* resolved kernel config, compiled DT, validation JSON, checksums and Debian
  package provenance.

Failed runs may upload logs/config only. Artifact existence alone is not success.

## Before any real boot attempt

1. Confirm live PCAM00 model/project, unlocked bootloader, applied DT, current
   boot/recovery versions and complete partition map.
2. Obtain and verify backups; confirm a tested way back to working Android.
3. Resolve ABL `fastboot boot` support, DTB selection and whether stock DTBO
   would be applied to this incompatible mainline device tree.
4. Confirm ABL supplies real RAM topology; DTS deliberately has a zero-length
   placeholder, not guessed memory size.
5. Review USB PHY, SMMU/TZ permissions and inherited PMIC/charging state.
6. Get explicit owner approval for the exact temporary boot procedure.

There are intentionally **no automated flash or reboot commands** in CI. Do not
remove/erase DTBO or flash boot/userdata merely because compilation succeeded.
A full Debian system, root filesystem on UFS, desktop and external hardware
support are later milestones, not delivered by this diagnostic image.
