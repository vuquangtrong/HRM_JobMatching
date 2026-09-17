"""
NLP and Keyword Extraction module for HRM_Backend.
Includes:
1. Local lightweight embedding model (FastEmbed or robust fallback) for semantic evaluation.
2. Technical skill & keyword taxonomy.
3. Experience & seniority level extraction.
4. PDF text parsing with pypdf.
"""

import os
import re
import json
import zlib
import math
import logging
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("hrm.nlp_extractor")

# ==========================================
# Technical Skills & Domain Taxonomy
# ==========================================

TECH_TAXONOMY = {
    # Programming Languages
    "python": r"\b(python\d?|py)\b",
    "c++": r"(?:\bcpp\b|\bc\+\+(?!\w))",
    "c#": r"(?:\bc#(?!\w)|\bcsharp\b|\b\.net\b)",
    "c": r"(?:\blanguage\s*c\b|\bngôn\s*ngữ\s*c\b|\bc\s*(?:/|&|\+)\s*c\+\+|\bc\s+(?:programming|embedded|developer|engineer)|\b(?:embedded|firmware)\s+c\b|\bc-lang\b)",
    "java": r"\b(java|jvm)\b(?!\s*script)",
    "javascript": r"\b(javascript|js|ecmascript)\b",
    "typescript": r"\b(typescript|ts)\b",
    "golang": r"\b(go|golang)\b",
    "rust": r"\b(rust)\b",
    "php": r"\b(php)\b",
    "swift": r"\b(swift)\b",
    "kotlin": r"\b(kotlin)\b",
    "bash": r"\b(bash|shell\s*scripting|sh\s*script)\b",
    "sql": r"\b(sql|mysql|postgresql|postgres|sqlite|oracle|tsql)\b",

    # Testing & QA
    "testing knowledge": r"\b(tester|testing|test\s+knowledge|qa|qc|quality\s+assurance)\b",
    "system test": r"\b(system\s+test|system\s+testing|e2e\s+test)\b",
    "component test": r"\b(component\s*(?:&|and|\+)?\s*system\s+test|component\s+test|component\s+testing)\b",
    "test automation": r"\b(automation\s+test|test\s+automation|automated\s+test|automated\s+testing)\b",
    "manual test": r"\b(manual\s+test|manual\s+testing)\b",
    "regression test": r"\b(regression\s+test|regression\s+testing)\b",
    "unit test": r"\b(unit\s+test|unit\s+testing|pytest|unittest|junit)\b",
    "test case design": r"\b(test\s+case|test\s+design|test\s+execution|test\s+plan)\b",
    "bug tracking": r"\b(bug\s+tickets?|jira|confluence|mantis|bugzilla)\b",
    "selenium": r"\b(selenium|appium|playwright|cypress)\b",
    "robot framework": r"\b(robot\s+framework|cucumber|bdd)\b",
    "istqb": r"\b(istqb)\b",

    # Embedded & Hardware
    "embedded": r"\b(embedded|vi\s*điều\s*khiển|nhúng)\b",
    "embedded background": r"\b(embedded\s+background|embedded\s+systems?|hệ\s*thống\s*nhúng)\b",
    "automotive": r"\b(automotive|autosar|ecu|can\s*(?:bus|protocol|message|network|controller)|lin\s*(?:bus|protocol)|canoe|canalyzer|vector\s*canoe|diag\s*(?:tester|diagnostics))\b",
    "microcontroller": r"\b(stm32|cortex|microcontroller|mcu|esp32|arduino|avr|pic\s*(?:microcontroller|mcu|\d{2,}))\b",
    "device under test": r"\b(device\s+under\s+test|dut\s*(?:testing|board|verification)|hardware\s+(?:test|testing))\b",
    "rtos": r"\b(rtos|freertos|embedded\s+linux)\b",
    "firmware": r"\b(firmware)\b",

    # Web, Cloud & Tools
    "react": r"\b(react|reactjs|react\.js)\b",
    "angular": r"\b(angular|angularjs)\b",
    "vue": r"\b(vue|vuejs)\b",
    "node.js": r"\b(node|nodejs|node\.js)\b",
    "fastapi": r"\b(fastapi)\b",
    "flask": r"\b(flask)\b",
    "django": r"\b(django)\b",
    "spring": r"\b(spring\s+boot|spring\s+framework|\bspring\b(?!\s*(?:20\d\d|semester|summer|winter|fall)))\b",
    "docker": r"\b(docker|containerization)\b",
    "kubernetes": r"\b(kubernetes|k8s)\b",
    "ci/cd": r"\b(ci/cd|cicd|jenkins|github\s+actions|gitlab\s+ci)\b",
    "git": r"\b(git|github|gitlab|bitbucket)\b",
    "linux": r"\b(linux|ubuntu|debian)\b",
    "aws": r"\b(aws|amazon\s+web\s+services)\b",
    "azure": r"\b(azure)\b",

    # Spoken Languages
    "english": r"\b(english|tiếng\s*anh|toeic|toiec|ielts|toefl)\b",
    "japanese": r"\b(japanese|tiếng\s*nhật|n1|n2|n3|n4|jlpt)\b",
    "german": r"\b(german|tiếng\s*đức)\b",
}

