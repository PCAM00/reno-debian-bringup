# Reno PCAM00：UEFI 原型的使用与刷机教程

> **状态表（先看这个）**
>
> | 项目 | 状态 |
> |---|---|
> | 云编译（GitHub Actions） | 见第 11 节，以实际 run 号与退出码为准 |
> | 产物独立校验 | 下载后在主机重算 SHA256，与 `SHA256SUMS` 比对 |
> | 手机真机启动 | **未做过，一次也没有** |
> | 可刷 boot 镜像 | **没有刻意生成**，原因见第 1 节末 |
> | 恢复路径演练 | **未演练** |
> | userdata 完整备份 | **没有**（约 203 GiB 已用） |
>
> 本教程教你**安全地拿到并验证编译产物**，以及**在满足全部前置条件后**怎么继续。
> 它**不承诺**这个 UEFI 能在 Reno 上启动，也**不提供**现在就能照抄执行的刷机命令。

---

## 1. 这份东西是什么

用固定版本的 U-Boot（`57UU/u-boot@a2a847b1`，U-Boot 2026.07）交叉编译出一个 aarch64 的
UEFI 环境，配一份 Reno 专属设备树和一个 EFI 应用样例（`helloworld.efi`）。

用途是先把"**能不能编译、板级资源对不对、EFI 代码能不能链接**"验证掉，
把不确定性从"整机能不能跑"缩小到"启动链能不能交接"。

它**不是**：

- 不是 Reno 的板级移植（U-Boot 里根本没有 Reno 板级支持）；
- 不是完整 Debian（连 rootfs 都没有）；
- 不是可刷固件（没有打包成 Android boot 镜像）；
- 不能证明屏幕、触控、Wi-Fi、蓝牙、音频、充电任何一项能用。

**为什么不做成"看起来能刷"的 boot.img**：Android boot 头里的 `kernel_addr`、`ramdisk_addr`、
`tags_addr`、页大小、header 版本都来自**样本文件**，不是 ABL 的真实落点。把未验证头部的镜像
包成 `boot.img`，只会诱导人直接 `fastboot flash boot`。正确顺序是：先确认 ABL 行为 → 再确认
内存交接 → 再谈封装 → 最后才谈写入。

---

## 2. 架构与数据流

```
ABL（原厂，未修改）
   │  加载 Android boot 镜像 / 或 fastboot boot 一次性加载
   ▼
本工程编译的 U-Boot
   │  解析上一阶段传入的 FDT，取 memory 与保留区
   ▼
UEFI 环境（EFI_LOADER）
   │  只允许从内存加载 EFI 应用，块层下面没有任何真实介质
   ▼
EFI 应用（现在是 helloworld.efi，以后才是 Debian 的 EFI stub）
```

UEFI 只是**启动接口**，让"加载什么、怎么传参"变规范；它**不会**自动带来屏幕、触控、
UFS、Wi-Fi 驱动。

---

## 3. 云编译

### 3.1 触发

仓库 **Actions** 页 → **"Reno UEFI memory-only prototype (compile only)"** → **Run workflow**（`main`）。

CI 顺序：

1. 拉固定提交的 U-Boot，并用 `git rev-parse` 断言提交号一致；
2. `bash -n` + 审计脚本单元测试（测试会故意构造危险配置，必须全部被拒绝）；
3. `qcom_defconfig` → 合并 Reno 片段 → `olddefconfig`；
4. 静态审计，**不通过直接失败**；
5. 交叉编译；
6. 再审计一次，并扫描二进制里有没有 `saveenv`/`fastboot`；
7. 打包 `SHA256SUMS` 并上传。

CI 里**没有** `adb`、`fastboot`、`reboot`、`flash`。

### 3.2 产物清单

| 文件 | 含义 |
|---|---|
| `u-boot.elf` | 带符号 ELF，核对架构与符号 |
| `u-boot.bin` | 实际会被加载的二进制 |
| `u-boot-nodtb.bin` | 未附加设备树的二进制 |
| `pcam00-uefi-memonly.dtb` | Reno 专属设备树 |
| `helloworld.efi` | EFI 样例，仅备将来验证加载器；编译出来不等于会执行它 |
| `uefi.config` | 解析后的最终配置，安全断言都基于它 |
| `validation.json` | 审计结果，必须 `"result": "PASS"` |
| `toolchain.txt` | 编译器版本与上源提交，用于复现 |
| `SHA256SUMS` | 各项校验和 |

---

## 4. 下载后的离线校验

