"""
HRM_Backend - Job Matching Platform REST API
FastAPI service running on the port from config.json (default 8765).
"""

import os
import re
import logging
import threading
import concurrent.futures
from typing import List, Dict, Any, Optional, Tuple
from contextlib import asynccontextmanager, contextmanager

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import (
    init_db,
    save_or_update_job,
    get_job_by_id,
    get_all_jobs,
    get_all_jobs_with_embeddings,
    save_or_update_candidate,
    get_candidate_by_id,
    get_all_candidates,
    get_all_candidates_with_embeddings,
    get_matching_candidates_for_job,
    get_matching_jobs_for_candidate,
    get_db_stats,
    clear_all_data
)
from extracting_engine import (
    extract_job_keywords,
    extract_candidate_keywords,
    download_and_extract_cv,
    get_semantic_model,
    LocalSemanticModel,
    get_llm_extractor,
    get_taxonomy_manager
)
from matching_engine import (
    recalculate_for_job,
    recalculate_for_candidate
)
from config import get_config

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("hrm.backend")


# --------------------------------------------------------------------------
# Idempotency & Concurrency Guards
# Bounded ref-counted per-record locks prevent duplicate processing and memory leaks.
# In-flight download futures ensure concurrent duplicate batches download each CV exactly once.
# --------------------------------------------------------------------------

_record_locks_guard = threading.Lock()
_record_locks: Dict[str, threading.Lock] = {}
_record_lock_refcount: Dict[str, int] = {}


@contextmanager
def record_lock(record_id: str):
    """Context manager providing per-record locking with automatic memory cleanup."""
    with _record_locks_guard:
        lock = _record_locks.setdefault(record_id, threading.Lock())
        _record_lock_refcount[record_id] = _record_lock_refcount.get(record_id, 0) + 1

    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _record_locks_guard:
            _record_lock_refcount[record_id] -= 1
            if _record_lock_refcount[record_id] <= 0:
                _record_lock_refcount.pop(record_id, None)
                _record_locks.pop(record_id, None)


def _get_record_lock(record_id: str):
    """Backward-compatible helper returning a record_lock context manager."""
    return record_lock(record_id)


_cv_download_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
_cv_download_guard = threading.Lock()
_cv_download_inflight: Dict[str, concurrent.futures.Future] = {}


def _do_download_cv(cid: str, url: str, name: str, token: str) -> Tuple[str, str]:
    """Worker task executing CV download and text extraction."""
    try:
        text = download_and_extract_cv(url, token)
        logger.info(f"Downloaded CV for '{name}' in thread pool ({len(text)} chars)")
        return cid, text
    except Exception as e:
        logger.warning(f"Could not download CV for '{name}' from {url}: {e}")
        return cid, ""


def _release_cv_download(candidate_id: str) -> None:
    """Clears the in-flight CV download marker once candidate processing completes."""
    with _cv_download_guard:
        _cv_download_inflight.pop(candidate_id, None)


