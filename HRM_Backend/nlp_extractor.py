"""
NLP and Keyword Extraction module for HRM_Backend.
Includes:
1. Local lightweight embedding model (FastEmbed or robust fallback) for semantic evaluation.
2. Technical skill & keyword taxonomy.
3. Experience & seniority level extraction.
4. PDF text parsing with pypdf.
"""

import re
import math
import logging
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
    "c": r"\b(c|c-lang)\b(?!\+|\#)",
    "java": r"\b(java|jvm)\b",
    "javascript": r"\b(javascript|js|ecmascript)\b",
    "typescript": r"\b(typescript|ts)\b",
    "golang": r"\b(go|golang)\b",
    "rust": r"\b(rust)\b",
    "php": r"\b(php)\b",
    "swift": r"\b(swift)\b",
    "kotlin": r"\b(kotlin)\b",
    "bash": r"\b(bash|shell|sh)\b",
    "sql": r"\b(sql|mysql|postgresql|postgres|sqlite|oracle|tsql)\b",

    # Testing & QA
    "testing knowledge": r"\b(testing|test knowledge|qa|quality assurance)\b",
    "system test": r"\b(system test|system testing|e2e test)\b",
    "component test": r"\b(component test|component testing)\b",
    "test automation": r"\b(automation test|test automation|automated test|automated testing)\b",
    "manual test": r"\b(manual test|manual testing)\b",
    "regression test": r"\b(regression test|regression testing)\b",
    "unit test": r"\b(unit test|unit testing|pytest|unittest|junit)\b",
    "test case design": r"\b(test case|test design|test execution|test plan)\b",
    "bug tracking": r"\b(bug tickets?|jira|confluence|mantis|bugzilla)\b",
    "selenium": r"\b(selenium|appium|playwright|cypress)\b",
    "robot framework": r"\b(robot framework|cucumber|bdd)\b",
    "istqb": r"\b(istqb)\b",

    # Embedded & Hardware
    "embedded": r"\b(embedded|vi điều khiển|nhúng)\b",
    "embedded background": r"\b(embedded background|embedded systems?|nhúng)\b",
    "automotive": r"\b(automotive|autosar|ecu|can|lin|canoe|diag|vector)\b",
    "microcontroller": r"\b(stm32|arm|cortex|microcontroller|mcu|esp32|arduino|pic|avr)\b",
    "device under test": r"\b(device under test|dut|hardware test)\b",
    "rtos": r"\b(rtos|freertos|embedded linux)\b",
    "firmware": r"\b(firmware)\b",

    # Web, Cloud & Tools
    "react": r"\b(react|reactjs|react\.js)\b",
    "angular": r"\b(angular|angularjs)\b",
    "vue": r"\b(vue|vuejs)\b",
    "node.js": r"\b(node|nodejs|node\.js)\b",
    "fastapi": r"\b(fastapi)\b",
    "flask": r"\b(flask)\b",
    "django": r"\b(django)\b",
    "spring": r"\b(spring|spring boot)\b",
    "docker": r"\b(docker|container)\b",
    "kubernetes": r"\b(kubernetes|k8s)\b",
    "ci/cd": r"\b(ci/cd|cicd|jenkins|github actions|gitlab ci)\b",
    "git": r"\b(git|github|gitlab|bitbucket)\b",
    "linux": r"\b(linux|ubuntu|debian)\b",
    "aws": r"\b(aws|amazon web services)\b",
    "azure": r"\b(azure)\b",

    # Spoken Languages
    "english": r"\b(english|tiếng anh|toiec|ielts)\b",
    "japanese": r"\b(japanese|tiếng nhật|n1|n2|n3|n4|jlpt)\b",
    "german": r"\b(german|tiếng đức)\b",
}

LEVEL_PATTERNS = {
    "Senior": r"\b(senior|lead|principal|trưởng nhóm|chuyên gia|5\+\s*years?|5\+\s*năm)\b",
    "Middle": r"\b(middle|mid-level|2-4\s*years?|3\+\s*years?|3\s*năm|2\s*năm)\b",
    "Junior": r"\b(junior|fresher|entry-level|1-2\s*years?|1\s*năm|dưới 1 năm)\b",
    "Intern": r"\b(intern|internship|thực tập)\b"
}


# Pre-selected List of Local Embedding Models
AVAILABLE_MODELS = [
    {
        "id": "BAAI/bge-small-en-v1.5",
        "name": "BGE Small EN (Recommended - Fast & Accurate)",
        "dim": 384,
        "size": "~67 MB"
    },
    {
        "id": "sentence-transformers/all-MiniLM-L6-v2",
        "name": "MiniLM-L6-v2 (Popular Standard)",
        "dim": 384,
        "size": "~80 MB"
    },
    {
        "id": "BAAI/bge-base-en-v1.5",
        "name": "BGE Base EN (High Accuracy)",
        "dim": 768,
        "size": "~210 MB"
    }
]


# ==========================================
# Local Semantic Embedding Model
# ==========================================