# Semantic Concept Definitions for AI Concept Vector Matching
SEMANTIC_SKILL_CONCEPTS = {
    "python": "python programming language scripting automated testing backend",
    "c++": "c++ object oriented systems programming stl memory management",
    "c#": "c# dotnet .net asp.net enterprise backend development",
    "c": "c programming low level firmware embedded systems hardware drivers",
    "java": "java jvm spring enterprise microservices backend development",
    "javascript": "javascript frontend web development ecmascript browser node",
    "typescript": "typescript typed javascript web components frontend",
    "golang": "go golang microservices concurrent backend systems",
    "rust": "rust systems programming memory safety performance",
    "php": "php web development laravel symphony backend",
    "sql": "sql relational database queries mysql postgresql schema design",
    "testing knowledge": "quality assurance software testing principles verification bug finding",
    "system test": "system testing end to end integration validation black box",
    "component test": "component testing unit module verification white box",
    "test automation": "automated testing test automation framework execution scripts",
    "manual test": "manual testing exploratory test execution bug verification",
    "regression test": "regression testing verification of stability bug fixes",
    "unit test": "unit testing test driven development tdd test runner",
    "test case design": "test case design specification coverage analysis test plan",
    "bug tracking": "bug tracking issue reporting jira defect management tickets",
    "selenium": "selenium webdriver browser automated testing e2e",
    "robot framework": "robot framework keyword driven automated acceptance test",
    "istqb": "istqb certified tester international software testing",
    "embedded": "embedded systems microcontroller firmware hardware interfacing",
    "automotive": "automotive autosar ecu vehicle can bus lin canoe diagnostics",
    "microcontroller": "microcontroller mcu stm32 cortex arm embedded controller",
    "device under test": "device under test hardware in the loop dut test bench",
    "rtos": "real time operating system rtos freertos embedded linux",
    "firmware": "firmware bare metal drivers bsp embedded development",
    "react": "react reactjs user interface components frontend web",
    "angular": "angular typescript web framework spa single page application",
    "vue": "vue vuejs progressive frontend user interface framework",
    "node.js": "node.js asynchronous javascript backend server runtime",
    "fastapi": "fastapi python async web api framework rest",
    "flask": "flask python lightweight microframework rest api",
    "django": "django python web framework orm full stack",
    "spring": "spring spring boot java enterprise microservices framework",
    "docker": "docker containerization containers devops deployment",
    "kubernetes": "kubernetes k8s container orchestration cluster management",
    "ci/cd": "continuous integration continuous delivery cicd jenkins pipeline",
    "git": "git version control repository branching github gitlab",
    "linux": "linux operating system bash shell administration",
    "aws": "amazon web services aws cloud infrastructure lambda ec2",
    "azure": "microsoft azure cloud platform services devops",
    "english": "english language communication speaking writing toeic ielts",
    "japanese": "japanese language communication jlpt n1 n2 n3",
    "german": "german language communication deutsch",
}

LEVEL_PATTERNS = {
    "Senior": r"\b(senior|lead|principal|trưởng\s*nhóm|chuyên\s*gia|5\+\s*years?|5\+\s*năm)\b",
    "Middle": r"\b(middle|mid-level|2-4\s*years?|3\+\s*years?|3\s*năm|2\s*năm)\b",
    "Junior": r"\b(junior|fresher|entry-level|1-2\s*years?|1\s*năm|dưới\s*1\s*năm)\b",
    "Intern": r"\b(intern|internship|thực\s*tập)\b"
}


