"""
HRM_Backend - Job Matching Platform REST API
FastAPI service running on custom port 8765.
"""

import os
import logging
from typing import List, Dict, Any, Optional, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import (
    init_db,
    save_or_update_job,
    get_job_by_id,
    get_all_jobs,
    save_or_update_candidate,
    get_candidate_by_id,
    get_all_candidates,
    get_matching_candidates_for_job,
    get_matching_jobs_for_candidate,
    get_db_stats,
    clear_all_data
)
from nlp_extractor import (
    extract_job_keywords,
    extract_candidate_keywords,
    download_and_extract_cv,
    get_semantic_model,
    LocalSemanticModel,
    AVAILABLE_MODELS
)
from matching_engine import (
    recalculate_for_job,
    recalculate_for_candidate
)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("hrm.backend")


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
    allow_credentials=True,
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
    cvs: Optional[List[str]] = Field(default_factory=list)
    cvInformation: Optional[str] = None
    experience: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None


class BatchCandidatePayload(BaseModel):
    jobRequestId: Optional[str] = None
    token: Optional[str] = Field(None, description="HRM Bearer Token to fetch CV PDFs")
    candidates: List[CandidatePayload]


class SelectModelPayload(BaseModel):
    model_name: str = Field(..., description="Local model name/ID to activate")
    clear_database: bool = Field(False, description="Clear database to avoid dimension/latent space mismatches")



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
        "port": int(os.environ.get("PORT", 8765)),
        "local_model": "FastEmbed (ONNX)" if model.fastembed_model is not None else "Resilient Subword Vectorizer",
        "stats": stats
    }


# ------------------------------------------
# Jobs APIs
# ------------------------------------------

@app.post("/api/jobs")
def ingest_job(payload: JobPayload, force_recalculate: bool = False):
    """
    Ingests or updates a Job Request from HRM_Ext.
    Extracts keywords, level, and embeddings.
    Calculates pre-calculated matches with all saved candidates ONLY IF the job is new
    or force_recalculate is explicitly requested.
    """
    title = payload.title or "Untitled Job"
    req = payload.request or ""
    desc = payload.jobDescription or ""

    existing_job = get_job_by_id(payload.id)
    is_new = existing_job is None

    if is_new:
        # Brand new job: extract keywords and semantic embedding
        extracted = extract_job_keywords(title, req, desc)
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
            "extracted_level": existing_job.get("extracted_level", ""),
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


@app.get("/api/jobs")
def list_jobs(q: Optional[str] = Query(None, description="Semantic search query to filter jobs")):
    """Lists all saved jobs. If 'q' is provided, ranks jobs semantically and by keyword matching."""
    all_jobs = get_all_jobs()
    if not q or not q.strip():
        return all_jobs

    query_text = q.strip().lower()
    model = get_semantic_model()
    q_vec = model.get_embedding(query_text)

    scored_jobs = []
    for j in all_jobs:
        full_job = get_job_by_id(j["id"])
        if not full_job:
            continue

        # Semantic cosine similarity
        j_vec = full_job.get("embedding")
        sem_score = LocalSemanticModel.cosine_similarity(q_vec, j_vec)

        # Keyword matching
        title = (full_job.get("title") or "").lower()
        req = (full_job.get("request") or "").lower()
        skills = [s.lower() for s in full_job.get("extracted_keywords") or []]

        kw_match = 0.0
        if query_text in title or query_text in req:
            kw_match += 0.5
        for s in skills:
            if s in query_text or query_text in s:
                kw_match += 0.3

        relevance = round(min(100.0, (sem_score * 50.0) + (kw_match * 50.0)), 1)
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

