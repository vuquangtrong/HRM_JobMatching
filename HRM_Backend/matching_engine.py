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
import json
import logging
from typing import Dict, Any, List, Optional
from database import (
    get_job_by_id,
    get_candidate_by_id,
    get_all_jobs,
    get_all_candidates,
    get_all_jobs_with_embeddings,
    get_all_candidates_with_embeddings,
    save_match_result,
    save_match_results_batch
)
from extracting_engine import LocalSemanticModel, get_semantic_model, get_cached_skill_embedding

logger = logging.getLogger("hrm.matching_engine")

ROLE_STOPWORDS = {
    "junior", "senior", "middle", "mid", "lead", "principal", "intern", "fresher",
    "associate", "staff", "trainee", "onsite", "remote", "hybrid", "hcm", "hanoi",
    "danang", "tokyo", "singapore", "vietnam", "fulltime", "parttime", "fpt", "bosch",
    "lts", "tma", "viettel", "and", "or", "in", "for", "with", "of", "the", "a", "an"
}


def clean_role_tokens(text: str) -> List[str]:
    """Extracts non-seniority, functional role words from a title or position."""
    words = re.findall(r"[a-zA-Z0-9\+#]+", (text or "").lower())
    return [w for w in words if w not in ROLE_STOPWORDS and len(w) > 1]


def calculate_role_compatibility(job_title: str, candidate_position: str) -> float:
    """
    Calculates alignment between candidate's position and the job title.
    Uses AI semantic embedding cosine similarity of functional roles combined
    with domain heuristics, explicitly ignoring seniority prefixes.
    """
    if not job_title or not candidate_position:
        return 0.5

    t_clean = " ".join(clean_role_tokens(job_title))
    p_clean = " ".join(clean_role_tokens(candidate_position))

    if not t_clean or not p_clean:
        return 0.5

    # 1. AI Semantic Embedding Similarity of functional roles
    t_emb = get_cached_skill_embedding(t_clean)
    p_emb = get_cached_skill_embedding(p_clean)
    sem_role_sim = LocalSemanticModel.cosine_similarity(t_emb, p_emb)

    # 2. Functional Domain Heuristics
    t_lower = t_clean.lower()
    p_lower = p_clean.lower()

    domain_score = 0.35
    is_test_role = any(x in p_lower for x in ["test", "qa", "qc", "quality"])
    is_test_job = any(x in t_lower for x in ["test", "qa", "qc", "quality"])
    is_dev_role = any(x in p_lower for x in ["dev", "engineer", "programmer", "architect", "software"])
    is_dev_job = any(x in t_lower for x in ["dev", "engineer", "programmer", "architect", "software"])

    if is_test_role and is_test_job:
        domain_score = 0.95
    elif is_dev_role and is_dev_job:
        domain_score = 0.90
    elif (is_test_role and is_dev_job) or (is_dev_role and is_test_job):
        # Cross-discipline testing/development
        domain_score = 0.65
    elif t_clean == p_clean:
        domain_score = 1.0
    else:
        # Check token intersection of functional words
        t_set = set(t_clean.split())
        p_set = set(p_clean.split())
        overlap = t_set.intersection(p_set)
        if overlap:
            domain_score = 0.70 + (0.30 * (len(overlap) / max(len(t_set), len(p_set))))

    # Blended score: 50% semantic embedding + 50% domain heuristic
    final_role_score = (sem_role_sim * 0.50) + (domain_score * 0.50)
    return round(min(1.0, max(0.1, final_role_score)), 3)


def parse_levels(level_val: Any) -> List[str]:
    """Parses single strings, lists, or JSON strings into lowercase level tokens."""
    if not level_val:
        return ["middle"]
    if isinstance(level_val, list):
        return [str(x).strip().lower() for x in level_val if str(x).strip()]
    s = str(level_val).strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip().lower() for x in parsed if str(x).strip()]
        except Exception:
            pass
    parts = re.split(r"[,/|]+", s)
    return [p.strip().lower() for p in parts if p.strip()]


