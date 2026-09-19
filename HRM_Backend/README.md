# HRM_Backend: Job Matching & Recruitment Intelligence Service

`HRM_Backend` is the local backend service for the **HRM_JobMatching** platform. It provides a **Hybrid Extraction Architecture (Regex Baseline + LLM Enrichment)**, local dense embedding vectorization, calibrated semantic search, and pre-calculated bi-directional job-candidate matching for recruitment workflows. All extraction logic lives in `extracting_engine.py`.

---

## Key Features

### 1. Hybrid Architecture: Regex Baseline + LLM Enrichment (`extracting_engine.py`)

- **Persistent Text-Based Taxonomy (`data/taxonomy.json`)**: Regular expression patterns for technical skills, seniority levels, spoken languages, and years of experience are stored in a human-readable, editable JSON text file.
- **Dynamic Runtime Taxonomy Updates**: When the local LLM extracts novel skills (e.g. `solidity`, `langchain`, `snowflake`), it automatically generates regex boundary patterns, updates the in-memory compiled patterns, and persists them back to `taxonomy.json` at runtime.
- **Guaranteed Fault-Tolerant Baseline**: If the local LLM is disabled, unpulled, or times out, the backend never stores empty records; the deterministic regex baseline immediately extracts canonical skills, level, and years of experience.
- **LLM Enrichment**: When the local LLM is available, it enriches records with open-vocabulary skills, extracts spoken languages, and generates concise natural language candidate summaries based on actual CV text.
- **LRU Skill Embedding Cache**: Matching performance uses cached embeddings for individual skills, eliminating repetitive ONNX forward passes and accelerating matching calculations by over **500x**.
- **Concurrent Download Coordination**: Concurrent duplicate batch submissions share background download futures to ensure each CV PDF is downloaded strictly once.
- **Backends**: Supports Ollama-native (`/api/chat` with JSON mode) and any OpenAI-compatible server (`/v1/chat/completions` — llama.cpp, LM Studio, vLLM, Ollama).
- **PDF CV Extraction**: Uses `pypdf` to parse attached CV files fetched via HRM Bearer tokens.

### 2. Open-Vocabulary Extraction Model for 4 GB Nvidia GPUs

Recommended setup for a 4 GB VRAM GPU:

```bash
ollama pull qwen2.5:3b
```

The extraction model is configured in `config.json` (`llm.model`), defaulting to **`qwen2.5:3b`** (~2.0 GB in 4-bit `Q4_K_M`) — strong instruction-following and JSON compliance for its size, fits alongside FastEmbed with headroom on a 4 GB card. Other good options: `qwen2.5:1.5b` (~1.0 GB) for more headroom, `llama3.2:3b` (~2.0 GB) for English. `setup_backend.sh` and `run.sh` read the model from `config.json` and pull/warn accordingly.

### 3. Local AI Embedding Model (Configurable via `config.json`)

- **Configurable FastEmbed Model**: The semantic model id, embedding dimension, and cache directory are read from `config.json` (`fastembed.*`), defaulting to **`BAAI/bge-large-en-v1.5`** (1024-dimensional, ~1.20 GB). Nothing is hardcoded in Python or shell scripts.
- **FastEmbed ONNX Runtime**: Local, in-process inference without requiring GPU or external API dependencies.
- **Deterministic Fallback**: If FastEmbed cannot load, backend automatically falls back to the internal resilient subword vectorizer (sized by `fastembed.dim`).
- **Keep Embedding Space Consistent**: Changing the FastEmbed model switches the latent space; re-ingest existing records (or clear the DB with `POST /api/database/clear`) after switching.

### 4. Calibrated Semantic Search Engine (Dump Query Rejection)