def process_single_candidate(cand_payload: CandidatePayload, token: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """
    Helper to save candidate.
    Only extracts embeddings, downloads CV, and recalculates matches IF the candidate is NEW.
    If candidate already exists, updates status and basic info without expensive matching recalculation.
    """
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
            "extracted_level": existing_cand.get("extracted_level", ""),
            "embedding": existing_cand.get("embedding"),
            "raw_data": cand_payload.raw_data or cand_payload.model_dump()
        }
        saved, _ = save_or_update_candidate(cand_data)
        logger.info(f"Existing candidate '{saved.get('name')}' (ID: {cand_payload.id}) status updated to '{cand_data['status']}'. Skipped matching recalculation.")
        return saved, False
    else:
        # Brand new candidate: extract keywords, embeddings, save to DB, and recalculate
        cv_urls = cand_payload.cvs or []
        cv_text = ""

        # Try downloading first CV PDF if token provided
        if cv_urls and token:
            first_url = cv_urls[0]
            try:
                cv_text = download_and_extract_cv(first_url, token)
                logger.info(f"Successfully downloaded and extracted CV for candidate '{cand_payload.name or 'Unknown Candidate'}'. Data length: {len(cv_text)} characters.")
            except Exception as e:
                logger.warning(f"Could not download CV from {first_url}: {e}")

        extracted = extract_candidate_keywords(
            name=cand_payload.name or "",
            position=cand_payload.position or "",
            location=cand_payload.location or "",
            cv_information=cand_payload.cvInformation,
            cv_text=cv_text,
            raw_status=cand_payload.status
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
    Batch ingests candidates extracted by HRM_Ext.
    Extracts status (e.g. PM_ROUND), position, keywords, and pre-calculates matches ONLY for new candidates.
    """
    token = payload.token
    processed = []
    new_count = 0
    updated_count = 0
    for cand in payload.candidates:
        try:
            saved, is_new = process_single_candidate(cand, token)
            processed.append(saved["id"])
            if is_new:
                new_count += 1
            else:
                updated_count += 1
        except Exception as e:
            logger.error(f"Error processing candidate {cand.id}: {e}")

    return {
        "success": True,
        "processed_count": len(processed),
        "new_count": new_count,
        "updated_count": updated_count,
        "candidate_ids": processed
    }


@app.get("/api/candidates")
def search_candidates(q: Optional[str] = Query(None, description="Free-text search query or skill")):
    """
    Searches or lists candidates.
    If query `q` is provided, computes similarity with candidate profiles/skills.
    """
    all_cands = get_all_candidates()
    if not q or not q.strip():
        return all_cands

    # Rank candidates against search prompt
    query_text = q.strip().lower()
    model = get_semantic_model()
    q_vec = model.get_embedding(query_text)

    scored_cands = []
    for c in all_cands:
        cand_full = get_candidate_by_id(c["id"])
        if not cand_full:
            continue

        # Semantic score
        c_vec = cand_full.get("embedding")
        sem_score = LocalSemanticModel.cosine_similarity(q_vec, c_vec)

        # Keyword match
        skills = [s.lower() for s in cand_full.get("extracted_keywords") or []]
        name = (cand_full.get("name") or "").lower()
        pos = (cand_full.get("position") or "").lower()
        status = (cand_full.get("status") or "").lower()

        kw_match = 0.0
        if query_text in name or query_text in pos or query_text in status:
            kw_match += 0.5
        for s in skills:
            if s in query_text or query_text in s:
                kw_match += 0.3

        total_score = round(min(100.0, (sem_score * 50.0) + (kw_match * 50.0)), 1)
        c["query_relevance"] = total_score
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
    """Returns pre-selected local models list and current active model."""
    model = get_semantic_model()
    return {
        "models": AVAILABLE_MODELS,
        "active_model": model.model_name,
        "is_loaded": model.fastembed_model is not None
    }


@app.post("/api/models/select")
def select_model(payload: SelectModelPayload):
    """Switches the active local semantic embedding model. Optionally clears DB to prevent dimension mismatches."""
    if payload.clear_database:
        clear_all_data()
        logger.info("Database cleared during model switch to prevent dimension mismatch.")

    model = get_semantic_model()
    success = model.switch_model(payload.model_name)
    logger.info(f"Switched model to '{payload.model_name}' (Loaded: {success}).")
    return {
        "success": success,
        "active_model": model.model_name,
        "is_loaded": model.fastembed_model is not None,
        "database_cleared": payload.clear_database
    }



if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8765))
    logger.info(f"Starting HRM_Backend on http://0.0.0.0:{port}...")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
