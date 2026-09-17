# HRM_Backend: Job Matching & Recruitment Intelligence Service

`HRM_Backend` is the local backend service for the **HRM_JobMatching** platform, providing AI-powered keyword extraction, CV analysis, and pre-calculated bi-directional job-candidate matching for recruitment workflows.

---

## Key Features

1. **Unique Service Port (`8765`)**:
   - Runs by default on port `8765` (configurable via `PORT` environment variable) to avoid common conflicts.
2. **Local Small AI Model for Semantic Understanding**:
   - Integrated **FastEmbed** (`BAAI/bge-small-en-v1.5`) ONNX embedding model (~67MB).
   - Generates 384-dimensional dense semantic vectors for job requirements and candidate profiles.
   - Calculates cosine similarity for semantic alignment without external API dependencies.
   - Includes automatic subword hashing fallback for 100% offline cold-start resilience.
3. **Pre-calculated Bi-directional Matching Engine (Optimized for New Records Only)**:
   - **New Records Only**: `recalculate_for_job` is executed ONLY when adding a brand new job; `recalculate_for_candidate` is executed ONLY when adding a brand new candidate.
   - When existing candidates or jobs are updated (e.g., candidate workflow status changes from `PM_ROUND` to `OFFER`), SQLite is updated in $O(1)$ time and matching recalculation is skipped.
   - Saves results into `job_candidate_matches` index table in SQLite.
   - When viewing a job or candidate, queries return immediately without expensive runtime recalculations.
4. **Candidate Status & Experience Tracking**:
   - Extracts and indexes candidate workflow status (e.g., `PM_ROUND`, `OPEN`, `INTERVIEW`, `OFFER`).
   - Extracts technical skills, years of experience, and seniority level (Junior, Middle, Senior, Lead).
5. **Virtual Environment Support (`uv` / `venv`)**:
   - Fast setup with `uv` (if available) or Python's native `venv`.
6. **Local AI Model Selection & Database Management**:
   - Supports selecting from pre-configured local ONNX models (`/api/models`, `/api/models/select`).
   - Clean database endpoint (`/api/database/clear`) with SQLite `VACUUM`.

---

## Architecture Overview

```mermaid
flowchart LR
    Ext[HRM_Ext Extension] -->|POST /api/jobs| API[FastAPI on port 8765]
    Ext -->|POST /api/candidates/batch| API
    Ext -->|GET /api/jobs?q=...| API
    Ext -->|GET /api/jobs/{id}/candidates| API
    Ext -->|GET /api/candidates?q=...| API
    Ext -->|GET /api/models| API
    Ext -->|POST /api/database/clear| API

    API --> NLP[NLP & Semantic Model]
    NLP --> FastEmbed[(Local ONNX Model)]
    NLP --> Matcher[Pre-calculated Matcher]
    Matcher --> DB[(SQLite: jobs, candidates, matches)]
```

---

## Getting Started

### 1. Setup Environment

Run the setup script (automatically detects `uv` or `venv`):

```bash
cd HRM_Backend
./setup_backend.sh
```

### 2. Start Backend Server

```bash
./run.sh
```

The API service will start on: `http://localhost:8765`
Interactive Swagger API documentation available at: `http://localhost:8765/docs`

### 3. Run Automated Tests

```bash
./.venv/bin/python -m unittest test_backend.py
```

---

## REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Service health, active model, and database stats |
| `POST` | `/api/jobs` | Ingest/update job request (recalculates matches ONLY if job is new) |
| `GET` | `/api/jobs` | List all saved jobs with candidate match counts (supports `?q=...` semantic filtering) |
| `GET` | `/api/jobs/{job_id}` | Get job request details |
| `GET` | `/api/jobs/{job_id}/candidates` | Get pre-calculated matching candidates (`name`, `status`, `matched_experience`, `matching_percentage`) |
| `POST` | `/api/candidates/batch` | Batch ingest candidates (recalculates matches ONLY for new candidates, fast status update for existing) |
| `GET` | `/api/candidates` | Search candidates via query prompt or list all |
| `GET` | `/api/candidates/{candidate_id}/jobs` | Get pre-calculated matching jobs (`job_title`, `matched_requests`, `matching_percentage`) |
| `GET` | `/api/models` | List available pre-configured local ONNX embedding models |
| `POST` | `/api/models/select` | Switch active local embedding model |
| `POST` | `/api/database/clear` | Wipe all jobs, candidates, and matches with SQLite `VACUUM` |