# Initialize DB tables on import
init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes SQLite database and warm-starts the semantic model on startup."""
    init_db()
    # Warm up local embedding model
    model = get_semantic_model()
    logger.info("Local Semantic Model loaded and ready.")
    yield


app = FastAPI(
    title="HRM Job Matching Platform API",
    version="1.0.0",
    description="Backend for HRM recruitment extension: keyword extraction, CV parsing, and pre-calculated matching.",
    lifespan=lifespan
)

# Enable CORS for browser extensions and HRM portal
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# Pydantic Request Models
# ==========================================

class JobPayload(BaseModel):
    id: str = Field(..., description="Job Request unique ID")
    title: Optional[str] = Field(None, description="Job title")
    code: Optional[str] = Field(None, description="Job code")
    level: Optional[str] = Field(None, description="Seniority level")
    levelCandidate: Optional[Any] = Field(None, description="HRM level candidate spec")
    request: Optional[str] = Field(None, description="Summary of requirements")
    jobDescription: Optional[str] = Field(None, description="Full job description HTML")
    raw_data: Optional[Dict[str, Any]] = None


class CandidatePayload(BaseModel):
    id: str = Field(..., description="Candidate ID or CV ID")
    application_id: Optional[str] = None
    code: Optional[str] = None
    name: Optional[str] = None
    position: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = "OPEN"
    level: Optional[str] = None
    cvs: Optional[List[str]] = Field(default_factory=list)
    cvInformation: Optional[str] = None
    experience: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None


class BatchCandidatePayload(BaseModel):
    jobRequestId: Optional[str] = None
    token: Optional[str] = Field(None, description="HRM Bearer Token to fetch CV PDFs")
    candidates: List[CandidatePayload]


# ==========================================
# REST Endpoints
# ==========================================

@app.get("/api/health")
def health_check():
    """Returns system status, active database stats, and model availability."""
    stats = get_db_stats()
    model = get_semantic_model()
    return {
        "status": "healthy",
        "port": int(get_config()["server"]["port"]),
        "local_model": "FastEmbed (ONNX)" if model.fastembed_model is not None else "Resilient Subword Vectorizer",
        "stats": stats
    }


# ------------------------------------------
# Jobs APIs
# ------------------------------------------

@app.post("/api/jobs")
def ingest_job(payload: JobPayload, force_recalculate: bool = False):
    """
    Ingests or updates a Job Request from HRM_Extension.
    Extracts keywords, level, and embeddings.
    Calculates pre-calculated matches with all saved candidates ONLY IF the job is new
    or force_recalculate is explicitly requested.
    Concurrent duplicate submissions of the same job are serialized per job id so the
    expensive extraction and pre-calculation run only once.
    """
    title = payload.title or "Untitled Job"
    req = payload.request or ""
    desc = payload.jobDescription or ""

    with _get_record_lock(payload.id):
        return _ingest_job_locked(payload, title, req, desc, force_recalculate)


def _ingest_job_locked(
    payload: JobPayload,
    title: str,
    req: str,
    desc: str,
    force_recalculate: bool
) -> Dict[str, Any]:
    """Executes the actual job ingest while holding the per-record lock."""
    existing_job = get_job_by_id(payload.id)
    is_new = existing_job is None

    if is_new:
        # Brand new job: extract keywords and semantic embedding
        extracted = extract_job_keywords(title, req, desc, raw_level=payload.level or payload.levelCandidate)
        job_data = {
            "id": payload.id,
            "title": title,
            "code": payload.code or "",
            "request": req,
            "job_description": desc,
            "extracted_keywords": extracted["skills"],
            "extracted_level": extracted["level"],
            "embedding": extracted["embedding"],
            "raw_data": payload.raw_data or payload.model_dump()
        }
        saved_job, _ = save_or_update_job(job_data)
        matches_count = recalculate_for_job(payload.id)
        logger.info(f"New Job '{title}' (ID: {payload.id}) added. Precalculated matches for {matches_count} candidates.")
    else:
        # Existing job: update metadata, reuse existing embeddings/keywords to stay fast
        job_data = {
            "id": payload.id,
            "title": title,
            "code": payload.code or existing_job.get("code", ""),
            "request": req or existing_job.get("request", ""),
            "job_description": desc or existing_job.get("job_description", ""),
            "extracted_keywords": existing_job.get("extracted_keywords", []),
            "extracted_level": payload.level or existing_job.get("extracted_level", ""),
            "embedding": existing_job.get("embedding"),
            "raw_data": payload.raw_data or payload.model_dump()
        }
        saved_job, _ = save_or_update_job(job_data)
        if force_recalculate:
            matches_count = recalculate_for_job(payload.id)
            logger.info(f"Existing Job '{title}' (ID: {payload.id}) force recalculated for {matches_count} candidates.")
        else:
            matches_count = 0
            logger.info(f"Existing Job '{title}' (ID: {payload.id}) updated. Skipped matching recalculation.")

    return {
        "success": True,
        "is_new": is_new,
        "message": f"Job '{title}' ingested successfully.",
        "job": saved_job,
        "precalculated_matches": matches_count
    }


# Search Query Stop Words
SEARCH_STOP_WORDS = {
    "and", "or", "in", "with", "for", "the", "a", "an", "to", "of", "on", "at", "by", "from", "is", "are"
}


def compute_search_relevance(
    query_text: str,
    text_fields: List[str],
    extracted_keywords: List[str],
    doc_embedding: Optional[List[float]],
    q_vec: List[float],
    query_skills: Optional[List[str]] = None,
    is_dense: Optional[bool] = None
) -> float:
    """
    Computes search relevance score [0.0, 100.0] combining:
    1. Exact query match in text fields
    2. Token-level overlap on non-stop words
    3. Skill token overlap between query and stored document skills
    4. Concept-to-concept overlap for recognized skills
    5. Bipolar semantic embedding cosine similarity (baseline-corrected for dense transformers)
    Returns 0.0 if there is neither keyword/concept match nor meaningful semantic similarity.
    """
    q_lower = query_text.strip().lower()
    q_tokens = [w for w in re.findall(r"[a-zA-Z0-9\+#]+", q_lower) if len(w) > 1 or w in ("c", "r")]
    content_tokens = [t for t in q_tokens if t not in SEARCH_STOP_WORDS] or q_tokens

    kw_score = 0.0
    combined_text = " ".join(f for f in text_fields if f).lower()

    # 1. Exact full query match
    if q_lower and q_lower in combined_text:
        kw_score += 0.55

    # 2. Token overlap on non-stop words
    all_words = set(re.findall(r"[a-zA-Z0-9\+#]+", combined_text))
    matched_tokens = sum(1 for t in content_tokens if t in all_words)
    if content_tokens:
        kw_score += 0.35 * (matched_tokens / len(content_tokens))

    # 3. Skill token overlap with stored document skills (LLM-extracted)
    doc_skills_lower = [s.lower() for s in (extracted_keywords or [])]
    skill_hits = sum(1 for t in content_tokens if any(t == s or t in s.split() for s in doc_skills_lower))
    if content_tokens and skill_hits:
        kw_score += 0.40 * (skill_hits / len(content_tokens))

    # 4. Concept-to-concept overlap for recognized skills
    if query_skills:
        concept_matches = sum(1 for qs in query_skills if qs.lower() in doc_skills_lower)
        if concept_matches:
            kw_score += 0.40 * (concept_matches / len(query_skills))

    kw_score = min(1.0, kw_score)

    # 5. Dense / Bipolar semantic similarity
    if is_dense is None:
        model = get_semantic_model()
        is_dense = model.fastembed_model is not None

    raw_sem = LocalSemanticModel.cosine_similarity(q_vec, doc_embedding)
    if is_dense:
        # Dense transformer models (BGE / MiniLM) exhibit cosine anisotropy baseline around ~0.45-0.52
        baseline = 0.54
        norm_sem = max(0.0, (raw_sem - baseline) / (1.0 - baseline)) if raw_sem > baseline else 0.0
    else:
        # Bipolar signed hash vectorizer is zero-mean orthogonal centered at 0.0
        norm_sem = max(0.0, raw_sem)

    # 6. Combined relevance calculation
    if kw_score > 0 and norm_sem > 0:
        relevance = round(min(100.0, (kw_score * 60.0) + (norm_sem * 40.0)), 1)
    elif kw_score > 0:
        relevance = round(min(100.0, kw_score * 70.0), 1)
    elif norm_sem >= 0.15:
        relevance = round(min(100.0, norm_sem * 80.0), 1)
    else:
        # Zero keyword match AND semantic similarity below threshold -> completely unrelated / dump word
        relevance = 0.0

    return relevance


@app.get("/api/jobs")
def list_jobs(
    q: Optional[str] = Query(None, description="Semantic search query to filter jobs"),
    min_score: float = Query(20.0, description="Minimum relevance threshold to filter out unrelated results")
):
    """Lists saved jobs. If 'q' is provided, filters and ranks jobs semantically and by keyword matching."""
    all_jobs = get_all_jobs()
    if not q or not q.strip():
        return all_jobs

    query_text = q.strip().lower()
    tax_mgr = get_taxonomy_manager()
    query_skills = tax_mgr.extract_skills(query_text)
    model = get_semantic_model()
    is_dense = model.fastembed_model is not None
    q_vec = model.get_embedding(query_text)

    # Fetch jobs with embeddings in a single query to eliminate N+1 overhead
    jobs_with_emb = get_all_jobs_with_embeddings()
    emb_map = {j["id"]: j for j in jobs_with_emb}

    scored_jobs = []
    for j in all_jobs:
        full_job = emb_map.get(j["id"])
        if not full_job:
            continue

        relevance = compute_search_relevance(
            query_text=query_text,
            text_fields=[full_job.get("title", ""), full_job.get("code", ""), full_job.get("request", "")],
            extracted_keywords=full_job.get("extracted_keywords", []),
            doc_embedding=full_job.get("embedding"),
            q_vec=q_vec,
            query_skills=query_skills,
            is_dense=is_dense
        )

        if relevance >= min_score:
            j["query_relevance"] = relevance
            scored_jobs.append(j)

    scored_jobs.sort(key=lambda x: x.get("query_relevance", 0.0), reverse=True)
    return scored_jobs


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Retrieves specific job request details."""
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/candidates")
def get_job_candidates(job_id: str, limit: int = Query(100, ge=1, le=200)):
    """
    Returns pre-calculated candidates matching the specified job.
    Includes: candidate name, status, matched experience, and matching percentage.
    """
    candidates = get_matching_candidates_for_job(job_id, limit=limit)
    return {
        "job_id": job_id,
        "total_matches": len(candidates),
        "candidates": candidates
    }


