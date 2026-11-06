# B机 v4：启动条件与本轮边界

## 本版是什么

这是原厂引导链直启的 Reno PCAM00 RAM 诊断候选，不是完整 Debian，也不是 U-Boot/EDK2 固件。**本轮只改源码、云编译及离线验包，不重启或刷手机。**

用户确认B机已解锁、root且做过环境隐藏，两台机器都有TWRP；B机进不了fastboot。本轮之前的只读检查已确认它正常运行Android11/F.55，不应再把它描述成“只能启动到TWRP”。不要求取消隐藏或重复全量数据备份。

## 能确定的事实

- B机完整ABL分区哈希与已离线审查的F.55样本相等。该样本标准fastboot注册表有16项，未注册plain `boot`，不能默认临时boot可用。
- `continue`走分区镜像加载/认证路径，不是`download`缓冲区的一次性启动替代。
- 本机boot与recovery为不同分区；有TWRP不等于能安全恢复任意候选，本轮未验证切入/恢复。
- 当前Android生效DT选择DTB4/DTBO32，项目18097、SoC360、board8/0。它不能证明新主线DT也会被同样处理。

## DTBO精确匹配不是只看compatible

已审ABL的精确条件为 `(match & 0x34150000) == 0x34150000`。相关位涉及SoC ID、board/platform、subtype、foundry、前两个PMIC型号。SoC/revision或board/PMIC revision还参与其他排序，不能仅因候选使用revision0、运行SoC为1.1就断言一定不匹配。

后续B机已只读返回foundry=3、subtype=0、platform_version=0x10000，第一PMIC socinfo型号为0x1001b；两个revid驱动节点分别名为pm660@0和pm660l@2，但该内核不导出其数值版本属性。F3板级变体据实把msm-id改为`<0x30168 0 0x30189 0>`；board和PMIC候选元组不改。PMIC1的ABL数值身份及实际hyp/overlay路径仍未证明，**不能宣称候选已确认跳过板级DTBO或可以成功启动**。具体依据见[F3匹配修正](<B-F3-match-fix.md>)。

原始stock DTBO32对stock base本地可合并，对旧Run3主线树合并失败；stock节点ABI、符号/phandle和主线不同。补`-@`、拷贝符号或数字phandle不能自动变成兼容板树。原厂overlay还可能改供电/GPIO等；CONFIG_BLOCK=n也不使错误DT对所有硬件无害。

因此验证器只承诺：生成的候选含有经审静态字段、单个未猜RAM的memory占位、固定保留区、已禁用的外设、正确USB依赖引用；报告明确标注 **ABL exact match / DTBO skip 未证实**。

## 这次修复与验证

- 不把旧Run3计为当前版。内核localversion、DT版本属性和CPIO版本文件均为v4。
- PID1同步执行健康检查和停止协调；没有会独自退出而留下服务的后台温度监测进程。
- 停止时先尝试结束HTTP进程组、关闭接口/解绑本诊断gadget，再请求普通重启。重启返回时不重启服务。卡在内核或不可中断设备访问时仍可能失效，不宣称硬件watchdog/安全认证。
- 对枚举到的温度zone拒绝坏读数，并追踪已见zone的消失/类型变化；这不是证明全部物理传感器已探测，也不是电池充电保护。
- 记录RAM阶段号，成功建立USB/HTTP后定期更新只读快照。USB起来之前仍没有已验证的外部日志通路；ACM只是枚举，不是早期控制台。
- boot封装校验包括有界FDT/gzip/newc、SHA1 ID、padding、单DTB、嵌入Image/DTB/ramdisk逐字节一致、实际init/helper/revision逐字节对源码。
- source-manifest列出经审源码树的size/SHA256；与可信源码对照，而不是执行产物自带脚本。

## TWRP的后续测试与恢复约束

**现在不提供自动刷机脚本，也不执行写入。** 后续必须先补候选的DTBO/内存兼容证据，并明确该台的恢复办法，然后再对当次持久写入取得用户确认。

若之后使用TWRP写入boot：

1. 保留本机现有recovery/TWRP，不能为测试覆盖它；确认它确实能独立进入。
2. 将该台当前可工作的原boot及校验值保留在可由TWRP/电脑读取的位置；不拿另一台备份或未知固件包覆盖。
3. 明确恢复的是哪个boot目标、当前版本/槽位；不擦DTBO、不改vbmeta、ABL/XBL/GPT。
4. **180秒的普通重启不会恢复Android。** 写入后再次开机会仍然启动当前boot中的诊断；返回Android需要用已确认的恢复路径恢复本机原boot。不能把自动重启包装成自动回滚。
5. 首次运行只观察Linux是否到PID1、USB/日志及返回路径，不做rootfs安装、格式化、扩容、驱动大集合启用。

以上是后续方案的约束，不是已经完成的实机测试。所有构建检查通过也不能自动满足这些条件。
