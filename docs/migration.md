# 2026-10-09：统一到 Persona AI

## 范围

保留 `beta/` 新版服务、`persona_web/` 新版界面和共享 Harness；不改数据库 schema，不自动迁移旧账号或单用户记录。`beta` 是包名，不是仍有两套产品。

旧 HTTP 服务、CLI、单用户存储、旧 MCP、旧网页、独立 extraction 包装和对应启动脚本已从活动源码移除。旧专用说明和案例入口也移出；有效的阶段、引用、协议和恢复测试迁到新版存储。`agent_loop.py`、`model_client.py` 仅为共享核心的导出别名，不是旧服务。

## 现在怎么启动

```bash
.venv/bin/pip install --no-deps -e .
.venv/bin/python web_app.py
```

依赖尚未安装时用 `.venv/bin/pip install -e '.[mcp,test]'`。原有 `.[beta,mcp,test]` 命令仍兼容。所有 Web 命令指向 `relationship_agent.beta.app:main`。MCP 仍需明确指定账号和人物，没有公开网络入口。

## 核心边界

- `Service.submit()` 负责认证后的任务准备、人物范围与上下文快照；不再调用旧 `RelationshipAgent.start()`。
- `engine.py` 只依赖 `WorkflowStore` 协议，负责阶段校验、检查点和结果提交。
- `ScopedStore` 是正式数据库实现，负责资源归属、记忆确认、版本失效和事务。
- 同一幂等键不能换输入、会话或已记录的人物选择；失败恢复也受三个活跃任务上限限制。
- 后台线程退出有边界；进程中断后租约到期转为可恢复失败，不假装取消远端已经收到的模型请求。

## 备份和数据保护

修改前工作区干净，源码基线为 `e27df714a3e37ecce777a1dffea7707086ad56ca`。本机完整源码归档为 `relationship-agent-before-e27df714.tar.gz`，存放于本次工作目录的 `relationship-cleanup-20261009/`。归档不包含私人数据库和密钥，以免复制敏感数据。

数据库、旧 `memory/`、用户记录、已有输出与配置原地保留；修改前后比对受保护文件哈希。回滚时把源码归档解压到**新目录**，先检查依赖与入口，不覆盖正在使用的数据目录或 `.env`。

## 验收与发布包

```bash
.venv/bin/python scripts/verify.py --browser --package
```

测试仅使用合成账号与临时数据库。浏览器首次需要 `.venv/bin/python -m playwright install chromium`。`verify.py` 会忽略外部 `PERSONA_TEST_DATABASE_URL`，防止一键验收操作真实数据库。

`build_release.py` 从全新临时源码目录构建 wheel，并检查旧模块、旧 UI、私有记录与配置均未入包。不要直接复用曾构建旧版本的 `build/lib` 目录；旧缓存可能重新带入已经删除的文件。

测试通过不等同生产发布。真实模型、PostgreSQL/容器、HTTPS、生产备份恢复及人工安全审查以当前代码重新验收，见 deployment.md。
