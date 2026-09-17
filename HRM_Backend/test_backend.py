"""
Unit and Integration tests for HRM_Backend.
Tests:
- Database schema and indexing
- NLP keyword and experience extraction
- Local semantic model embedding and cosine similarity
- Bi-directional pre-calculated matching engine
- Ingestion of sample data from HRM portal
"""

import os
import sys
import unittest
import tempfile
import json

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

from database import (
    init_db,
    save_or_update_job,
    get_job_by_id,
    get_all_jobs,
    save_or_update_candidate,
    get_candidate_by_id,
    get_all_candidates,
    save_match_result,
    get_matching_candidates_for_job,
    get_matching_jobs_for_candidate,
    get_db_stats
)
from nlp_extractor import (
    clean_html,
    extract_skills,
    extract_seniority,
    extract_years_of_experience,
    extract_job_keywords,
    extract_candidate_keywords,
    LocalSemanticModel,
    get_semantic_model
)
from matching_engine import (
    compute_match,
    recalculate_for_job,
    recalculate_for_candidate
)


class TestHRMBackend(unittest.TestCase):

    def setUp(self):
        # Create temporary database for testing
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_hrm.db")
        os.environ["HRM_DB_PATH"] = self.db_path
        init_db(self.db_path)

    def tearDown(self):
        os.environ.pop("HRM_DB_PATH", None)
        self.temp_dir.cleanup()

    def test_database_crud(self):
        # Test Job insertion
        job_data = {
            "id": "job-1",
            "title": "Senior Python Backend Developer",
            "code": "DEV-01",
            "request": "Python script, FastAPI, PostgreSQL, Docker",
            "job_description": "<p>Build scalable microservices with Python and Docker.</p>",
            "extracted_keywords": ["python", "fastapi", "docker", "sql"],
            "extracted_level": "Senior"
        }
        saved_job, is_new_job = save_or_update_job(job_data, self.db_path)
        self.assertTrue(is_new_job)
        self.assertIsNotNone(saved_job)
        self.assertEqual(saved_job["id"], "job-1")
        self.assertIn("python", saved_job["extracted_keywords"])

        # Test Candidate insertion with PM_ROUND status
        cand_data = {
            "id": "cand-1",
            "application_id": "app-1",
            "code": "C1001",
            "name": "Nguyen Van A",
            "position": "Python Developer",
            "location": "Ha Noi",
            "status": "PM_ROUND",
            "cv_urls": ["https://hrm.ltsgroup.tech/cv1.pdf"],
            "extracted_keywords": ["python", "docker", "fastapi"],
            "extracted_level": "Senior"
        }
        saved_cand, is_new_cand = save_or_update_candidate(cand_data, self.db_path)
        self.assertTrue(is_new_cand)
        self.assertIsNotNone(saved_cand)
        self.assertEqual(saved_cand["status"], "PM_ROUND")
        self.assertEqual(saved_cand["position"], "Python Developer")

        # Verify DB Stats
        stats = get_db_stats(self.db_path)
        self.assertEqual(stats["jobs_count"], 1)
        self.assertEqual(stats["candidates_count"], 1)

    def test_nlp_extraction(self):
        # HTML cleaning
        html = "<p>Tasks</p><ul><li>Design <strong>C++</strong> test cases</li></ul>"
        cleaned = clean_html(html)
        self.assertNotIn("<p>", cleaned)
        self.assertIn("Design C++ test cases", cleaned)

        # Skill extraction
        skills = extract_skills("We need someone with Python, C++, Docker and System Test experience.")
        self.assertIn("python", skills)
        self.assertIn("c++", skills)
        self.assertIn("docker", skills)
        self.assertIn("system test", skills)

        # Seniority extraction
        self.assertEqual(extract_seniority("Senior Software Engineer"), "Senior")
        self.assertEqual(extract_seniority("Junior Tester"), "Junior")
        self.assertEqual(extract_seniority("Middle QA Specialist"), "Middle")

        # Years of experience extraction
        self.assertEqual(extract_years_of_experience("Require 3+ years in software testing"), 3)

    def test_local_semantic_model(self):
        model = get_semantic_model()
        vec1 = model.get_embedding("Python automation testing with pytest and selenium")
        vec2 = model.get_embedding("Automated test engineer writing Python scripts")
        vec3 = model.get_embedding("Cooking Vietnamese traditional soup with vegetables")

        self.assertEqual(len(vec1), model.dim)
        sim_relevant = LocalSemanticModel.cosine_similarity(vec1, vec2)
        sim_irrelevant = LocalSemanticModel.cosine_similarity(vec1, vec3)

        self.assertGreater(sim_relevant, 0.0)
        self.assertGreater(sim_relevant, sim_irrelevant)

    def test_precalculated_matching(self):
        # Insert Job
        job_data = {
            "id": "job-python-test",
            "title": "Python - Component & System Test Engineer",
            "request": "Python script, Testing Knowledge, Embedded background, English",
            "job_description": "Testing embedded systems with Python",
            "extracted_keywords": ["python", "testing knowledge", "embedded", "english", "system test"],
            "extracted_level": "Middle"
        }
        save_or_update_job(job_data, self.db_path)

        # Insert 2 Candidates (one strong match, one weak match)
        cand_strong = {
            "id": "cand-strong",
            "name": "Ho Le Minh Hai",
            "position": "Tester",
            "status": "PM_ROUND",
            "extracted_keywords": ["python", "testing knowledge", "embedded", "english", "system test"],
            "extracted_level": "Middle",
            "extracted_experiences": {"years_experience": 3, "summary": "Middle Embedded Tester"}
        }
        save_or_update_candidate(cand_strong, self.db_path)

        cand_weak = {
            "id": "cand-weak",
            "name": "Tran Van B",
            "position": "Frontend Developer",
            "status": "OPEN",
            "extracted_keywords": ["react", "javascript", "css"],
            "extracted_level": "Junior",
            "extracted_experiences": {"years_experience": 1, "summary": "Junior Frontend"}
        }
        save_or_update_candidate(cand_weak, self.db_path)

        # Trigger precalculated matching
        recalculate_for_job("job-python-test", self.db_path)

        # Query matches for Job
        matches = get_matching_candidates_for_job("job-python-test", db_path=self.db_path)
        self.assertEqual(len(matches), 2)
        # Strong match should be first
        self.assertEqual(matches[0]["id"], "cand-strong")
        self.assertGreater(matches[0]["matching_percentage"], matches[1]["matching_percentage"])
        self.assertIn("python", matches[0]["matched_skills"])
        self.assertIn("Python", matches[0]["matched_experience"])

        # Query matches for Candidate
        cand_matches = get_matching_jobs_for_candidate("cand-strong", db_path=self.db_path)
        self.assertEqual(len(cand_matches), 1)
        self.assertEqual(cand_matches[0]["id"], "job-python-test")
        self.assertGreater(cand_matches[0]["matching_percentage"], 60.0)

    def test_sample_dataset_ingestion(self):
        """Test with sample JSON files from HRM_Ext directory."""
        job_sample_path = os.path.join(
            os.path.dirname(__file__), "..", "HRM_Ext", "sample_response_fetch_job-requests.json"
        )
        cand_sample_path = os.path.join(
            os.path.dirname(__file__), "..", "HRM_Ext", "sample_response_fetch_candidate_candidates.json"
        )

        if not os.path.exists(job_sample_path) or not os.path.exists(cand_sample_path):
            self.skipTest("Sample files not found")

        with open(job_sample_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Find the JSON object after the fetch(...) code block
            json_start = content.find("{\n  \"id\":")
            if json_start != -1:
                job_raw = json.loads(content[json_start:])
                extracted_job = extract_job_keywords(
                    job_raw.get("title", ""),
                    job_raw.get("request", ""),
                    job_raw.get("jobDescription", ""),
                    raw_level=job_raw.get("levelCandidate") or job_raw.get("level")
                )
                save_or_update_job({
                    "id": job_raw["id"],
                    "title": job_raw.get("title"),
                    "code": job_raw.get("code"),
                    "request": job_raw.get("request"),
                    "job_description": job_raw.get("jobDescription"),
                    "extracted_keywords": extracted_job["skills"],
                    "extracted_level": extracted_job["level"],
                    "embedding": extracted_job["embedding"]
                }, self.db_path)

        with open(cand_sample_path, "r", encoding="utf-8") as f:
            content = f.read()
            json_start = content.find("[\n  {")
            if json_start != -1:
                cand_list = json.loads(content[json_start:])
                for item in cand_list:
                    cv = item.get("cv", {})
                    cand_urls = json.loads(cv.get("cvs", "[]")) if isinstance(cv.get("cvs"), str) else cv.get("cvs", [])
                    extracted_cand = extract_candidate_keywords(
                        name=cv.get("name", ""),
                        position=cv.get("position", ""),
                        location=cv.get("location", ""),
                        cv_information=cv.get("cvInformation"),
                        raw_status=item.get("status"),
                        raw_level=cv.get("level"),
                        raw_experience=cv.get("experience"),
                        cv_urls=cand_urls,
                        raw_languages=cv.get("languages")
                    )
                    save_or_update_candidate({
                        "id": cv.get("id") or item.get("id"),
                        "application_id": item.get("id"),
                        "code": cv.get("code"),
                        "name": cv.get("name"),
                        "position": cv.get("position"),
                        "location": cv.get("location"),
                        "status": item.get("status", "OPEN"),
                        "cv_urls": cand_urls,
                        "extracted_keywords": extracted_cand["skills"],
                        "extracted_level": extracted_cand["level"],
                        "embedding": extracted_cand["embedding"]
                    }, self.db_path)

                    recalculate_for_candidate(cv.get("id") or item.get("id"), self.db_path)

        # Check that match occurred
        jobs = get_all_jobs(self.db_path)
        self.assertGreater(len(jobs), 0)
        cands = get_all_candidates(self.db_path)
        self.assertGreater(len(cands), 0)

        job_id = jobs[0]["id"]
        matched_cands = get_matching_candidates_for_job(job_id, db_path=self.db_path)
        self.assertGreater(len(matched_cands), 0)
        first_cand = matched_cands[0]
        self.assertEqual(first_cand["status"], "PM_ROUND")
        self.assertGreater(first_cand["matching_percentage"], 30.0)

    def test_fastapi_endpoints(self):
        """Test REST API routes using FastAPI TestClient."""
        from fastapi.testclient import TestClient
        from app import app

        with TestClient(app) as client:
            # 1. Health check
            res = client.get("/api/health")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "healthy")
            self.assertEqual(data["port"], 8765)

            # 2. Ingest Job
            job_payload = {
                "id": "job-api-1",
                "title": "BOSCH - Onsite HCM - Software Developer - Ngon Ngu C++",
                "request": "C/C++ Senior Developers from 3+ experience, Embedded background",
                "jobDescription": "<p>C++ proficiency, Linux, Automotive</p>"
            }
            res = client.post("/api/jobs", json=job_payload)
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json()["success"])

            # 3. Ingest Candidates Batch
            cand_payload = {
                "jobRequestId": "job-api-1",
                "candidates": [
                    {
                        "id": "cand-api-1",
                        "code": "C10455",
                        "name": "Nguyen Van Cuong",
                        "position": "Tester",
                        "location": "Ha Noi",
                        "status": "PM_ROUND",
                        "cvs": ["https://hrm.ltsgroup.tech/cv1.pdf"]
                    },
                    {
                        "id": "cand-api-2",
                        "code": "C10456",
                        "name": "Le Thi B",
                        "position": "C++ Developer",
                        "location": "HCM",
                        "status": "INTERVIEW",
                        "cvs": []
                    }
                ]
            }
            res = client.post("/api/candidates/batch", json=cand_payload)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["processed_count"], 2)

            # 4. Get matching candidates for job
            res = client.get("/api/jobs/job-api-1/candidates")
            self.assertEqual(res.status_code, 200)
            cands_match = res.json()["candidates"]
            self.assertEqual(len(cands_match), 2)
            self.assertIn("status", cands_match[0])
            self.assertIn("matching_percentage", cands_match[0])
            self.assertIn("matched_experience", cands_match[0])

            # 5. Search candidates
            res = client.get("/api/candidates?q=C%2B%2B")
            self.assertEqual(res.status_code, 200)
            found_cands = res.json()
            self.assertGreater(len(found_cands), 0)

            # 6. Get matching jobs for candidate
            res = client.get("/api/candidates/cand-api-2/jobs")
            self.assertEqual(res.status_code, 200)
            jobs_match = res.json()["jobs"]
            self.assertGreater(len(jobs_match), 0)
            self.assertIn("job_title", jobs_match[0])
            self.assertIn("matched_requests", jobs_match[0])
            self.assertIn("matching_percentage", jobs_match[0])

    def test_recalculate_only_called_on_new_records(self):
        """
        Critical performance test:
        Verify that recalculate_for_job and recalculate_for_candidate are ONLY invoked
        when a NEW job or NEW candidate is added.
        When existing jobs or candidates are sent (e.g. status changes),
        their fields are updated but matching recalculations are skipped.
        """
        from fastapi.testclient import TestClient
        from app import app

        with TestClient(app) as client:
            job_id = "perf-job-1"
            cand_id = "perf-cand-1"

            # 1. Ingest brand new job -> is_new must be True
            res1 = client.post("/api/jobs", json={
                "id": job_id,
                "title": "Embedded Python Engineer",
                "request": "Python, Embedded, System Test"
            })
            self.assertEqual(res1.status_code, 200)
            self.assertTrue(res1.json()["is_new"])

            # 2. Ingest brand new candidate -> new_count = 1, updated_count = 0
            res2 = client.post("/api/candidates/batch", json={
                "jobRequestId": job_id,
                "candidates": [
                    {
                        "id": cand_id,
                        "name": "Candidate Test",
                        "position": "Embedded Tester",
                        "status": "PM_ROUND"
                    }
                ]
            })
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(res2.json()["new_count"], 1)
            self.assertEqual(res2.json()["updated_count"], 0)

            # Check matching candidate listing
            res_matches1 = client.get(f"/api/jobs/{job_id}/candidates")
            self.assertEqual(len(res_matches1.json()["candidates"]), 1)
            self.assertEqual(res_matches1.json()["candidates"][0]["status"], "PM_ROUND")

            # 3. Send EXISTING job again (e.g. page refresh) -> is_new must be False, precalculated_matches = 0
            res3 = client.post("/api/jobs", json={
                "id": job_id,
                "title": "Embedded Python Engineer (Updated)",
                "request": "Python, Embedded, System Test"
            })
            self.assertEqual(res3.status_code, 200)
            self.assertFalse(res3.json()["is_new"])
            self.assertEqual(res3.json()["precalculated_matches"], 0)

            # 4. Send EXISTING candidate again with status changed to 'OFFER'
            # -> new_count = 0, updated_count = 1
            res4 = client.post("/api/candidates/batch", json={
                "jobRequestId": job_id,
                "candidates": [
                    {
                        "id": cand_id,
                        "name": "Candidate Test",
                        "position": "Embedded Tester",
                        "status": "OFFER"
                    }
                ]
            })
            self.assertEqual(res4.status_code, 200)
            self.assertEqual(res4.json()["new_count"], 0)
            self.assertEqual(res4.json()["updated_count"], 1)

            # Verify the status in job matching immediately reflects 'OFFER' without recalculation
            res_matches2 = client.get(f"/api/jobs/{job_id}/candidates")
            self.assertEqual(len(res_matches2.json()["candidates"]), 1)
            self.assertEqual(res_matches2.json()["candidates"][0]["status"], "OFFER")

    def test_job_semantic_search(self):
        """Test GET /api/jobs?q=... semantic filtering of jobs."""
        from fastapi.testclient import TestClient
        from app import app

        with TestClient(app) as client:
            client.post("/api/jobs", json={
                "id": "search-job-1",
                "title": "C++ Automotive Embedded Engineer",
                "request": "C++, AUTOSAR, CAN bus, Embedded"
            })
            client.post("/api/jobs", json={
                "id": "search-job-2",
                "title": "React Frontend Developer",
                "request": "React, TypeScript, CSS, HTML"
            })

            # Search C++ (filters out unrelated React Developer)
            res = client.get("/api/jobs?q=C%2B%2B")
            self.assertEqual(res.status_code, 200)
            jobs = res.json()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["id"], "search-job-1")
            self.assertGreater(jobs[0]["query_relevance"], 40.0)

            # Dump word query returns 0 jobs
            res_dump = client.get("/api/jobs?q=dumpwordasdfghjkl")
            self.assertEqual(res_dump.status_code, 200)
            self.assertEqual(len(res_dump.json()), 0)

            # Query with min_score=0 returns all jobs ranked
            res_all = client.get("/api/jobs?q=C%2B%2B&min_score=0")
            self.assertEqual(res_all.status_code, 200)
            all_ranked = res_all.json()
            self.assertEqual(len(all_ranked), 2)
            self.assertEqual(all_ranked[0]["id"], "search-job-1")
            self.assertGreater(all_ranked[0]["query_relevance"], all_ranked[1]["query_relevance"])

    def test_candidate_semantic_search(self):
        """Test GET /api/candidates?q=... semantic filtering and relevance score."""
        from fastapi.testclient import TestClient
        from app import app

        with TestClient(app) as client:
            client.post("/api/candidates", json={
                "id": "search-cand-1",
                "name": "Ho Le Minh Hai",
                "position": "Tester",
                "status": "PM_ROUND",
                "extracted_keywords": ["python", "testing knowledge", "system test"]
            })
            client.post("/api/candidates", json={
                "id": "search-cand-2",
                "name": "Tran Van B",
                "position": "Frontend Developer",
                "status": "OPEN",
                "extracted_keywords": ["react", "javascript", "css"]
            })

            # Search Tester -> returns only cand-1
            res = client.get("/api/candidates?q=Tester")
            self.assertEqual(res.status_code, 200)
            cands = res.json()
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0]["id"], "search-cand-1")
            self.assertIn("query_relevance", cands[0])
            self.assertGreater(cands[0]["query_relevance"], 40.0)

            # Search with dump word -> returns 0 candidates
            res_dump = client.get("/api/candidates?q=asdfghjklgibberish")
            self.assertEqual(res_dump.status_code, 200)
            self.assertEqual(len(res_dump.json()), 0)

    def test_model_selection(self):
        """Test GET /api/models and POST /api/models/select for Base and Large models."""
        from fastapi.testclient import TestClient
        from app import app

        with TestClient(app) as client:
            res = client.get("/api/models")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("models", data)
            self.assertGreaterEqual(len(data["models"]), 3)
            self.assertEqual(data["active_model"], "BAAI/bge-large-en-v1.5")

            # Verify only Base and Large models are present (dim 768 or 1024), no small models
            for m in data["models"]:
                self.assertNotIn("small", m["id"].lower())
                self.assertNotIn("minilm", m["id"].lower())
                self.assertIn(m["dim"], (768, 1024))

            # Switch model to base model
            new_model = "BAAI/bge-base-en-v1.5"
            res_switch = client.post("/api/models/select", json={"model_name": new_model})
            self.assertEqual(res_switch.status_code, 200)
            self.assertEqual(res_switch.json()["active_model"], new_model)
            self.assertFalse(res_switch.json()["database_cleared"])

            # Switch model with clear_database=True
            res_switch_clear = client.post("/api/models/select", json={"model_name": "thenlper/gte-large", "clear_database": True})
            self.assertEqual(res_switch_clear.status_code, 200)
            self.assertTrue(res_switch_clear.json()["database_cleared"])


    def test_clear_database(self):
        """Test POST /api/database/clear removes all records."""
        from fastapi.testclient import TestClient
        from app import app

        with TestClient(app) as client:
            # Seed a job
            client.post("/api/jobs", json={
                "id": "clear-job-1",
                "title": "Tester"
            })
            self.assertGreater(len(client.get("/api/jobs").json()), 0)

            # Clear DB
            res_clear = client.post("/api/database/clear")
            self.assertEqual(res_clear.status_code, 200)
            self.assertTrue(res_clear.json()["success"])

            # Verify empty
            self.assertEqual(len(client.get("/api/jobs").json()), 0)
            self.assertEqual(len(client.get("/api/candidates").json()), 0)


if __name__ == "__main__":
    unittest.main()
