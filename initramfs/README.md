# Debian-sourced RAM diagnostics for experimental Reno/SDM710

This is **not an installed Debian system or a desktop**. Only the official
`busybox-static:arm64` executable and `base-files:arm64` release metadata are
copied into a small custom initramfs. There is no package database, systemd,
root filesystem installation, network shell, SSH, telnet, getty, or mass storage.

## Build on GitHub Actions Ubuntu 24.04 x86_64

Prerequisites (install via Ubuntu's authenticated APT; no GitHub token required):

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends apt dpkg debian-archive-keyring \
  gpgv ca-certificates binutils python3 gzip coreutils bash qemu-user
OUT_DIR="$RUNNER_TEMP/reno-initramfs" VERIFY_APPLETS=required bash build.sh
PYTHONDONTWRITEBYTECODE=1 python3 test_initramfs.py
```

Invoke from this directory, or use the full script path. Run the build as the
normal runner user, not sudo. Output must be a disposable, dedicated directory
on a filesystem with POSIX permissions/symlinks (not Android shared storage).
The script only writes in `OUT_DIR`, including its isolated APT workspace; it
does not modify host APT configuration, add an architecture, install target
packages, run dpkg maintainer scripts, chroot, or run a foreign init. QEMU is
optional and executes only `busybox --list`; require it in CI to fail closed if
any required applet is missing. `VERIFY_APPLETS=off` executes no foreign code.

| Environment | Meaning |
| --- | --- |
| `OUT_DIR` | Required absolute directory; must not resolve to `/`. Existing named outputs are replaced. No whitespace, double quotes or backslashes (APT config syntax). |
| `DEBIAN_ARCHIVE_KEYRING` | Absolute initial trust anchor; default `/usr/share/keyrings/debian-archive-keyring.gpg`. |
| `VERIFY_APPLETS` | `auto` (default), `required`, or `off`. Unverified builds have an explicit report warning. |
| `RECOVERY_SECONDS` | Decimal 0–99999; default `180`; `0` disables automatic reboot. Built into image, not a kernel command-line parser. |
| `SOURCE_DATE_EPOCH` | Decimal archive mtime, default `0`; maximum 4294967295. gzip has no timestamp. |

Outputs: `initramfs-debian-arm64.cpio.gz`, `package-provenance.txt`, and
`provenance/` (authenticated InRelease files, source definitions and effective
keyring). The report contains package versions, architectures and SHA256s.
Source definitions record temporary trust-anchor paths, not reusable APT config.
APT validates signatures and package hashes against HTTPS official Debian
repositories. Ubuntu Noble's older Debian keyring is upgraded **by extraction**
from the authenticated bookworm `debian-archive-keyring` package before trixie
is queried. Missing keys or bad signatures abort, never fall back to insecure
sources. Current suite versions float; metadata and hashes record the selected
packages, but this is not a snapshot-pinned historical reproducibility promise.

## Kernel/runtime contract

Build in ARM64 ELF/script execution, gzip initramfs support, `CONFIG_BLOCK=n`,
procfs, sysfs, devtmpfs, tmpfs, configfs, IPv4 networking, USB gadget/DWC3 device
mode and configfs NCM + ACM; use built-ins, not modules. The image's executable
`/init` and a `0600` character `/dev/console` (major 5, minor 1) are encoded by the
unprivileged Python newc writer. No cpio executable, sudo or `mknod` is needed.
The kernel/DTS must actually register a working UDC and USB PHY. Nothing here
validates hardware bring-up, powers unsupported components, or installs a kernel.

Init mounts only proc/sysfs/devtmpfs/tmpfs/configfs. It never discovers, mounts,
formats, repairs, resizes or writes internal partitions; it writes no
persist/misc/firmware data or bootloader reboot requests. It configures a
RAM-backed NCM+ACM gadget, waits up to 40 seconds for a UDC and reads the NCM
function's actual interface name. Lab-only VID/PID `1d6b:0104` must not be treated
as a production USB identity. Fixed MACs mean connect only one such device per
link:

- phone: `02:00:00:07:00:02`, `192.168.7.2/30`
- USB host: `02:00:00:07:00:01`; manually assign `192.168.7.1/30`
- diagnostic URL: `http://192.168.7.2:8080/`

No DHCP, DNS, routing or Internet is configured. The only network listener is
BusyBox httpd bound to the USB IP/8080, dropped to UID/GID 65534 and allowlisted
to `.1`. It serves a **read-only mounted, noexec** tmpfs of static boot status,
dmesg, release metadata and provenance. No CGI, links, uploads or commands are
present. Logs are an initial snapshot, not live streaming. This diagnostic
endpoint is not encrypted/authenticated: connect only to a trusted USB host.
ACM enumerates for driver testing only; no serial shell/input service is started.

The default independent 180-second timer runs `reboot -f` (ordinary kernel
reboot, no bootloader argument or misc write). This is a best-effort recovery
convenience, **not a safety guarantee**: a kernel hang or missing reboot driver
can defeat it. Failure paths idle instead of spawning a shell. The timer starts
before mounts/USB setup and intentionally disconnects diagnostics when it fires.

## Validation limits

Offline tests check shell syntax, newc records/padding/root ownership/console,
deterministic serialization and a limited RAM-only surface regression check.
They never execute init. Android shared storage cannot make symlinks, so that
fixture assertion is omitted there; Ubuntu covers it. Full authenticated
package resolution and optional ARM64 applet probing require the Ubuntu runner.
Neither local tests nor successful image generation prove the phone boots.
