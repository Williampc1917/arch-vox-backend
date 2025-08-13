# Arch-Vox — Voice‑First Gmail Assistant (MVP)

##  One‑liner

Voice‑first Gmail assistant that uses speech recognition, AI summarization, and a secure cloud backend to triage, read, and reply to email hands‑free.

---

##  Why It’s Impressive

* **Real‑time voice flow** with low‑latency STT/TTS orchestration.
* **Cloud‑native architecture**: FastAPI on GCP Cloud Run.
* **Privacy‑by‑design**: No raw email bodies stored.
* **Robust authentication**: Supabase Auth with RLS.
* **Production readiness**: Health/readiness probes for Redis, Postgres, Supabase, and Vapi.
* **Scalable caching** with Redis (Upstash).
* **Clear latency goals**: p95 ≤ 1.6s for open‑thread TTS.

---

##  My Role & Contributions

Designed and implemented core backend including:

* Supabase Auth integration with HS256 JWT verification.
* Protected API routes.
* Redis and Postgres service modules.
* Health and readiness endpoints.
* Supabase Postgres schema + RLS policies.
* Environment configuration with Pydantic.

---

##  Architecture Overview

A real-time, voice-first Gmail assistant: the iOS app streams speech to Vapi for STT/TTS, the FastAPI backend interprets intent (lightweight parser LLM), performs Gmail actions, and replies with natural TTS—under secure, quota-enforced sessions.

### High-Level Flow

1. **iOS App (Swift)** — Handles Supabase login and mic; streams audio to Vapi.
2. **Vapi (STT/TTS)** — Transcribes speech, sends signed webhooks to the backend; plays TTS responses.
3. **Backend (FastAPI on Cloud Run)** — Verifies Supabase JWT + Vapi HMAC, enforces quotas, manages stateful voice sessions, and routes actions via a small FSM.

---

##  AI Orchestration

* **Parser LLM** (`gpt-4o-mini`): Fast action/argument extraction.
* **Composer LLM** (`gpt-4o`): On-demand summaries, drafts, tone refinements.

---

##  Data Layer

* **Redis (Upstash)** — Session state, inbox index, cached summaries, idempotency keys, rate limits.
* **Supabase Postgres** — Users, encrypted OAuth tokens, audit logs.
* **Google Secret Manager** — Centralized secrets.
* **Gmail API** — List/get/send, save drafts; bodies fetched only when requested (“tell me more”), never stored.

---

##  Why This Is Notable

* **Low-latency loop** — Snippet-first triage avoids LLM until needed; cache-first summaries; target p95 ≤ 1.6s for thread open.
* **Multi-model efficiency** — Cheap parser gates expensive composer calls.
* **Strong security posture** — Supabase JWTs, signed webhooks + anti-replay, RLS in Postgres, secrets in GSM.
* **Safety & UX** — Idempotent send, explicit confirmation, daily quotas/rate limits in Redis.
* **Privacy by design** — No raw email bodies persisted; summaries cached by content hash.

---

##  Conversation State Machine (Brief)

A tiny FSM keeps the voice experience safe and predictable:

* **Idle → Reading** (user: “What’s new?”)
* **Reading → ThreadOpen** (user: “Tell me more / open #2”)
* **ThreadOpen → Replying** (compose / refine / options)
* **Replying → Done** (explicit confirm send or save draft)
* **Any → Idle** (end session; Redis keys cleared)

This prevents illegal actions (e.g., “send” with no draft), keeps latency tight, and supports idempotency and quota gating entirely via Redis.

---

##  Features — Implemented vs. Planned

**Implemented:**

* Supabase Auth (HS256 JWT verification)
* Protected route example
* Health checks: Redis, Postgres, Supabase, Vapi
* `.env.local` config loader
* Redis service with TTL
* Postgres liveness check
* DB schema + RLS

**Planned:**

* Gmail OAuth integration
* Inbox triage from subject/snippet only
* Thread open + LLM summarization
* Compose/refine/options + confirm send
* iOS + Vapi streaming flow

---

##  Core Technology Stack

| Layer                  | Choice                            | Reasoning                                                  |
| ---------------------- | --------------------------------- | ---------------------------------------------------------- |
| **Frontend**           | Swift (iOS)                       | Native voice UX, Siri support, best performance for mobile |
| **Voice Stack**        | Vapi + Whisper + Google TTS       | High‑accuracy STT, natural‑sounding output                 |
| **LLM**                | GPT-4o (OpenAI)                   | Fast multi-modal LLM                                       |
| **Backend Lang**       | Python (FastAPI)                  | Async support, rich GPT/Gmail libraries                    |
| **Hosting**            | Google Cloud Run (Docker)         | Auto‑scaling, cheap/free tier                              |
| **Auth**               | Supabase Auth                     | Secure login with minimal setup                            |
| **Database**           | Supabase PostgreSQL               | RLS, scalable, realtime                                    |
| **Cache**              | Redis (Upstash)                   | Cloud-native, free-tier                                    |
| **Secrets Management** | Google Secret Manager             | Secure, centralized                                        |
| **CI/CD**              | GitHub Actions                    | Automated deploys to Cloud Run                             |
| **Monitoring**         | Google Cloud Monitoring + Logging | Errors, alerts, metrics                                    |
| **Testing**            | Pytest                            | Lightweight backend coverage                               |
| **Architecture Style** | Modular Monolith (FastAPI)        | Clean separation of concerns                               |

---

##  Security & Privacy Highlights

* RLS on all user‑bound tables.
* Sensitive tables deny all to clients.
* OAuth tokens encrypted at app layer.
* Minimal Gmail scopes.
* Ephemeral Redis sessions (TTL 15m).

---

##  Quick Start

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
curl -s http://127.0.0.1:8000/healthz
```

---

##  Sample Output

```json
{
  "overall_ok": true,
  "checks": {
    "redis": {"ok": true, "latency_ms": 2.1},
    "supabase_auth": {"ok": true},
    "postgres": {"ok": true, "latency_ms": 5.0},
    "vapi": {"ok": true, "status": 404}
  }
}
```

---

##  Next Steps / Roadmap

1. Gmail OAuth clients.
2. Inbox triage endpoint.
3. Thread summarization.
4. Compose/refine/send endpoints.
5. iOS voice flow integration with Vapi.
