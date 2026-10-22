## 改了什么
<!-- 一句话 -->

## 为什么
<!-- 关联 issue / 实测依据 -->

## 怎么验证的
```bash
# 贴命令与输出
```

## 自查
- [ ] `python -m compileall -q .` 通过
- [ ] `bash run_tests.sh` 全绿
- [ ] `bash scripts/check_no_secrets.sh` 0 命中
- [ ] 没有硬编码机器路径（`/home/xxx`、`/mnt/x/...`）
- [ ] 没有把 Key 写进被跟踪的文件
- [ ] 已更新 CHANGELOG 的 Unreleased 段