# Pre-selected List of Local Embedding Models (Base and Large models only for high accuracy)
AVAILABLE_MODELS = [
    {
        "id": "BAAI/bge-large-en-v1.5",
        "name": "BGE Large EN v1.5",
        "dim": 1024,
        "size": "~1.20 GB"
    },
    {
        "id": "BAAI/bge-base-en-v1.5",
        "name": "BGE Base EN v1.5",
        "dim": 768,
        "size": "~210 MB"
    },
    {
        "id": "thenlper/gte-large",
        "name": "GTE Large",
        "dim": 1024,
        "size": "~1.20 GB"
    },
    {
        "id": "thenlper/gte-base",
        "name": "GTE Base",
        "dim": 768,
        "size": "~440 MB"
    },
    {
        "id": "snowflake/snowflake-arctic-embed-l",
        "name": "Snowflake Arctic Embed L",
        "dim": 1024,
        "size": "~1.02 GB"
    },
    {
        "id": "snowflake/snowflake-arctic-embed-m",
        "name": "Snowflake Arctic Embed M",
        "dim": 768,
        "size": "~430 MB"
    },
    {
        "id": "mixedbread-ai/mxbai-embed-large-v1",
        "name": "MixedBread mxbai Large v1",
        "dim": 1024,
        "size": "~640 MB"
    },
    {
        "id": "jinaai/jina-embeddings-v2-base-en",
        "name": "Jina Embeddings v2 Base EN",
        "dim": 768,
        "size": "~520 MB"
    }
]

MODEL_DIMS = {m["id"]: m["dim"] for m in AVAILABLE_MODELS}


# ==========================================
# Local Semantic Embedding Model
# ==========================================

