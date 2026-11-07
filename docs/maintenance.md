# 代码维护与注释规范

更新：2026-11-07。适用于独立的 Persona AI 项目。

初次接手可先按 [代码阅读顺序](code-reading-guide.md) 跟踪一次消息，再进入各模块。

## 文件与职责

| 修改内容 | 入口 |
| --- | --- |
| 模型怎样回答、如何判断意图 | `src/relationship_agent/prompts.py` |
| 输出字段、引用和来源校验 | `src/relationship_agent/schemas.py` |
| 工作流阶段与恢复 | `src/relationship_agent/engine.py` |
| HTTP 模型协议、重试、请求限额 | `src/relationship_agent/providers.py` |
| 出站请求禁止重定向的共享策略 | `src/relationship_agent/http.py` |
| 任务排队、每请求费用预约、执行 | `src/relationship_agent/beta/service.py` |
| 人物、记忆、账号范围及删除 | `src/relationship_agent/beta/store.py` |
| 登录、接口和输入模型 | `src/relationship_agent/beta/app.py`、`auth.py` |
| 页面、浏览器交互、样式 | `src/relationship_agent/persona_web/` |
| 发布、备份、评测与模型配置命令 | `scripts/`、`evals/` |

`agent_loop.py`、`model_client.py` 和两个顶层启动文件保留为兼容入口。
`beta` 安装 extra 也保留为部署命令的兼容别名。不要因文件短或名字旧而删除。

已删除 `skills.py`：它只是无人引用的提示词/结构字典包装，不是可安装 Skill 的加载器。
活动提示词只有 `prompts.py` 一份；`schemas.INSTRUCTIONS` 保留重导出以兼容原导入路径。

## 文档字符串与注释

- Python 文件使用模块文档字符串 `"""…"""` 说明职责；公开类和函数说明用途。
- 涉及权限、记忆版本、删除范围、重试计费或外部请求的函数，应补充副作用与限制。
- 简单函数使用一句说明；复杂函数按需要说明输入、返回值与失败条件，不机械添加空的参数表。
- 注释解释“为什么”，例如为何只检索已确认记忆、为何失败后不自动重试。不要逐行复述赋值或循环。
- 测试方法用描述性名称表达行为；测试模块和可复用测试工具说明隔离条件。不要求每个断言旁写同义注释。
- HTML 使用 `<!-- … -->`，JS/CSS 使用 `/* … */` 或 JS 的 `//`；不要把 Python 三引号放进前端文件。
- 提示词改动必须与行为评测一起审查。只有注释/格式变化不升级 `PROMPT_VERSION`；语义变化应升级以阻止旧任务恢复。

## 可执行检查

```bash
.venv/bin/pip install -e '.[beta,mcp,test,dev]'
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
node --check src/relationship_agent/persona_web/app.js
.venv/bin/python scripts/verify.py
# 构建发布包；需要获取构建依赖
.venv/bin/python scripts/verify.py --package
# 仅在允许 Chromium 启动的环境运行
.venv/bin/python scripts/verify.py --browser
```

Ruff 配置位于 `pyproject.toml`，检查未定义名称/无用导入、公共接口文档字符串和统一格式。
CI 与本地验收都运行同一组 Python 检查。前端采用 Prettier 3.6.2 格式，保持无构建步骤的静态资源形式。

## 本次整理边界

- 配置模型、真实模型 smoke、浏览器验收及备份循环均移入 `main()`，导入脚本不会读写配置、花费模型额度或启动服务。
- 重复的禁止 HTTP 重定向实现合并，保留原来的失败处理策略。
- 删除未被引用的 `store.data()`；未删除现有测试、评测数据、兼容入口或部署文件。
- 所有原有提示词逐项比对保持一致；十个核心模块去除文档字符串后的 AST 保持一致。
- `memory/`、`.env`、用户输出、备份和 IDE 设置不属于此次清理范围。源码修改前副本保存在本机 `/tmp/persona-cleanup-before-20261107/`，这是临时回滚材料，不能替代 Git 或持久备份。

## 仍然存在的技术债

这次整理不意味着没有架构债务：`engine.execute` 仍集中处理多个工作流，`store.py` 同时承担多种数据资源，前端 `app.js` 仍有较多界面状态。暂时保留是为了避免在文档整理中改写权限、删除和恢复行为。后续应按工作流/资源拆分，每次拆分配合现有回归，而不是为缩短文件盲目增加抽象层。

SQLite/全局短写锁、有限关键词安全预检、保守的整账号记忆版本失效仍是当前 Beta 的明确取舍。格式检查和 Mock 测试不能证明模型建议质量，也不代表公网生产验收完成。


## 补充注释复核（2026-11-07）

在上一轮整理之后，再次为 44 个 Python 文件、前端及部署配置补充阅读说明：
模块职责、上下文与任务字段、关键分支、失败恢复、异步响应失效、记忆确认和测试目的。
标准 JSON 数据保持合法格式，字段解释放在阅读指南中。

本轮只改注释与文档。去除文档字符串后，44 个 Python 文件的 AST 与本轮修改前一致；
10 段提示词逐项相同；13 个其他已修改代码/配置文件移除注释后内容相同。
106 个回归测试、两套 Mock 评测、Ruff 检查、Python 格式检查、JS 和 shell 语法检查通过。
没有调用真实模型，也没有进行新的公网部署验收。
