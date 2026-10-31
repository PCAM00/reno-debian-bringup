#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
: "${KERNEL_DIR:?absolute Linux source directory required}"
: "${KERNEL_OUT:?absolute empty build directory required}"
: "${OUT_DIR:?absolute empty artifact directory required}"
[[ $KERNEL_DIR = /* && $KERNEL_OUT = /* && $OUT_DIR = /* ]]
[[ ! -e $KERNEL_OUT/.config ]] || { echo 'Refusing to reuse configured build directory' >&2; exit 1; }
mkdir -p "$KERNEL_OUT" "$OUT_DIR"
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
export KBUILD_BUILD_USER=reno-builder KBUILD_BUILD_HOST=github-actions
export KBUILD_BUILD_TIMESTAMP='2026-10-31 00:00:00 UTC'
export SOURCE_DATE_EPOCH=1793404800
# GitHub-hosted build only; no phone, adb, flash, credentials or block targets.
cp "$HERE/dts/sdm710-oppo-pcam00-ramdiag.dts" "$KERNEL_DIR/arch/arm64/boot/dts/qcom/"
printf '\ndtb-$(CONFIG_ARCH_QCOM) += sdm710-oppo-pcam00-ramdiag.dtb\n' >> "$KERNEL_DIR/arch/arm64/boot/dts/qcom/Makefile"
make -C "$KERNEL_DIR" O="$KERNEL_OUT" KCONFIG_ALLCONFIG="$HERE/config/ramdiag.config" allnoconfig
cp "$KERNEL_OUT/.config" "$OUT_DIR/kernel.config"
python3 "$HERE/scripts/validate.py" --config "$KERNEL_OUT/.config"
make -C "$KERNEL_DIR" O="$KERNEL_OUT" -j"$(nproc)" Image qcom/sdm710-oppo-pcam00-ramdiag.dtb
DTB="$KERNEL_OUT/arch/arm64/boot/dts/qcom/sdm710-oppo-pcam00-ramdiag.dtb"
python3 "$HERE/scripts/validate.py" --config "$KERNEL_OUT/.config" --dtb "$DTB" --output "$OUT_DIR/validation.json"
cp "$KERNEL_OUT/arch/arm64/boot/Image" "$OUT_DIR/Image"
cp "$DTB" "$OUT_DIR/sdm710-oppo-pcam00-ramdiag.dtb"
cp "$HERE/dts/sdm710-oppo-pcam00-ramdiag.dts" "$OUT_DIR/"
dtc -I dtb -O dts "$DTB" -o "$OUT_DIR/compiled-tree.dts" 2> "$OUT_DIR/dtc-warnings.txt"
make -s -C "$KERNEL_DIR" O="$KERNEL_OUT" kernelrelease > "$OUT_DIR/kernelrelease.txt"
VERIFY_APPLETS=required OUT_DIR="$OUT_DIR" bash "$HERE/initramfs/build.sh"
python3 "$HERE/scripts/pack_boot.py" --image "$OUT_DIR/Image" --dtb "$DTB" \
  --ramdisk "$OUT_DIR/initramfs-debian-arm64.cpio.gz" --output "$OUT_DIR/boot-pcam00-ramdiag-UNTESTED.img"
cp "$HERE/README.md" "$OUT_DIR/README.md"
python3 - "$OUT_DIR" "$KERNEL_DIR" <<'PY'
import hashlib,json,pathlib,subprocess,sys
out,kernel=map(pathlib.Path,sys.argv[1:])
files={p.name:{'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(out.iterdir()) if p.is_file()}
manifest={'stage':'compile-only; not flashed or booted on Reno','kernel_commit':subprocess.check_output(['git','-C',str(kernel),'rev-parse','HEAD'],text=True).strip(),'files':files}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(out/'SHA256SUMS').write_text(''.join(f"{v['sha256']}  {k}\n" for k,v in files.items()))
PY
