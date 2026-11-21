# 贡献指南

英文版见 [CONTRIBUTING.md](CONTRIBUTING.md)。

感谢你改进 `atb`！请保持它小巧、纯本地、零依赖。

## 零依赖原则

只使用 Python 标准库与 `git` 命令行。不要引入第三方 Python 依赖，也不要在运行时调用模型 API。

## 运行测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

请确保测试在两个时区下都通过：

```bash
TZ=UTC PYTHONPATH=src python3 -m unittest discover -s tests
TZ=Asia/Shanghai PYTHONPATH=src python3 -m unittest discover -s tests
```

## 试用端到端演示

```bash
bash examples/walkthrough.sh
```

该脚本会在临时目录中创建一个一次性 git 仓库，跑一遍主要命令，不会改动本仓库。

## `atb` 自己管理自己的任务

`atb` 的开发任务本身就用 `atb` 管理：每个任务都是 `.taskboard/tasks/` 下的一份任务单（TOML 头部 + Markdown 正文）。可以直接翻阅这些任务单，作为 `allow`/`deny` 范围、依赖与验收清单的真实示例。
