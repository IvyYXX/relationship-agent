# Persona AI

A relationship conversation assistant that remembers the people and experiences you choose to save.

**Status:** local invite-beta implementation. Public hosting, live-model quality review and real-user studies remain pending. Mock evaluation is not evidence of advice quality.

## Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[beta,mcp,test,dev]'
.venv/bin/python persona_app.py
```

Open http://127.0.0.1:8770/, switch to English, create an account and add a person. Only the nickname is required. Accounts and confirmed person memories are independent; legacy local data is never auto-assigned.

To configure a model, run `.venv/bin/python scripts/configure_model.py`, enter an authorized key locally, then restart. The script writes an ignored, mode-0600 `.env`; users do not see provider settings.

## What is implemented

One checkpointed agent workflow with person profiles, confirmed event memory, bounded retrieval, source-aware answers, optional academic search and rehearsal. Ordinary conversation now combines intent selection and the answer in one model call; specialized workflows run only when needed. Reports, sources and traces use progressive disclosure. Empathy keywords no longer replace answers with generic scripts. User accounts use opaque HttpOnly sessions, scrypt password hashing, CSRF and origin checks. Each resource is authorized on the server. Durable jobs support leases, idempotency and resume; budgets count every actual model attempt, including retries.

Retrieval uses registered names/aliases and scoped lexical ranking, not vectors. Character creation means profiles of people, not independent autonomous agents or accurate digital replicas. Astrology is metadata, not behavioral evidence. Simulations are not real events.

Correction invalidates old conversational context. Deletion conservatively removes related chats to prevent deleted information from being reused. This trades continuity for clear memory control; the UI explains the consequence before deletion.

## Evidence and remaining work

Run `python -m unittest discover -s tests -p 'test_*.py' -v` and `python evals/persona_eval.py`. Forty synthetic bilingual multi-turn cases compare chat-only and person-memory variants. Human ratings remain blank until reviewed. Local QA users and UI feedback are synthetic.

Production requires PostgreSQL, HTTPS, an invitation code, server-side model credentials, configured model prices, a privacy contact, validated backups and completed launch checks. Deployment manifests and CI are supplied. The owner has purchased a Tencent Cloud server; the latest application deployment and HTTPS still need verification.

See [deployment](deployment.md), [architecture](persona-architecture.md), [evaluation](evaluation.md), and [release status](release-status.md).

## Verification

The November 7 maintenance pass adds module/public-interface docstrings, shared HTTP redirect policy, import-safe operator scripts, and enforced Ruff checks. Prompt text is unchanged. The local suite passes 106 regressions, 8 structural scenarios and 160 mock turns; these are engineering checks, not model-quality scores. Current cloud deployment and production PostgreSQL/container checks remain unverified. See [verification](verification.md), [maintenance](maintenance.md), and [migration](migration.md).