class LocalSemanticModel:
    """
    Handles dense vector embeddings locally.
    Uses FastEmbed (ONNX) if installed, otherwise uses an internal
    deterministic crc32 subword vectorizer ensuring 100% offline persistence and consistency.
    """

    def __init__(self, model_name: str = "BAAI/bge-large-en-v1.5"):
        self.model_name = model_name
        self.dim = 1024
        self.fastembed_model = None
        self._concept_cache: Dict[str, List[float]] = {}
        self._initialize_model()

    def _initialize_model(self):
        # Determine dimension based on model configuration or name
        if self.model_name in MODEL_DIMS:
            self.dim = MODEL_DIMS[self.model_name]
        elif "large" in self.model_name.lower() or "-l" in self.model_name.lower():
            self.dim = 1024
        else:
            self.dim = 768

        self._concept_cache.clear()
        try:
            from fastembed import TextEmbedding
            logger.info(f"Loading FastEmbed model '{self.model_name}'...")
            self.fastembed_model = TextEmbedding(model_name=self.model_name, cache_dir="./fastembed_cache")
            if hasattr(self.fastembed_model, "embedding_size"):
                self.dim = self.fastembed_model.embedding_size
            logger.info(
                f"FastEmbed local model initialized successfully (dim={self.dim}).")
        except Exception as e:
            logger.warning(
                f"FastEmbed not loaded ({e}). Using resilient internal deterministic vectorizer (dim={self.dim}).")
            self.fastembed_model = None

    def switch_model(self, model_name: str) -> bool:
        """Switches the active embedding model."""
        self.model_name = model_name
        self._initialize_model()
        return self.fastembed_model is not None

    def get_embedding(self, text: str) -> List[float]:
        """Generates normalized vector embedding for text."""
        if not text or not text.strip():
            return [0.0] * self.dim

        if self.fastembed_model is not None:
            try:
                embeddings = list(self.fastembed_model.embed([text]))
                if embeddings and len(embeddings) > 0:
                    vec = embeddings[0].tolist()
                    return vec
            except Exception as e:
                logger.warning(
                    f"FastEmbed inference error: {e}. Falling back to internal vectorizer.")

        # Fallback Vectorizer (deterministic crc32 signed subword vector)
        return self._hash_vectorize(text, dim=self.dim)

    def _hash_vectorize(self, text: str, dim: Optional[int] = None) -> List[float]:
        """
        Fast, 100% deterministic bipolar signed subword hashing vectorizer using crc32.
        Preserves vector dimensions identically across process restarts and uses
        bipolar signs (+1 / -1) to ensure zero-mean orthogonal expectation for unrelated texts.
        """
        target_dim = dim or self.dim
        vec = [0.0] * target_dim
        words = re.findall(r"[a-zA-Z0-9\+#]+", text.lower())
        if not words:
            return vec

        for w in words:
            # Hash full word deterministically with bipolar sign
            h1 = zlib.crc32(w.encode("utf-8")) % target_dim
            s1 = 1.0 if (zlib.crc32((w + "_s").encode("utf-8")) & 1) else -1.0
            vec[h1] += s1 * 2.0

            # Hash char n-grams (3-grams) for morphological similarity
            for i in range(max(1, len(w) - 2)):
                tri = w[i:i+3]
                h2 = zlib.crc32(tri.encode("utf-8")) % target_dim
                s2 = 1.0 if (zlib.crc32(
                    (tri + "_s").encode("utf-8")) & 1) else -1.0
                vec[h2] += s2 * 0.5

        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-9:
            vec = [round(x / norm, 5) for x in vec]
        return vec

    def get_concept_embedding(self, concept_key: str, concept_desc: str) -> List[float]:
        """Retrieves or caches the prototype embedding for an AI skill concept."""
        if concept_key not in self._concept_cache:
            self._concept_cache[concept_key] = self.get_embedding(concept_desc)
        return self._concept_cache[concept_key]

    @staticmethod
    def cosine_similarity(v1: Optional[List[float]], v2: Optional[List[float]]) -> float:
        """Computes cosine similarity between two vectors scaled to [0.0, 1.0]."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0

        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))

        if norm1 < 1e-9 or norm2 < 1e-9:
            return 0.0

        cos_sim = dot / (norm1 * norm2)
        # Bounded between 0.0 and 1.0
        return round(max(0.0, min(1.0, cos_sim)), 4)


# Global singleton instance
_model_instance = None


def get_semantic_model() -> LocalSemanticModel:
    global _model_instance
    if _model_instance is None:
        _model_instance = LocalSemanticModel()
    return _model_instance


# ==========================================
# Text Processing & Entity Extraction
# ==========================================

def clean_html(text: Optional[str]) -> str:
    """Strips HTML tags and normalizes whitespace."""
    if not text:
        return ""
    # Simple regex HTML cleaner (safe & fast)
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"&[a-zA-Z0-9#]+;", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_skills(text: str, semantic_boost: bool = True) -> List[str]:
    """
    Extracts technical skills and tools from text using both AI semantic concept
    understanding and context-guarded taxonomy.
    """
    if not text:
        return []

    found_skills = set()
    text_lower = text.lower()

    # 1. Context-Guarded Taxonomy Check
    for skill_name, pattern in TECH_TAXONOMY.items():
        if re.search(pattern, text_lower, re.IGNORECASE):
            found_skills.add(skill_name)

    # 2. AI Semantic Concept Matching
    # Segments text into meaningful phrases and matches against skill concept embeddings
    if semantic_boost:
        model = get_semantic_model()
        chunks = [c.strip() for c in re.split(
            r"[\n\r•\-\*;,]+", text) if len(c.strip()) > 3]
        for chunk in chunks[:30]:  # Process top 30 key requirement lines
            chunk_emb = model.get_embedding(chunk)
            for skill_name, concept_desc in SEMANTIC_SKILL_CONCEPTS.items():
                if skill_name in found_skills:
                    continue
                concept_emb = model.get_concept_embedding(
                    skill_name, concept_desc)
                sim = LocalSemanticModel.cosine_similarity(
                    chunk_emb, concept_emb)
                # Strong semantic alignment
                if sim >= 0.76:
                    found_skills.add(skill_name)

    return sorted(list(found_skills))


def extract_seniority(text: str, default: str = "Middle", raw_level: Optional[Any] = None) -> str:
    """Identifies seniority level from explicit raw_level or text."""
    if raw_level:
        if isinstance(raw_level, list) and len(raw_level) > 0:
            return str(raw_level[0]).capitalize()
        if isinstance(raw_level, str) and raw_level.strip():
            cleaned = raw_level.strip()
            if cleaned.startswith("[") and cleaned.endswith("]"):
                try:
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        return str(parsed[0]).capitalize()
                except Exception:
                    pass
            for lvl in ["Senior", "Middle", "Junior", "Intern"]:
                if lvl.lower() in cleaned.lower():
                    return lvl

    if not text:
        return default

    for level, pattern in LEVEL_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return level
    return default


def extract_years_of_experience(text: str) -> Optional[int]:
    """
    Extracts number of years of experience if explicitly mentioned.
    Explicitly guards against age mentions (e.g. '25 years old', '25 tuổi').
    """
    if not text:
        return None

    # Matches "3+ years exp", "3 years", "3 năm kinh nghiệm", "5+ year"
    pattern = r"(\d+)\s*(?:\+|plus)?\s*(?:years?|năm)(?!\s*(?:old|tuổi))\s*(?:of\s+)?(?:exp|experience|kinh\s*nghiệm)?"
    matches = re.finditer(pattern, text, re.IGNORECASE)
    for m in matches:
        try:
            val = int(m.group(1))
            if 1 <= val <= 35:
                # Check 15 characters after match to ensure no age context
                after = text[m.end():m.end()+15].lower()
                if "old" in after or "tuổi" in after:
                    continue
                return val
        except ValueError:
            pass
    return None


def extract_job_keywords(
    title: str,
    request: str,
    job_description: str,
    raw_level: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Extracts structured keywords, level, and semantic embedding for a Job Request.
    """
    cleaned_desc = clean_html(job_description or "")
    combined_text = f"{title or ''}\n{request or ''}\n{cleaned_desc}"

    skills = extract_skills(combined_text)
    level = extract_seniority(f"{title} {request}", raw_level=raw_level)
    years_exp = extract_years_of_experience(f"{title} {request}")

    # Generate semantic embedding vector
    model = get_semantic_model()
    embedding = model.get_embedding(combined_text[:2500])

    return {
        "skills": skills,
        "level": level,
        "years_experience": years_exp,
        "cleaned_text": combined_text,
        "embedding": embedding
    }


