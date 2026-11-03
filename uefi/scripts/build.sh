#!/usr/bin/env bash
# Build the Reno memory-only U-Boot/UEFI prototype.
# COMPILE-ONLY: produces ELF/bin/DTB/EFI sample plus audit records. No boot image.
set -euo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
: "${UBOOT_DIR:?absolute U-Boot source tree required}"
: "${UBOOT_OUT:?absolute build output directory required}"
: "${OUT_DIR:?absolute artifact directory required}"
case $UBOOT_DIR$UBOOT_OUT$OUT_DIR in /*/*/*) ;; *) echo 'paths must be absolute' >&2; exit 1;; esac
[[ -d $UBOOT_DIR ]] || { echo "missing U-Boot tree: $UBOOT_DIR" >&2; exit 1; }
[[ -f $UBOOT_DIR/Makefile ]] || { echo 'not a U-Boot tree' >&2; exit 1; }
[[ -e $UBOOT_OUT/.config ]] && { echo "refusing to reuse: $UBOOT_OUT/.config exists" >&2; exit 1; }

DT=sdm710-oppo-pcam00-uefi-memonly
frag=$HERE/board/oppo/pcam00/pcam00-uefi-memonly.config
envf=$HERE/board/oppo/pcam00/pcam00-uefi-memonly.env
for f in "$HERE/dts/$DT.dts" "$frag" "$envf"; do
	[[ -f $f ]] || { echo "missing source: $f" >&2; exit 1; }
done

mkdir -p "$UBOOT_OUT" "$OUT_DIR" "$UBOOT_DIR/board/oppo/pcam00"
install -m 0644 "$HERE/dts/$DT.dts" "$UBOOT_DIR/dts/upstream/src/arm64/qcom/$DT.dts"
install -m 0644 "$envf" "$UBOOT_DIR/board/oppo/pcam00/pcam00-uefi-memonly.env"

export ARCH=arm64
export CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
export KBUILD_BUILD_USER=reno-uefi
export KBUILD_BUILD_HOST=github-actions

echo '== qcom_defconfig =='
make -C "$UBOOT_DIR" O="$UBOOT_OUT" qcom_defconfig

echo '== merge Reno fragment =='
bash "$UBOOT_DIR/scripts/kconfig/merge_config.sh" -m -O "$UBOOT_OUT" \
	"$UBOOT_OUT/.config" "$frag"

echo '== olddefconfig =='
make -C "$UBOOT_DIR" O="$UBOOT_OUT" olddefconfig
cp "$UBOOT_OUT/.config" "$OUT_DIR/uefi.config"

echo '== static config audit =='
python3 "$HERE/scripts/validate_config.py" --config "$UBOOT_OUT/.config" \
	--output "$OUT_DIR/validation.json"

echo '== compile =='
make -C "$UBOOT_DIR" O="$UBOOT_OUT" -j"$(nproc)"

echo '== collect artifacts =='
dtb="$UBOOT_OUT/dts/upstream/src/arm64/qcom/$DT.dtb"
for pair in "u-boot:u-boot.elf" "u-boot-nodtb.bin:u-boot-nodtb.bin" \
	    "u-boot.bin:u-boot.bin" "lib/efi_loader/helloworld.efi:helloworld.efi"; do
	src=${pair%%:*}; dst=${pair##*:}
	[[ -f $UBOOT_OUT/$src ]] || { echo "missing build output: $src" >&2; exit 1; }
	cp "$UBOOT_OUT/$src" "$OUT_DIR/$dst"
done
[[ -f $dtb ]] || { echo "missing DTB: $dtb" >&2; exit 1; }
cp "$dtb" "$OUT_DIR/pcam00-uefi-memonly.dtb"

echo '== binary write-surface scan =='
python3 "$HERE/scripts/validate_config.py" --config "$UBOOT_OUT/.config" \
	--elf "$OUT_DIR/u-boot.elf" --bin "$OUT_DIR/u-boot.bin" \
	--output "$OUT_DIR/validation.json"

{
	echo "uboot_commit=$(git -C "$UBOOT_DIR" rev-parse HEAD)"
	echo "uboot_describe=$(git -C "$UBOOT_DIR" describe --always --dirty 2>/dev/null || echo unknown)"
	echo "cross_compile=$(command -v "${CROSS_COMPILE}gcc")"
	echo "gcc_version=$("${CROSS_COMPILE}gcc" --version | head -n1)"
	echo "build_host=$(uname -srm)"
} > "$OUT_DIR/toolchain.txt"

( cd "$OUT_DIR" && sha256sum u-boot.elf u-boot-nodtb.bin u-boot.bin \
	helloworld.efi pcam00-uefi-memonly.dtb uefi.config validation.json \
	toolchain.txt > SHA256SUMS )
echo '== done =='
