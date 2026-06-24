# scripts/

ego-knowledge 的仓库级辅助脚本目录；可安装 CLI 实现位于
`src/ego_knowledge/scripts/`。

| 脚本 | 用途 | 调度方式 |
| --- | --- | --- |
| `threshold_dryrun.py` | 阈值相关干跑辅助脚本 | 手动验证 |
| `watch_github_cron.sh` | cron wrapper，封装 `ek watch-github` 的工作目录与输出重定向 | 自行配置 cron 或其他调度器 |
