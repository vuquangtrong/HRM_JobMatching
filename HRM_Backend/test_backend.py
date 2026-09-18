"""
Unit and Integration tests for HRM_Backend.
Tests:
- Database schema and indexing
- LLM-driven keyword/experience extraction (disabled-LLM behavior)
- Local semantic model embedding and cosine similarity
- Bi-directional pre-calculated matching engine
- Ingestion of sample data from HRM portal
"""

import os
import sys
import unittest
import tempfile
import json
from unittest import mock

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
from extracting_engine import (
    clean_html,
    extract_job_keywords,
    extract_candidate_keywords,
    LocalSemanticModel,
    get_semantic_model,
    LLMExtractor,
    TaxonomyManager,
    get_taxonomy_manager
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
        # There is no regex fallback: make the LLM deterministically "down" so
        # tests exercise the empty-extraction path without network access.
        self._llm_patcher = mock.patch(
            "extracting_engine.get_llm_extractor",
            return_value=LLMExtractor(enabled=False, cache_size=16),
        )
        self._llm_patcher.start()

    def tearDown(self):
        self._llm_patcher.stop()
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

        # Hybrid extraction: with LLM disabled, deterministic regex baseline guarantees
        # canonical skills, seniority level, and years of experience without network access.
        job = extract_job_keywords("Senior C++ Developer", "C++ and Python, 3+ years experience", "")
        self.assertIn("c++", job["skills"])
        self.assertIn("python", job["skills"])
        self.assertEqual(job["level"], "Senior")
        self.assertEqual(job["years_experience"], 3)

        cand = extract_candidate_keywords(
            name="Candidate A", position="Tester", location="Ha Noi",
            cv_text="C++ embedded developer with 3 years experience",
        )
        self.assertIn("c++", cand["skills"])
        self.assertIn("embedded", cand["skills"])
        self.assertEqual(cand["years_experience"], 3)

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
        """Test with sample JSON files from HRM_Extension directory."""
        job_sample_path = os.path.join(
            os.path.dirname(__file__), "..", "HRM_Extension", "sample_response_fetch_job-requests.json"
        )
        cand_sample_path = os.path.join(
            os.path.dirname(__file__), "..", "HRM_Extension", "sample_response_fetch_candidate_candidates.json"
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
        # Hybrid baseline extracted skills and role compatibility give strong match score
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

    def test_concurrent_duplicate_ingest_processed_once(self):
        """
        Reproduction of the console.log incident: HRM_Extension submits the same job and the same
        candidate batch concurrently from two browser contexts. Regression: concurrent
        duplicate submissions must be processed exactly ONCE - the losing submission observes
        the winner's committed record and only updates metadata. CV download, LLM extraction,
        and match recalculation must not run twice for the same record.
        """
        import threading
        from fastapi.testclient import TestClient
        from app import app

        job_id = "dup-job-1"
        cand_id = "dup-cand-1"
        results = []

        import time
        download_calls = []
        recalc_calls = []

        def fake_download_cv(url, token=None):
            download_calls.append(url)
            time.sleep(0.05)
            return "PDF extracted text covering Python, C, C++, Embedded, LIN, CAN."

        def fake_recalculate_for_job(jid, db_path=None):
            recalc_calls.append(("job", jid))
            return 0

        def fake_recalculate_for_candidate(cid, db_path=None):
            recalc_calls.append(("candidate", cid))
            return 0

        job_payload = {
            "id": job_id,
            "title": "BOSCH - Onsite HCM - AutoSAR Embedded SW Engineer - Ngon Ngu C",
            "request": "AutoSAR, C/C++, Embedded, LIN, CAN",
            "jobDescription": "<p>C, C++, AutoSAR, Bootloader, embedded</p>"
        }
        batch_payload = {
            "jobRequestId": job_id,
            "token": "fake-token",
            "candidates": [
                {
                    "id": cand_id,
                    "name": "Pham Quoc Tho",
                    "position": "Embedded Engineer",
                    "status": "OPEN",
                    "cvs": ["https://hrm.ltsgroup.tech/dup-cv.pdf"]
                }
            ]
        }

        def submit_job(client):
            res = client.post("/api/jobs", json=job_payload)
            results.append(("job", res.json()))

        def submit_batch(client):
            res = client.post("/api/candidates/batch", json=batch_payload)
            results.append(("batch", res.json()))

        start = threading.Barrier(4, timeout=15)

        def run(target, client):
            start.wait(timeout=15)
            target(client)

        with mock.patch("app.download_and_extract_cv", side_effect=fake_download_cv), \
             mock.patch("app.recalculate_for_job", side_effect=fake_recalculate_for_job), \
             mock.patch("app.recalculate_for_candidate", side_effect=fake_recalculate_for_candidate):
            with TestClient(app) as client:
                threads = [
                    threading.Thread(target=run, args=(submit_job, client)),
                    threading.Thread(target=run, args=(submit_job, client)),
                    threading.Thread(target=run, args=(submit_batch, client)),
                    threading.Thread(target=run, args=(submit_batch, client)),
                ]
                for threaded in threads:
                    threaded.start()
                for threaded in threads:
                    threaded.join(timeout=120)
                    self.assertFalse(threaded.is_alive(), "ingest worker thread did not finish")

        job_flags = [res["is_new"] for name, res in results if name == "job"]
        self.assertEqual(sorted(job_flags), [False, True])

        batch_results = [res for name, res in results if name == "batch"]
        self.assertEqual(len(batch_results), 2)
        for br in batch_results:
            self.assertEqual(br["processed_count"], 1)
        self.assertEqual(sum(br["new_count"] for br in batch_results), 1)
        self.assertEqual(sum(br["updated_count"] for br in batch_results), 1)

        self.assertEqual(len(download_calls), 1, "CV must be downloaded exactly once for concurrent duplicates")
        self.assertEqual(
            sorted(kind for kind, _ in recalc_calls),
            ["candidate", "job"],
            "each new record type must be recalculated exactly once"
        )

        stats = get_db_stats(self.db_path)
        self.assertEqual(stats["jobs_count"], 1)
        self.assertEqual(stats["candidates_count"], 1)

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

    def test_model_info(self):
        """Test GET /api/models returns read-only backend LLM/FastEmbed model information."""
        from fastapi.testclient import TestClient
        from app import app

        with TestClient(app) as client:
            res = client.get("/api/models")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("llm", data)
            self.assertIn("fastembed", data)

            self.assertIn("model", data["llm"])
            self.assertIn("enabled", data["llm"])

            self.assertIn("models", data["fastembed"])
            self.assertEqual(len(data["fastembed"]["models"]), 1)
            self.assertEqual(data["fastembed"]["models"][0]["id"], "BAAI/bge-large-en-v1.5")
            self.assertEqual(data["fastembed"]["active_model"], "BAAI/bge-large-en-v1.5")
            self.assertIn("is_loaded", data["fastembed"])


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

    def test_taxonomy_manager_crud_and_dynamic_update(self):
        """Test TaxonomyManager loads taxonomy.json, extracts skills, and dynamically updates novel terms."""
        temp_tax_file = os.path.join(self.temp_dir.name, "taxonomy.json")
        mgr = TaxonomyManager(filepath=temp_tax_file)

        # 1. Baseline extraction
        skills = mgr.extract_skills("Experienced in Python, C++, Docker, and System Test")
        self.assertIn("python", skills)
        self.assertIn("c++", skills)
        self.assertIn("docker", skills)
        self.assertIn("system test", skills)

        # 2. Seniority & years
        self.assertEqual(mgr.extract_seniority("Senior Software Engineer"), "Senior")
        self.assertEqual(mgr.extract_seniority("Lead Embedded Architect"), "Lead")
        self.assertEqual(mgr.extract_years_of_experience("5+ years of experience"), 5)

        # 3. Dynamic registration of novel skills by LLM
        novel_skills = ["solidity", "terraform", "langchain"]
        added = mgr.register_new_skills(novel_skills)
        self.assertEqual(len(added), 3)

        # 4. Extracted immediately via regex
        updated_skills = mgr.extract_skills("We need a Solidity smart contract and Terraform cloud engineer with Langchain")
        self.assertIn("solidity", updated_skills)
        self.assertIn("terraform", updated_skills)
        self.assertIn("langchain", updated_skills)

        # 5. Persisted to disk
        self.assertTrue(os.path.exists(temp_tax_file))
        with open(temp_tax_file, "r", encoding="utf-8") as f:
            disk_tax = json.load(f)
        self.assertIn("solidity", disk_tax["skills"])
        self.assertIn("terraform", disk_tax["skills"])

    def test_hybrid_extraction_with_llm_enrichment(self):
        """Test hybrid pipeline merges regex baseline and LLM open-vocabulary extraction."""
        fake_llm_result = {
            "skills": ["python", "graphql", "apache kafka"],
            "years_experience": 4,
            "level": "Senior",
            "languages": ["english", "japanese"],
            "summary": "Experienced Python backend engineer specializing in Kafka streaming."
        }

        mock_extractor = mock.MagicMock(spec=LLMExtractor)
        mock_extractor.is_enabled.return_value = True
        mock_extractor.extract.return_value = fake_llm_result

        with mock.patch("extracting_engine.get_llm_extractor", return_value=mock_extractor):
            job = extract_job_keywords(
                title="Senior Backend Engineer",
                request="Must know Docker and Python",
                job_description="<p>Hands-on Kafka and GraphQL experience required.</p>"
            )

            # Baseline regex caught 'docker' and 'python'; LLM caught 'graphql' and 'apache kafka'
            self.assertIn("docker", job["skills"])
            self.assertIn("python", job["skills"])
            self.assertIn("graphql", job["skills"])
            self.assertIn("apache kafka", job["skills"])
            self.assertEqual(job["level"], "Senior")
            self.assertEqual(job["years_experience"], 4)

            # Novel skills should now be registered into the taxonomy!
            tax_mgr = get_taxonomy_manager()
            self.assertIn("graphql", tax_mgr.skills)

    def test_cosine_similarity_numpy_safety(self):
        """Test LocalSemanticModel.cosine_similarity is safe with numpy arrays and floats."""
        import numpy as np
        v1 = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        v2 = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        sim = LocalSemanticModel.cosine_similarity(v1, v2)
        self.assertEqual(sim, 1.0)

        # Empty / None / mismatched lengths return 0.0 without throwing
        self.assertEqual(LocalSemanticModel.cosine_similarity(None, v2), 0.0)
        self.assertEqual(LocalSemanticModel.cosine_similarity([], v2), 0.0)
        self.assertEqual(LocalSemanticModel.cosine_similarity(np.array([]), v2), 0.0)
        self.assertEqual(LocalSemanticModel.cosine_similarity(v1, np.array([0.1])), 0.0)

    def test_cached_skill_matching_performance(self):
        """Benchmark matching with cached embeddings completes in milliseconds."""
        import time
        model = get_semantic_model()
        job = {
            "title": "Senior Embedded Engineer",
            "extracted_keywords": ["c", "c++", "embedded", "autosar", "can bus", "rtos", "unit test"],
            "extracted_level": "Senior",
            "embedding": model.get_embedding("Senior Embedded Engineer")
        }
        cand = {
            "name": "Nguyen Van B",
            "position": "Embedded Developer",
            "extracted_keywords": ["c", "cpp", "freertos", "autosar", "can bus", "python", "git"],
            "extracted_level": "Senior",
            "extracted_experiences": {"years_experience": 4},
            "embedding": model.get_embedding("Embedded Developer")
        }

        # First run warms cache if needed
        compute_match(job, cand)

        t0 = time.time()
        for _ in range(25):
            res = compute_match(job, cand)
        t1 = time.time()
        total_time = t1 - t0
        self.assertLess(total_time, 1.5, f"25 candidate matches took {total_time:.3f}s, expected < 1.5s")
        self.assertGreater(res["matching_percentage"], 50.0)


if __name__ == "__main__":
    unittest.main()
