# Claro AI

**Claro AI is a voice-first relationship intelligence assistant for Gmail and Calendar.**  
It helps busy professionals stay ahead of the ~20 relationships that matter most by
triaging communication, tracking commitments, and adapting to their personal email style —
**per person**, not just in general.

---

## 🌍 Overview

Modern work runs on email and meetings — but what actually breaks is **context**.  
We don’t fail because we forget messages; we fail because we forget **people**, what we
promised them, and when we last followed up.

Claro AI is an experiment in building a **relationship-centric inbox**:

- Focused on your **top 20 relationships**, not thousands of threads  
- Controlled through **natural conversation** instead of manual triage  
- Designed to be **secure, private, and production-grade**, even as an MVP  

This repo contains the early product and infrastructure work behind that vision.

---

## 🔭 Full Feature Set (Product Vision)

### 1️⃣ 🎙️ Voice-First Interface

You don’t open an inbox — **you ask**.

- Natural voice conversation (ask/respond)
- Read messages aloud, summarize, and confirm actions
- Morning briefings / “catch me up” flows
- Voice commands for triage: “Next”, “Skip”, “Send”, “Archive”, etc.

---

### 2️⃣ ⭐ Top 20 VIP System

A transparent ranking system focused on the relationships that matter most.

- Inputs: Gmail + Calendar metadata  
- Output: weighted “relationship activity” score → top 50–100 contacts  
- Suggested VIP list (15–20 people) with short explanations  
- Manual confirm and edit; user locks in a final list of up to 20 VIPs

---

### 3️⃣ 🧠 Relationship Engine (Lite)

A lightweight relationship graph between the user and their VIPs.

- **Health score per contact** (0–100) with **Green / Yellow / Red** status  
- Signals:
  - Recency of inbound/outbound contact
  - Frequency over the last 14–30 days
  - Reply rate (small weight)
  - Open commitments (penalized when overdue)

**Commitment tracking**

- Extract phrases like “I’ll send…”, “by Friday…”, “let’s discuss next week…”  
- Store: `who / what / due_date / status (pending | fulfilled)`  
- Used in:
  - Briefings
  - Per-person context view
  - “Broken Promise” alerts

**Writing / tone learning (per person)**

- For **each VIP**, Claro builds a **per-contact style profile** from up to the last 20 emails  
- Profiles capture greeting, length, structure, pacing, and sign-off habits  
- When drafting with the LLM, replies to a VIP use **their specific style profile**  
  - Emails to Jennifer can look different from emails to David — even from the same user  
- Non-VIPs fall back to 3 user-defined defaults (Professional, Friendly, Casual),
  collected during onboarding with 3 example emails

**Lightweight topic extraction**

- Keyword/LLM tags for “last topics discussed” and short summaries  
- Feeds into briefings and per-person context views

---

### 4️⃣ 👤 Per-Person Context View

A single view for each VIP that answers: **“Where did we leave off?”**

- Last contact (email date) + next meeting (if any)  
- Pending commitments and unresponded topics  
- Last few emails summarized (3–5 with dates)  
- “Last 5 topics” as keyword clusters  
- Relationship score with color + number  

No “typical cadence” analytics — just clear, current, human-readable context.

---

### 5️⃣ 🔔 Alerts & Nudges

Proactive nudges so you don’t drop the ball.

1. **Ghosting**
   - VIP inbound message unanswered for **>48 hours**
   - Example: “No response to Sarah in 2 days — draft a note?”

2. **Meeting Overdue**
   - No meeting with a VIP for **>21 days**
   - Example: “No recent meeting with David — want to add one?”

3. **Broken Promise**
   - A due date like “by Friday” has passed with **no related email or event** in the thread
   - Example: “You promised Jennifer pricing by Friday — nothing sent yet. Draft now?”

Static thresholds + health scoring (recency, frequency, reply rate, commitments) —
no opaque cadence models.

---

### 6️⃣ 🌐 Relationship Dashboard

A simple, voice-first view of your top relationships.

- Visual list or small network of up to 20 VIPs  
- Color nodes: Green / Yellow / Red with optional score overlay  
- Voice-driven navigation:
  - “Open Jennifer” → Per-Person Context View  

---

### 7️⃣ 📥 + 🗓️ Gmail & Calendar Orchestration

The underlying pipes that power the assistant.

- **Gmail**
  - Read threads and metadata
  - Summarize messages
  - Draft, send, and archive email

- **Calendar**
  - Read events for today and tomorrow
  - Create simple events on the **user’s own calendar** when asked  
  - No cross-availability scheduling; instead can draft an email with suggested times

---

### 8️⃣ 🎙️ Supported Voice Commands (MVP Surface)

- **“Catch me up”** – Brief VIP emails, today/tomorrow meetings, and overdue commitments  
- **“Next”** – Move to the next triage item in the current session  
- **“Read it” / “Summarize it”** – Hear key points of an email or meeting  
- **“Reply” / “Draft reply”**
  - Short-intent mode: “Tell her I’ll send the report tomorrow.” → expanded in the user’s tone  
  - Dictation mode: user speaks the full text; Claro cleans and lightly tone-aligns it  
