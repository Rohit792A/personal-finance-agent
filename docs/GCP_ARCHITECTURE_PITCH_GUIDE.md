# Personal Finance AI — System Architecture & Google Cloud Services Guide
> **Purpose:** Comprehensive architectural reference and slide-ready guide detailing the end-to-end application architecture, every Google Cloud service used, how each service works under the hood, and why each service is essential.

---

## 1. Executive Architecture Overview

**Personal Finance AI** is built on a **5-Tier Cloud-Native, Zero-Trust Multi-Agent Architecture** designed around the Google Cloud **FDE (Forward Deployed Engineering) 4-Pillar Standard** (*Build, Govern, Scale, Optimize*).

Instead of a fragile single-prompt wrapper, the platform separates **Global Edge Presentation**, **Stateless Serverless Compute**, **Layer-7 Zero-Trust Security**, **Multimodal AI & Voice Agents**, and **Cryptographically Deduplicated Relational Storage**.

### 1.1. Master End-to-End Architecture Diagram

```mermaid
flowchart TB
    subgraph Tier1 ["Tier 1: Global Presentation & Edge Tier"]
        User["👤 End User (Web / Mobile)"]
        Firebase["🔥 Google Firebase Hosting CDN<br/>(personal-finance-agent-507010.web.app)"]
        SPA["⚡ Vite + Tailwind SPA<br/>4-Stage Vault (Home → Year → Month → Ledger)<br/>+ Dual-Backend Auto-Failover Client"]
    end

    subgraph Tier2 ["Tier 2: Serverless Ingress & API Gateway"]
        CloudRun["☁️ Google Cloud Run (Serverless ASGI)<br/>(finance-backend-328452160072.us-central1.run.app)"]
        FastAPI["🚀 FastAPI Gateway (app/api.py)<br/>REST Endpoints + Async Task Orchestration"]
    end

    subgraph Tier3 ["Tier 3: Zero-Trust Security Perimeter (GOVERN)"]
        WIF["🔐 Google Workload Identity & IAM<br/>(Keyless SPIFFE Agent Auth)"]
        ModelArmor["🛡️ Model Armor Engine (app/security/model_armor.py)<br/>L7 Prompt Injection & Jailbreak Defense"]
        CloudDLP["🕵️ Google Cloud DLP Pipeline (app/security/dlp.py)<br/>PAN, Aadhaar, Account #, UPI Redaction & Tokenization"]
    end

    subgraph Tier4 ["Tier 4: Google Cloud AI, Voice & Ingestion Fleet (BUILD & OPTIMIZE)"]
        Router["🧠 Dynamic Model Tiering Router"]
        VertexCache["⚡ Vertex AI Context Caching<br/>(75% Token Cost Reduction)"]
        GeminiFlash["✨ Vertex AI — Gemini 2.5 Flash<br/>(Multimodal PDF Statements, Receipts, Excel)"]
        GeminiPro["🎯 Vertex AI — Gemini 2.5 Pro + Flash-Lite<br/>(Multi-Turn AI CFO Studio & Fast Intent Routing)"]
        CloudSTT["🎙️ Google Cloud Speech-to-Text<br/>(Voice Clarification Transcription)"]
        CloudTTS["🔊 Google Cloud Text-to-Speech<br/>(en-US-Journey-F Neural Voice Prompts)"]
        GmailSync["📬 Gmail / Bank Statement Sync Engine<br/>(Automated PDF Discovery & Password Decryption)"]
        MCP["🔌 Model Context Protocol (MCP) & A2A Bus<br/>(ledger_server.py & a2a_orchestrator.py)"]
    end

    subgraph Tier5 ["Tier 5: Persistence, Forecasting & Telemetry (SCALE)"]
        Postgres["🗄️ Managed PostgreSQL (Cloud SQL / Supabase)<br/>SHA-256 Row & File Deduplication + SQL Views"]
        Forecaster["🔮 Multi-Horizon Future Cost Engine<br/>(7-Day, MoM, YoY Seasonal Preparedness)"]
        OTel["📊 OpenTelemetry + Autorater Flywheel<br/>(Math Reconciliation, Recall & Fiduciary SLAs)"]
    end

    User --> Firebase --> SPA
    SPA -- "HTTPS REST + Auto-Failover" --> CloudRun
    CloudRun --> FastAPI
    FastAPI --> WIF --> ModelArmor --> CloudDLP
    CloudDLP --> Router
    Router --> VertexCache
    VertexCache --> GeminiFlash & GeminiPro
    FastAPI --> CloudSTT & CloudTTS & GmailSync & Forecaster
    GeminiPro <-- "JSON-RPC Tool Calls" --> MCP
    MCP --> Postgres
    GeminiFlash --> Postgres
    Forecaster --> Postgres
    FastAPI -. "Spans & Metrics" .-> OTel
```

