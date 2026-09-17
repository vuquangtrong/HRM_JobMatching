# HRM_Backend: Job Matching & Recruitment Intelligence Service

`HRM_Backend` is the local backend service for the **HRM_JobMatching** platform. It provides AI-powered keyword extraction, CV analysis, local dense embedding vectorization, calibrated semantic search, and pre-calculated bi-directional job-candidate matching for recruitment workflows.

---

## Key Features

### 1. High-Accuracy Local AI Embedding Models (Base & Large Only)

- **Small Models Deprecated**: Small models (e.g. 384-dimensional models) have been completely removed from `AVAILABLE_MODELS` in favor of Base and Large models for maximum semantic representation accuracy.
- **Pre-configured ONNX Models**:
  - **`BAAI/bge-large-en-v1.5`** (**Default - Recommended for Maximum Accuracy**): 1024-dimensional dense vectors (~1.20 GB).
  - **`BAAI/bge-base-en-v1.5`** (**High Accuracy Base**): 768-dimensional dense vectors (~210 MB).
  - **`mixedbread-ai/mxbai-embed-large-v1`** (**SOTA English Large**): 1024-dimensional dense vectors (~640 MB).
  - **`thenlper/gte-large`** (**General Text Embeddings Large**): 1024-dimensional dense vectors (~1.20 GB).
  - **`thenlper/gte-base`** (**General Text Embeddings Base**): 768-dimensional dense vectors (~440 MB).
  - **`snowflake/snowflake-arctic-embed-m`** (**Snowflake Arctic Base**): 768-dimensional dense vectors (~430 MB).
  - **`snowflake/snowflake-arctic-embed-l`** (**Snowflake Arctic Large**): 1024-dimensional dense vectors (~1.02 GB).
  - **`jinaai/jina-embeddings-v2-base-en`** (**Long Context 8K Base**): 768-dimensional dense vectors (~520 MB).
- **FastEmbed ONNX Runtime**: Local, in-process inference without requiring GPU or external API dependencies.
- **Dynamic Model Selection**: Switch models on the fly via `POST /api/models/select` with optional database reset (`clear_database=true`) to avoid dimension mismatches.

### 2. Calibrated Semantic Search Engine (Dump Query Rejection)

- **Dense Baseline Normalization**: Dense transformer embeddings suffer from vector space anisotropy where unrelated text baselines at $\sim 0.45 - 0.52$ cosine similarity. The engine applies baseline subtraction ($B = 0.54$) so unrelated strings and dump queries (`dump word`, `asdfghjkl`, etc.) normalize to $0.0$ semantic relevance.
- **Bipolar Signed Hash Fallback**: For offline environments without FastEmbed, a deterministic CRC32 subword vectorizer uses bipolar signs ($\pm 1.0$) to guarantee zero-mean orthogonal expectation ($E[\cos(u, v)] = 0.0$) between unrelated documents.
- **Relevance Scoring & Filtering**: Combines exact text matching, token overlap, AI skill taxonomy overlap, and normalized semantic similarity. Both `/api/jobs` and `/api/candidates` accept a configurable `min_score` threshold (default `20.0`) to guarantee that dump or unrelated queries return 0 results (`[]`).

### 3. Pre-calculated Bi-directional Matching Engine

- **Optimized for New Records Only**:
  - `recalculate_for_job` runs **only** when ingesting a brand-new job request.
  - `recalculate_for_candidate` runs **only** when ingesting a brand-new candidate.
  - When existing candidates or jobs are re-sent with updated status (e.g., candidate workflow changes from `PM_ROUND` to `OFFER`), SQLite is updated in $O(1)$ time and matching recalculation is skipped.
- **N+1 Database Query Elimination**: Embeddings and keywords are loaded in single-pass batch queries, accelerating search and matching operations.
- **Immediate Response**: Match results are stored in SQLite (`job_candidate_matches`). Browsing matches is instantaneous.

### 4. AI Keyword & Entity Taxonomy

