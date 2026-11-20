# Contributing / 贡献指南

Chinese version: [`CONTRIBUTING.zh-CN.md`](CONTRIBUTING.zh-CN.md).

Thanks for helping improve `atb`! Keep it small, local and dependency-free.
感谢你改进 `atb`！请保持它小巧、纯本地、零依赖。

## Zero dependencies / 零依赖原则

Only the Python standard library and the `git` CLI may be used. Do not add
third-party Python dependencies, and do not call model APIs at runtime.
只使用 Python 标准库与 `git` 命令行。不要引入第三方 Python 依赖，也不要在运行时调用模型 API。

## Run the tests / 运行测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

Please make sure the suite passes under both time zones:
请确保测试在两个时区下都通过：

```bash
TZ=UTC PYTHONPATH=src python3 -m unittest discover -s tests
TZ=Asia/Shanghai PYTHONPATH=src python3 -m unittest discover -s tests
```

## Try the walkthrough / 试用端到端演示

```bash
bash examples/walkthrough.sh
```

This creates a throwaway git repository under a temp directory and exercises
the main commands without touching this repo.
该脚本会在临时目录中创建一个一次性 git 仓库，跑一遍主要命令，不会改动本仓库。

## `atb` manages its own tasks / `atb` 自己管理自己的任务

Development of `atb` is tracked with `atb` itself: every task lives under
`.taskboard/tasks/` as a task sheet (TOML header + Markdown body). Read a few of
them for real examples of `allow`/`deny` scopes, dependencies and acceptance
checklists.
`atb` 的开发任务本身就用 `atb` 管理：每个任务都是 `.taskboard/tasks/` 下的一份任务单
（TOML 头部 + Markdown 正文）。可以直接翻阅这些任务单，作为 `allow`/`deny` 范围、
依赖与验收清单的真实示例。
