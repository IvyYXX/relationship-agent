# Persona AI

**Remember. Relate.**

A relationship conversation agent with person-specific memory. Bring a conversation or an experience, get help interpreting the interaction and drafting a reply, and carry the memories you confirm into your next chat.

[中文](../README.md) · [Architecture](persona-architecture.md) · [Code guide](code-reading-guide.md) · [Deployment](deployment.md) · [Evaluation](evaluation.md)

## What can you do with it?

- **Talk things through.** Receive a direct response to your question, with emotional support or practical suggestions as needed.
- **Keep track of people and experiences.** Create separate profiles, confirm memories, and correct information when it changes.
- **Analyze conversations.** Upload TXT or text-based PDF files, ask a specific question, and inspect verbatim source excerpts.
- **Prepare a reply or an important conversation.** Request a draft or a rehearsal; simulated exchanges stay separate from real events.
- **Ask for evidence.** Use academic search when you want research sources behind an explanation.

For example: add a person, upload a conversation, and ask, “He invited me for coffee on Friday. I want to go, but I can only confirm the time tomorrow. Help me write one short reply.” Confirm any experience you want remembered for a later conversation.

Profiles contain information you provide. Interpretations are possibilities, not access to another person's thoughts or reliable predictions of their behavior. Memories can be corrected or deleted, and account data can be exported.

## Quick start

Requires **Python 3.10+**. On macOS / Linux:

```bash
git clone https://github.com/IvyYXX/relationship-agent.git
cd relationship-agent
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python persona_app.py
```

