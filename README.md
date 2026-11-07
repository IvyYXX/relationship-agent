# Persona AI

**Remember. Relate.** 一个带人物记忆的关系交流助手：创建人物档案，持续聊天，确认相处经历，修正记忆；需要时再检索研究来源或练习沟通。

[English](docs/README.en.md) · [产品与架构](docs/persona-architecture.md) · [部署](docs/deployment.md) · [验收与状态](docs/release-status.md) · [评测](docs/evaluation.md) · [代码维护](docs/maintenance.md) · [代码阅读顺序](docs/code-reading-guide.md)

**当前状态：邀请制 Beta 的本地实现。公网部署、真实模型质量评审和真实用户测评尚未完成。** 没有把离线 Mock 通过率当作模型效果或用户满意度。没有自动花费模型额度。

## 运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[beta,mcp,test,dev]'
.venv/bin/python web_app.py
```

打开 **http://127.0.0.1:8770/**。本地默认使用明显标注的演示模式。点击“创建账号”，使用自己的用户名和至少12位密码；本地未设置邀请码时无需填写。使用昵称即可，建议先用虚构数据体验。

已有本地 QA 账号仅供演示，不用于真实私人内容或公网环境。公网使用独立的 PostgreSQL 数据库，不上传本地 memory 目录。

在 PyCharm 中运行 `web_app.py` 或 `persona_app.py` 均启动同一个 Persona AI。安装后的 `persona-ai`、`relationship-agent`、`relationship-agent-web` 和 `python -m relationship_agent` 也指向这一服务。旧入口、旧界面与单用户存储实现已移出；原数据库、记录和 `.env` 不会被删除或自动归入新账号。操作与回滚说明见 [迁移记录](docs/migration.md)。

## 真实模型

普通用户页面没有 API 设置。管理员在本机隐藏输入 Key：

```bash
.venv/bin/python scripts/configure_model.py
.venv/bin/python persona_app.py
```

配置工具写入 Git 忽略的 `.env`，权限为0600，不打印 Key，不调用模型。GLM、OpenAI 或其他兼容服务使用对应的模型、Base URL、协议和 Key。可以重复运行更换 Key。也可以使用 `zsh run-api.sh` 临时输入 GLM Key，它只留在进程环境中。

上线前必须在 `.env` / 托管平台填入当前服务商价格与全站预算，使用自己有权用于该产品的凭据。默认每用户每天40次、全站每天200次模型 HTTP 尝试，还受每日预算限制；所有重试均计数，跨进程和重启不重置。普通聊天将意图判断和直接回答合并为一个模型阶段；知识解释、用户明确请求的搜索、详细报告和演练按需执行，可能包含更多调用。异常重试也会额外计数。预算是本应用的保守预约额度，**不是服务商账户余额或账单保证**；搜索服务可能另行收费。

## 用户闭环

1. 登录，点击左栏紫色 `＋ 添加人物`，填写昵称和可选资料。
2. 点击“聊聊这个人”，或在聊天中提到其已登记昵称／别名。
3. 普通聊天结合人物记忆和历史，直接回答当前问题；用户需要知识解释、明确要求研究来源、详细报告或演练时，才进入对应工作流。
4. 明确的人物陈述可形成待确认卡片；确认前不用于跨会话记忆。用户可把它归为经历、感受或印象。
5. 新对话检索同一账号下该人物的已确认记录。多人或代词指代不清时先澄清。
6. 用户纠正记忆后，旧上下文失效。删除人物或事件会清理涉及该人物的相关对话，避免旧结果重新成为上下文；操作前明确提示。
7. 用户可评价回复、导出数据、删除对话和注销账号。

## 工程范围与取舍

- 一个主 Agent，以自然对话为默认路径；保留按需、可恢复的专门工作流，不声称多个自主 Agent 已实现。
- 人物档案、连续对话、回复建议和可纠正记忆为核心；来源与报告折叠展示，Trace 留在运行详情中。不会用同理心关键词检查把正常建议替换成固定安慰话。
- 人物识别使用已登记昵称／别名及用户选择；陌生人物不会被悄悄创建。
- 人物检索先做账号和人物过滤，再用词项相关度与时间排序；**目前不是向量检索**。
- 人物资料、用户经历、用户感受／印象、模型推测和演练保持不同来源；星座仅作为资料，不用于行为预测。
- SQLAlchemy 支持 SQLite 本地开发和 PostgreSQL 部署，生产配置强制 PostgreSQL、HTTPS、邀请码与真实模型配置。
- 不透明 HttpOnly 会话 Cookie、哈希凭据、Origin/CSRF 检查、登录限流；每个 API 对资源归属再次校验。
- 持久任务队列、租约、检查点、幂等提交。不同会话可并行，同一会话只允许一个活跃任务。进程崩溃后租约过期的任务显式失败，用户可恢复。
- 删除/编辑时阻止与运行中的任务竞态。修改记忆后排除旧上下文是保守实现，会暂时减少近期聊天连续性。
- 学术检索保留 Google Scholar/SerpAPI、Semantic Scholar、OpenAlex 适配，引用校验沿用现有实现；已知人物名在外发搜索前再次清理。没有虚构“已读全文”。
- 中文/英文提示与 UI；小范围规则预检高风险输入，所有模型分支保留安全提示。规则不能覆盖所有表达，必须补充真实模型评测。
- 按人物限定的只读 MCP、加密备份/恢复、脱敏统计、明确同意后才可通过反馈审阅对话。

## 测试和评测

当前本地验证、测试范围与发布门槛见 [verification.md](docs/verification.md)。旧提交的 CI 成功不能代表本次重构通过 CI；真实模型与真人反馈仍待完成。

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python evals/persona_eval.py
.venv/bin/python scripts/verify.py --browser --package
```