def extract_candidate_keywords(
    name: str,
    position: str,
    location: str,
    cv_information: Optional[str] = None,
    cv_text: Optional[str] = None,
    raw_status: Optional[str] = None,
    raw_level: Optional[Any] = None,
    raw_experience: Optional[Any] = None,
    cv_urls: Optional[List[str]] = None,
    raw_languages: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Extracts structured keywords, experience summary, and semantic embedding for a Candidate.
    """
    cv_info_cleaned = ""
    if cv_information:
        cv_info_cleaned = clean_html(cv_information)

    # Extract useful metadata from CV URLs (file names, slugs)
    url_text = ""
    if cv_urls and isinstance(cv_urls, list):
        slugs = []
        for u in cv_urls:
            if isinstance(u, str):
                cleaned_u = re.sub(r"[^a-zA-Z0-9\+#]+", " ", u)
                slugs.append(cleaned_u)
        url_text = " ".join(slugs)

    # Format languages
    lang_text = ""
    if raw_languages:
        if isinstance(raw_languages, list):
            lang_text = " ".join(str(l) for l in raw_languages)
        elif isinstance(raw_languages, str):
            lang_text = raw_languages

    combined_text = f"{position or ''} {name or ''} {location or ''}\n{lang_text}\n{url_text}\n{cv_info_cleaned}\n{cv_text or ''}"

    skills = extract_skills(combined_text)
    level = extract_seniority(
        f"{position} {cv_info_cleaned}", raw_level=raw_level)

    years_exp = None
    if raw_experience:
        years_exp = extract_years_of_experience(str(raw_experience))
    if years_exp is None:
        years_exp = extract_years_of_experience(
            f"{position} {cv_info_cleaned} {cv_text or ''}")

    model = get_semantic_model()
    embedding = model.get_embedding(combined_text[:2500])

    return {
        "skills": skills,
        "level": level,
        "years_experience": years_exp,
        "summary": cv_info_cleaned[:500] if cv_info_cleaned else f"{position} based in {location}",
        "cleaned_text": combined_text,
        "embedding": embedding
    }


# ==========================================
# PDF Parsing & CV Downloading
# ==========================================

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extracts text content from PDF bytes using pypdf."""
    if not pdf_bytes:
        return ""

    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = []
        for page in reader.pages[:10]:  # Read first 10 pages maximum
            t = page.extract_text()
            if t:
                text_parts.append(t)
        return "\n".join(text_parts).strip()
    except Exception as e:
        logger.warning(f"Error parsing PDF with pypdf: {e}")
        return ""


def download_and_extract_cv(cv_url: str, bearer_token: Optional[str] = None) -> str:
    """
    Downloads a candidate CV file from HRM and extracts its text.
    Validates URL scheme and destination to prevent SSRF vulnerabilities.
    """
    if not cv_url:
        return ""

    try:
        parsed = urlparse(cv_url)
        if parsed.scheme not in ("http", "https"):
            logger.warning(f"Rejected invalid CV URL scheme: {parsed.scheme}")
            return ""

        host = (parsed.hostname or "").lower()
        if host in ("169.254.169.254", "metadata.google.internal") or (host.startswith("127.") and not (parsed.port and parsed.port == 8765)):
            logger.warning(f"Rejected SSRF host: {host}")
            return ""

        import requests
        headers = {
            "Accept": "application/pdf,*/*",
            "User-Agent": "HRM-Assistant-Backend/1.0"
        }
        if bearer_token and ("ltsgroup.tech" in host or host in ("localhost", "127.0.0.1")):
            headers["Authorization"] = f"Bearer {bearer_token}"

        response = requests.get(cv_url, headers=headers, timeout=12)
        if response.status_code == 200:
            return extract_text_from_pdf_bytes(response.content)
        else:
            logger.warning(
                f"Failed to fetch CV from {cv_url} (HTTP {response.status_code})")
            return ""
    except Exception as e:
        logger.warning(f"Exception downloading CV from {cv_url}: {e}")
        return ""