- **Dense Baseline Normalization**: Dense transformer embeddings suffer from vector space anisotropy where unrelated text baselines at $\sim 0.45 - 0.52$ cosine similarity. The engine applies baseline subtraction ($B = 0.54$) so unrelated strings and dump queries (`dump word`, `asdfghjkl`, etc.) normalize to $0.0$ semantic relevance.
- **Bipolar Signed Hash Fallback**: For offline environments without FastEmbed, a deterministic CRC32 subword vectorizer uses bipolar signs ($\pm 1.0$) to guarantee zero-mean orthogonal expectation ($E[\cos(u, v)] = 0.0$) between unrelated documents.
- **Relevance Scoring & Filtering**: Combines exact text matching, token overlap, stored (LLM-extracted) skill token overlap, and normalized semantic similarity. Both `/api/jobs` and `/api/candidates` accept a configurable `min_score` threshold (default `20.0`) to guarantee that dump or unrelated queries return 0 results (`[]`).
- **Search is LLM-free at query time**: Queries are ranked with dense embeddings + keyword overlap only (no per-query LLM latency).

### 5. Pre-calculated Bi-directional Matching Engine

- **Optimized for New Records Only**:
  - `recalculate_for_job` runs **only** when ingesting a brand-new job request.
  - `recalculate_for_candidate` runs **only** when ingesting a brand-new candidate.
  - When existing candidates or jobs are re-sent with updated status (e.g., candidate workflow changes from `PM_ROUND` to `OFFER`), SQLite is updated in $O(1)$ time and matching recalculation is skipped.
- **N+1 Database Query Elimination**: Embeddings and keywords are loaded in single-pass batch queries, accelerating search and matching operations.
- **Immediate Response**: Match results are stored in SQLite (`job_candidate_matches`). Browsing matches is instantaneous.

### 6. Service Port & Environment Setup

- Runs on the port from `config.json` (`server.port`, default **`8765`**).
- Compatible with Python virtual environments (`venv` or `uv`).

### 7. Candidate-Job Application Relationships & Multi-Job Support

- **Multi-Job Applications**: Candidates can apply to multiple jobs. Applications are persisted in SQLite in the `candidate_applications` table.
- **Automatic Status Inheritance**: When candidates are ingested from HRM (which returns `jobRequests` for each candidate), the candidate is automatically linked to the applied job, inheriting the candidate's recruitment status (e.g. `PM_ROUND`, `INTERVIEW`, `OFFER`).
- **Dynamic Application Endpoint (`POST /api/candidates/{candidate_id}/apply`)**: Allows applying a candidate to a new job on-the-fly from the extension matching views. If no status is specified in the request body, it automatically inherits the candidate's current recruitment status.
- **Bi-directional Application State**: Matching endpoints (`/api/jobs/{job_id}/candidates` and `/api/candidates/{candidate_id}/jobs`) return `applied_jobs`, `is_applied`, and `application_status` fields, enabling real-time status badges and `Review`/`Applied` button states.

### Configuration File (`config.json`)

All model settings live in `HRM_Backend/config.json`. `setup_backend.sh` reads it to prepare data (pull the LLM model, optionally pre-download the FastEmbed cache), `run.sh` reads it for the port/LLM defaults, and the backend loads it at runtime. An optional gitignored `config.local.json` is merged on top for machine-specific overrides.

```json
{
  "server": { "port": 8765 },
  "llm": {
    "enabled": true,
    "base_url": "http://localhost:11434",
    "model": "qwen2.5:3b",
    "timeout_seconds": 30,
    "cache_size": 4096
  },
  "fastembed": {
    "model": "BAAI/bge-large-en-v1.5",
    "dim": 1024,
    "cache_dir": "./fastembed_cache",
    "prewarm": false
  },
  "taxonomy": { "path": "./data/taxonomy.json" }
}
```

Precedence (highest wins): environment variables → `config.local.json` → `config.json` → built-in defaults.

### Environment Variables (override `config.json`)

| Variable | Description |
| --- | --- |
| `LLM_ENABLED` | Set `0` to disable LLM extraction (records ingest with empty extracted fields); also skips LLM startup in `run.sh` |
| `LLM_BASE_URL` | Base URL of the local LLM server (Ollama or OpenAI-compatible) |
| `LLM_MODEL` | Local model tag to use for extraction |
| `LLM_TIMEOUT` | Seconds per LLM request |
| `LLM_CACHE_SIZE` | Max cached extractions (keyed by text hash) |
| `SEMANTIC_MODEL` | FastEmbed model id |
| `SEMANTIC_MODEL_DIM` | Embedding dimension (fallback vectorizer size; FastEmbed reports real dim) |
| `FASTEMBED_CACHE_DIR` | Directory where FastEmbed caches downloaded models |
| `HRM_TAXONOMY_PATH` | Path to the taxonomy JSON file |
| `PORT` | HTTP port |
| `HRM_CONFIG_PATH` | Path to an alternative config JSON file |
| `HRM_SETUP_LLM` | Set `0` to skip Ollama install/model-pull steps in `setup_backend.sh` |
| `HRM_LLM_MODEL` | Model tag that `setup_backend.sh` ensures is pulled |
| `HRM_SETUP_FASTEMBED` | Set `1` for `setup_backend.sh` to pre-download the FastEmbed model cache |