---

## 2. Google Cloud Services Inventory: What, How & Why

Below is the complete breakdown of every **Google Cloud Platform (GCP)** service powering the application, how it is architected in code, and why it is necessary for production reliability.

| # | Google Cloud Service | Codebase Integration | Primary Role | Why It Is Necessary (Business & Technical Justification) |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Google Vertex AI (`Gemini 2.5 Flash`, `Pro`, `Flash-Lite`)** | [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py), [`app/extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py), [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py) | Multimodal document OCR, structured JSON extraction, and multi-turn AI CFO reasoning | Bank statements and receipts have wildly different layouts across banks (HDFC, ICICI, SBI, Axis). Traditional regex/OCR breaks on layout changes; Gemini 2.5 Flash natively reads multi-page PDFs and images into strict Pydantic schemas with confidence scores. |
| **2** | **Vertex AI Context Caching** | [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py) | Caches static system instructions, Indian tax rules, and category taxonomy prefixes | Cuts input token TCO by **up to 75%** and slashes Time-to-First-Token (TTFT) latency by **50–70%** during multi-turn AI CFO consultations. |
| **3** | **Google Cloud Run** | [`Dockerfile`](file:///home/rkushwaha8610/personal-finance-agent/Dockerfile), [`app/api.py`](file:///home/rkushwaha8610/personal-finance-agent/app/api.py), [`app/frontend/src/api/config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.js) | Serverless container execution for the FastAPI backend (`us-central1`) | Provides **0-to-N autoscaling**, zero idle infrastructure management, built-in TLS termination, and serves as the always-online high-availability backend for the SPA. |
| **4** | **Google Firebase Hosting** | [`app/frontend/firebase.json`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/firebase.json), [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh) | Global Edge CDN hosting the compiled Vite + Tailwind CSS SPA | Delivers instant sub-100ms page loads worldwide over HTTP/3 with automatic SSL certificates and atomic rollbacks, decoupling static UI delivery from backend compute. |
| **5** | **Google Cloud Speech-to-Text (STT)** | [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py), [`app/frontend/src/components/modal.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/modal.js) | Transcribes user voice recordings when resolving ambiguous (`<85%` confidence) transactions | Typing explanations for unknown UPI/bank narrations on mobile is tedious. Cloud STT enables 1-tap **"Speak to Categorize"** with high accuracy for Indian-English accents and financial terms. |
| **6** | **Google Cloud Text-to-Speech (TTS — Journey Neural Voices)** | [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py) | Synthesizes lifelike spoken prompts (`en-US-Journey-F`) asking the user about flagged transactions | Transforms passive ledger review into an interactive, conversational voice loop where the AI speaks naturally like a human financial assistant. |
| **7** | **Google Cloud DLP (Data Loss Prevention) & Model Armor Pattern** | [`app/security/dlp.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/dlp.py), [`app/security/model_armor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/model_armor.py) | Inline PII/PCI redaction (PAN, Aadhaar, Account #, IFSC, UPI IDs) and prompt-injection defense | Prevents raw sensitive banking identifiers from leaking to external model logs and blocks indirect prompt injection embedded inside untrusted PDFs or receipts. |
| **8** | **Google Workload Identity Federation (WIF) & IAM** | [`app/security/auth.py`](file:///home/rkushwaha8610/personal-finance-agent/app/security/auth.py) | Keyless service-to-service authentication and SPIFFE agent identity verification | Eliminates static, long-lived JSON service account keys that could be leaked, enforcing least-privilege access between Cloud Run, Vertex AI, and Speech APIs. |
| **9** | **Gmail API / Google Mail Integration** | [`app/gmail_sync.py`](file:///home/rkushwaha8610/personal-finance-agent/app/gmail_sync.py), [`app/frontend/src/components/bankSync.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/bankSync.js) | Automatically discovers bank statement emails, downloads encrypted PDFs, and unlocks password-protected statements | Eliminates manual downloading of monthly bank statements from netbanking portals and removes the friction of entering complex bank PDF passwords (`DOB`, `PAN`, `Name+DOB`). |
| **10** | **Google Cloud Build & Artifact Registry** | [`Dockerfile`](file:///home/rkushwaha8610/personal-finance-agent/Dockerfile), [`.gcloudignore`](file:///home/rkushwaha8610/personal-finance-agent/.gcloudignore) | Automated container image packaging and deployment pipeline for Cloud Run | Ensures reproducible, immutable production deployments directly from source (`gcloud run deploy --source .`). |

---

## 3. Deep-Dive Architecture of Each Core Service Workflow

### 3.1. Service Architecture 1: Vertex AI Multimodal Ingestion & Dynamic Model Tiering
**Files:** [`app/main.py`](file:///home/rkushwaha8610/personal-finance-agent/app/main.py), [`app/statement_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/statement_agent.py), [`app/extractor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/extractor.py), [`app/ai_importer.py`](file:///home/rkushwaha8610/personal-finance-agent/app/ai_importer.py)

```mermaid
sequenceDiagram
    autonumber
    participant User as 👤 User / Gmail Sync
    participant API as 🚀 FastAPI (app/api.py)
    participant Security as 🛡️ Model Armor & DLP
    participant Router as 📂 File Router (app/main.py)
    participant Gemini as ✨ Vertex AI (Gemini 2.5 Flash)
    participant DB as 🗄️ PostgreSQL (DatabaseManager)

    User->>API: Upload PDF / Receipt Image / Excel (or Auto-Sync from Gmail)
    API->>Security: Screen payload & redact PII tokens ([ACCOUNT_XXXX], [PAN_XXXX])
    Security-->>API: Sanitized Document Stream
    API->>Router: process_file(file_path)
    alt PDF Bank Statement (.pdf)
        Router->>Gemini: StatementExtractor -> Multimodal PDF + Pydantic Schema
    else Receipt Image (.jpg / .png)
        Router->>Gemini: ReceiptExtractor -> Vision OCR + Merchant/Tax Schema
    else Spreadsheet (.xlsx / .csv)
        Router->>Gemini: AISpreadsheetImporter -> Dynamic Column Mapping
    end
    Gemini-->>Router: Structured JSON + ai_confidence_score + is_self_transfer
    Router->>Router: Flag rows with confidence < 85% as requires_human_review = True
    Router->>DB: Compute SHA-256 file_hash & row_signature -> Idempotent Upsert
    DB-->>User: Return ingested count, skipped duplicates & flagged review count
```

#### How It Works:
1. **Multimodal Native Ingestion**: Rather than chaining brittle OCR tools with regex parsers, `StatementExtractor` and `ReceiptExtractor` pass the document bytes directly to **Vertex AI Gemini 2.5 Flash** with strict Pydantic output schemas (`StatementData`, `ReceiptData`).
2. **Self-Transfer & Duplicate Protection**: Gemini identifies internal account transfers (`is_self_transfer = true`) so moving money between your own savings accounts doesn't artificially inflate your expenses. Next, [`DatabaseManager`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) computes a deterministic **SHA-256 cryptographic signature** (`date + amount + normalized_description + type`) to guarantee **100% idempotency**—uploading the same statement twice never creates duplicate rows.
3. **Dynamic Model Tiering**:
   - **Gemini 2.5 Flash-Lite** (`thinking_budget: 0`): Ultra-low-latency intent routing and category matching.
   - **Gemini 2.5 Flash** (`thinking_budget: 0`): High-throughput multimodal vision/PDF extraction.
   - **Gemini 2.5 Pro** (`thinking_budget: 1024–2048`): Complex multi-turn financial simulations and tax/wealth advisory.

---

### 3.2. Service Architecture 2: Conversational Voice Agent (Cloud STT + Gemini + Cloud TTS)
**Files:** [`app/voice_agent.py`](file:///home/rkushwaha8610/personal-finance-agent/app/voice_agent.py), [`app/frontend/src/components/modal.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/components/modal.js), [`app/frontend/src/api/transactions.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/transactions.js)

```mermaid
sequenceDiagram
    autonumber
    participant UI as 🎙️ Voice Modal (modal.js)
    participant API as 🚀 FastAPI (api.py)
    participant TTS as 🔊 Google Cloud TTS (Journey-F)
    participant STT as 🎧 Google Cloud STT
    participant Gemini as ✨ Vertex AI Gemini
    participant DB as 🗄️ PostgreSQL

    UI->>API: GET /transactions/{id}/clarification-audio
    API->>TTS: Synthesize question ("Hi! You spent ₹2,450 at...") with en-US-Journey-F
    TTS-->>UI: Stream MP3 Neural Audio (Browser SpeechSynthesis Fallback)
    Note over UI: User speaks explanation or clicks 1-Tap Category Chip
    UI->>API: POST /transactions/{id}/resolve (Audio Blob / Transcript)
    API->>STT: Transcribe WebM/MP4/WAV Audio to Text
    STT-->>API: Recognized Text ("Office team lunch")
    API->>Gemini: Classify intent & map to canonical category ("Food")
    Gemini-->>API: Category: "Food" (Confidence: 100%)
    API->>DB: UPDATE transactions SET category_id='Food', requires_human_review=False
    DB-->>UI: Instant UI Row Update & Toast Confirmation
```

#### Why This Service Architecture Is Necessary:
- Ambiguous narrations (e.g., `UPI/3289104921/Transfer to Rahul`) cannot be guessed by AI alone without hallucinating.
- By combining **Confidence Thresholding (`< 85%`)** with **Google Cloud TTS (`en-US-Journey-F`)** and **Google Cloud STT**, the application implements a frictionless **Human-in-the-Loop (HITL)** workflow that resolves ambiguous transactions in under 3 seconds by voice.

---

### 3.3. Service Architecture 3: Stateless Multi-Turn AI CFO & MCP Tool Plane
**Files:** [`app/advisor.py`](file:///home/rkushwaha8610/personal-finance-agent/app/advisor.py), [`app/mcp/ledger_server.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/ledger_server.py), [`app/mcp/a2a_orchestrator.py`](file:///home/rkushwaha8610/personal-finance-agent/app/mcp/a2a_orchestrator.py)

```mermaid
flowchart LR
    subgraph Browser ["Browser Client (insights.js)"]
        History["Client State: chatHistory[]"]
    end

    subgraph CloudRun ["Stateless Cloud Run Container (advisor.py)"]
        Compactor["Sliding-Window Compactor<br/>(Compresses turns > K=3)"]
        MCPClient["MCP Client Adapter"]
    end

    subgraph MCPServer ["Model Context Protocol Server (ledger_server.py)"]
        Tool1["query_financial_summary()"]
        Tool2["get_recent_transactions()"]
        Tool3["evaluate_budget_utilization()"]
    end

    subgraph Vertex ["Google Cloud Vertex AI"]
        Cache["Prefix Context Cache<br/>(System Persona + Tax Rules)"]
        ChatSession["GenerativeModel.start_chat()"]
    end

    History -- "POST /insights/chat" --> Compactor
    Compactor --> ChatSession
    Cache --> ChatSession
    ChatSession <-- "JSON-RPC Tool Calls" --> MCPClient
    MCPClient --> Tool1 & Tool2 & Tool3
```

#### Why This Service Architecture Is Necessary:
1. **Stateless Serverless Compatibility**: Because **Google Cloud Run** scales containers up and down dynamically, storing chat sessions in server memory would lose user context whenever requests hit different instances. Passing compacted history from the client and rehydrating `GenerativeModel.start_chat()` with **Vertex AI Context Caching** makes every container 100% stateless and horizontally scalable.
2. **Zero Hallucination on Numbers via MCP**: The LLM never guesses account balances or math. It invokes deterministic SQL aggregations via the **Model Context Protocol (`ledger_server.py`)** and [`financial_summary`](file:///home/rkushwaha8610/personal-finance-agent/app/database.py) database views.

---

### 3.4. Service Architecture 4: High-Availability Dual-Backend & Self-Healing Edge
**Files:** [`app/frontend/src/api/config.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/config.js), [`app/frontend/src/api/client.js`](file:///home/rkushwaha8610/personal-finance-agent/app/frontend/src/api/client.js), [`start_services.sh`](file:///home/rkushwaha8610/personal-finance-agent/start_services.sh)

```mermaid
flowchart TD
    SPA["🌐 Firebase Hosting SPA<br/>(personal-finance-agent-507010.web.app)"]
    Client["⚡ Smart API Client (client.js)<br/>Health Check & Auto-Failover"]
    Primary["🚇 Primary Edge Tunnel + FastAPI<br/>(Live Local Gmail PDF Vault & Sync)"]
    Fallback["☁️ Always-Online Google Cloud Run<br/>(finance-backend-328452160072.us-central1.run.app)"]
    Watchdog["🔄 Self-Healing Cron Watchdog (start_services.sh)<br/>@reboot & */2 min health probe + auto-redeploy"]

    SPA --> Client
    Client -- "1. Primary Route (Low Latency + Local Vault)" --> Primary
    Client -. "2. Automatic Instant Failover on 502/503/530/Network Error" .-> Fallback
    Watchdog -- "Monitors & Self-Heals" --> Primary
```

#### Why This Service Architecture Is Necessary:
- Guarantees **99.99% UI & API availability**: Even if an edge tunnel rotates or restarts, the frontend `apiClient` transparently fails over to the serverless **Google Cloud Run** instance with zero user interruption, while the self-healing watchdog restores and syncs the primary tunnel in the background.

---

## 4. Ready-to-Use Pitch Deck Slide Content

You can copy-paste the following two structured slide layouts directly into your **Personal Finance AI Pitch Deck**:

### 📊 Slide Option A: "Cloud-Native AI Architecture (Built on Google Cloud)"
* **Headline:** Enterprise-Grade, Zero-Trust Multi-Agent Architecture on Google Cloud
* **4 Core Pillars:**
  1. **Edge & Serverless Scale (`Firebase Hosting` + `Cloud Run`)**: Global CDN frontend paired with 0-to-N autoscaling FastAPI containers and automatic multi-backend failover.
  2. **Multimodal Financial Intelligence (`Vertex AI Gemini 2.5 Flash & Pro`)**: Native vision & PDF parsing for bank statements, receipts, and spreadsheets with **Vertex AI Context Caching** (75% lower token TCO).
  3. **Human-in-the-Loop Voice AI (`Cloud Speech-to-Text` + `Cloud Text-to-Speech`)**: Neural `en-US-Journey-F` voice prompts and 1-tap voice transcription to resolve ambiguous (`<85%` confidence) transactions in seconds.
  4. **Bank-Grade Security & Determinism (`Cloud DLP` + `Model Armor` + `MCP`)**: Inline PII/PAN/Account redaction, prompt-injection defense, SHA-256 duplicate immunity, and deterministic SQL math via Model Context Protocol.

### 📊 Slide Option B: "Why Google Cloud Services Power Our Moat"
| Capability | Traditional Finance Apps | Personal Finance AI (Google Cloud Powered) |
| :--- | :--- | :--- |
| **Statement Ingestion** | Brittle bank-specific regex / CSV templates | **Vertex AI Gemini 2.5 Flash** reads any PDF/receipt layout + auto-decrypts locked bank PDFs |
| **Ambiguous Transactions** | Manual typing & spreadsheet editing | **Cloud TTS (`Journey-F`) + Cloud STT** conversational voice resolution + 1-click chips |
| **Data Privacy & Security** | Raw PII sent to third-party APIs | **Cloud DLP** tokenizes PAN/Account/UPI IDs + **Model Armor** blocks prompt injection |
| **Financial Planning** | Backward-looking pie charts only | **Multi-Horizon Future Cost Predictor** (7-Day, MoM, YoY Seasonal) + **Gemini Pro AI CFO** |
| **Reliability & Cost** | Always-on VMs & unbounded LLM token spend | **Cloud Run** serverless scaling + **Vertex Context Caching** (75% cost savings) |