```bash
cd pcam00-uefi-memonly-*

# 1) 校验和
sha256sum -c SHA256SUMS

# 2) 审计必须 PASS 且无错误
python3 -c "import json;d=json.load(open('validation.json'));\
assert d['result']=='PASS',d;assert not d['errors'],d['errors'];print('audit PASS')"

# 3) 必须是 64 位 ARM
file u-boot.elf u-boot.bin helloworld.efi
readelf -h u-boot.elf | grep -E 'Class|Machine|Type'
```

`readelf` 应显示 `ELF64` / `AArch64`；不是的话说明工具链用错了，别往手机上放。

```bash
# 4) 设备树关键属性
fdtget -t s pcam00-uefi-memonly.dtb / model
fdtget -t s pcam00-uefi-memonly.dtb / compatible
fdtget     pcam00-uefi-memonly.dtb / oppo,prjversion   # 期望 1 18097
fdtget     pcam00-uefi-memonly.dtb / qcom,board-id     # 期望 8 0
fdtget     pcam00-uefi-memonly.dtb /memory reg         # 期望 0 0 0 0（由 bootloader 填）
fdtget -t s pcam00-uefi-memonly.dtb /soc@0/ufshc@1d84000 status   # 期望 disabled
```

```bash
# 5) 二进制里不应有刷写命令（只扫描会被加载的 PT_LOAD 段）
python3 -c "import json;d=json.load(open('validation.json'));\
print('\n'.join(d['checks']));print('warnings:',d.get('warnings'))"
```

```bash
# 6) 确认危险项是"最终配置里为 n"
grep -E 'CONFIG_(SCSI|UFS|UFS_QCOM|MMC|NVME|MTD|USB_STORAGE|FASTBOOT|USB_FUNCTION_FASTBOOT|CMD_DFU|DFU_MMC|DFU_SCSI|CMD_BCB|CMD_SAVEENV|CMD_GPT|EFI_CAPSULE_ON_DISK|EFI_VARIABLE_FILE_STORE|BOOTSTD)' uefi.config
```

---

## 5. 回家之后：先只读确认，再备份

> 以下命令**供你本人在确认设备、明确同意只读备份时执行**。本轮助手没有执行任何一条。

### 5.1 定向确认（不读序列号、不全量 getprop）

```bash
adb get-state
adb exec-out su -c 'id -u; getprop ro.product.model; getprop ro.product.device; \
  getprop ro.build.display.id; getprop ro.boot.slot_suffix'
```

`ro.product.model` 应为 `PCAM00`。**这只是软件证据**，仍要你自己确认手里就是这台机器，
且 ADB 只连了这一台。

```bash
adb exec-out su -c '
for d in /dev/block/by-name /dev/block/bootdevice/by-name; do
  [ -d "$d" ] || continue
  for p in boot boot_a boot_b recovery recovery_a recovery_b dtbo dtbo_a dtbo_b vbmeta vbmeta_a vbmeta_b; do
    b="$d/$p"; [ -b "$b" ] || continue
    r=$(readlink -f "$b") || exit 1
    n=$(blockdev --getsize64 "$b") || exit 1
    printf "%s -> %s : %s bytes\n" "$b" "$r" "$n"
  done
done'
```

**只有实际存在的分区才进下一步**。不要猜 `sde`/`sdf`/`sdX`——系统盘在 UFS，静态设备号是整 LUN。

### 5.2 备份当前**能启动的** boot/recovery/dtbo/vbmeta

要点：备份**你现在正在用的、被 Magisk 修补过的**镜像，不是固件包里的原厂样本。

