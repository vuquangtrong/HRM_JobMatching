"""
Database module for HRM_Backend using SQLite.
Stores jobs, candidates, and pre-calculated matches with indexed lookups.
"""

import os
import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

DB_PATH = os.environ.get("HRM_DB_PATH", os.path.join(os.path.dirname(__file__), "hrm_matching.db"))


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a sqlite3 connection with Row factory enabled."""
    target_path = db_path or os.environ.get("HRM_DB_PATH", os.path.join(os.path.dirname(__file__), "hrm_matching.db"))
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes the SQLite schema and indices."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            # 1. Jobs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    code TEXT,
                    request TEXT,
                    job_description TEXT,
                    extracted_keywords TEXT,
                    extracted_level TEXT,
                    embedding TEXT,
                    raw_data TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
            """)

            # 2. Candidates table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY,
                    application_id TEXT,
                    code TEXT,
                    name TEXT,
                    position TEXT,
                    location TEXT,
                    status TEXT DEFAULT 'OPEN',
                    cv_urls TEXT,
                    cv_text TEXT,
                    extracted_keywords TEXT,
                    extracted_experiences TEXT,
                    extracted_level TEXT,
                    embedding TEXT,
                    raw_data TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
            """)

            # 3. Pre-calculated Job-Candidate matches table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_candidate_matches (
                    job_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    matching_percentage REAL NOT NULL,
                    semantic_score REAL DEFAULT 0.0,
                    skills_score REAL DEFAULT 0.0,
                    matched_skills TEXT,
                    matched_experience TEXT,
                    matched_requests TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (job_id, candidate_id),
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                    FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
                );
            """)

            # Fast lookup indices
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_matches_job_score 
                ON job_candidate_matches (job_id, matching_percentage DESC);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_matches_candidate_score 
                ON job_candidate_matches (candidate_id, matching_percentage DESC);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_candidates_status 
                ON candidates (status);
            """)
    finally:
        conn.close()


# ==========================================
# Jobs CRUD
# ==========================================

def save_or_update_job(job_data: Dict[str, Any], db_path: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Inserts or updates a job record in SQLite, returning (job, is_new)."""
    conn = get_db_connection(db_path)
    now = datetime.now(timezone.utc).isoformat()
    job_id = str(job_data["id"])
    
    extracted_keywords = json.dumps(job_data.get("extracted_keywords", []))
    embedding = json.dumps(job_data.get("embedding", [])) if job_data.get("embedding") is not None else None
    raw_data = json.dumps(job_data.get("raw_data", {}))

    try:
        with conn:
            existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            is_new = existing is None

            conn.execute("""
                INSERT INTO jobs (
                    id, title, code, request, job_description, extracted_keywords,
                    extracted_level, embedding, raw_data, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    code = coalesce(excluded.code, jobs.code),
                    request = excluded.request,
                    job_description = excluded.job_description,
                    extracted_keywords = coalesce(excluded.extracted_keywords, jobs.extracted_keywords),
                    extracted_level = coalesce(excluded.extracted_level, jobs.extracted_level),
                    embedding = coalesce(excluded.embedding, jobs.embedding),
                    raw_data = excluded.raw_data,
                    updated_at = excluded.updated_at;
            """, (
                job_id,
                job_data.get("title") or "Untitled Job",
                job_data.get("code") or "",
                job_data.get("request") or "",
                job_data.get("job_description") or "",
                extracted_keywords,
                job_data.get("extracted_level") or "",
                embedding,
                raw_data,
                now,
                now
            ))
        return get_job_by_id(job_id, db_path), is_new
    finally:
        conn.close()