- **“Send”** – Send via Gmail, update relationship activity, and store any commitments  
- **“Archive” / “Skip”** – Clear or skip items during triage  
- **“Show calendar” / “What’s next?”** – Read the next 3–5 meetings with VIP emphasis  
- **“Remind me to follow up”** – Create a manual reminder; auto-add when messages contain promises  

> “Schedule with [Name]” is intentionally **excluded** from the MVP — no cross-availability logic.

---

### 9️⃣ 🧪 Example Conversational Flow

**User:** “Catch me up.”  
**Claro:** “4 VIP emails and 3 meetings today. Jennifer needs pricing; Sarah’s QBR is tomorrow.”  

**User:** “Read Jennifer’s email.”  
**Claro:** “She’s asking for pricing; you promised it by Friday.”  

**User:** “Tell her I’ll send it tomorrow.”  
**Claro:** “Drafted in your professional tone. Send?”  

**User:** “Send.”  
**Claro:** “Sent. I’ll remind you tomorrow morning to confirm.”

---

## ✅ Current MVP Capabilities (Implemented)

Right now, the repo focuses on getting the **foundations** right:

- **Secure authentication & data layer**
  - Supabase-backed signup/login with per-user data isolation

- **Gmail connection**
  - OAuth flow to connect a user’s Gmail account
  - Secure token storage for server-side access

- **Onboarding**
  - Guided account and Gmail setup
  - Style-collection step where users provide **3 example emails**  
    - Used to bootstrap global user tone and default styles

- **Top 20 ranking scaffold**
  - Pulls contact metadata from Gmail
  - Ranks and displays a candidate list of top contacts
  - UI to adjust and confirm the user’s VIP list, which subsequent features will target

---

## 🚧 In Progress

Actively being developed:

- **Context data pipeline for the LLM**
  - Building a unified “relationship timeline” across email and (later) calendar
  - Structuring data for:
    - “Catch me up” briefings
    - Per-person context views
    - Commitment and promise tracking

- **LLM-powered briefings & replies**
  - Generating high-signal “Catch me up” digests for VIPs  
  - Drafting replies using:
    - The user’s global email style
    - VIP-specific **per-contact style profiles** where available

- **Alerting logic**
  - Implementing ghosting, meeting-overdue, and broken-promise rules on top of the relationship engine

---

## 🏗️ Architecture & Tech Stack (High-Level)

- **Client**
  - Native iOS app written in **Swift**
  - Handles auth state, microphone access, push notifications, and talks to the backend over HTTPS/JSON

- **Backend**
  - **Python 3.11** + **FastAPI** for the REST API
  - Containerized with **Docker** and deployed on **GCP**-managed services

- **Data & State**
  - **Supabase / Postgres** for persistent data with row-level security
  - **Upstash Redis** for caching, short-lived state, and background coordination

- **Auth & Integrations**
  - **Supabase Auth** with ES256 JWTs for authentication and authorization
  - **Google OAuth 2.0** + **Gmail** and **Calendar** APIs for mail and events

- **AI / LLM**
  - **OpenAI** APIs for summarization and drafting
  - Per-user and **per-VIP style profiles** for personalized email generation

- **Tooling**
  - Dependencies managed via `pyproject.toml`
  - Dev tooling: **pytest**, **pytest-asyncio**, **ruff**, **mypy**, **black**

---

## 🔐 Security & Privacy

Claro AI is treated like a real product, not a demo. The backend is being built to align with
Google’s OAuth limited-use requirements and SOC 2–style expectations.

- **Strong authentication & isolation**
  - Every Gmail route verifies a Supabase-issued ES256 JWT against the live JWKS.
  - Auth dependencies ensure a user can only act on **their own** mailbox and data.

- **Least-privilege OAuth scopes**
  - Google OAuth is locked down to the minimal Gmail/Calendar scopes required for the MVP
    (e.g. `gmail.readonly`, `gmail.send`, `gmail.modify` plus limited calendar scopes).
  - The service will not start without a correctly configured client ID, secret, and redirect URI.

- **Encrypted token storage**
  - OAuth access and refresh tokens are encrypted with Fernet using an environment-provided key
    before they ever touch the database.
  - A dedicated token service owns all token lifecycle operations (encrypt-on-write,
    decrypt-on-read, expiry tracking, retries).

- **Token lifecycle & revocation**
  - Background jobs refresh tokens ahead of expiry and automatically disconnect users when
    refreshes fail, so we never retain access longer than necessary.
  - Disconnect flows revoke tokens with Google and reset onboarding state so users must
    explicitly re-consent before we can access their mailbox again.

- **Data minimization & aggregation**
  - Downstream analytics use hashed contact identifiers (`contact_hash`) instead of raw email
    addresses, so VIP scoring and relationship signals never expose sensitive contact metadata
    outside the aggregation boundary.

- **Observability & auditability**
  - Structured JSON logging includes timestamps and user context for every significant action.
  - Dedicated health endpoints expose Gmail/token/DB status to support automated monitoring
    and audit evidence collection.

This model will continue to evolve as more features come online, including formalized
documentation for data flows, key rotation, retention, and incident response.

---

## 📄 License

This project is licensed under the **MIT License**.  
See the `LICENSE` file for details.
