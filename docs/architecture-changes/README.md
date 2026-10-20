# Architecture Change Requests（ACR）

修改受保护模块（`chris/personality/ chris/memory/ chris/temporal/ chris/world/ chris/behavior/
chris/llm/ chris/events.py`）的公共接口前，必须先在此目录创建 ACR 文件：

```
ACR-XXX-title.md
```

编号递增（ACR-001、ACR-002…）。模板见 `ACR-000-template.md`。

ACR 状态：`PROPOSED / APPROVED / IMPLEMENTED / REJECTED`。

未经用户批准，Agent 不得实施 ARCHITECTURE CHANGE。
