# Persona AI

**Remember. Relate.**

一个带人物记忆的情感交流 Agent。结合你提供的聊天和相处经历，帮你理解互动、组织回复、决定下一步；你确认过的记忆，可以在下次对话中继续使用。

[English](docs/README.en.md) · [快速开始](#快速开始) · [Harness 设计](#harness-设计) · [代码导读](docs/code-reading-guide.md) · [部署指南](docs/deployment.md) · [评测](docs/evaluation.md)

## 它能帮你做什么？

你不必每次重新解释“他是谁、之前发生过什么”。为重要的人建立档案，把相关经历逐步补充进去，再围绕当下的问题继续聊。

| 你想做的事 | Persona AI 如何帮助你 |
| --- | --- |
| “我现在很乱，先听我说说。” | 自然对话，回应情绪，再根据你的需要梳理问题或给建议。 |
| “他这句话是什么意思，我怎么回？” | 结合聊天原文、人物资料和已确认经历，提供可能的解释与回复建议。 |
| “上次说的是小林，不是小周。” | 分开管理人物，支持纠正记忆；指代不清时先澄清。 |
| “帮我看看这份聊天记录。” | 上传 TXT 或文字型 PDF，围绕具体问题分析，并查看回答对应的原文引用。 |
| “下次见面我想说清楚，陪我练一下。” | 按需启动沟通演练，演练内容与真实经历分开处理。 |
| “这个说法有什么研究依据？” | 按需检索学术来源，展示可核对的引用。 |

**默认直接回答你的问题。** 详细分析、研究搜索和对话演练在需要时才展开。

### 一个典型用法

1. 添加人物“小林”，填写昵称、别名和你愿意提供的背景。
2. 点击“聊聊这个人”，粘贴对话或上传聊天记录。
3. 提问：**“他约我周五喝咖啡，我想答应，但明天才能确认时间。帮我写一句回复。”**
4. 查看回复建议；使用附件分析时，还可以展开对应的原文依据。
5. 把需要保留的经历确认为人物记忆，下次聊到小林时继续使用。

人物档案记录的是你提供的信息，分析是基于这些信息的推测，不代表能够读懂或准确预测对方。你可以纠正、删除记忆，也可以导出自己的数据。

## 快速开始

需要 **Python 3.10 或更高版本**。以下命令适用于 macOS / Linux：

```bash
git clone https://github.com/IvyYXX/relationship-agent.git
cd relationship-agent
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python persona_app.py
```

打开 **[http://127.0.0.1:8770/](http://127.0.0.1:8770/)**，点击“创建账号”，设置用户名和至少 12 位密码。本地未配置邀请码时无需填写，界面支持中文和英文。

新安装且未配置模型时，默认运行 **Mock 演示模式**：无需 API Key、不调用付费模型，适合体验页面和操作流程。要评估实际回答质量，请连接真实模型。已有 `.env` 的安装会沿用其中的配置。

`web_app.py` 和 `persona_app.py` 均启动同一应用，可直接在 PyCharm 中运行。本地地址仅供本机访问；让其他用户通过网址使用，请按[部署指南](docs/deployment.md)配置服务器。

### 连接真实模型

先停止当前服务，再执行：

```bash
.venv/bin/python scripts/configure_model.py
.venv/bin/python persona_app.py
```

按提示选择 `glm`、`openai` 或 `custom`，填写模型 ID、Base URL 和 API Key。配置工具使用 Chat Completions 协议；其他协议配置见[部署指南](docs/deployment.md)。重复运行即可更换服务商或 Key。

Key 在终端隐藏输入，保存到 Git 忽略的 `.env`，文件权限限制为当前用户读写。配置步骤本身不调用模型；接入后发送问题会产生服务商费用。网页用户无需填写管理员的 Key。

### 上传聊天记录与 PDF

输入框旁点击“上传附件” → 预览并勾选文件 → 选择相关人物 → 输入具体问题。

- 支持 UTF-8 TXT 和文字型 PDF；单份不超过 2 MB、PDF 不超过 40 页、提取文字不超过 10 万字符，每次最多选择 3 份。
- 回答提供文件名、页码／行号和逐字引用。附件中的内容经用户确认后，才写入人物记忆。
- 上传本身不调用模型；API 模式下发送分析问题才使用模型额度。
- 长文件检索相关片段，并显示覆盖范围；不将局部阅读当作全文分析。目前不支持截图、扫描 PDF 或自动读取微信。

存储、引用校验和删除范围见[附件说明](docs/attachments.md)。

## Harness 设计

对于开发者，这个项目展示了如何把人物记忆、按需工具和可靠执行组织成一个可交互的 Agent 应用。**Harness 是围绕模型的运行层**，负责上下文、任务执行、校验、恢复与成本控制。

```text
用户消息 / 选中的附件
        ↓
账号与资源权限校验 → 人物识别、已确认记忆、历史与附件片段
        ↓
主 Agent：普通对话直接回答；按需进入搜索、报告或演练
        ↓
阶段执行 → 输出与来源校验 → 保存结果 → 页面展示
        ↓
用户反馈 / 确认或纠正记忆 → 后续对话

贯穿执行：持久队列 · 检查点 · Trace · 请求限额与预算
```

| 工程能力 | 实现与取舍 |
| --- | --- |
| **可控的人物记忆** | 按账号和人物限定检索，区分经历、感受／印象、模型推测与演练。确认后才用于跨会话人物记忆。检索使用词项相关度和时间排序。 |
| **按需执行** | 一个主 Agent 配合专门工作流。普通聊天将意图选择与直接回答合并为一个模型阶段，避免每次都走完整报告流程。 |
| **可核对的附件分析** | 限制解析资源与上下文长度，校验引用是否来自选中的原文，展示检索覆盖范围。 |
| **任务恢复与追踪** | 持久队列、租约、阶段检查点、幂等提交与 Trace。同一会话只允许一个活跃任务，不同会话可并行；租约过期后任务显式失败，可由用户恢复。 |
| **模型适配与成本控制** | 模型、接口协议和凭据独立配置，每次实际 HTTP 尝试及重试均计数，配合持久限额与预算预约。 |
| **多用户数据边界** | 服务端逐资源检查归属、HttpOnly 会话 Cookie、密码哈希、Origin/CSRF 检查与登录限流；SQLite 用于本地，PostgreSQL 用于生产部署。 |
| **工具与扩展** | Google Scholar/SerpAPI、Semantic Scholar、OpenAlex 学术适配；可选的只读 MCP 限定一个账号和一个人物。 |

设计边界：当前采用词项检索，不是向量检索；人物档案不是独立自主 Agent。纠正记忆会使旧上下文失效；部分删除操作会连带清理相关对话，以免旧信息再次被使用，页面会在操作前说明范围。详见[产品与架构](docs/persona-architecture.md)。

## 开发与评测

安装开发依赖并运行检查：

```bash
.venv/bin/pip install -e '.[test,dev]'
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python evals/persona_eval.py
.venv/bin/python scripts/verify.py --package
```

浏览器自动验收需先安装 Chromium：

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/verify.py --browser --package
```

评测包含 **40 个中英文合成多轮场景**，比较有／无人物记忆。默认使用 Mock，不消耗模型额度，用于验证流程和约束。真实模型回答质量需另外人工评审，不能用 Mock 通过率代替。

2026-11-08 的本地验证记录包含 122 项回归、8 个结构案例和 160 轮 Mock，以及一次真实模型附件联调；检查范围与限制见[验收记录](docs/verification.md)，这些记录不代表任意后续提交都已通过验证。

小额真实模型评测示例：

```bash
.venv/bin/python evals/persona_eval.py --mode api --limit 1 --max-calls 8 --output outputs/live-persona-eval.json
```

调用上限覆盖整个进程的两种记忆条件与重试；达到上限会记录失败，不会无限重试。请用合成或获得授权的数据评测，勿提交真实用户输出。

### 从哪里读代码

```text
persona_app.py / web_app.py          应用入口
src/relationship_agent/
  persona_web/                      HTML / CSS / JavaScript 界面
  beta/app.py                       FastAPI、认证与资源接口
  beta/service.py                   人物上下文、任务队列与预算
  beta/store.py                     用户隔离、人物与事件记忆
  beta/database.py                  SQLite / PostgreSQL 数据表与事务
  beta/attachments.py               附件归属、存储与记忆确认
  attachments.py                    TXT / PDF 提取与片段检索
  prompts.py                        各阶段提示词
  engine.py                         工作流、阶段执行与检查点
  schemas.py                        输出结构与来源校验
  providers.py                      模型协议适配与请求计量
  beta/ops.py                       统计、加密备份恢复与反馈审阅
  beta/mcp_server.py                人物范围内的只读 MCP
  beta/auth.py                      密码与会话管理
evals/persona_cases.py               多轮评测场景
```

想调整回答风格，从 `prompts.py` 开始；想理解消息如何执行，从 `beta/service.py` 与 `engine.py` 开始。完整路径见[代码阅读顺序](docs/code-reading-guide.md)和[维护指南](docs/maintenance.md)。

欢迎通过 Issue 提交脱敏的复现步骤、预期与实际行为；涉及回答质量时，请说明模型和相关配置，不要附 API Key 或私人聊天记录。新功能建议同时给出用户场景和验收方式。

### 可选：只读 MCP

```bash
.venv/bin/pip install -e '.[mcp]'
.venv/bin/python -m relationship_agent.beta.mcp_server --username YOUR_USERNAME --person-id PERSON_ID
```

提供 `get_person_profile`、`search_person_events` 两个工具，只返回指定人物的档案和已确认记录。由本机操作者主动启动，不随网页服务启动；连接它的 Host 会取得该人物的数据。

## 部署与数据管理

仓库提供 Dockerfile、PostgreSQL/Caddy Compose、Render Blueprint，以及加密备份和恢复工具。公网启动要求 PostgreSQL、HTTPS、邀请码、真实模型配置及其他生产检查项。完整步骤见[部署指南](docs/deployment.md)，发布验收项见[发布清单](docs/release-status.md)。

- 默认每用户每天 40 次、全站每天 200 次模型 HTTP 尝试，包含重试；可通过配置调整，并受每日预算限制。
- 按服务商价格配置预算。应用的预算预约不等于服务商余额或最终账单，搜索服务可能另行收费。
- 真实模型运行时，所需的对话、人物上下文和选中的附件片段会发送给配置的模型服务商。部署者需说明数据处理方式与隐私联系渠道。
- 不要提交 `.env`、私人数据库、聊天记录、备份或真实评测输出。公开服务使用独立的生产数据库。
- 数据和旧版本入口的迁移方式见[迁移记录](docs/migration.md)。

## 设计参考

本项目独立开发。记忆分层受 [Generative Agents](https://arxiv.org/abs/2304.03442) 的思路启发，应用于人物经历与持续对话，没有复现其虚拟小镇。
