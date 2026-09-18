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

- **`qwen2.5:3b`** (**Recommended**, ~2.0 GB in 4-bit `Q4_K_M`) — strong instruction-following and JSON compliance for its size; the **default** in `LLM_MODEL`. Easily fits alongside FastEmbed with headroom on a 4 GB card.
- **`qwen2.5:1.5b`** (~1.0 GB) — even lighter and faster if you want maximum headroom or lower latency.
- **`llama3.2:3b`** (~2.0 GB) — reasonable alternative English instruction model.

Point `LLM_MODEL` at the tag you pull. If no LLM is running, the backend seamlessly relies on the deterministic regex baseline.

### 3. Fixed Local AI Embedding Model

- **Single FastEmbed Model**: Backend is locked to **`BAAI/bge-large-en-v1.5`** (1024-dimensional, ~1.20 GB).
- **FastEmbed ONNX Runtime**: Local, in-process inference without requiring GPU or external API dependencies.
- **Deterministic Fallback**: If FastEmbed cannot load, backend automatically falls back to the internal resilient subword vectorizer.
- **No Runtime Model Switching**: Model selection endpoints were removed to keep embedding dimensions and latent space consistent.

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

- Runs on default port **`8765`** (configurable via `PORT` environment variable).
- Compatible with Python virtual environments (`venv` or `uv`).

### LLM Extraction Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_ENABLED` | `1` | Set `0` to disable LLM extraction (records ingest with empty extracted fields); also skips LLM startup in `run.sh` |
| `LLM_BASE_URL` | `http://localhost:11434` | Base URL of the local LLM server (Ollama or OpenAI-compatible) |
| `LLM_MODEL` | `qwen2.5:3b` | Local model tag to use for extraction |
| `LLM_TIMEOUT` | `120` | Seconds per LLM request |
| `LLM_CACHE_SIZE` | `8192` | Max cached extractions (keyed by text hash) |
| `HRM_SETUP_LLM` | `1` | Set `0` to skip Ollama install/model-pull steps in `setup_backend.sh` |
| `HRM_LLM_MODEL` | `qwen2.5:3b` | Model tag that `setup_backend.sh` ensures is pulled |

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
    Ext -->|POST /api/database/clear| API

    API --> EE[extracting_engine.py]
    EE -->|structured JSON| LLM[(Local LLM: qwen2.5:3b / Ollama)]
    EE -->|dense vectors| FastEmbed[(BAAI/bge-large-en-v1.5 1024-dim)]
    EE -->|CV PDF text| PDF[pypdf]
    EE --> Matcher[Pre-calculated Matching Engine]
    Matcher --> DB[(SQLite: jobs, candidates, matches)]
```

---

## Getting Started

### 1. Setup Environment

Run the setup script (automatically detects `uv` or `venv` and installs dependencies). It also offers to install/pull the local LLM (Ollama) for extraction:

```bash
cd HRM_Backend
./setup_backend.sh
```

### 2. Start the Local LLM (for extraction)

`run.sh` starts `ollama serve` automatically if it isn't already running. To pull the recommended model manually (fits a 4 GB Nvidia GPU in Q4):

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
| `GET` | `/api/jobs/{job_id}/candidates` | `limit: int = 100` | Get pre-calculated matching candidates with `matching_percentage`, `matched_skills`, and `matched_experience` |
| `POST` | `/api/candidates` | `force_recalculate: bool = False` | Ingest single candidate |
| `POST` | `/api/candidates/batch` | None | Batch ingest candidates with token (recalculates matching ONLY for new candidates; $O(1)$ status update for existing) |
| `GET` | `/api/candidates` | `q: Optional[str]`, `min_score: float = 20.0` | Search candidates via prompt with `query_relevance` score badge or list all |
| `GET` | `/api/candidates/{candidate_id}/jobs` | `limit: int = 100` | Get pre-calculated matching jobs for candidate with `matching_percentage` |
| `GET` | `/api/models` | None | Read-only backend model info (LLM model + FastEmbed fixed model status) |
| `POST` | `/api/database/clear` | None | Wipe all jobs, candidates, and matching indices with SQLite `VACUUM` |
