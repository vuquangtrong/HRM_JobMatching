"""
Matching Engine for HRM_Backend.
Performs pre-calculated, bi-directional matching between Jobs and Candidates.
Combines:
1. Semantic vector similarity (from local model)
2. Hard technical skills overlap
3. Role & position compatibility
4. Seniority / experience alignment
"""

import re
import logging
from typing import Dict, Any, List, Optional
from database import (
    get_job_by_id,
    get_candidate_by_id,
    get_all_jobs,
    get_all_candidates,
    save_match_result
)
from nlp_extractor import LocalSemanticModel

logger = logging.getLogger("hrm.matching_engine")


def calculate_role_compatibility(job_title: str, candidate_position: str) -> float:
    """Calculates alignment between candidate's position and the job title."""
    if not job_title or not candidate_position:
        return 0.5

    t_lower = job_title.lower()
    p_lower = candidate_position.lower()

    # Exact token match
    p_words = set(re.findall(r"\w+", p_lower))
    t_words = set(re.findall(r"\w+", t_lower))

    if not p_words:
        return 0.5

    overlap = p_words.intersection(t_words)
    if len(overlap) == len(p_words):
        return 1.0
    elif len(overlap) > 0:
        return 0.8

    # Related domains
    if ("tester" in p_lower or "qa" in p_lower or "qc" in p_lower) and ("test" in t_lower or "qa" in t_lower):
        return 0.95
    if ("developer" in p_lower or "engineer" in p_lower) and ("developer" in t_lower or "engineer" in t_lower):
        return 0.9

    return 0.4


def calculate_level_compatibility(job_level: Optional[str], candidate_level: Optional[str]) -> float:
    """Calculates compatibility between requested level and candidate level."""
    levels = ["intern", "junior", "middle", "senior", "lead"]
    jl = (job_level or "middle").lower()
    cl = (candidate_level or "middle").lower()

    if jl == cl:
        return 1.0

    try:
        ji = levels.index(jl)
        ci = levels.index(cl)
        diff = abs(ji - ci)
        if diff == 1:
            # e.g., Middle applying for Senior or Junior
            return 0.75
        elif diff == 2:
            return 0.45
        else:
            return 0.25
    except ValueError:
        return 0.7


def compute_match(job: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes comprehensive match score and structured explanations for a Job-Candidate pair.
    """
    job_skills = set(job.get("extracted_keywords") or [])
    cand_skills = set(candidate.get("extracted_keywords") or [])

    # 1. Technical Skills Overlap Score
    matched_skills = sorted(list(job_skills.intersection(cand_skills)))
    if job_skills:
        skills_score = len(matched_skills) / len(job_skills)
    else:
        # If no specific skills required, base on presence of candidate skills
        skills_score = 0.5 if cand_skills else 0.3
    skills_score = min(1.0, skills_score)

    # 2. Local Semantic Embedding Cosine Similarity
    job_emb = job.get("embedding")
    cand_emb = candidate.get("embedding")
    semantic_score = LocalSemanticModel.cosine_similarity(job_emb, cand_emb)

    # 3. Role & Position Compatibility
    job_title = job.get("title") or ""
    cand_pos = candidate.get("position") or ""
    role_score = calculate_role_compatibility(job_title, cand_pos)

    # 4. Seniority & Level Compatibility
    job_lvl = job.get("extracted_level")
    cand_lvl = candidate.get("extracted_level")
    level_score = calculate_level_compatibility(job_lvl, cand_lvl)

    # Weighted Overall Score
    # 35% Skills + 35% Semantic Similarity + 20% Role + 10% Seniority Level
    raw_percent = (
        (skills_score * 0.35) +
        (semantic_score * 0.35) +
        (role_score * 0.20) +
        (level_score * 0.10)
    ) * 100.0

    # Ensure reasonable boundaries
    matching_percentage = round(max(5.0, min(99.5, raw_percent)), 1)

    # Formulate human-readable explanations
    matched_skills_str = ", ".join(s.title() for s in matched_skills[:5]) if matched_skills else "General technical skills"

    cand_exp = candidate.get("extracted_experiences") or {}
    cand_years = cand_exp.get("years_experience")
    exp_suffix = f" ({cand_years}+ years exp)" if cand_years else ""

    matched_experience = (
        f"{cand_lvl or 'Middle'} {cand_pos or 'Candidate'}{exp_suffix}. "
        f"Matched skills: {matched_skills_str}."
    )

    if job.get("request"):
        matched_requests = f"Fulfills {len(matched_skills)}/{max(1, len(job_skills))} requirements: {matched_skills_str}"
    else:
        matched_requests = f"Matches {job_title} with skills in {matched_skills_str}"

    return {
        "matching_percentage": matching_percentage,
        "semantic_score": round(semantic_score, 3),
        "skills_score": round(skills_score, 3),
        "matched_skills": matched_skills,
        "matched_experience": matched_experience,
        "matched_requests": matched_requests
    }


def recalculate_for_job(job_id: str, db_path: Optional[str] = None) -> int:
    """
    Computes and saves match results for a newly added/updated job with ALL saved candidates.
    Called on job ingestion to ensure instant retrieval later.
    """
    job = get_job_by_id(job_id, db_path)
    if not job:
        logger.warning(f"recalculate_for_job: Job {job_id} not found.")
        return 0

    candidates = get_all_candidates(db_path)
    logger.info(f"Running pre-calculated matching for Job '{job.get('title')}' with {len(candidates)} candidates...")

    count = 0
    for cand_summary in candidates:
        # Fetch full candidate record with embedding
        cand = get_candidate_by_id(cand_summary["id"], db_path)
        if not cand:
            continue

        res = compute_match(job, cand)
        save_match_result(
            job_id=job["id"],
            candidate_id=cand["id"],
            matching_percentage=res["matching_percentage"],
            semantic_score=res["semantic_score"],
            skills_score=res["skills_score"],
            matched_skills=res["matched_skills"],
            matched_experience=res["matched_experience"],
            matched_requests=res["matched_requests"],
            db_path=db_path
        )
        count += 1

    return count


def recalculate_for_candidate(candidate_id: str, db_path: Optional[str] = None) -> int:
    """
    Computes and saves match results for a newly added/updated candidate with ALL saved jobs.
    Called on candidate ingestion to ensure instant retrieval later.
    """
    candidate = get_candidate_by_id(candidate_id, db_path)
    if not candidate:
        logger.warning(f"recalculate_for_candidate: Candidate {candidate_id} not found.")
        return 0

    jobs = get_all_jobs(db_path)
    logger.info(f"Running pre-calculated matching for Candidate '{candidate.get('name')}' with {len(jobs)} jobs...")

    count = 0
    for job_summary in jobs:
        # Fetch full job record with embedding
        job = get_job_by_id(job_summary["id"], db_path)
        if not job:
            continue

        res = compute_match(job, candidate)
        save_match_result(
            job_id=job["id"],
            candidate_id=candidate["id"],
            matching_percentage=res["matching_percentage"],
            semantic_score=res["semantic_score"],
            skills_score=res["skills_score"],
            matched_skills=res["matched_skills"],
            matched_experience=res["matched_experience"],
            matched_requests=res["matched_requests"],
            db_path=db_path
        )
        count += 1

    return count
