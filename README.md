# AI-Powered Transaction Processing Pipeline

Backend + DevOps internship assignment: an async REST API that ingests dirty financial transaction CSVs, processes them through a Celery worker pipeline with LLM classification, and returns structured reports via polling endpoints.

**Stack:** FastAPI · PostgreSQL · Celery · Redis · Gemini · Docker Compose

---

## Features

- **Async job processing** — upload returns `job_id` immediately; worker handles heavy lifting
- **Data cleaning** — dates, amounts, casing, missing categories, duplicate removal
- **Anomaly detection** — statistical outliers (3× median) + USD on domestic merchants
- **LLM classification** — batched Gemini calls for uncategorised transactions
- **LLM narrative summary** — spend totals, top merchants, risk level, 2–3 sentence report
- **Retry + fallback** — LLM retries 3× with backoff; job completes even if LLM fails
- **One-command deploy** — `docker compose up --build` starts API, worker, Postgres, Redis

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and **running**
- Git (optional, for cloning)

No separate Postgres, Redis, or Python install required.

---

## Quick Start

```bash
# 1. Clone the repository
git clone <https://github.com/mujeebmasi/Alemeno-Assignment>
cd <repo-folder>

# 2. (Recommended) Add Gemini API key
cp .env.example .env
# Edit .env → set GEMINI_API_KEY=...

# 3. Start everything
MAKE SURE YOUR DOCKER DESKTOP IS RUNNING IN BACKGROUND
docker compose down
docker compose up --build -d
docker compose ps
```

**API:** http://localhost:8000  
**Swagger docs:** http://localhost:8000/docs  
**Health check:** http://localhost:8000/health

Stop services:

```bash
docker compose down
```

---

## Configuration

Copy `.env.example` to `.env` before starting Docker:

```env
GEMINI_API_KEY=your_gemini_api_key_here
LLM_PROVIDER=gemini
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Recommended | Free key from [Google AI Studio](https://aistudio.google.com/apikey) |
| `LLM_PROVIDER` | No | `gemini` (default) or `ollama` |
| `OLLAMA_BASE_URL` | No | Only if using local Ollama |

> **Note:** Without `GEMINI_API_KEY`, the pipeline still completes using rule-based fallbacks.

Database and Redis URLs are pre-configured in `docker-compose.yml` — no external services needed.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/jobs/upload` | Upload CSV, returns `job_id` |
| `GET` | `/jobs/{job_id}/status` | Poll job status (+ summary when done) |
| `GET` | `/jobs/{job_id}/results` | Full cleaned data, anomalies, breakdown |
| `GET` | `/jobs?status=` | List jobs (filter: pending, processing, completed, failed) |

---

## Example Usage

### 1. Health check

```bash
curl http://localhost:8000/health
```

```json
{"status":"ok"}
```

### 2. Upload CSV

```bash
curl -X POST http://localhost:8000/jobs/upload \
  -F "file=@sample_data/transactions.csv"
```

```json
{
  "job_id": 1,
  "status": "pending",
  "message": "Job created and queued for processing"
}
```

### 3. Poll status (wait ~5–10 seconds)

Replace `1` with your `job_id`:

```bash
curl http://localhost:8000/jobs/1/status
```

When completed:

```json
{
  "job_id": 1,
  "status": "completed",
  "filename": "transactions.csv",
  "row_count_raw": 95,
  "row_count_clean": 85,
  "summary": {
    "total_spend_inr": 1032918.22,
    "total_spend_usd": 35219.2,
    "anomaly_count": 5,
    "risk_level": "high",
    "narrative": "Processed 85 transactions..."
  }
}
```

### 4. Fetch full results

```bash
curl http://localhost:8000/jobs/1/results
```

Returns:
- `transactions` — 85 cleaned rows
- `anomalies` — ~5 flagged rows with reasons
- `category_breakdown` — spend per category
- `summary` — totals, narrative, risk level

### 5. List all jobs

```bash
curl http://localhost:8000/jobs
curl "http://localhost:8000/jobs?status=completed"
```

### Swagger UI (easiest for testing)

Open http://localhost:8000/docs → try each endpoint interactively.

---

## Processing Pipeline

When a job is dequeued, the worker runs these steps in order:

| Step | Module | Action |
|------|--------|--------|
| 1 | `cleaning.py` | Normalize dates (ISO 8601), strip `$`, uppercase status/currency, fill categories, remove duplicates |
| 2 | `anomaly.py` | Flag amount > 3× account median; flag USD + domestic merchant (Swiggy, Ola, IRCTC) |
| 3 | `llm.py` | Batch-classify uncategorised rows via Gemini |
| 4 | `llm.py` | Generate JSON summary (spend, top merchants, narrative, risk) |
| 5 | `pipeline.py` | Persist transactions + summary; mark job completed |

LLM calls retry up to 3 times (1s → 2s → 4s backoff). Failed batches use fallback rules and continue.

---

## Project Structure