Open [http://127.0.0.1:8770/](http://127.0.0.1:8770/), switch to English, and create an account with a password of at least 12 characters. No invitation code is needed locally unless one is configured.

A fresh installation without model configuration uses **Mock demo mode**: no API key or paid model calls are needed to explore the interface. Connect a real model to evaluate answer quality. Existing `.env` settings are retained.

`web_app.py` and `persona_app.py` start the same application and can also be run in PyCharm. The local URL is accessible only from your computer; see [deployment](deployment.md) for a shared website.

### Connect a model

Stop the running server, then run:

```bash
.venv/bin/python scripts/configure_model.py
.venv/bin/python persona_app.py
```

Choose `glm`, `openai`, or `custom`, then enter the model ID, Base URL, and key. The configuration helper uses the Chat Completions protocol; see the deployment guide for other protocol settings. Run it again to change providers or keys.

The helper stores the key in a Git-ignored, mode-0600 `.env` without printing it or calling a model. Questions sent in API mode incur provider charges. Website users do not need to enter the administrator's key.

### Attachments

Upload → preview and select files → select the relevant person → ask a question.

Supports UTF-8 TXT and text-based PDF: up to 2 MB per file, 40 PDF pages, 100,000 extracted characters, and 3 selected files per question. Uploading alone makes no model call. Answers include checked quotations with file and page/line references; saving an attachment excerpt to person memory requires confirmation.

Long documents use lexical excerpt retrieval with visible coverage, not exhaustive analysis. Screenshots, scanned PDFs, and automatic messaging-app imports are not supported. See [attachment handling and deletion](attachments.md).

## Harness design

The harness is the runtime around the model: it assembles context, executes stages, validates output, tracks progress, and controls requests and cost.

```text
Message / selected attachments
  → account and resource authorization
  → person identity, confirmed memories, conversation and excerpts
  → direct answer or an on-demand search / report / rehearsal workflow
  → output and source validation
  → persisted result and user feedback

Across execution: durable queue · checkpoints · traces · request limits and budgets
```

Key engineering choices:

- **Scoped memory:** account/person filtering, lexical relevance and recency ranking, explicit separation of experiences, feelings, impressions, model inferences, and rehearsals.
- **Direct answers by default:** one main agent with specialized workflows. Ordinary chat combines intent selection and the answer in one model stage.
- **Verifiable excerpts:** bounded parsing and retrieval, quotation checks, and visible document coverage.
- **Recoverable execution:** durable jobs, leases, checkpoints, idempotent submission, one active job per conversation, and parallel work across conversations. Expired jobs fail explicitly and can be resumed by the user.
- **Provider configuration and metering:** configurable model/protocol/credentials, persistent limits, and accounting for every actual HTTP attempt, including retries.
- **User isolation:** server-side resource ownership checks, HttpOnly session cookies, password hashing, Origin/CSRF checks, and login rate limits. SQLite locally; PostgreSQL in production.
- **Optional tools:** Google Scholar through SerpAPI, Semantic Scholar, OpenAlex, and a person-scoped read-only MCP server.

Retrieval is lexical, not vector-based. Person profiles are not independent autonomous agents. Memory corrections invalidate old context; some deletions remove related conversations to prevent reuse of old information. The interface explains the scope before deletion. See [architecture](persona-architecture.md).

## Development and evaluation

```bash
.venv/bin/pip install -e '.[test,dev]'
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python evals/persona_eval.py
.venv/bin/python scripts/verify.py --package
```

For automated browser checks:

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/verify.py --browser --package
```

Forty synthetic bilingual multi-turn scenarios compare runs with and without person memory. Mock is the default and checks engineering behavior without paid model calls. Advice quality requires separate live-model review.

The November 8, 2026 local verification record reports 122 regression tests, 8 structural cases, 160 mock turns, and one live-model attachment smoke test. These are dated results, not a guarantee about later revisions or real-user outcomes. See [verification scope and limitations](verification.md).

A bounded live-model evaluation:

```bash
.venv/bin/python evals/persona_eval.py --mode api --limit 1 --max-calls 8 --output outputs/live-persona-eval.json
```

The cap includes both memory variants and retries. Do not commit real user outputs.

### Where to start reading

| Area | Entry point under `src/relationship_agent/` |
| --- | --- |
| Answer style and stage instructions | `prompts.py` |
| Context, queues, budgets | `beta/service.py` |
| Workflow and checkpoints | `engine.py` |
| Person memory and account isolation | `beta/store.py` |
| File parsing, retrieval, and storage | `attachments.py`, `beta/attachments.py` |
| Provider adapters and metering | `providers.py` |
| Output and citation validation | `schemas.py` |
| API and frontend | `beta/app.py`, `persona_web/` |

See the [code-reading guide](code-reading-guide.md) and [maintenance guide](maintenance.md). Issues are welcome with sanitized reproduction steps, expected/actual behavior, and relevant model settings. Never include keys or private conversations. Feature proposals should describe a user scenario and acceptance criteria.

### Optional read-only MCP

```bash
.venv/bin/pip install -e '.[mcp]'
.venv/bin/python -m relationship_agent.beta.mcp_server --username YOUR_USERNAME --person-id PERSON_ID
```

Provides `get_person_profile` and `search_person_events` for one account and one person. It starts only when explicitly run, independently of the web service. The connected host receives that person's profile and confirmed memories.

## Deployment and data

Docker, PostgreSQL/Caddy Compose, Render Blueprint, and encrypted backup/restore tools are provided. Production startup requires PostgreSQL, HTTPS, invitation access, real-model configuration, and other production checks. Follow the [deployment guide](deployment.md) and [release checklist](release-status.md).

Defaults allow 40 model HTTP attempts per user per day and 200 globally, including retries, subject to a daily budget. Configure provider prices and limits; application reservations are not provider balances or billing guarantees. Search services may charge separately.

API mode sends required conversation, person context, and selected excerpts to the configured model provider. Deployers must explain data processing and provide a privacy contact. Do not commit `.env`, personal databases, chats, backups, or real evaluation outputs. Use a separate production database; see [migration](migration.md) for upgrades.

## Inspiration

Independently developed. Memory separation draws inspiration from [Generative Agents](https://arxiv.org/abs/2304.03442), applied to person-specific experiences and ongoing conversation rather than a recreation of its simulated town.
