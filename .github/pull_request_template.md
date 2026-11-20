## What changed / 改了什么

<!-- A short summary of the changes. 简要说明本次改动。 -->

## Why / 为什么改

<!-- The problem or motivation behind the change. 对应的问题或动机。 -->

## How tested / 怎么测试的

Ran the test suite (please also run it under both time zones):
已运行测试（请同时保证两个时区下通过）：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
TZ=UTC PYTHONPATH=src python3 -m unittest discover -s tests
TZ=Asia/Shanghai PYTHONPATH=src python3 -m unittest discover -s tests
```

## Docs & CHANGELOG / 文档与变更日志

- [ ] Docs updated if needed（如涉及文档，已同步更新）
- [ ] CHANGELOG updated if needed（如需记入 CHANGELOG，已更新）
