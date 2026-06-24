# Contributing

感谢贡献 ego-knowledge。欢迎提交 Issue 和 Pull Request。

## 开发环境

```bash
uv sync --extra dev
```

## 提交前检查

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q
```

默认测试配置会排除 `slow` 标记的压力测试；需要全量压力验证时可显式选择对应测试。

## 贡献授权

提交贡献即表示贡献者有权提交这些内容，并授予项目维护者一项可再许可、可商业使用的免费、不可撤销许可，用于将贡献纳入本项目及其后续授权安排。

该授权不转让贡献者的版权；贡献仍可按项目当前开源许可证分发。

## Pull Request 流程

1. Fork 仓库并创建功能分支。
2. 确保提交前检查全部通过。
3. 提交 Pull Request，描述改动内容与动机。
4. 维护者审查通过后合并。

## 范围建议

- 优先提交小而完整的改动。
- 新增 kind、relation、CLI 参数或 schema 字段时，同步更新 `docs/reference/`。
- 涉及数据破坏风险、商业授权、隐私或安全边界时，请先开 Issue 讨论。