```
.
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Environment settings
│   ├── database.py          # PostgreSQL connection
│   ├── models.py            # Job, Transaction, JobSummary tables
│   ├── schemas.py           # API response models
│   ├── api/jobs.py          # REST endpoints
│   ├── services/
│   │   ├── cleaning.py      # CSV validation + normalization
│   │   ├── anomaly.py       # Anomaly rules
│   │   ├── llm.py           # Gemini integration + fallbacks
│   │   └── pipeline.py      # Pipeline orchestrator
│   └── worker/
│       ├── celery_app.py    # Celery + Redis config
│       └── tasks.py         # Background task
├── sample_data/
│   └── transactions.csv     # Sample dataset (~95 dirty rows)
├── docker-compose.yml       # API, worker, Postgres, Redis
├── Dockerfile
├── requirements.txt
├── .env.example
├── ARCHITECTURE.md          # Detailed diagrams & design docs
└── README.md
```

---

## Architecture

```
Client ──POST /upload──► FastAPI ──► PostgreSQL (Job: pending)
                            │
                            └──► Redis ──► Celery Worker
                                              │
                                              ├── clean + anomaly detect
                                              ├── Gemini LLM
                                              └── save results → PostgreSQL

Client ──GET /status, /results──► FastAPI ──► PostgreSQL
```

For sequence diagrams, database schema, code flow maps, and video script → see **[ARCHITECTURE.md](./ARCHITECTURE.md)**.

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `jobs` | One row per CSV upload (status, row counts, timestamps) |
| `transactions` | Cleaned rows with anomaly flags and LLM categories |
| `job_summaries` | Spend totals, top merchants, narrative, risk level |

Job statuses: `pending` → `processing` → `completed` | `failed`

---

## Codebase Review

### Architecture overview

The codebase is split into a thin API layer, a background worker, and reusable service modules. That keeps HTTP request handling separate from CSV processing, anomaly detection, and LLM logic. The main flow is:

1. The API receives a CSV upload and validates the file.
2. A `Job` row is created in PostgreSQL with status `pending`.
3. The file content is sent to Celery through Redis.
4. The worker cleans the data, detects anomalies, classifies missing categories, generates a summary, and persists results.
5. The client polls `/status` and `/results` to retrieve the finished job.

### Why this structure works

- `app/api/jobs.py` stays focused on request/response handling.
- `app/services/cleaning.py`, `app/services/anomaly.py`, and `app/services/llm.py` keep the business logic reusable and testable.
- `app/services/pipeline.py` acts as the single orchestrator for the full job lifecycle.
- `app/models.py` mirrors the product shape with one job, many transactions, and one summary.
- `app/worker/tasks.py` keeps slow work off the API thread so uploads return immediately.

### Request lifecycle

`POST /jobs/upload` reads the file, validates the CSV columns, creates the database job record, and enqueues the Celery task. The Celery worker decodes the file, updates the job to `processing`, runs the cleaning and anomaly steps, calls the LLM for uncategorised rows and the narrative summary, then writes cleaned transactions and the job summary back to PostgreSQL. After that, the job is marked `completed`, and the API endpoints simply read the stored results.

### Bottlenecks at scale

If traffic jumped by 100x, the first pressure points would be memory usage from loading CSVs in the API and worker, the limited Celery concurrency, row-by-row database inserts, and LLM latency or provider failures. Redis would also become a backlog point if workers could not keep up.

### Next iteration for enterprise scale

For a production-grade version, I would move uploads to object storage, enqueue file references instead of raw content, split the pipeline into smaller idempotent stages, batch database writes, and put the LLM behind rate limiting and circuit breakers. The trade-off is more infrastructure and operational complexity, but the result is better throughput, easier retries, and stronger failure isolation.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Terminal hangs after `docker compose up` | Normal — logs stream in foreground. Use `-d` flag or open new terminal. |
| Job stuck on `pending` | `docker compose restart worker` then upload again with new `job_id` |
| `Job is not ready` on `/results` | Poll `/status` until `completed` (~5–10 sec) |
| `No LLM provider configured` | Add `GEMINI_API_KEY` to `.env`, restart: `docker compose down && docker compose up -d` |
| Port 8000 in use | `docker compose down` or stop other app on port 8000 |
| Docker not starting | Ensure Docker Desktop is running |

View logs:

```bash
docker compose logs api
docker compose logs worker
docker compose ps
```

---

## Sample Data

`sample_data/transactions.csv` contains ~95 intentionally dirty rows:
- Mixed date formats, `$` prefixes, inconsistent casing
- Missing categories, duplicate rows, suspiciously large amounts
- Expected output: **95 raw → 85 clean**, **~5 anomalies**

---

## Assignment Submission

- [ ] Public GitHub repository
- [ ] `docker compose up --build` works on fresh clone
- [ ] README curl examples verified
- [ ] Architecture diagram (draw.io) — see [ARCHITECTURE.md](./ARCHITECTURE.md) §13
- [ ] 3-minute technical video (camera on)

---

## License

MIT — free to use for learning and portfolio purposes.