```bash
set -euo pipefail
umask 077

[[ $(adb get-state) == device ]]
[[ $(adb exec-out su -c 'id -u' | tr -d '\r\n') == 0 ]]
[[ $(adb exec-out su -c 'getprop ro.product.model' | tr -d '\r\n') == PCAM00 ]]

BY=/dev/block/by-name            # 换成 5.1 实际确认的目录
parts=(boot recovery dtbo vbmeta) # 与实机布局一致；A/B 机器写对应槽位
case "$BY" in
  /dev/block/by-name|/dev/block/bootdevice/by-name) ;;
  *) exit 1 ;;
esac

out=$(mktemp -d "${PWD:?}/pcam00-liveboot.XXXXXXXX")

adb exec-out su -c '
getprop ro.product.model
getprop ro.product.device
getprop ro.build.display.id
getprop ro.boot.slot_suffix' > "$out/device-evidence.txt"

for p in "${parts[@]}"; do
  case "$p" in
    boot|boot_a|boot_b|recovery|recovery_a|recovery_b|\
    dtbo|dtbo_a|dtbo_b|vbmeta|vbmeta_a|vbmeta_b) ;;
    *) exit 1 ;;
  esac

  b="$BY/$p"
  [[ $(adb exec-out su -c "if [ -b '$b' ]; then printf OK; fi" | tr -d '\r\n') == OK ]]

  real=$(adb exec-out su -c "readlink -f '$b'" | tr -d '\r\n')
  [[ "$real" == /dev/block/* ]]

  bytes=$(adb exec-out su -c "blockdev --getsize64 '$b'" | tr -d '\r\n')
  [[ "$bytes" =~ ^[1-9][0-9]*$ ]]

  before=$(adb exec-out su -c "sha256sum '$b'" | tr -d '\r'); before=${before%% *}
  [[ "$before" =~ ^[0-9a-f]{64}$ ]]

  f="$out/live-$p.img"
  adb exec-out su -c "dd if='$b' bs=1048576 2>/dev/null" > "$f"   # 只有 if=，不写手机
  [[ $(wc -c < "$f") -eq "$bytes" ]]

  host=$(sha256sum "$f"); host=${host%% *}

  after=$(adb exec-out su -c "sha256sum '$b'" | tr -d '\r'); after=${after%% *}
  [[ "$after" =~ ^[0-9a-f]{64}$ ]]

  # 传输前设备哈希 = 主机哈希 = 传输后设备哈希
  [[ "$before" == "$host" && "$host" == "$after" ]]

  printf '%s\t%s\t%s\t%s\n' "$p" "$real" "$bytes" "$host" >> "$out/manifest.tsv"
  printf '%s  live-%s.img\n' "$host" "$p" >> "$out/SHA256SUMS"
done

# 相对路径，复制到第二个介质后可在原地复验
( cd -- "$out"
  sha256sum manifest.tsv device-evidence.txt >> SHA256SUMS
  sha256sum -c SHA256SUMS )

echo "备份已校验：$out"
```

备份过程中**不要** OTA、不要再 Magisk 修补、不要更新 recovery，否则前后哈希对不上。

把整个目录复制到**第二块离线介质**，然后在第二份目录里再跑一次 `sha256sum -c SHA256SUMS`。

### 5.3 这份备份**不包含**什么

- 不含 userdata（约 203 GiB）与应用私有数据；
- 不含校准区、`persist`、`modemst`、`fsg`、RPMB、密钥；
- 不含完整 AVB 链（若有 `vbmeta_system`/`vbmeta_vendor` 需另外确认备份）；
- 不是"救砖已验证"；EDL/Firehose 是否可用、授权码在哪都未确认。

**四区齐全 ≠ 数据安全。** 要保数据须另做逻辑导出并**抽查恢复**。

---

## 6. 启动关卡表（未知就停）

| # | 关卡 | 当前状态 | 通过标准 |
|---|---|---|---|
| 1 | 目标设备确认 | 部分 | 实物 + `ro.product.model=PCAM00` + ADB 只连一台 |
| 2 | 分区布局与槽位 | 有记录，需复核 | 5.1 输出与记录一致 |
| 3 | 解锁状态 | **矛盾未解** | getprop 报 locked/green、cmdline 报 orange，必须查清 |
| 4 | ABL 是否支持 `fastboot boot` | **未知** | 该 ABL 的实现审计或可靠既有证据；**不能靠试发命令探测** |
| 5 | 单 DTB 选择与 stock DTBO 合并 | **未知** | 明确用哪个 DTB、是否自动叠 DTBO |
| 6 | 真实 RAM 与保留区交接 | **未知** | 明确 `/memory` 由谁填、保留区不被覆盖 |
| 7 | TZ/DMA 权限 | **未知** | 未验证 |
| 8 | 默认启动路径无持久写 | 本工程已做静态保证 | `validation.json` PASS |
| 9 | 已验证的恢复路径 | **未演练** | 实际走一遍"回到 Android" |
| 10 | userdata 完整备份 | **没有** | 逻辑导出 + 抽查恢复成功 |

**第 3、4、5、6、9 项任一为"未知"，就不要往下走。**

---

## 7. 如果 ABL 支持一次性加载（条件性临时启动）

前提：第 6 节全部有证据 + 你本人对**这一次**操作明确同意 + 有人看护 + 主机能看日志。

> 临时启动 = 已验证支持 `fastboot boot` 的本机 ABL
> ＋ 与这个 ABL 兼容、SHA256 匹配的 PCAM00 UEFI Android-boot 封装
> ＋ 逐次授权。

**本教程现在不给可复制执行的启动命令**，因为：还没有这个 UEFI 的 boot 封装（本工程刻意不生成）；
头部加载地址来自样本字段未验证；ABL 行为未知。

关卡补齐后再回来填这一节。

