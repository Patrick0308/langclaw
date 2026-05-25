# AGENTS.md

## 项目定位
Langclaw 是一个多渠道 AI Agent **框架**（不是终端应用），基于 LangChain、LangGraph、deepagents。
开发者通过 `pip install langclaw` 在其业务中构建 Agent 系统，可类比 Flask/FastAPI 在 Web 领域的定位。

- Python 版本：3.11+
- 核心理念：显式注册、可插拔后端、统一消息管线

## 包结构总览

| 包/模块 | 职责 |
|---|---|
| `app.py` | `Langclaw` 主入口：装饰器、生命周期、全局 wiring |
| `agents/` | LangGraph Agent 构建、工具绑定、子代理委派 |
| `gateway/` | 渠道路由与编排（`GatewayManager`）、命令分发、消息派发 |
| `bus/` | 消息总线抽象：asyncio（开发）、RabbitMQ/Kafka（生产） |
| `middleware/` | 请求中间件管线：RBAC、限流、内容过滤、PII 脱敏 |
| `config/` | Pydantic Settings（环境变量前缀 `LANGCLAW__`，嵌套分隔 `__`） |
| `cron/` | APScheduler v4 定时任务 |
| `session/` | `(channel, user, context) -> LangGraph thread_id` 映射 |
| `checkpointer/` | 会话状态持久化：SQLite（开发）、Postgres（生产） |
| `providers/` | 通过 `init_chat_model` 做 LLM 模型解析 |
| `cli/` | Typer CLI：`langclaw gateway/agent/cron/status` |

## 开发命令

```bash
uv sync --group dev
uv run pytest tests/ -v
uv run ruff check . && uv run ruff format .
uv run pre-commit run --all-files
```

## 架构不变量（必须遵守）

详见 `docs/ARCHITECTURE.md`。以下规则不可破坏：

1. 消息主链路：
   `Channel -> InboundMessage -> Bus -> GatewayManager -> Middleware -> Agent -> OutboundMessage -> Channel`
2. 命令（`/start`、`/reset`、`/help`）绕过 Bus 和 LLM，直接由 `gateway/commands.py` 中 `CommandRouter` 处理。
3. Cron 任务需发布 `InboundMessage` 到同一 Bus，走与普通消息一致的完整处理链路。
4. 可插拔后端遵循统一模式：`base.py` 抽象 + 工厂函数（如 `make_message_bus`、`make_checkpointer_backend`）。
5. 中间件顺序有语义，修改前必须检查 `agents/builder.py` 的组装顺序。
6. 坚持显式注册，不做自动发现：工具、渠道、中间件都在 `Langclaw` app 对象上注册。

## 代码规范

1. 每个模块都使用：`from __future__ import annotations`
2. 仅用于类型的导入放在 `TYPE_CHECKING` 分支内
3. 统一现代类型语法：`list[T]`、`dict[K, V]`、`str | None`（禁止 `typing.List`、`Optional`）
4. 统一使用 `loguru.logger`，禁止标准库 `logging`
5. 文档字符串使用 Google 风格（Args/Returns/Raises）
6. Tool 失败时返回 `{"error": "..."}`，不要把异常抛入 Agent 主流程
7. 代码风格由 Ruff 统一治理，以 `pyproject.toml` 中 `[tool.ruff]` 为准

## 代理协作建议

1. 改动前先确认是否触碰“架构不变量”。
2. 涉及消息流、命令路由、定时任务时，优先验证是否仍走统一管线。
3. 新增后端时先补抽象接口，再补工厂与默认实现。
4. 调整中间件时必须关注顺序影响并补测试。
5. 提交前至少运行：

```bash
uv run pytest tests/ -v
uv run ruff check . && uv run ruff format .
```

## 提交质量门槛

- 测试通过
- Ruff 检查通过
- 不引入与现有架构原则冲突的隐式行为
- 新增能力有最小可验证样例（测试或 CLI 路径）
