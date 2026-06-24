# AI-Powered Transaction Processing Pipeline

A backend system that accepts a **dirty CSV of financial transactions**, processes it **asynchronously in the background**, uses an **LLM to classify and summarize** the data, and lets you **poll for results** via a REST API.

Built for the Backend + DevOps internship assignment.

---

## Table of Contents

1. [What This Project Does](#what-this-project-does)
2. [Tech Stack](#tech-stack)
3. [How to Run in VS Code](#how-to-run-in-vs-code)
4. [System Architecture](#system-architecture) — see also **[ARCHITECTURE.md](./ARCHITECTURE.md)** for full diagrams, flows, and video script
5. [Request Flow (Step by Step)](#request-flow-step-by-step)
6. [Processing Pipeline (What the Worker Does)](#processing-pipeline-what-the-worker-does)
7. [Project Structure — Every File Explained](#project-structure--every-file-explained)
8. [Database Tables](#database-tables)
9. [API Endpoints](#api-endpoints)
10. [Environment Variables](#environment-variables)
11. [Testing the API](#testing-the-api)
12. [How to Explain This in Your Video](#how-to-explain-this-in-your-video)
13. [Submission Checklist](#submission-checklist)
14. [Troubleshooting](#troubleshooting)

---

## What This Project Does

You upload a CSV file like `sample_data/transactions.csv` (your `alemeno.csv` dataset).

The system:
1. Saves a **Job** record in PostgreSQL with status `pending`
2. Sends the work to a **Celery worker** via **Redis** (job queue)
3. The worker **cleans** the data, **detects anomalies**, calls the **LLM** for classification + summary
4. Saves cleaned **transactions** and a **summary report** to the database
5. You poll `GET /jobs/{id}/status` until status is `completed`
6. You fetch full results from `GET /jobs/{id}/results`

**Why async?** CSV processing + LLM calls can take several seconds. The API returns immediately with a `job_id` so the client doesn't have to wait.

---

## Tech Stack

| Layer | Technology | Why |
|-------|------------|-----|
| API | FastAPI | Fast, auto-generates docs at `/docs` |
| Database | PostgreSQL | Stores jobs, transactions, summaries |
| Job Queue | Celery + Redis | Background processing, decouples API from worker |
| LLM | Gemini 1.5 Flash | Free tier, classifies categories + writes narrative |
| Containers | Docker Compose | One command starts everything |

**You do NOT need** external Postgres/Redis URLs. Docker runs them internally.

---

## How to Run in VS Code

### Prerequisites

Install these once on your machine:
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (must be running)
- [VS Code](https://code.visualstudio.com/)
- Optional: VS Code extension **"Docker"** by Microsoft (to see containers in sidebar)

### Step 1 — Open the project

1. Open VS Code
2. **File → Open Folder** → select `Internship Assignment`
3. You should see the `app/`, `sample_data/`, `docker-compose.yml` folders/files in the Explorer

### Step 2 — Add Gemini API key (recommended)

1. In VS Code terminal (**Terminal → New Terminal**), run:
   ```bash
   copy .env.example .env
   ```
2. Open `.env` and paste your key from [Google AI Studio](https://aistudio.google.com/apikey):
   ```env
   GEMINI_API_KEY=your_key_here
   LLM_PROVIDER=gemini
   ```
3. **Never commit `.env`** — it's in `.gitignore`

> Without a key, the app still works using rule-based fallbacks for LLM steps.

### Step 3 — Start all services

In the VS Code terminal:

```bash
docker compose up --build
```

Wait until you see logs like:
- `api-1  | Uvicorn running on http://0.0.0.0:8000`
- `worker-1 | celery@... ready.`

**To run in background** (frees the terminal):
```bash
docker compose up --build -d
```

### Step 4 — Test in browser

Open: **http://localhost:8000/docs**

This is Swagger UI — you can test all endpoints from here.

### Step 5 — Upload the sample CSV

In terminal (new tab) or VS Code terminal:

```bash
curl -X POST http://localhost:8000/jobs/upload -F "file=@sample_data/transactions.csv"
```

You'll get back something like:
```json
{"job_id": 1, "status": "pending", "message": "Job created and queued for processing"}
```

Poll status:
```bash
curl http://localhost:8000/jobs/1/status
```

Get full results when `status` is `completed`:
```bash
curl http://localhost:8000/jobs/1/results
```

### Step 6 — Stop everything

```bash
docker compose down
```

### VS Code Docker sidebar (optional)

If you installed the Docker extension:
- Click the **Docker** icon in the left sidebar
- Under **Containers**, you'll see `internshipassignment-api-1`, `worker-1`, `db-1`, `redis-1`
- Right-click → **View Logs** to debug

---

## System Architecture

```
┌─────────────┐     POST /jobs/upload      ┌─────────────┐
│   Client    │ ──────────────────────────► │   FastAPI   │
│ (curl/web)  │ ◄── job_id (immediate) ──── │   (api)     │
└─────────────┘                             └──────┬──────┘
                                                   │
                    ┌──────────────────────────────┼──────────────────────────────┐
                    │                              │                              │
                    ▼                              ▼                              ▼
             ┌─────────────┐               ┌─────────────┐               ┌─────────────┐
             │  PostgreSQL │               │    Redis    │               │   Celery    │
             │  (db)       │               │  (broker)   │──────────────►│  Worker     │
             │             │◄──────────────│             │   task queue  │             │
             │ jobs        │   read/write  └─────────────┘               └──────┬──────┘
             │ transactions│                                                     │
             │ summaries   │◄────────────────────────────────────────────────────┘
             └─────────────┘              saves results after processing

                                                    │
                                                    ▼
                                             ┌─────────────┐
                                             │ Gemini API  │
                                             │ (LLM calls) │
                                             └─────────────┘
```

### The 4 Docker containers

| Container | Role |
|-----------|------|
| `api` | Receives HTTP requests, creates jobs, returns responses |
| `worker` | Picks up jobs from Redis, runs the processing pipeline |
| `db` | PostgreSQL — permanent storage |
| `redis` | Message broker — holds the task queue |

---

## Request Flow (Step by Step)

Here is exactly what happens when you call `POST /jobs/upload`:

```
1. Client sends CSV file
        ↓
2. API (jobs.py → upload_csv)
   - Checks file is .csv and not empty
   - Validates required columns exist
   - Creates Job row in DB with status = "pending"
   - Encodes CSV as base64 (safe for Redis message)
   - Calls process_transaction_job.delay(job_id, encoded_csv)
   - Returns { job_id, status: "pending" } immediately  ← client gets this in ~100ms
        ↓
3. Redis receives the Celery task message
        ↓
4. Worker (tasks.py → process_transaction_job)
   - Decodes base64 back to CSV bytes
   - Calls pipeline.process_job()
        ↓
5. Pipeline (pipeline.py) runs 5 steps:
   a) clean_transactions()     — fix dates, amounts, casing, remove duplicates
   b) detect_anomalies()       — flag outliers and USD+domestic merchant
   c) classify_uncategorised() — LLM assigns categories (batched)
   d) generate_narrative_summary() — LLM writes JSON summary
   e) Save all transactions + JobSummary to PostgreSQL
   - Sets Job status = "completed"
        ↓
6. Client polls GET /jobs/{id}/status
   - Returns status + brief summary when done
        ↓
7. Client calls GET /jobs/{id}/results
   - Returns full transaction list, anomalies, category breakdown, narrative
```

---

## Processing Pipeline (What the Worker Does)

### Step A — Data Cleaning (`services/cleaning.py`)

| Problem in CSV | Fix applied |
|----------------|-------------|
| Dates like `04-09-24` | Converted to ISO `2024-09-04` |
| Amounts like `$11,325.79` | Stripped to `11325.79` |
| Status `success` / `SUCCESS` | Uppercased to `SUCCESS` |
| Currency `inr` | Uppercased to `INR` |
| Missing category | Filled with `Uncategorised` |
| Duplicate rows | Removed (exact match on all columns) |

Your dataset: **95 raw rows → 85 clean rows** (10 duplicates removed).

### Step B — Anomaly Detection (`services/anomaly.py`)

Two rules:

1. **Statistical outlier**: amount > 3× that account's median spend
   - Example: TXN2001 (Flipkart, ₹146,100) for ACC005

2. **Currency mismatch**: USD currency but merchant is domestic-only (Swiggy, Ola, IRCTC)
   - Example: Zomato in USD when it's an Indian food app

### Step C — LLM Classification (`services/llm.py`)

- Only runs on rows with category = `Uncategorised`
- Sends **batches of 15** transactions per API call (not one call per row)
- LLM picks from: Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other
- If LLM fails after 3 retries → uses merchant-name rules as fallback, marks `llm_failed=true`

### Step D — LLM Narrative Summary (`services/llm.py`)

One LLM call returns JSON with:
- `total_spend_inr`, `total_spend_usd`
- `top_merchants` (top 3)
- `anomaly_count`
- `narrative` (2-3 sentences)
- `risk_level` (`low` / `medium` / `high`)

### Step E — Retry Logic

- LLM calls retry up to **3 times** with delays: 1s → 2s → 4s (exponential backoff)
- If all retries fail, job **still completes** — fallback logic kicks in

---

## Project Structure — Every File Explained

```
Internship Assignment/
│
├── docker-compose.yml      # Starts api, worker, db, redis — ONE command
├── Dockerfile              # Python 3.12 image, installs deps, copies app code
├── requirements.txt        # Python packages (fastapi, celery, pandas, etc.)
├── .env.example            # Template for API keys (copy to .env)
├── .gitignore              # Ignores .env, __pycache__, etc.
├── README.md               # This file
│
├── sample_data/
│   └── transactions.csv    # Your alemeno.csv dataset (95 dirty rows)
│
└── app/                    # Main application code
    ├── main.py             # FastAPI app entry point, /health endpoint
    ├── config.py           # Reads .env settings (DB URL, API keys)
    ├── database.py         # PostgreSQL connection + get_db() for API routes
    ├── models.py           # Database table definitions (Job, Transaction, JobSummary)
    ├── schemas.py          # API response shapes (Pydantic models)
    │
    ├── api/
    │   └── jobs.py         # All 4 REST endpoints live here
    │
    ├── services/
    │   ├── cleaning.py     # CSV validation + data normalization
    │   ├── anomaly.py      # Outlier + currency anomaly rules
    │   ├── llm.py          # Gemini API calls + fallbacks
    │   └── pipeline.py     # Orchestrates all 5 processing steps
    │
    └── worker/
        ├── celery_app.py   # Celery configuration (connects to Redis)
        └── tasks.py        # Background task that runs the pipeline
```

### File-by-file detail

#### `app/main.py`
- Creates the FastAPI application
- On startup: creates database tables if they don't exist
- Registers the `/jobs` router
- Exposes `GET /health` for health checks

#### `app/config.py`
- Loads settings from environment variables / `.env` file
- Contains: database URL, Redis URL, Gemini API key, batch size, domestic merchant list

#### `app/database.py`
- `engine` — connection to PostgreSQL
- `SessionLocal` — factory for database sessions
- `get_db()` — yields a DB session per API request (auto-closes after)

#### `app/models.py`
- **Job** — one row per CSV upload (status, filename, row counts, timestamps)
- **Transaction** — one row per cleaned transaction row
- **JobSummary** — one summary per completed job (spend totals, narrative, risk)

#### `app/schemas.py`
- Defines the **JSON shape** of API responses
- Separate from `models.py` (DB tables) — this is what the client sees
- Example: `JobStatusResponse`, `JobResultsResponse`

#### `app/api/jobs.py`
| Endpoint | Function | What it does |
|----------|----------|--------------|
| `POST /jobs/upload` | `upload_csv` | Validate CSV, create job, enqueue worker task |
| `GET /jobs/{id}/status` | `get_job_status` | Return current job status + brief summary |
| `GET /jobs/{id}/results` | `get_job_results` | Return full transactions, anomalies, breakdown |
| `GET /jobs` | `list_jobs` | List all jobs, filter by `?status=` |

#### `app/services/cleaning.py`
- `validate_csv()` — quick check before creating a job
- `clean_transactions()` — full normalization, returns cleaned DataFrame

#### `app/services/anomaly.py`
- `detect_anomalies()` — adds `is_anomaly` and `anomaly_reason` columns

#### `app/services/llm.py`
- `classify_uncategorised()` — batched LLM category assignment
- `generate_narrative_summary()` — single LLM call for JSON summary
- `category_breakdown()` — per-category spend totals (no LLM needed)
- `_fallback_classify()` / `_fallback_summary()` — used when LLM unavailable

#### `app/services/pipeline.py`
- `process_job()` — **the main orchestrator**
- Calls cleaning → anomaly → LLM → saves to DB → marks job complete

#### `app/worker/celery_app.py`
- Configures Celery to use Redis as message broker
- Auto-discovers tasks in `app/worker/tasks.py`

#### `app/worker/tasks.py`
- `process_transaction_job` — the Celery task
- Decodes CSV from base64, opens DB session, calls `process_job()`
- Creates DB tables on worker startup

#### `docker-compose.yml`
- Defines 4 services and how they connect
- API exposed on port **8000**
- Postgres and Redis are internal only (no external ports needed)

---

## Database Tables

### `jobs`
| Column | Type | Description |
|--------|------|-------------|
| id | int | Primary key, used as job_id in API |
| filename | string | Original uploaded filename |
| status | enum | pending → processing → completed / failed |
| row_count_raw | int | Rows before dedup |
| row_count_clean | int | Rows after dedup |
| created_at | datetime | When upload happened |
| completed_at | datetime | When processing finished |
| error_message | text | Set if status = failed |

### `transactions`
| Column | Type | Description |
|--------|------|-------------|
| job_id | FK | Links to jobs.id |
| txn_id, date, merchant, amount... | | Cleaned CSV columns |
| is_anomaly | bool | True if flagged |
| anomaly_reason | text | Why it was flagged |
| llm_category | string | Category assigned by LLM |
| llm_failed | bool | True if LLM fallback was used |

### `job_summaries`
| Column | Type | Description |
|--------|------|-------------|
| job_id | FK | One summary per job |
| total_spend_inr / usd | float | Spend totals |
| top_merchants | JSON | Top 3 merchants by spend |
| anomaly_count | int | Number of flagged rows |
| narrative | text | LLM-written summary paragraph |
| risk_level | string | low / medium / high |
| category_breakdown | JSON | Spend per category |

---

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/health` | Health check |
| POST | `/jobs/upload` | Upload CSV, get job_id |
| GET | `/jobs/{job_id}/status` | Poll job status |
| GET | `/jobs/{job_id}/results` | Full results (when completed) |
| GET | `/jobs?status=completed` | List jobs |

Interactive docs: **http://localhost:8000/docs**

---

## Environment Variables

| Variable | Required? | Default | Description |
|----------|-----------|---------|-------------|
| `GEMINI_API_KEY` | Recommended | — | Google Gemini API key |
| `LLM_PROVIDER` | No | `gemini` | `gemini` or `ollama` |
| `DATABASE_URL` | No | set in docker-compose | Postgres connection string |
| `REDIS_URL` | No | set in docker-compose | Redis connection string |

Create `.env` from `.env.example` — Docker Compose reads it automatically.

---

## Testing the API

```bash
# 1. Health check
curl http://localhost:8000/health

# 2. Upload CSV
curl -X POST http://localhost:8000/jobs/upload \
  -F "file=@sample_data/transactions.csv"

# 3. Poll status (replace 1 with your job_id)
curl http://localhost:8000/jobs/1/status

# 4. Get full results
curl http://localhost:8000/jobs/1/results

# 5. List all completed jobs
curl "http://localhost:8000/jobs?status=completed"
```

---

## How to Explain This in Your Video

### Part 1 — System Design (~1 min)

Show the architecture diagram and say:
> "Client uploads CSV to FastAPI. API saves a Job to Postgres and pushes a task to Redis. Celery worker picks it up, cleans data, detects anomalies, calls Gemini for classification and summary, then saves results. Client polls for status and fetches results."

**Key design choices to mention:**
- **Async via Celery** — API doesn't block during LLM calls
- **PostgreSQL** — persistent storage for jobs and results
- **Batched LLM calls** — 15 rows per call, not 1 per row (cost + speed)
- **Fallback logic** — job completes even if LLM fails
- **Docker Compose** — zero manual setup for reviewers

### Part 2 — Scale & Bottlenecks (~2 min)

**Where it breaks at 100× traffic:**
| Bottleneck | Why |
|------------|-----|
| Single Celery worker | One worker processes one job at a time |
| LLM API rate limits | Gemini free tier has quotas |
| Postgres connection pool | Default pool may exhaust under load |
| Redis single instance | Becomes message broker bottleneck |
| Synchronous polling | Clients hammering `/status` endpoint |

**Enterprise fixes:**
| Change | Trade-off |
|--------|-----------|
| Multiple Celery workers | More memory, need task deduplication |
| Kubernetes + auto-scaling | Complexity, ops overhead |
| Read replicas for Postgres | Replication lag for status polls |
| Webhooks instead of polling | Client needs callback URL |
| Redis Cluster | Higher infra cost |
| LLM response caching | Stale results for identical inputs |

---

## Submission Checklist

- [ ] Public GitHub repo link
- [ ] `docker compose up --build` works on fresh clone
- [ ] README with curl examples (this file)
- [ ] Architecture diagram (draw.io / Miro) — public link
- [ ] 3-minute video with camera on — public link
- [ ] Optional: `GEMINI_API_KEY` in `.env` for real LLM output

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `port 8000 already in use` | Stop other apps on 8000, or change port in docker-compose.yml |
| Job stuck on `pending` | Check worker logs: `docker logs internshipassignment-worker-1` |
| `No LLM provider configured` | Add `GEMINI_API_KEY` to `.env` and restart: `docker compose down && docker compose up` |
| Docker not starting | Make sure Docker Desktop is running |
| Code changes not picked up | Restart worker: `docker compose restart worker` |
| `Job is not ready` on /results | Wait a few seconds, poll `/status` until `completed` |

---

## Sample Data

`sample_data/transactions.csv` is your original `alemeno.csv` file — 95 intentionally dirty transaction rows used for testing and demo.
