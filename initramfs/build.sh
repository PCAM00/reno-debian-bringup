#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
: "${OUT_DIR:?Set OUT_DIR to an absolute output directory}"
[[ $OUT_DIR = /* && $OUT_DIR != / ]] || exit 2
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
KEYRING=${DEBIAN_ARCHIVE_KEYRING:-/usr/share/keyrings/debian-archive-keyring.gpg}
VERIFY_APPLETS=${VERIFY_APPLETS:-auto}
RECOVERY_SECONDS=${RECOVERY_SECONDS:-180}
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-0}
[[ $VERIFY_APPLETS =~ ^(auto|required|off)$ ]] || exit 2
[[ $RECOVERY_SECONDS =~ ^[0-9]+$ && ${#RECOVERY_SECONDS} -le 5 ]] || exit 2
[[ $SOURCE_DATE_EPOCH =~ ^[0-9]+$ && ${#SOURCE_DATE_EPOCH} -le 10 ]] || exit 2
for cmd in apt-get dpkg-deb gpgv readelf python3 gzip sha256sum realpath; do command -v "$cmd" >/dev/null; done
[[ -r $KEYRING && $KEYRING = /* ]] || { echo 'Missing absolute Debian archive keyring' >&2; exit 1; }
# These paths are embedded in APT configuration and sources syntax.
for value in "$OUT_DIR" "$KEYRING"; do
    [[ $value != *[[:space:]\"\\]* ]] || { echo 'APT paths cannot contain whitespace, quotes or backslashes' >&2; exit 2; }
done
mkdir -p -- "$OUT_DIR"
OUT_DIR=$(realpath -- "$OUT_DIR")
[[ $OUT_DIR != / ]] || exit 2
work=$(mktemp -d "$OUT_DIR/.initramfs-work.XXXXXX")
cleanup() {
    local resolved
    resolved=$(realpath -- "$work")
    if [[ $resolved = "$work" && $(dirname -- "$resolved") = "$OUT_DIR" && $(basename -- "$resolved") = .initramfs-work.* ]]; then
        rm -rf -- "$resolved"
    fi
}
trap cleanup EXIT
mkdir -p "$work"/{state/lists/partial,cache/archives/partial,log,downloads,root,base,keyring}
mkdir -p "$OUT_DIR/provenance"
: > "$work/status"
# APT_CONFIG is read first: exclude host sources, hooks, preferences and dpkg state.
cat > "$work/apt.conf" <<EOF
Dir::Etc::main "-";
Dir::Etc::parts "-";
Dir::Etc::sourcelist "$work/sources.list";
Dir::Etc::sourceparts "-";
Dir::Etc::preferences "-";
Dir::Etc::preferencesparts "-";
Dir::State "$work/state";
Dir::State::status "$work/status";
Dir::Cache "$work/cache";
Dir::Log "$work/log";
APT::Architecture "arm64";
APT::Architectures { "arm64"; };
APT::Sandbox::User "$(id -un)";
Acquire::Languages "none";
Acquire::AllowInsecureRepositories "false";
Acquire::AllowDowngradeToInsecureRepositories "false";
APT::Get::AllowUnauthenticated "false";
Acquire::Check-Valid-Until "true";
EOF
export APT_CONFIG="$work/apt.conf"
# Noble's older keyring trusts bookworm: authenticate bootstrap to current keys.
printf 'deb [arch=arm64 signed-by=%s] https://deb.debian.org/debian bookworm main\n' "$KEYRING" > "$work/sources.list"
apt-get -o APT::Update::Error-Mode=any update
(cd "$work/downloads" && apt-get download debian-archive-keyring)
keydeb=("$work/downloads"/debian-archive-keyring_*.deb)
[[ ${#keydeb[@]} = 1 && -f ${keydeb[0]} ]]
dpkg-deb -x "${keydeb[0]}" "$work/keyring"
cp "$work/sources.list" "$OUT_DIR/provenance/bootstrap.sources.list"
cp "$work/state/lists/"*InRelease "$OUT_DIR/provenance/"
key="$work/keyring/usr/share/keyrings/debian-archive-keyring.gpg"
[[ -s $key ]]
printf 'deb [arch=arm64 signed-by=%s] https://deb.debian.org/debian trixie main\n' "$key" > "$work/sources.list"
apt-get -o APT::Update::Error-Mode=any update
(cd "$work/downloads" && apt-get download busybox-static:arm64 base-files:arm64)
cp "$work/sources.list" "$OUT_DIR/provenance/trixie.sources.list"
cp "$work/state/lists/"*InRelease "$OUT_DIR/provenance/"
cp "$key" "$OUT_DIR/provenance/debian-archive-keyring.gpg"
report="$OUT_DIR/package-provenance.txt"
{
    printf 'Debian-sourced minimal diagnostics; NOT an installed/full Debian system.\n'
    printf 'Repository: https://deb.debian.org/debian ; suites: bookworm (keyring), trixie\n'
    printf 'APT signature/package hash verification required; no maintainer scripts.\n'
    printf 'SOURCE_DATE_EPOCH=%s\nRECOVERY_SECONDS=%s\n' "$SOURCE_DATE_EPOCH" "$RECOVERY_SECONDS"
    printf '\nInitial trust keyring SHA256:\n'; sha256sum "$KEYRING"
    for deb in "$work/downloads/"*.deb; do
        printf '\n'; dpkg-deb -f "$deb" Package Version Architecture Source
        printf 'SHA256: '; sha256sum "$deb" | cut -d' ' -f1
    done
} > "$report"
busydeb=("$work/downloads"/busybox-static_*.deb)
basedeb=("$work/downloads"/base-files_*.deb)
[[ ${#busydeb[@]} = 1 && ${#basedeb[@]} = 1 ]]
[[ $(dpkg-deb -f "${busydeb[0]}" Architecture) = arm64 ]]
[[ $(dpkg-deb -f "${basedeb[0]}" Architecture) = arm64 ]]
dpkg-deb -x "${busydeb[0]}" "$work/busybox"
dpkg-deb -x "${basedeb[0]}" "$work/base"
root="$work/root"
mkdir -p "$root"/{bin,etc,dev,proc,sys,run,tmp,www}
busy="$work/busybox/usr/bin/busybox"
[[ -f $busy ]] || busy="$work/busybox/bin/busybox"
cp "$busy" "$root/bin/busybox"
readelf -h "$root/bin/busybox" > "$work/elf-header"
readelf -l "$root/bin/busybox" > "$work/elf-program"
python3 - "$work/elf-header" "$work/elf-program" <<'PY'
import pathlib, sys
assert 'AArch64' in pathlib.Path(sys.argv[1]).read_text(), 'Not AArch64'
assert 'INTERP' not in pathlib.Path(sys.argv[2]).read_text(), 'Not static'
PY
applets='sh mount mkdir cat dmesg ip httpd sleep reboot ln chmod'
if [[ $VERIFY_APPLETS != off ]] && command -v qemu-aarch64 >/dev/null; then
    qemu-aarch64 "$root/bin/busybox" --list > "$work/applets"
    python3 - "$work/applets" $applets <<'PY'
import pathlib, sys
missing = set(sys.argv[2:]) - set(pathlib.Path(sys.argv[1]).read_text().splitlines())
assert not missing, f'Missing BusyBox applets: {sorted(missing)}'
PY
    printf '\nRequired applets: verified via qemu-aarch64 --list\n' >> "$report"
else
    [[ $VERIFY_APPLETS != required ]] || { echo 'Install qemu-user to verify applets' >&2; exit 1; }
    printf '\nWARNING: applets unverified; use qemu-user and VERIFY_APPLETS=required.\n' >> "$report"
fi
for applet in $applets; do ln -s busybox "$root/bin/$applet"; done
cp "$work/base/usr/lib/os-release" "$root/etc/os-release"
cp "$work/base/etc/debian_version" "$root/etc/debian_version"
printf '%s\n' "$RECOVERY_SECONDS" > "$root/etc/recovery-seconds"
printf 'A:192.168.7.1\nD:*\n' > "$root/etc/httpd.conf"
cp "$report" "$root/etc/package-provenance.txt"
cp "$HERE/safety.sh" "$root/etc/ramdiag-safety.sh"
cp "$HERE/init" "$root/init"
chmod 0755 "$root/init" "$root/bin/busybox"
python3 "$HERE/mkcpio.py" "$root" "$SOURCE_DATE_EPOCH" | gzip -n -9 > "$OUT_DIR/initramfs-debian-arm64.cpio.gz"
sha256sum "$OUT_DIR/initramfs-debian-arm64.cpio.gz" >> "$report"
printf 'Built %s\n' "$OUT_DIR/initramfs-debian-arm64.cpio.gz"