观察项：是否出现输出；30 秒内是否自行重启/黑屏卡死；是否明显发热。
异常立刻断开并长按电源键复位。**不要**把定时重启当唯一保险。

---

## 8. 如果 ABL **不**支持一次性加载

那就诚实写：**当前 ABL 没有已验证的一次性启动路线，本候选不能按本教程安全试启动。**

正式写入是另一条独立路线，需要单独验证与单独授权：

```
【已验证的写入通道】
   → 【人工核对的单一 boot 类目标】
   → 【指定 SHA256、确认兼容的 UEFI 封装】
   → 【该版本已验证的启动方式】
   → 【已演练的原镜像恢复路线】
```

五个方框填实之前，不要出现任何 `fastboot flash`。

**不要**为绕开问题去擦 DTBO、关 AVB、改 XBL/ABL/GPT、动 `persist`/`modemst`。

---

## 9. 失败之后怎么回来

1. 优先走已验证过的路径（通常还是 fastboot 或 recovery）；
2. 恢复到**当时能启动的 live patched boot**，配套 dtbo/vbmeta 一起；
3. 不要用固件包里的原厂样本覆盖当前工作系统——未必能在现有解锁/AVB 状态下启动；
4. 没搞清状态时不要重锁 bootloader。

---

## 10. 后续里程碑（按顺序，别跳）

1. **EFI 入口/退出**：U-Boot 能起来、能返回 Android（这才叫第一份实验结果）；
2. 从内存加载 `helloworld.efi` 并看到输出；
3. 用 EFI stub 启动一个**最小** Linux（仍 RAM-only、无 rootfs）；
4. UFS 只读：需要额外做"拒写补丁"——U-Boot 的 SCSI 层默认带 `.write`/`.erase`；
5. 显示、触控、USB、Wi-Fi、蓝牙、音频、充电——逐项验证；
6. 最后才是 rootfs 与日常可用性。

第一版 UEFI ≠ 完整 Debian ≠ 硬件已通。

---

## 11. 本次运行记录（真实结果）

| 项目 | 结果 |
|---|---|
| 第一次运行 | run `41651109704`，**失败**（失败点见下） |
| 第二次运行 | run `41652024234`，commit `6d02dcc`，**success** |
| 产物 | `pcam00-uefi-memonly-3`（3.95 MB） |
| 编译器 | aarch64-linux-gnu-gcc 13.3.0（Ubuntu 24.04） |
| U-Boot 上源 | `a2a847b1a84e6bcbdbc64f0c20e07fd69e0452fd` |
| 独立校验 | 8 个文件 `sha256sum -c` 全 OK；`validation.json` = PASS，0 错误 |
| 配置审计 | 9 项必需全开；40 项可写/刷写能力全关 |
| 架构 | ELF64 / AArch64，但 **Type=DYN、入口 0x0** |
| 设备树 | `oppo,pcam00`+`qcom,sdm710`+`qcom,sdm670`，项目 18097，board-id 8 0，memory 全 0 |
| 真机 | **没有跑过** |

### 第一次为什么失败（这是检查在起作用）

第一次运行编译本身是成功的，是**构建后的二进制审计**判定失败：
`u-boot.elf` 里出现 `fastboot`。追查后发现它只是网络代码里的环境变量名
`FASTBOOT_UDP`/`FASTBOOT_TCP`，位于 ELF 的符号字符串区，`u-boot.bin` 里 0 次。
于是把扫描改为**只看 ELF 的 `PT_LOAD` 段**（真正会被加载进内存的字节），
并补了单元测试：段内命中必须报错、段外命中必须忽略。这不是放宽标准，是修正了扫描对象。

### 两个必须知道的事实

1. **产物是位置无关（`ET_DYN`、入口 `0x0`）的重定位镜像。**
   它不能被 `dd` 到某个地址就跳过去执行，也不是能让 ABL 直接当裸内核加载的东西。
   它需要上一阶段的加载器按 qcom 的重定位规则处理，或者被包进合适的容器。
   这一条直接决定了"现在还不能刷"。

2. **`EFI_PARTITION`/`ISO_PARTITION` 没能关掉。**
   `EFI_LOADER` 拉入 `BOOT_DEFAULTS_FEATURES`，后者 `imply` 它们，显式关闭未生效。
   它们是分区表**解析器**，背后没有任何块设备，因此不构成写入能力；
   构建告警里的 `gpt` 字样就来自这里。其余 40 项可写能力确实全为 `n`。

**编译成功 ≠ 能在手机上启动。** 真正的第一份实验结果，是第 10 节第 1 项的"能起来、能返回"。