- **Domain Taxonomy**: Automatically extracts technical skills across Programming Languages (`C++`, `C`, `Python`, `Java`, `TypeScript`, etc.), Testing/QA (`System Test`, `Component Test`, `Automation`, `Selenium`, `Robot Framework`, `ISTQB`), Embedded/Automotive (`AUTOSAR`, `ECU`, `CAN`, `LIN`, `CANoe`, `Microcontrollers`), Cloud/DevOps, and Foreign Languages (`English`, `Japanese`, `German`).
- **Seniority & Experience Parsing**: Detects seniority levels (`Intern`, `Junior`, `Middle`, `Senior`, `Lead`) and years of experience.
- **PDF CV Extraction**: Uses `pypdf` to parse attached CV files fetched via HRM Bearer tokens.

### 5. Service Port & Environment Setup

- Runs on default port **`8765`** (configurable via `PORT` environment variable).
- Compatible with Python virtual environments (`venv` or `uv`).

---

## Architecture Overview

```mermaid
flowchart LR
    Ext[HRM_Ext Extension] -->|POST /api/jobs| API[FastAPI on port 8765]
    Ext -->|POST /api/candidates/batch| API
    Ext -->|GET /api/jobs?q=...&min_score=...| API
    Ext -->|GET /api/jobs/{id}/candidates| API
    Ext -->|GET /api/candidates?q=...&min_score=...| API
    Ext -->|GET /api/candidates/{id}/jobs| API
    Ext -->|GET /api/models| API
    Ext -->|POST /api/models/select| API
    Ext -->|POST /api/database/clear| API

    API --> NLP[NLP & Calibrated Semantic Search]
    NLP --> FastEmbed[(BAAI/bge-large-en-v1.5 1024-dim)]
    NLP --> HashFallback[(Bipolar Signed Hash Vectorizer)]
    NLP --> Matcher[Pre-calculated Matching Engine]
    Matcher --> DB[(SQLite: jobs, candidates, matches)]
```

---

## Getting Started

### 1. Setup Environment

Run the setup script (automatically detects `uv` or `venv` and installs dependencies):

```bash
cd HRM_Backend
./setup_backend.sh
```

### 2. Start Backend Server

```bash
./run.sh
```

- **API Service**: `http://localhost:8765`
- **Swagger Documentation**: `http://localhost:8765/docs`
- **Health & Active Model Check**: `http://localhost:8765/api/health`

### 3. Run Automated Tests

Execute the comprehensive test suite (11 unit tests covering ingestion, semantic search, candidate matching, dump query rejection, dynamic model switching, and database clearing):

```bash
./.venv/bin/python3 -m unittest test_backend.py
```

---

## REST API Reference

| Method | Endpoint | Parameters | Description |
| --- | --- | --- | --- |
| `GET` | `/api/health` | None | Service health, port, active model type, and database counts |
| `POST` | `/api/jobs` | `force_recalculate: bool = False` | Ingest/update job request (recalculates matching ONLY for new jobs) |
| `GET` | `/api/jobs` | `q: Optional[str]`, `min_score: float = 20.0` | List jobs with match counts; supports calibrated semantic search |
| `GET` | `/api/jobs/{job_id}` | None | Get specific job request specification and extracted keywords |
| `GET` | `/api/jobs/{job_id}/candidates` | `limit: int = 100` | Get pre-calculated matching candidates with `matching_percentage`, `matched_skills`, and `matched_experience` |
| `POST` | `/api/candidates` | `force_recalculate: bool = False` | Ingest single candidate |
| `POST` | `/api/candidates/batch` | None | Batch ingest candidates with token (recalculates matching ONLY for new candidates; $O(1)$ status update for existing) |
| `GET` | `/api/candidates` | `q: Optional[str]`, `min_score: float = 20.0` | Search candidates via prompt with `query_relevance` score badge or list all |
| `GET` | `/api/candidates/{candidate_id}/jobs` | `limit: int = 100` | Get pre-calculated matching jobs for candidate with `matching_percentage` |
| `GET` | `/api/models` | None | List available local embedding models and identify active model |
| `POST` | `/api/models/select` | `{"model_name": str, "clear_database": bool}` | Switch active local embedding model |
| `POST` | `/api/database/clear` | None | Wipe all jobs, candidates, and matching indices with SQLite `VACUUM` |