---

## Architecture Overview

```mermaid
flowchart LR
    Ext[HRM_Extension Extension] -->|POST /api/jobs| API[FastAPI on port 8765]
    Ext -->|POST /api/candidates/batch| API
    Ext -->|POST /api/candidates/{id}/apply| API
    Ext -->|GET /api/jobs?q=...&min_score=...| API
    Ext -->|GET /api/jobs/{id}/candidates| API
    Ext -->|GET /api/candidates?q=...&min_score=...| API
    Ext -->|GET /api/candidates/{id}/jobs| API
    Ext -->|GET /api/models| API
    Ext -->|POST /api/database/clear| API

    API --> EE[extracting_engine.py]
    EE -->|structured JSON| LLM[(Local LLM: config.json llm.model / Ollama)]
    EE -->|dense vectors| FastEmbed[(FastEmbed: config.json fastembed.model)]
    EE -->|CV PDF text| PDF[pypdf]
    EE --> Matcher[Pre-calculated Matching Engine]
    Matcher --> DB[(SQLite: jobs, candidates, matches, candidate_applications)]
```

---

## Getting Started

### 1. Setup Environment

Run the setup script (automatically detects `uv` or `venv` and installs dependencies). It reads `config.json` and offers to install/pull the local LLM (Ollama) for extraction; set `"prewarm": true` under `fastembed` to also pre-download the embedding model:

```bash
cd HRM_Backend
./setup_backend.sh
```

### 2. Start the Local LLM (for extraction)

`run.sh` reads `llm.base_url` and `llm.model` from `config.json` and starts `ollama serve` automatically if it isn't already running. To pull the recommended model manually (fits a 4 GB Nvidia GPU in Q4):

```bash
ollama pull qwen2.5:3b
```

### 3. Start Backend Server

```bash
./run.sh
```

- **API Service**: `http://localhost:8765`
- **Swagger Documentation**: `http://localhost:8765/docs`
- **Health & Active Model Check**: `http://localhost:8765/api/health`

### 4. Run Automated Tests

Execute both suites (backend + LLM extraction with a simulated LLM server):

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
| `GET` | `/api/jobs/{job_id}/candidates` | `limit: int = 100` | Get pre-calculated matching candidates with `matching_percentage`, `matched_skills`, `matched_experience`, `applied_jobs`, and `is_applied` |
| `POST` | `/api/candidates` | `force_recalculate: bool = False` | Ingest single candidate (records `applied_job` application if provided) |
| `POST` | `/api/candidates/batch` | None | Batch ingest candidates with token (records application for `jobRequestId` with inherited candidate status; recalculates matching ONLY for new candidates) |
| `GET` | `/api/candidates` | `q: Optional[str]`, `min_score: float = 20.0` | Search candidates via prompt with `query_relevance` score badge or list all (includes `applied_jobs`) |
| `GET` | `/api/candidates/{candidate_id}` | None | Get a single candidate with full extracted CV information (keywords, experiences, level, CV text) for the review page |
| `GET` | `/api/candidates/{candidate_id}/jobs` | `limit: int = 100` | Get pre-calculated matching jobs for candidate with `matching_percentage`, `is_applied`, and `application_status` |
| `POST` | `/api/candidates/{candidate_id}/apply` | Body: `{"job_id": "...", "status": "..."}` | Apply candidate to a job; inherits current candidate status if `status` is omitted |
| `GET` | `/api/models` | None | Read-only backend model info (LLM model + FastEmbed fixed model status) |
| `POST` | `/api/database/clear` | None | Wipe all jobs, candidates, applications, and matching indices with SQLite `VACUUM` |