def calculate_level_compatibility(job_level: Any, candidate_level: Any) -> float:
    """Calculates compatibility between requested level(s) and candidate level."""
    levels = ["intern", "junior", "middle", "senior", "lead"]
    j_levels = parse_levels(job_level)
    c_levels = parse_levels(candidate_level)
    cl = c_levels[0] if c_levels else "middle"

    # If candidate level matches any of the job's accepted levels
    if cl in j_levels:
        return 1.0

    # Distance to closest accepted level
    best_score = 0.25
    try:
        ci = levels.index(cl)
        for jl in j_levels:
            if jl in levels:
                ji = levels.index(jl)
                diff = abs(ji - ci)
                if diff == 1:
                    score = 0.75
                elif diff == 2:
                    score = 0.45
                else:
                    score = 0.25
                if score > best_score:
                    best_score = score
    except ValueError:
        return 0.70

    return best_score


def compute_match(job: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes comprehensive AI match score and structured explanations for a Job-Candidate pair.
    Utilizes:
    1. AI Soft Semantic Skill cross-matching (matches semantically related skills)
    2. Deep dense vector cosine similarity (holistic profile alignment)
    3. AI Semantic Role compatibility
    4. Adaptive multi-level seniority compatibility
    """
    job_skills = list(job.get("extracted_keywords") or [])
    cand_skills = list(candidate.get("extracted_keywords") or [])

    model = get_semantic_model()
    matched_skills = []
    skill_match_scores = []

    # 1. AI Soft Semantic Skills Matching
    if job_skills:
        cand_skills_lower = [s.lower() for s in cand_skills]
        cand_skill_embs = [get_cached_skill_embedding(cs) for cs in cand_skills]

        for js in job_skills:
            js_lower = js.lower()
            if js_lower in cand_skills_lower:
                matched_skills.append(js)
                skill_match_scores.append(1.0)
            else:
                # Semantic similarity check with candidate skills using cached embeddings
                js_emb = get_cached_skill_embedding(js)
                best_sim = 0.0
                best_cs = None
                for cs, cs_emb in zip(cand_skills, cand_skill_embs):
                    sim = LocalSemanticModel.cosine_similarity(js_emb, cs_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best_cs = cs
                # If semantically related (>= 0.74), award proportional credit
                if best_sim >= 0.74 and best_cs:
                    matched_skills.append(js)
                    skill_match_scores.append(best_sim)
                else:
                    skill_match_scores.append(0.0)

        skills_score = sum(skill_match_scores) / len(job_skills)
    else:
        # If no specific skills required, base on presence of candidate skills
        skills_score = 0.6 if cand_skills else 0.3
    skills_score = min(1.0, skills_score)

    # 2. Holistic Profile Semantic Vector Similarity
    job_emb = job.get("embedding")
    cand_emb = candidate.get("embedding")
    if not job_emb:
        job_text = f"{job.get('title') or ''} {job.get('request') or ''} {job.get('job_description') or ''} {' '.join(job_skills)}"
        if job_text.strip():
            job_emb = model.get_embedding(job_text[:2500])
    if not cand_emb:
        cand_text = f"{candidate.get('position') or ''} {candidate.get('name') or ''} {' '.join(cand_skills)} {candidate.get('cv_text') or ''}"
        if cand_text.strip():
            cand_emb = model.get_embedding(cand_text[:2500])
    semantic_score = LocalSemanticModel.cosine_similarity(job_emb, cand_emb)

    # 3. AI Semantic Role & Position Compatibility
    job_title = job.get("title") or ""
    cand_pos = candidate.get("position") or ""
    role_score = calculate_role_compatibility(job_title, cand_pos)

    # 4. Seniority & Level Compatibility
    job_lvl = job.get("extracted_level")
    cand_lvl = candidate.get("extracted_level")
    level_score = calculate_level_compatibility(job_lvl, cand_lvl)

    # Weighted Overall Score
    # 35% Skills + 35% Semantic Profile + 20% Role + 10% Seniority Level
    raw_percent = (
        (skills_score * 0.35) +
        (semantic_score * 0.35) +
        (role_score * 0.20) +
        (level_score * 0.10)
    ) * 100.0

    # Ensure reasonable boundaries
    matching_percentage = round(max(5.0, min(99.5, raw_percent)), 1)

    # Deduplicate matched skills preserving order
    unique_matched_skills = []
    for s in matched_skills:
        if s not in unique_matched_skills:
            unique_matched_skills.append(s)

    matched_skills_str = ", ".join(s.title() for s in unique_matched_skills[:5]) if unique_matched_skills else "General technical skills"

    cand_exp = candidate.get("extracted_experiences") or {}
    cand_years = cand_exp.get("years_experience")
    exp_suffix = f" ({cand_years}+ years exp)" if cand_years else ""

    matched_experience = (
        f"{cand_lvl or 'Middle'} {cand_pos or 'Candidate'}{exp_suffix}. "
        f"Matched skills: {matched_skills_str}."
    )

    if job.get("request"):
        matched_requests = f"Fulfills {len(unique_matched_skills)}/{max(1, len(job_skills))} requirements: {matched_skills_str}"
    else:
        matched_requests = f"Matches {job_title} with skills in {matched_skills_str}"

    return {
        "matching_percentage": matching_percentage,
        "semantic_score": round(semantic_score, 3),
        "skills_score": round(skills_score, 3),
        "matched_skills": unique_matched_skills,
        "matched_experience": matched_experience,
        "matched_requests": matched_requests
    }


def recalculate_for_job(job_id: str, db_path: Optional[str] = None) -> int:
    """
    Computes and saves match results for a newly added/updated job with ALL saved candidates.
    Optimized: fetches candidates in a single query and commits all matches in one batch transaction.
    """
    job = get_job_by_id(job_id, db_path)
    if not job:
        logger.warning(f"recalculate_for_job: Job {job_id} not found.")
        return 0

    candidates = get_all_candidates_with_embeddings(db_path)
    logger.info(f"Running pre-calculated matching for Job '{job.get('title')}' with {len(candidates)} candidates...")

    matches_batch = []
    for cand in candidates:
        res = compute_match(job, cand)
        matches_batch.append({
            "job_id": job["id"],
            "candidate_id": cand["id"],
            "matching_percentage": res["matching_percentage"],
            "semantic_score": res["semantic_score"],
            "skills_score": res["skills_score"],
            "matched_skills": res["matched_skills"],
            "matched_experience": res["matched_experience"],
            "matched_requests": res["matched_requests"]
        })

    saved_count = save_match_results_batch(matches_batch, db_path)
    return saved_count


def recalculate_for_candidate(candidate_id: str, db_path: Optional[str] = None) -> int:
    """
    Computes and saves match results for a newly added/updated candidate with ALL saved jobs.
    Optimized: fetches jobs in a single query and commits all matches in one batch transaction.
    """
    candidate = get_candidate_by_id(candidate_id, db_path)
    if not candidate:
        logger.warning(f"recalculate_for_candidate: Candidate {candidate_id} not found.")
        return 0

    jobs = get_all_jobs_with_embeddings(db_path)
    logger.info(f"Running pre-calculated matching for Candidate '{candidate.get('name')}' with {len(jobs)} jobs...")

    matches_batch = []
    for job in jobs:
        res = compute_match(job, candidate)
        matches_batch.append({
            "job_id": job["id"],
            "candidate_id": candidate["id"],
            "matching_percentage": res["matching_percentage"],
            "semantic_score": res["semantic_score"],
            "skills_score": res["skills_score"],
            "matched_skills": res["matched_skills"],
            "matched_experience": res["matched_experience"],
            "matched_requests": res["matched_requests"]
        })

    saved_count = save_match_results_batch(matches_batch, db_path)
    return saved_count
