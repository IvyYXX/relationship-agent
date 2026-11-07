# Direct replies for support and advice

Updated 2026-11-05. Local change only; not deployed to Tencent Cloud.

## Problem and change

Requests for reassurance plus practical advice could enter the multi-stage analysis workflow. That workflow always generated a simulated conversation. The chat repair instruction also explicitly withheld advice, even when requested.

- Ordinary support, practical suggestions and short reply drafts use `chat`.
- Explicit structured relationship analysis uses `analysis`.
- Explicit conversation simulation uses the new router intent `rehearsal`. Results retain the existing analysis envelope for rendering; ordinary analysis has a null `dialogue_rehearsal` and creates no simulation record.
- Chat prompts address the user directly, briefly acknowledge emotion, and supply requested practical steps. Follow-up questions are optional.
- The repair prompt respects advice requests. Simulated outputs do not propose real person memories.
- Prompt version: `persona-2-direct-replies`. Existing completed replies are not rewritten. Previously checkpointed routes retain their old decisions when resumed; send a new message to evaluate the new routing.

## Verification

- 96 unit/integration tests passed, including five new regressions for mixed support/advice, analysis without simulation, explicit rehearsal, simulation memory boundaries and advice-preserving repair.
- 8/8 offline structural scenarios passed (full-pipeline scenarios now explicitly request rehearsal).
- Five live GLM-5.3 HTTP calls using synthetic inputs: two full chat turns through Service/queue/store (route + chat each), plus one explicit rehearsal routing check. The user's reported work-anxiety wording produced a direct supportive reply with three suggestions; the next turn requested two suggestions and no rehearsal and received two suggestions without a simulated dialogue.
- A test-only attempt to reuse a completed job's accounting hook for the extra router check was correctly rejected before HTTP with `Execution lease lost`. The check was rerun with a separate one-request client.

These samples validate this regression, not general emotional-support quality. One follow-up still contained an overconfident time-management reassurance and a questionable meeting-multitasking suggestion; broader advice-quality review remains necessary. No real user conversations were used as evaluation fixtures. No cloud release or broad model-quality score is claimed.

## Follow-up: answer the question, preserve earlier user reports

A real follow-up requesting a man's traits still selected a report and produced explanations of why the user asked. Inspection showed 14 history messages were available to the router but omitted from the report's reasoning/action stages.

- `persona-3-question-first`: ordinary requests to analyze a behavior/person remain direct chat. Detailed reports require an explicit request; ordinary new events do not automatically trigger report revision.
- Removed the exact customer question from production routing examples. It remains a regression fixture only.
- Across workflow changes, retain earlier user turns under the existing account/person/epoch/budget filters; exclude generated reports and rehearsals. This is conversation history, not confirmed long-term memory.
- Explicit report stages now receive user-only history. Quote validation accepts these user reports, rejects assistant-generated evidence, and permits fewer than two hypotheses when evidence is limited.
- The chat instruction answers the subject asked about, avoids repeating the user's question and uses existing background before asking for more.

Verification: 100 automated tests and 8 structural scenarios passed. Four additional live GLM requests exercised two isolated synthetic sessions: the exact traits question with an earlier report's user background, and the same question without background. Both used route + chat only. The first referenced the supplied events; the second requested relevant details. This is targeted verification, not a comprehensive quality score; the first sample still mixed in an English word and asked more than one follow-up question. No real conversation was sent to the model for this evaluation. Historical replies are unchanged.


## 第三次调整：对话优先，Harness 保留

提示版本：`persona-4-conversation-first`。

- 新增 `respond` 合同，在一个模型阶段中完成意图判断和普通聊天回答。聊天必须有 reply；其他意图必须先返回空 reply，再执行对应工作流。
- 人物档案、用户历史、已确认记忆继续进入上下文。来源分层、租户/人物过滤、检查点、重复提交防护、额度与请求计量保留。
- 删除按固定同理心关键词重写回复的逻辑。完全重复的回复最多请求修复一次，仍重复则明确失败，不再伪造通用安慰作为成功结果。
- 搜索、详细报告和演练按需触发。网页仅保留一个聊天输入框；来源和报告默认折叠，用户请求的演练展开。Trace 保留为折叠的运行详情。
- 回复草稿不得编造共同经历、地点、此前约定或见面间隔，也不得沿用旧助手草稿中的虚构细节。

### 本次验证

- 103 项自动化回归通过，覆盖默认单次模型调用、历史与人物记忆、合同拒绝、重试恢复、重复回答、权限和预算。
- 8/8 结构场景通过；40 个双语 Mock 案例 × 两种记忆配置 × 两轮，共 160 轮完成。Mock 结果不代表建议质量。
- 真实 GLM 共 6 次 HTTP 请求，全部为临时数据库中的虚构情境，不读取真实聊天。前四次验证安抚加策略、人物行为、回复措辞及拒绝搜索/演练，均只有 respond 一次调用。
- 首轮回复措辞中发现模型编造了“上次说的那家店”。补充约束后再做两次真实复测，并在测试历史中故意放入错误草稿；新回答未沿用虚构地点或经历。该小样本不能证明所有输出都不会编造。
- 独立 Playwright 浏览器在本机 macOS 环境启动失败；改用 Codex 内置浏览器，在 8771 隔离 Mock 服务确认登录、单输入框、回车发送、自然回答展示、报告折叠/展开、演练展开、来源折叠和英文切换，未见浏览器脚本错误。未将独立浏览器脚本记为通过。
- 本地真实模型服务已在 8770 重启，readyz 返回 ready。本次不涉及云端部署。