# ------------------------------------------
# Candidates APIs
# ------------------------------------------

def process_single_candidate(
    cand_payload: CandidatePayload,
    token: Optional[str] = None,
    prefetched_cv_text: Optional[str] = None
) -> Tuple[Dict[str, Any], bool]:
    """
    Helper to save candidate.
    Only extracts embeddings, downloads CV, and recalculates matches IF the candidate is NEW.
    If candidate already exists, updates status and basic info without expensive matching recalculation.
    Serialized per candidate id: concurrent duplicate submissions of the same candidate process
    it exactly once; the losing submission falls back to the cheap metadata update.
    """
    with _get_record_lock(cand_payload.id):
        return _process_single_candidate_locked(cand_payload, token, prefetched_cv_text)


def _process_single_candidate_locked(
    cand_payload: CandidatePayload,
    token: Optional[str],
    prefetched_cv_text: Optional[str]
) -> Tuple[Dict[str, Any], bool]:
    """Executes the actual candidate ingest while holding the per-record lock."""
    try:
        existing_cand = get_candidate_by_id(cand_payload.id)
        is_new = existing_cand is None

        if not is_new:
            # Existing candidate: status, location or details might be updated!
            # Do NOT re-download CV, do NOT regenerate heavy embedding, do NOT run recalculate_for_candidate!
            cand_data = {
                "id": cand_payload.id,
                "application_id": cand_payload.application_id or existing_cand.get("application_id", ""),
                "code": cand_payload.code or existing_cand.get("code", ""),
                "name": cand_payload.name or existing_cand.get("name", ""),
                "position": cand_payload.position or existing_cand.get("position", ""),
                "location": cand_payload.location or existing_cand.get("location", ""),
                "status": cand_payload.status or existing_cand.get("status", "OPEN"),
                "cv_urls": cand_payload.cvs or existing_cand.get("cv_urls", []),
                "cv_text": existing_cand.get("cv_text", ""),
                "extracted_keywords": existing_cand.get("extracted_keywords", []),
                "extracted_experiences": existing_cand.get("extracted_experiences", {}),
                "extracted_level": cand_payload.level or existing_cand.get("extracted_level", ""),
                "embedding": existing_cand.get("embedding"),
                "raw_data": cand_payload.raw_data or cand_payload.model_dump()
            }
            saved, _ = save_or_update_candidate(cand_data)
            logger.info(f"Existing candidate '{saved.get('name')}' (ID: {cand_payload.id}) status updated to '{cand_data['status']}'. Skipped matching recalculation.")
            return saved, False
        else:
            # Brand new candidate: extract keywords, embeddings, save to DB, and recalculate
            cv_urls = cand_payload.cvs or []
            cv_text = prefetched_cv_text or ""

            # Try downloading first CV PDF if token provided and not prefetched
            if not cv_text and cv_urls and token:
                first_url = cv_urls[0]
                try:
                    cv_text = download_and_extract_cv(first_url, token)
                    logger.info(f"Successfully downloaded and extracted CV for candidate '{cand_payload.name or 'Unknown Candidate'}'. Data length: {len(cv_text)} characters.")
                except Exception as e:
                    logger.warning(f"Could not download CV from {first_url}: {e}")

            raw_langs = None
            if cand_payload.raw_data and isinstance(cand_payload.raw_data, dict):
                raw_langs = cand_payload.raw_data.get("languages") or (cand_payload.raw_data.get("cv", {}) or {}).get("languages")

            extracted = extract_candidate_keywords(
                name=cand_payload.name or "",
                position=cand_payload.position or "",
                location=cand_payload.location or "",
                cv_information=cand_payload.cvInformation,
                cv_text=cv_text,
                raw_level=cand_payload.level,
                raw_experience=cand_payload.experience,
                cv_urls=cv_urls,
                raw_languages=raw_langs
            )

            cand_data = {
                "id": cand_payload.id,
                "application_id": cand_payload.application_id or "",
                "code": cand_payload.code or "",
                "name": cand_payload.name or "Unknown Candidate",
                "position": cand_payload.position or "",
                "location": cand_payload.location or "",
                "status": cand_payload.status or "OPEN",
                "cv_urls": cv_urls,
                "cv_text": cv_text,
                "extracted_keywords": extracted["skills"],
                "extracted_experiences": {
                    "years_experience": extracted["years_experience"],
                    "summary": extracted["summary"]
                },
                "extracted_level": extracted["level"],
                "embedding": extracted["embedding"],
                "raw_data": cand_payload.raw_data or cand_payload.model_dump()
            }

            saved, _ = save_or_update_candidate(cand_data)
            recalculate_for_candidate(cand_payload.id)
            logger.info(f"New candidate '{saved.get('name')}' (ID: {cand_payload.id}) added. Precalculated matches with all jobs.")
            return saved, True
    finally:
        _release_cv_download(cand_payload.id)