40个中英文多轮案例，比较有／无人物记忆；默认全部Mock，不花模型额度。真实模型小额联调示例：

```bash
.venv/bin/python evals/persona_eval.py --mode api --limit 1 --max-calls 8 --output outputs/live-persona-eval.json
```

调用上限覆盖整个进程、两种变体、路由与修复尝试。达到上限会记录失败而不是无限重试。阅读实际回复、填写人工评分后才可发布质量结论。不要提交真实用户输出。

## 可选开发扩展：MCP

暂不作为面向用户的核心功能，也不会随网页服务自动启动。

仅由本机操作者主动启动、限定一个账号和一个人物，不对公网暴露：

```bash
.venv/bin/python -m relationship_agent.beta.mcp_server --username YOUR_USERNAME --person-id PERSON_ID
```

工具：`get_person_profile`、`search_person_events`。它们只读，返回来源标签，不返回其他人物或未经确认的记忆。Host 会获得这个人物的数据，启用前需了解这一点。

## 部署与运营

提供 Dockerfile、PostgreSQL/Caddy Compose、Render Blueprint、PostgreSQL CI、数据库备份和恢复工具。它们是部署材料，**不等于已经上线**。域名、托管账号、模型配置、备份存储与真实用户反馈仍需实际接通并验收。

开发模式仅允许监听本机；公开服务必须满足生产配置检查。不要提交 `.env`、私人数据库、备份、聊天记录或报告。部署详见 [deployment.md](docs/deployment.md)。

## 代码地图

```text
web_app.py / persona_app.py       新版入口
src/relationship_agent/beta/
  app.py                          FastAPI、登录与资源接口
  auth.py                         密码哈希、会话、登录限流
  database.py                     新数据库 schema v1 与事务
  store.py                        用户隔离、人物与事件、引擎存储适配
  service.py                      意图与人物上下文、队列、租约、预算
  ops.py                          脱敏指标、加密备份恢复、反馈审阅
  mcp_server.py                   账号+人物限定的只读 MCP
src/relationship_agent/persona_web/  新版 HTML/CSS/JS
src/relationship_agent/prompts.py    各阶段提示词（回答方式与意图）
src/relationship_agent/schemas.py    输出结构与来源校验
src/relationship_agent/engine.py     复用的阶段执行与检查点
src/relationship_agent/providers.py  多协议模型适配与逐请求计量钩子
evals/persona_cases.py               40个合成多轮场景
```

本项目独立开发。记忆分层受 Generative Agents 思路启发，没有复现其虚拟小镇。
