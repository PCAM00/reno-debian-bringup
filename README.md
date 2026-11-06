# OPPO Reno PCAM00 B — direct Linux RAM diagnostics v4

**REVISION: `pcam00-b-ramdiag-v4` · COMPILE-ONLY · NOT BOOTED ON HARDWARE · DO NOT FLASH.**

The user approved preparing and cloud-building this revision, not rebooting or
flashing the phone. It is not a finished Debian installation, U-Boot image or
EDK2 port. A successful Actions job is only compilation/structural evidence.

## Why a new revision

The downloaded Run3 artifact predates later source changes. It uses performance
by default and lacks the new thermal helper and board USB tuning. Its historical
hashes are intact, but it must not be described as this v4 implementation.

This build embeds the version marker in kernel localversion, board DT and cpio,
archives an explicit reviewed source scope, and verifies the embedded Image,
DTB, init, helpers and revision byte-for-byte. Code changes must not be credited
to an older download.

## Intended milestone and limits

- Native ARM64 Linux reaches a custom RAM-resident `/init`.
- USB2 device-mode NCM offers plain text diagnostics at `http://192.168.7.2:8080/`
  to one trusted host `192.168.7.1/30`. No SSH, shell, CGI, upload, DHCP, routing
  or internet service.
- ACM enumerates only; it is **not an early printk console or interactive shell**.
- While healthy, `/live/state.txt`, `/live/stages.log`, `/live/thermal.txt`,
  `/live/cpu-policy.txt` and `/live/dmesg.txt` refresh every five seconds through
  a read-only bind view. The endpoint is unauthenticated: trusted USB host only.
- Before USB/HTTP works, RAM phase records cannot be observed externally. A
  silent USB connection cannot locate the failed boot stage.

Display, touch, GPU, audio, Wi-Fi, Bluetooth and persistent rootfs remain later
work. The tiny userland comes from authenticated Debian trixie packages
(`busybox-static` and `base-files`); it is not proot/chroot or installed Debian.

## Runtime design

- `CONFIG_BLOCK=n`; modules/DEVMEM/EFI variables/remoteproc/physical storage
  interfaces disabled, UFS/MMC nodes disabled. No internal filesystem repair,
  formatting, expansion, BCB or firmware/NVM modification.
- Fixed reserved-memory ranges retained without overlap. RAM is one zero-length
  `/memory` placeholder for the bootloader; the included SoC's old memory node
  is removed. This does not prove runtime topology or dynamic reservations.
- Reno USB TUNE1=0x77/BIAS2=0x20 and supply mappings follow the source audit.
  Removing this PHY's nvmem consumer prevents later trim overwrite; no fuse or
  NVM content is changed. Mainline operation on this board is still untested.
- CPU powersave is required in resolved config; PID1 checks actual policy without
  programming frequency sysfs values.
- After at most ten seconds of settling, all enumerated thermal zones must be
  valid and below 70000 mC. Observed zones may not disappear/change type. This
  does not prove every physical sensor probed, and is not battery/skin protection.
- PID1 makes synchronous stop decisions. It attempts to terminate the diagnostic
  HTTP process group, bring NCM down/unbind its own gadget, then request an
  ordinary reboot. A returning reboot never restarts the diagnostic services.
- The 180-second deadline is checked by PID1 using monotonic uptime, **not a
  hardware watchdog**. A pre-PID1 hang, blocking driver, scheduler/reset failure
  can defeat it. Inherited charging state is unmanaged and USB supplies VBUS.

**An ordinary reboot does not restore Android after a TWRP boot-partition write.**
It starts the installed boot image again. Restoring this phone's original boot
via a verified recovery route is a separate manual action.

## Bootloader / DTBO gate still open

Read-only B checks established normal F.55 Android/root operation and full ABL
partition byte identity with the analyzed sample. The standard fastboot command
table has no plain `boot`; `continue` loads a partition image rather than the
buffer. That differs from being unable to enter fastboot mode.

ABL has a conditional exact-match branch skipping board-DTBO selection. Current
candidate fields do not prove it: foundry, subtype and PMIC model match remain
incomplete. Stock DTBO is not ABI-compatible with the mainline tree. **Do not
erase DTBO, graft stock phandles, modify firmware or equate dtc success with
runtime handoff.**

See [B v4 gate and TWRP notes](<docs/B-v4-启动条件与TWRP说明.md>) and
[official source audit](<docs/oppo-source-audit.md>).

## Immutable sources

- Kernel: [57UU/linux-sirius@17601ca](https://github.com/57UU/linux-sirius/tree/17601ca5198a736f696447a9b6c568e4be97cc9c),
  Makefile version 7.2.3. Reuse only common SoC/PMIC includes, not Xiaomi's board,
  partition numbers, boot scripts or rootfs.
- Vendor evidence: [OPPO Reno@6171172](https://github.com/oppo-source/Reno-9.0-kernel-source/tree/61711727601fe83c11947f308e9987aaf2217652),
  Android9/4.9.112, cross-checked with F.55/4.9.227. Public source hashes are in
  [the index](<docs/oppo-source-files.json>).
- Debian packages come from authenticated repositories with recorded versions;
  future resolution is not claimed to be snapshot-pinned.

No private backups, calibration, full device FDT, credentials or proprietary
firmware are published in the build repository.

## Build and verify

Manually dispatch the Linux Actions workflow. It installs tools on the hosted
runner only; no device command exists in CI. It runs syntax/mocked guard and
negative image tests, compiles `allnoconfig` plus the reviewed fragment, checks
resolved config and DT, builds cpio, packages header-v1/page4096 gzip Image plus
one DTB, then independently parses every embedded component and source revision.

Outputs on success include Image, DTB, cpio, unsigned
`boot-pcam00-ramdiag-UNTESTED.img`, config, verification JSON, source snapshot,
Debian package provenance and recursive checksums. Failure evidence may contain
incomplete output; filename/ZIP existence is not success.

Using a separately trusted source checkout, never artifact-supplied executable
code:

```sh
python3 scripts/artifact_check.py --artifacts /absolute/unpacked/artifacts \
  --source-root /absolute/trusted/source --output /absolute/new-report.json
```

Source-manifest hashes establish equality to supplied trusted source, not remote
build authentication. Verify the GitHub run's actual commit separately.

**No automatic flash/recovery installer is included.** TWRP steps stay conditional
on DTBO/handoff compatibility, a specified image hash and a working route to the
phone's own original boot. Existing unlock/root/backup status is respected.
