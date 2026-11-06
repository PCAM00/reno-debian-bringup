#!/usr/bin/env bash
# Native Linux candidate only. Never invokes ADB, fastboot, recovery or devices.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
: "${KERNEL_DIR:?absolute Linux source directory required}"
: "${KERNEL_OUT:?absolute empty build directory required}"
: "${OUT_DIR:?absolute empty artifact directory required}"
[[ $KERNEL_DIR = /* && $KERNEL_OUT = /* && $OUT_DIR = /* ]]
[[ $KERNEL_DIR != / && $KERNEL_OUT != / && $OUT_DIR != / ]]
[[ ! -e $KERNEL_OUT/.config ]] || { echo 'Refusing to reuse configured build directory' >&2; exit 1; }
mkdir -p "$KERNEL_OUT" "$OUT_DIR"
python3 - "$OUT_DIR" "$KERNEL_OUT" "$HERE" <<'PY'
from pathlib import Path
import sys
out,kout,src=map(lambda p:Path(p).resolve(),sys.argv[1:])
if out==kout or out==src or kout==src or out.is_relative_to(kout) or kout.is_relative_to(out):
    raise SystemExit('Build and artifact paths must be distinct')
if any(out.iterdir()) or any(kout.iterdir()):
    raise SystemExit('Fresh output directories required; no stale artifact reuse')
PY
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
export KBUILD_BUILD_USER=reno-builder KBUILD_BUILD_HOST=github-actions
export KBUILD_BUILD_TIMESTAMP='2026-10-31 00:00:00 UTC'
export SOURCE_DATE_EPOCH=1793404800
export PYTHONDONTWRITEBYTECODE=1
[[ $(<"$HERE/REVISION") = pcam00-b-ramdiag-v4 ]]
[[ $(git -C "$KERNEL_DIR" rev-parse HEAD) = 17601ca5198a736f696447a9b6c568e4be97cc9c ]]
# Fixed revision, no vendor board or proprietary binary upload.
cp "$HERE/dts/sdm710-oppo-pcam00-ramdiag.dts" "$KERNEL_DIR/arch/arm64/boot/dts/qcom/"
printf '\ndtb-$(CONFIG_ARCH_QCOM) += sdm710-oppo-pcam00-ramdiag.dtb\n' >> "$KERNEL_DIR/arch/arm64/boot/dts/qcom/Makefile"
make -C "$KERNEL_DIR" O="$KERNEL_OUT" KCONFIG_ALLCONFIG="$HERE/config/ramdiag.config" allnoconfig
cp "$KERNEL_OUT/.config" "$OUT_DIR/kernel.config"
python3 "$HERE/scripts/validate.py" --config "$KERNEL_OUT/.config"
make -C "$KERNEL_DIR" O="$KERNEL_OUT" -j"$(nproc)" Image qcom/sdm710-oppo-pcam00-ramdiag.dtb
DTB="$KERNEL_OUT/arch/arm64/boot/dts/qcom/sdm710-oppo-pcam00-ramdiag.dtb"
python3 "$HERE/scripts/validate.py" --config "$KERNEL_OUT/.config" --dtb "$DTB" --output "$OUT_DIR/validation.json"
TEST_CONFIG="$KERNEL_OUT/.config" TEST_DTB="$DTB" python3 "$HERE/scripts/test_board.py"
cp "$KERNEL_OUT/arch/arm64/boot/Image" "$OUT_DIR/Image"
cp "$DTB" "$OUT_DIR/sdm710-oppo-pcam00-ramdiag.dtb"
cp "$HERE/dts/sdm710-oppo-pcam00-ramdiag.dts" "$OUT_DIR/"
dtc -I dtb -O dts "$DTB" -o "$OUT_DIR/compiled-tree.dts" 2> "$OUT_DIR/dtc-warnings.txt"
make -s -C "$KERNEL_DIR" O="$KERNEL_OUT" kernelrelease > "$OUT_DIR/kernelrelease.txt"
VERIFY_APPLETS=required RECOVERY_SECONDS=180 OUT_DIR="$OUT_DIR" bash "$HERE/initramfs/build.sh"
python3 "$HERE/scripts/pack_boot.py" --image "$OUT_DIR/Image" --dtb "$DTB" \
  --ramdisk "$OUT_DIR/initramfs-debian-arm64.cpio.gz" --output "$OUT_DIR/boot-pcam00-ramdiag-UNTESTED.img"
cp "$HERE/README.md" "$OUT_DIR/README.md"
cp "$HERE/REVISION" "$OUT_DIR/REVISION"
mkdir -p "$OUT_DIR/source-audit"
cp "$HERE/docs/oppo-source-audit.md" "$HERE/docs/oppo-source-files.json" "$HERE/docs/B-v4-启动条件与TWRP说明.md" "$OUT_DIR/source-audit/"
# Source snapshot is an explicit reviewed text scope; never archives its parent
# project, target backups, credentials, reference firmware, or CI artifacts.
python3 - "$HERE" "$OUT_DIR" <<'PY'
import json,os,pathlib,shutil,subprocess,sys
src,out=map(pathlib.Path,sys.argv[1:])
sys.path.insert(0,str(src/'scripts'))
from artifact_check import source_inventory
files=source_inventory(src)
manifest={'revision':'pcam00-b-ramdiag-v4','files':files}
source_commit=subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()
for name in files:
    p=out/'sources'/name;p.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(src/name,p)
with (out/'source-manifest.json').open('x') as f:json.dump(manifest,f,indent=2);f.write('\n')
with (out/'toolchain.txt').open('x') as f:
    f.write(subprocess.check_output(['aarch64-linux-gnu-gcc','--version'],text=True).splitlines()[0]+'\n')
    f.write('kernel_commit=17601ca5198a736f696447a9b6c568e4be97cc9c\n')
    f.write('source_commit='+source_commit+'\n')
    f.write('source_date_epoch='+os.environ['SOURCE_DATE_EPOCH']+'\n')
PY
python3 "$HERE/scripts/artifact_check.py" --artifacts "$OUT_DIR" --source-root "$HERE" --output "$OUT_DIR/artifact-verification.json"
python3 - "$OUT_DIR" "$KERNEL_DIR" <<'PY'
import hashlib,json,pathlib,subprocess,sys
out,kernel=map(pathlib.Path,sys.argv[1:])
check=json.loads((out/'artifact-verification.json').read_text())
if check['result']!='PASS' or check['source_manifest']['result']!='PASS':
    raise SystemExit('Artifact verifier/source equality not PASS')
files={}
for p in sorted(out.rglob('*')):
    if p.is_file():files[p.relative_to(out).as_posix()]={'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
manifest={'stage':'compile-only; not flashed or booted on Reno','revision':'pcam00-b-ramdiag-v4',
          'kernel_commit':subprocess.check_output(['git','-C',str(kernel),'rev-parse','HEAD'],text=True).strip(),
          'hardware_tested':False,'files':files}
with (out/'manifest.json').open('x') as f:json.dump(manifest,f,indent=2);f.write('\n')
manifest_bytes=(out/'manifest.json').read_bytes()
checks=''.join(f"{v['sha256']}  {k}\n" for k,v in files.items())
checks+=hashlib.sha256(manifest_bytes).hexdigest()+'  manifest.json\n'
with (out/'SHA256SUMS').open('x') as f:f.write(checks)
print('Artifact content, embedded source revision and recursive hashes checked; hardware remains UNTESTED.')
PY