@app.post("/api/candidates")
def ingest_candidate(payload: CandidatePayload):
    """Ingests a single candidate."""
    saved, is_new = process_single_candidate(payload)
    return {
        "success": True,
        "is_new": is_new,
        "candidate": saved
    }


@app.post("/api/candidates/batch")
def batch_ingest_candidates(payload: BatchCandidatePayload):
    """
    Batch ingests candidates extracted by HRM_Extension.
    Downloads CVs in parallel and coordinates duplicate concurrent submissions so CVs
    are downloaded strictly once. Ingests candidates and pre-calculates matches.
    """
    token = payload.token
    candidates = payload.candidates
    cv_texts_map = {}

    # Coordinate CV downloads concurrently
    if token and candidates:
        active_futures: Dict[str, concurrent.futures.Future] = {}
        for cand in candidates:
            with record_lock(cand.id):
                already_exists = get_candidate_by_id(cand.id) is not None
            if already_exists or not cand.cvs:
                continue

            with _cv_download_guard:
                if cand.id in _cv_download_inflight:
                    active_futures[cand.id] = _cv_download_inflight[cand.id]
                    continue
                first_url = cand.cvs[0]
                cand_name = cand.name or "Unknown Candidate"
                future = _cv_download_pool.submit(_do_download_cv, cand.id, first_url, cand_name, token)
                _cv_download_inflight[cand.id] = future
                active_futures[cand.id] = future

        # Await all CV downloads for this batch
        for cid, fut in active_futures.items():
            try:
                _, text = fut.result(timeout=45)
                if text:
                    cv_texts_map[cid] = text
            except Exception as e:
                logger.warning(f"Error resolving CV download for {cid}: {e}")

    def _process_item(cand: CandidatePayload) -> Tuple[Optional[Dict[str, Any]], bool]:
        try:
            prefetched = cv_texts_map.get(cand.id)
            saved, is_new = process_single_candidate(cand, token, prefetched_cv_text=prefetched)
            return saved, is_new
        except Exception as e:
            logger.error(f"Error processing candidate {cand.id}: {e}")
            return None, False

    processed = []
    new_count = 0
    updated_count = 0

    # Process candidates concurrently with a bounded pool to avoid request timeouts
    workers = min(4, len(candidates) or 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for saved, is_new in pool.map(_process_item, candidates):
            if saved and "id" in saved:
                processed.append(saved["id"])
                if is_new:
                    new_count += 1
                else:
                    updated_count += 1

    return {
        "success": True,
        "processed_count": len(processed),
        "new_count": new_count,
        "updated_count": updated_count,
        "candidate_ids": processed
    }


@app.get("/api/candidates")
def search_candidates(
    q: Optional[str] = Query(None, description="Free-text search query or skill"),
    min_score: float = Query(20.0, description="Minimum relevance threshold to filter out unrelated results")
):
    """
    Searches or lists candidates.
    If query `q` is provided, filters and ranks candidates semantically and by keyword matching.
    """
    all_cands = get_all_candidates()
    if not q or not q.strip():
        return all_cands

    # Rank candidates against search prompt
    query_text = q.strip().lower()
    tax_mgr = get_taxonomy_manager()
    query_skills = tax_mgr.extract_skills(query_text)
    model = get_semantic_model()
    is_dense = model.fastembed_model is not None
    q_vec = model.get_embedding(query_text)

    # Fetch candidates with embeddings in a single query to eliminate N+1 overhead
    cands_with_emb = get_all_candidates_with_embeddings()
    emb_map = {c["id"]: c for c in cands_with_emb}

    scored_cands = []
    for c in all_cands:
        cand_full = emb_map.get(c["id"])
        if not cand_full:
            continue

        relevance = compute_search_relevance(
            query_text=query_text,
            text_fields=[
                cand_full.get("name", ""),
                cand_full.get("position", ""),
                cand_full.get("status", ""),
                cand_full.get("code", ""),
                cand_full.get("location", "")
            ],
            extracted_keywords=cand_full.get("extracted_keywords", []),
            doc_embedding=cand_full.get("embedding"),
            q_vec=q_vec,
            query_skills=query_skills,
            is_dense=is_dense
        )

        if relevance >= min_score:
            c["query_relevance"] = relevance
            scored_cands.append(c)

    scored_cands.sort(key=lambda x: x.get("query_relevance", 0.0), reverse=True)
    return scored_cands


@app.get("/api/candidates/{candidate_id}/jobs")
def get_candidate_matching_jobs(candidate_id: str, limit: int = Query(100, ge=1, le=200)):
    """
    Returns pre-calculated jobs matching the specified candidate.
    Includes: job title, matched requests, and matching percentage.
    """
    jobs = get_matching_jobs_for_candidate(candidate_id, limit=limit)
    return {
        "candidate_id": candidate_id,
        "total_matches": len(jobs),
        "jobs": jobs
    }


# ------------------------------------------
# Database & Model Management APIs
# ------------------------------------------

@app.post("/api/database/clear")
def clear_database():
    """Clears all jobs, candidates, and pre-calculated matches from database."""
    clear_all_data()
    logger.info("Database cleared by user request.")
    return {
        "success": True,
        "message": "All jobs, candidates, and matches have been cleared from database."
    }


@app.get("/api/models")
def get_models():
    """Returns read-only backend model information from the active config."""
    cfg = get_config()
    model = get_semantic_model()
    llm_extractor = get_llm_extractor()
    llm_runtime = llm_extractor.get_runtime_info()
    fastembed_cfg = cfg["fastembed"]
    return {
        "llm": {
            "enabled": llm_extractor.is_enabled(),
            "model": cfg["llm"]["model"],
            "runtime": llm_runtime,
        },
        "fastembed": {
            "models": [
                {
                    "id": fastembed_cfg["model"],
                    "name": fastembed_cfg["model"],
                    "dim": model.dim,
                }
            ],
            "active_model": model.model_name,
            "is_loaded": model.fastembed_model is not None,
            "fallback": "Resilient Subword Vectorizer" if model.fastembed_model is None else None,
        }
    }



if __name__ == "__main__":
    import uvicorn
    port = int(get_config()["server"]["port"])
    logger.info(f"Starting HRM_Backend on http://0.0.0.0:{port}...")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