def get_job_by_id(job_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a job by its ID."""
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        res = dict(row)
        res["extracted_keywords"] = json.loads(res["extracted_keywords"] or "[]")
        res["embedding"] = json.loads(res["embedding"] or "[]") if res["embedding"] else None
        res["raw_data"] = json.loads(res["raw_data"] or "{}")
        return res
    finally:
        conn.close()


def get_all_jobs(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves all jobs with candidate match count."""
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT j.*, 
                   COUNT(m.candidate_id) AS matched_candidates_count,
                   MAX(m.matching_percentage) AS top_match_percentage
            FROM jobs j
            LEFT JOIN job_candidate_matches m ON j.id = m.job_id
            GROUP BY j.id
            ORDER BY j.updated_at DESC;
        """).fetchall()
        jobs = []
        for r in rows:
            item = dict(r)
            item["extracted_keywords"] = json.loads(item["extracted_keywords"] or "[]")
            item["embedding"] = None  # Don't serialize heavy vector in summary lists
            item["raw_data"] = None
            jobs.append(item)
        return jobs
    finally:
        conn.close()


# ==========================================
# Candidates CRUD
# ==========================================

def save_or_update_candidate(cand_data: Dict[str, Any], db_path: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Inserts or updates a candidate record in SQLite, returning (candidate, is_new)."""
    conn = get_db_connection(db_path)
    now = datetime.now(timezone.utc).isoformat()
    cand_id = str(cand_data["id"])

    cv_urls = json.dumps(cand_data.get("cv_urls", []))
    extracted_keywords = json.dumps(cand_data.get("extracted_keywords", []))
    extracted_experiences = json.dumps(cand_data.get("extracted_experiences", {}))
    embedding = json.dumps(cand_data.get("embedding", [])) if cand_data.get("embedding") is not None else None
    raw_data = json.dumps(cand_data.get("raw_data", {}))

    try:
        with conn:
            existing = conn.execute("SELECT id FROM candidates WHERE id = ?", (cand_id,)).fetchone()
            is_new = existing is None

            conn.execute("""
                INSERT INTO candidates (
                    id, application_id, code, name, position, location, status,
                    cv_urls, cv_text, extracted_keywords, extracted_experiences,
                    extracted_level, embedding, raw_data, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    application_id = coalesce(excluded.application_id, candidates.application_id),
                    code = coalesce(excluded.code, candidates.code),
                    name = excluded.name,
                    position = excluded.position,
                    location = excluded.location,
                    status = excluded.status,
                    cv_urls = excluded.cv_urls,
                    cv_text = coalesce(excluded.cv_text, candidates.cv_text),
                    extracted_keywords = coalesce(excluded.extracted_keywords, candidates.extracted_keywords),
                    extracted_experiences = coalesce(excluded.extracted_experiences, candidates.extracted_experiences),
                    extracted_level = coalesce(excluded.extracted_level, candidates.extracted_level),
                    embedding = coalesce(excluded.embedding, candidates.embedding),
                    raw_data = excluded.raw_data,
                    updated_at = excluded.updated_at;
            """, (
                cand_id,
                cand_data.get("application_id") or "",
                cand_data.get("code") or "",
                cand_data.get("name") or "Unknown Candidate",
                cand_data.get("position") or "",
                cand_data.get("location") or "",
                cand_data.get("status") or "OPEN",
                cv_urls,
                cand_data.get("cv_text") or "",
                extracted_keywords,
                extracted_experiences,
                cand_data.get("extracted_level") or "",
                embedding,
                raw_data,
                now,
                now
            ))
        return get_candidate_by_id(cand_id, db_path), is_new
    finally:
        conn.close()


def get_candidate_by_id(cand_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves candidate by ID."""
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM candidates WHERE id = ?", (cand_id,)).fetchone()
        if not row:
            return None
        res = dict(row)
        res["cv_urls"] = json.loads(res["cv_urls"] or "[]")
        res["extracted_keywords"] = json.loads(res["extracted_keywords"] or "[]")
        res["extracted_experiences"] = json.loads(res["extracted_experiences"] or "{}")
        res["embedding"] = json.loads(res["embedding"] or "[]") if res["embedding"] else None
        res["raw_data"] = json.loads(res["raw_data"] or "{}")
        return res
    finally:
        conn.close()


def get_all_candidates(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves all candidates."""
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM candidates ORDER BY updated_at DESC").fetchall()
        cands = []
        for r in rows:
            res = dict(r)
            res["cv_urls"] = json.loads(res["cv_urls"] or "[]")
            res["extracted_keywords"] = json.loads(res["extracted_keywords"] or "[]")
            res["extracted_experiences"] = json.loads(res["extracted_experiences"] or "{}")
            res["embedding"] = None  # Don't serialize heavy vector
            res["raw_data"] = None
            cands.append(res)
        return cands
    finally:
        conn.close()


# ==========================================
# Pre-calculated Matches CRUD
# ==========================================

def save_match_result(
    job_id: str,
    candidate_id: str,
    matching_percentage: float,
    semantic_score: float,
    skills_score: float,
    matched_skills: List[str],
    matched_experience: str,
    matched_requests: str,
    db_path: Optional[str] = None
) -> None:
    """Inserts or updates pre-calculated match record in SQLite."""
    conn = get_db_connection(db_path)
    now = datetime.now(timezone.utc).isoformat()
    matched_skills_json = json.dumps(matched_skills)

    try:
        with conn:
            conn.execute("""
                INSERT INTO job_candidate_matches (
                    job_id, candidate_id, matching_percentage, semantic_score,
                    skills_score, matched_skills, matched_experience, matched_requests, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, candidate_id) DO UPDATE SET
                    matching_percentage = excluded.matching_percentage,
                    semantic_score = excluded.semantic_score,
                    skills_score = excluded.skills_score,
                    matched_skills = excluded.matched_skills,
                    matched_experience = excluded.matched_experience,
                    matched_requests = excluded.matched_requests,
                    updated_at = excluded.updated_at;
            """, (
                job_id,
                candidate_id,
                round(matching_percentage, 1),
                round(semantic_score, 3),
                round(skills_score, 3),
                matched_skills_json,
                matched_experience,
                matched_requests,
                now
            ))
    finally:
        conn.close()


def get_matching_candidates_for_job(job_id: str, limit: int = 100, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns pre-calculated candidates matching a job, sorted by matching percentage descending.
    Instant O(1) indexed query.
    """
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT 
                c.id, c.code, c.name, c.position, c.location, c.status, c.cv_urls,
                m.matching_percentage, m.semantic_score, m.skills_score,
                m.matched_skills, m.matched_experience, m.matched_requests, m.updated_at
            FROM job_candidate_matches m
            JOIN candidates c ON m.candidate_id = c.id
            WHERE m.job_id = ?
            ORDER BY m.matching_percentage DESC
            LIMIT ?;
        """, (job_id, limit)).fetchall()

        results = []
        for r in rows:
            item = dict(r)
            item["cv_urls"] = json.loads(item["cv_urls"] or "[]")
            item["matched_skills"] = json.loads(item["matched_skills"] or "[]")
            results.append(item)
        return results
    finally:
        conn.close()


def get_matching_jobs_for_candidate(candidate_id: str, limit: int = 100, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns pre-calculated jobs matching a candidate, sorted by matching percentage descending.
    Instant O(1) indexed query.
    """
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT 
                j.id, j.title, j.title AS job_title, j.code, j.request,
                m.matching_percentage, m.semantic_score, m.skills_score,
                m.matched_skills, m.matched_experience, m.matched_requests, m.updated_at
            FROM job_candidate_matches m
            JOIN jobs j ON m.job_id = j.id
            WHERE m.candidate_id = ?
            ORDER BY m.matching_percentage DESC
            LIMIT ?;
        """, (candidate_id, limit)).fetchall()

        results = []
        for r in rows:
            item = dict(r)
            item["matched_skills"] = json.loads(item["matched_skills"] or "[]")
            results.append(item)
        return results
    finally:
        conn.close()


def get_db_stats(db_path: Optional[str] = None) -> Dict[str, int]:
    """Returns total counts for jobs, candidates, and match pairs."""
    conn = get_db_connection(db_path)
    try:
        jobs_cnt = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        cands_cnt = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        matches_cnt = conn.execute("SELECT COUNT(*) FROM job_candidate_matches").fetchone()[0]
        return {
            "jobs_count": jobs_cnt,
            "candidates_count": cands_cnt,
            "matches_count": matches_cnt
        }
    finally:
        conn.close()


def clear_all_data(db_path: Optional[str] = None) -> None:
    """Deletes all records from jobs, candidates, and job_candidate_matches tables."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM job_candidate_matches;")
            conn.execute("DELETE FROM candidates;")
            conn.execute("DELETE FROM jobs;")
        # VACUUM outside transaction
        conn.isolation_level = None
        conn.execute("VACUUM;")
    finally:
        conn.close()
