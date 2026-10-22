# DSH headless profile 的项目补丁

`headless-cordis.patch.yml` 是 `$DSH_HOME/profiles/headless/cordis.patch.yml` 的副本。

它给 headless（学习批次用的 profile）加了【系统提示层】的硬约束：

- 本机文件分 WSL/Windows 两侧；读 WSL 文件只能用 read/glob/grep，
  绝不用 pwsh 访问 WSL 路径（会被 ACL 拒 → 反复重试 → 超时）
- pwsh 的 cwd 是 E:\WSL，只在需要跑命令时用
- 工作台能力优先用 paper_lora_* 工具，不要翻项目源码/快照找答案
- 调用预算：一个任务最多 12 次工具调用
- 拿到工具结果后不要反复"自己验证"；信息不足就直说
- 不要创建探测用的知识库/脚本/临时文件

## 为什么放在这里（而不是写在任务文本里）

实测（146 条轨迹）：把同样的规则写在【任务前缀】里，ACL 报错不降反升（25 → 39 次）。
写在系统提示层后（本文件）才是高优先级约束。

## 安装

```bash
cp docs/dsh/headless-cordis.patch.yml \
   "$DSH_HOME/profiles/headless/cordis.patch.yml"   # 默认 $DSH_HOME=~/.dsh
dsh --profile headless --dump-config | grep -A4 'id: system-prompt'   # 验证生效
```
