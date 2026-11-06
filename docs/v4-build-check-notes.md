# v4 构建校验迭代记录

## Linux banner 唯一性误判

首个B-v4云构建Run5（commit `4f3aa7999086e111033201359809715d23d0cd08`，run `42231630382`）完成了内核/DT/CPIO编译和实际Debian BusyBox惰性测试，最后被独立验包器拒绝：`Image must contain one unique Linux version string`。只上传FAILED日志，没有作为成功候选交付。

查证而非直接跳过检查：

- 固定内核[init/version.c:43–52](https://github.com/57UU/linux-sirius/blob/17601ca5198a736f696447a9b6c568e4be97cc9c/init/version.c#L43-L52)明确说明 `linux_banner` 先weak编译，再在最终阶段重新定义。
- [init/version-timestamp.c:23–26](https://github.com/57UU/linux-sirius/blob/17601ca5198a736f696447a9b6c568e4be97cc9c/init/version-timestamp.c#L23-L26)提供实际横幅。
- 已保存真实Run3 Image中也有两条同release的合法NUL结尾横幅，分别含`# SMP`与`#1 SMP`，不是拼接不同版本内核的证据。

修复后的验证器允许一条或两条这样的横幅，但**每条**都要满足边界、长度、NUL、printable和与实际kernelrelease文件完全相同的release前缀；压缩配置中的诱饵、第三条、旧版本混入或不完整终止都拒绝。报告保留全部匹配地址/内容，不假称找到了运行时最终符号。

独立的Image内嵌IKCONFIG gzip CRC验证和与kernel.config的逐字节相等检查保持不变；boot结构、实际init/helper/revision、源码快照一致性检查也没有移除。

新增双横幅正/负测试；此前真实Image只用于核验横幅扫描函数，**不把Run3判为v4通过**。构建修复不说明手机可以启动。