class LocalSemanticModel:
    """
    Handles dense vector embeddings locally.
    Uses FastEmbed (ONNX) if installed, otherwise uses an internal
    subword TF-IDF / term-frequency vectorizer ensuring 100% offline availability.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.model_name = model_name
        self.fastembed_model = None
        self._initialize_model()

    def _initialize_model(self):
        try:
            from fastembed import TextEmbedding
            logger.info(f"Loading FastEmbed model '{self.model_name}'...")
            self.fastembed_model = TextEmbedding(model_name=self.model_name)
            logger.info("FastEmbed local model initialized successfully.")
        except Exception as e:
            logger.warning(
                f"FastEmbed not loaded ({e}). Using resilient internal semantic vectorizer.")
            self.fastembed_model = None

    def switch_model(self, model_name: str) -> bool:
        """Switches the active embedding model."""
        self.model_name = model_name
        self._initialize_model()
        return self.fastembed_model is not None

    def get_embedding(self, text: str) -> List[float]:
        """Generates normalized vector embedding for text."""
        if not text or not text.strip():
            return [0.0] * 384

        if self.fastembed_model is not None:
            try:
                embeddings = list(self.fastembed_model.embed([text]))
                if embeddings and len(embeddings) > 0:
                    vec = embeddings[0].tolist()
                    return vec
            except Exception as e:
                logger.warning(
                    f"FastEmbed inference error: {e}. Falling back to internal vectorizer.")

        # Fallback Vectorizer (384-dimensional hashed subword vector)
        return self._hash_vectorize(text, dim=384)

    def _hash_vectorize(self, text: str, dim: int = 384) -> List[float]:
        """Fast, deterministic subword hashing vectorizer for semantic fallback."""
        vec = [0.0] * dim
        words = re.findall(r"\w+", text.lower())
        if not words:
            return vec

        for w in words:
            # Hash full word
            h1 = hash(w) % dim
            vec[h1] += 1.0

            # Hash char n-grams (3-grams) for morphological similarity
            for i in range(max(1, len(w) - 2)):
                tri = w[i:i+3]
                h2 = hash(tri) % dim
                vec[h2] += 0.5

        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-9:
            vec = [round(x / norm, 5) for x in vec]
        return vec

    @staticmethod
    def cosine_similarity(v1: Optional[List[float]], v2: Optional[List[float]]) -> float:
        """Computes cosine similarity between two vectors in [-1.0, 1.0] scaled to [0.0, 1.0]."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0

        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))

        if norm1 < 1e-9 or norm2 < 1e-9:
            return 0.0

        cos_sim = dot / (norm1 * norm2)
        # Scale [-1, 1] to [0, 1]
        scaled = max(0.0, min(1.0, (cos_sim + 1.0) / 2.0))
        return round(scaled, 4)


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


def extract_skills(text: str) -> List[str]:
    """Extracts technical skills and tools from text based on taxonomy."""
    if not text:
        return []

    found_skills = []
    text_lower = text.lower()
    for skill_name, pattern in TECH_TAXONOMY.items():
        if re.search(pattern, text_lower, re.IGNORECASE):
            found_skills.append(skill_name)
    return sorted(list(set(found_skills)))


def extract_seniority(text: str) -> str:
    """Identifies seniority level from text."""
    if not text:
        return "Middle"

    for level, pattern in LEVEL_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return level
    return "Middle"


def extract_years_of_experience(text: str) -> Optional[int]:
    """Extracts number of years of experience if explicitly mentioned."""
    if not text:
        return None

    # Matches "3+ years", "3 years", "3 năm", "5+ year"
    m = re.search(r"(\d+)\s*(?:\+|plus)?\s*(?:years?|năm)",
                  text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def extract_job_keywords(title: str, request: str, job_description: str) -> Dict[str, Any]:
    """
    Extracts structured keywords, level, and combined text for a Job Request.
    """
    cleaned_desc = clean_html(job_description or "")
    combined_text = f"{title or ''}\n{request or ''}\n{cleaned_desc}"

    skills = extract_skills(combined_text)
    level = extract_seniority(f"{title} {request}")
    years_exp = extract_years_of_experience(f"{title} {request}")

    # Generate semantic embedding vector
    model = get_semantic_model()
    embedding = model.get_embedding(combined_text[:2000])

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
    raw_status: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extracts structured keywords, experience summary, and embedding for a Candidate.
    """
    cv_info_cleaned = ""
    if cv_information:
        cv_info_cleaned = clean_html(cv_information)

    combined_text = f"{position or ''} {name or ''} {location or ''}\n{cv_info_cleaned}\n{cv_text or ''}"

    skills = extract_skills(combined_text)
    level = extract_seniority(f"{position} {cv_info_cleaned}")
    years_exp = extract_years_of_experience(
        f"{position} {cv_info_cleaned} {cv_text or ''}")

    model = get_semantic_model()
    embedding = model.get_embedding(combined_text[:2000])

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
    """
    if not cv_url:
        return ""

    try:
        import requests
        headers = {
            "Accept": "application/pdf,*/*",
            "User-Agent": "HRM-Assistant-Backend/1.0"
        }
        if bearer_token:
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
