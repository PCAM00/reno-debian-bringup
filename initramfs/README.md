# Debian-sourced PCAM00 B v4 initramfs

**Target PID1 code. Do not run or source the [init program](<init>) on the host.**
This is a native RAM diagnostic, not installed Debian or a desktop. Debian
`busybox-static:arm64` and `base-files` are extracted using authenticated APT;
maintainer scripts, chroot, container init and target boot code are not run.

## Runtime contract

- One 180-second monotonic deadline after initial RAM/virtual filesystem setup;
  synchronously checked by PID1, not a hardware watchdog or automatic rollback.
- Enumerated thermal zones must all return plausible readings below the diagnostic
  70000-mC threshold. Partial failures are rejected, and baseline identity/missing
  zones are checked. This does not prove all sensors probed or provide battery
  protection. CPU governors are read-only checked for powersave.
- PID1 attempts to terminate its HTTP session process group, lower the NCM
  interface/unbind its own gadget, then request bounded ordinary reboot. Duplicate
  stop signals are ignored during cleanup. If reboot returns, it stays stopped.
  Kernel uninterruptible sleep, early hangs and power/reset faults remain risks.
- No block storage, NVM, bootloader state, persistent log or partition write.
- RAM stage records become visible only once USB NCM/HTTP works. No external
  pre-USB console is claimed. ACM is only enumeration, not an interactive shell.
- Periodic plain-text snapshots are exported via a read-only bind view at
  `/live/`; no CGI/symlink/upload/command endpoint. Only trusted directly attached
  host 192.168.7.1 may access 192.168.7.2:8080. No routing/DHCP/Internet.
- USB MaxPower=100 is descriptor metadata, not a charging current limit.

If boot was persistently replaced via TWRP, ordinary reboot boots the replacement
again. It does not restore original Android. TWRP tests are a later separate
operation, after compatibility and the phone's own restoration route are clear.

## Packaging

[Build script](<build.sh>) is intended for the GitHub-hosted Ubuntu24.04 runner,
not the phone/SSH bridge. It extracts Debian packages in an isolated APT directory,
requires architecture/static-ELF checks, verifies required BusyBox applets via
QEMU-user (not a phone emulation) and writes a root-owned gzip/newc archive.

Files [safety helper](<safety.sh>), [runtime coordinator](<runtime.sh>) and
[USB helper](<usb.sh>) are copied byte-for-byte to their `/etc/ramdiag-*` paths,
as is the source [version marker](<../REVISION>) to `/etc/ramdiag-revision`.
The kernel boot wrapper is checked by the [artifact verifier](<../scripts/artifact_check.py>),
which compares all these embedded files against trusted reviewed source.

[Tests](<test_initramfs.py>) source only side-effect-free state/thermal functions
with synthetic sysfs and mocked terminate/unbind/reboot hooks. They never launch
the target init or touch actual devices. Optional CI applet semantic checks run
only inert scripts under QEMU on the runner, never mounting or rebooting it.

The source-only helpers have no side effects until explicitly called. The USB
helper does have hardware/configfs operations when called by target PID1; do not
call it on a workstation or running Android system.

Authenticated Debian versions and signatures are recorded, not promised
snapshot-pinned. Build scripts require fresh dedicated artifact directories;
old outputs must not be presented as a new revision. No flash script is included.
