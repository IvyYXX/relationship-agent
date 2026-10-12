# Persona AI 统一版本验收

日期：2026-10-09。对象为本次未提交的工作区，不将旧提交 CI 的结果当作本次验证。

## 已执行的检查

- 91 项独立回归：共享 Harness 32 项、学术适配 9 项、Persona 核心 36 项、运维/MCP 3 项、入口/安装包/后台服务 11 项。移除了重复发现旧测试的 test_smoke，数量不再重复计算。
- 8/8 结构案例通过新版 Service、持久队列与 ScopedStore；检查来源分层、引用真实性约束、阶段执行与安全预检。不是建议质量通过率。
- 40 个双语案例 × 2 种记忆条件 × 2 轮，共 160 轮 Mock 完成；人工质量评分仍为 NOT_EVALUATED。
- 本地 HTTP 模型桩验证两种模型协议、鉴权、格式修复、失败脱敏、恢复和逐请求计量；没有调用真实模型服务。
- 加密备份与空库恢复、令牌不恢复、未同意反馈不允许审阅、真实 stdio MCP 账号/人物范围验证。
- 隔离源码构建 wheel，检查新版静态资源、四个命令入口、旧模块与私人文件排除；修复了旧 build/lib 缓存使旧代码混入安装包的问题。
- Python 格式化与 Ruff F 类检查、JavaScript 语法检查；密钥与数据库未写入发布包。

受保护文件比对：20 个基线文件中，19 个哈希一致，包括所有旧/新数据库、用户记录、已有评测输出和其他 IDE 配置。唯一变化为 `.idea/workspace.xml`（IDE 会话状态）；本次未编辑该文件，也未回写覆盖它。项目原本没有 `.env`，未创建或改变模型凭据。

## 浏览器验收

应用内浏览器连接本次创建的临时 SQLite 服务，用纯合成账号实际操作：登录、人物创建、Enter 发送、记忆卡片、确认保存、新会话检索、刷新恢复、纠正记忆后重新提问、英文切换。确认更正后的回答使用新记录。

桌面宽度 1360、移动宽度 390 均检查 DOM 无横向溢出。移动端发现侧栏可能遮住人物面板，已在打开档案/人物表单时关闭侧栏，复验后“人物 → 聊聊这个人”能正确切换和聚焦。浏览器未出现页面错误日志。测试服务和临时页面已关闭。

本机 Playwright Chromium 启动被 macOS 沙箱 MachPort 权限限制，**不能写成脚本自动验收通过**；本轮通过的是上述应用内浏览器流程。scripts/browser_check.py 保留独立浏览器自动验收，并新增移动人物面板检查，需在允许启动 Chromium 的环境或 CI 执行。纠正与删除的数据库/HTTP 断言由自动测试覆盖。

## 复现

```bash
.venv/bin/python scripts/verify.py --package
# 在可启动 Chromium 的环境增加浏览器验收
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/verify.py --browser --package
```

脚本为每项检查保存日志和 summary.json；一键验收忽略外部 PERSONA_TEST_DATABASE_URL，保证不清理真实库。PostgreSQL CI 使用专门的临时测试数据库，不能指向用户数据。

本机日志、备份和发行包在本次工作目录的 relationship-cleanup-20261009/；源码迁移说明见 [migration.md](migration.md)。

## 尚未完成的发布门槛

- 本机未发现模型 Key，未进行真实模型质量、真实 API 延迟或成本验证。
- 本机没有可用 Docker/PostgreSQL；本次变更未推送，尚未获得本次代码的容器/PostgreSQL CI 结果。
- 未进行云部署、HTTPS 验收、生产数据备份恢复或真实用户试用。
- 原 CI 链接只属于历史提交，不是本轮代码的证据。

这些门槛未完成前，交付状态为“经过本地工程验收的新版 Beta”，不能保证成熟生产上线。发布步骤见 [deployment.md](deployment.md)。
