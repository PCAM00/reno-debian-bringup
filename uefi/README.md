# OPPO Reno PCAM00 — U-Boot/UEFI memory-only prototype

**状态：COMPILE-ONLY / UNTESTED / DO NOT FLASH。**

这里只做一件事：用固定版本的 U-Boot，交叉编译出一个**不含任何物理存储驱动、不含任何刷写入口**的
aarch64 UEFI 环境，外加一个 Reno 专属设备树和一个 EFI 应用样例，用来验证工具链、板级资源和
EFI 代码确实能链接成功。

它**不是**可刷的 Reno 固件，**不是**完整 Debian，也**没有**在手机上运行过。

## 为什么先做这一步

Reno 的第一次真实启动还缺这些未验证前提：ABL 是否支持一次性加载、单 DTB 与 stock DTBO 的
合并行为、真实内存布局与保留区、TZ/DMA 权限、以及一条已验证的回到 Android 的路径。
在这些都未知时直接产出"看起来能刷"的 boot 镜像是不负责任的。

所以第一份产物只有：`u-boot.elf`、`u-boot.bin`、`pcam00-uefi-memonly.dtb`、
`helloworld.efi`、解析后的 `.config`、审计 JSON 和校验和。

## 安全边界（由 `scripts/validate_config.py` 强制）

必须开启：`EFI_LOADER`、`EFI_BINARY_EXEC`、`CMD_BOOTEFI`、`CMD_BOOTEFI_BINARY`、
`EFI_MEDIA`（它 select `BLK`，但背后没有任何块设备）、`EFI_VARIABLE_NO_STORE`、
`ENV_IS_NOWHERE`、`BLK`、`SYS_DEVICE_NULLDEV`。

必须关闭：SCSI/UFS/MMC/NVMe/MTD/USB 存储、fastboot、DFU、BCB、GPT、`saveenv`、
FAT/ext4 写、EFI capsule/FMP、EFI boot manager、distro boot、preboot、
分区表解析（`PARTITIONS`/`EFI_PARTITION`/`DOS_PARTITION`），
以及 `CMD_MEMORY`/`CMD_IO`/`CMD_GPIO`/`CMD_I2C`/`CMD_REGULATOR` 这些能直接戳硬件的命令。

第一轮 CI 实际抓到一个真问题：`u-boot.elf` 里出现了 `fastboot` 字样，
但定位后发现它只是网络代码里的环境变量名 `FASTBOOT_UDP`/`FASTBOOT_TCP`，
位于 ELF 的符号字符串区，`u-boot.bin` 里 0 次。
因此扫描改为**只检查 ELF 的 `PT_LOAD` 段**——也就是真正会被加载进内存的那些字节；
符号表不再参与判定。这个改动有单元测试保证：段内命中必须报错，段外命中必须忽略。

> `EFI_LOADER` 在 Kconfig 里依赖 `BLK`，所以**不能**照搬 Linux 的 `CONFIG_BLOCK=n` 说法。
> 这里的边界是"编译进去的块层下面没有任何真实介质驱动"，并且构建后会扫描二进制，
> 出现 `saveenv`/`fastboot` 字样就直接失败。

这不是"整个启动链绝对零写"的证明：ABL、TZ 在上电时先于本程序运行，其副作用不在本工程范围内。

## 构建

只能走 GitHub Actions（本机没有 aarch64 交叉工具链，也不该在这里装）：

- workflow：`.github/workflows/build-uefi.yml`
- 固定上源：`57UU/u-boot@a2a847b1a84e6bcbdbc64f0c20e07fd69e0452fd`
- 流程：`qcom_defconfig` → 合并 `board/oppo/pcam00/pcam00-uefi-memonly.config`
  → `olddefconfig` → 静态审计 → 编译 → 二进制扫描 → 打包校验和

## 已验证 / 未验证

已验证（本机离线）：

- 单元测试证明审计脚本是 fail-closed 的：任一禁用项被打开、任一必需项被关闭、
  设备树名不对、`BOOTDELAY` 不是 -1、二进制里出现 `saveenv`/`fastboot`，都会失败。
- 设备树经 `cpp` + `dtc` 编译通过，关键属性为：`oppo,pcam00`/`qcom,sdm710`/`qcom,sdm670`、
  `oppo,prjversion = <1 18097>`、`qcom,msm-id = <0x168 0 0x189 0>`、`qcom,board-id = <8 0>`、
  `memory reg = 0 0 0 0`（由 bootloader 填写，不猜容量）。

未验证：本工程是否能在 CI 编译通过、产物是否可被任何 ABL 加载、以及是否能显示/联网/读盘。

详见 `docs/UEFI-试启动与刷机教程.md`。
