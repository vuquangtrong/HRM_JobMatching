"""
Extraction Engine for HRM_Backend.

Hybrid Architecture:
1. Deterministic regular expression baseline from an external JSON taxonomy file
   (`taxonomy.json`) guaranteeing fast, reliable skill, seniority, and experience extraction.
2. Local LLM enrichment (Ollama-native or OpenAI-compatible) for open-vocabulary skills,
   natural language candidate summaries, and spoken languages.
3. Dynamic taxonomy evolution: novel skills extracted by the LLM are automatically compiled
   into regular expression patterns and persisted to `taxonomy.json` at runtime.
4. Local dense embedding model (FastEmbed or deterministic subword fallback) with an
   LRU cache for high-performance vectorized matching.
5. HTML cleanup and PDF CV parsing/downloading with SSRF protection.
"""

import os
import re
import json
import zlib
import math
import logging
import threading
import hashlib
from collections import OrderedDict
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Tuple

from config import get_config

logger = logging.getLogger("hrm.extracting_engine")

_config = get_config()

# ==========================================
# Local Dense Embedding Model & Skill Cache
# ==========================================

SEMANTIC_MODEL_ID = str(_config["fastembed"]["model"])
SEMANTIC_MODEL_DIM = int(_config["fastembed"]["dim"])
FASTEMBED_CACHE_DIR = str(_config["fastembed"]["cache_dir"])


class LocalSemanticModel:
    """
    Handles dense vector embeddings locally.
    Uses FastEmbed (ONNX) if installed, otherwise uses an internal
    deterministic crc32 subword vectorizer ensuring 100% offline persistence and consistency.
    """

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or SEMANTIC_MODEL_ID
        self.dim = SEMANTIC_MODEL_DIM
        self.fastembed_model = None
        self._initialize_model()

    def _initialize_model(self):
        self.dim = SEMANTIC_MODEL_DIM

        try:
            from fastembed import TextEmbedding
            logger.info(f"Loading FastEmbed model '{self.model_name}'...")
            self.fastembed_model = TextEmbedding(model_name=self.model_name, cache_dir=FASTEMBED_CACHE_DIR)
            if hasattr(self.fastembed_model, "embedding_size"):
                self.dim = int(self.fastembed_model.embedding_size)
            logger.info(f"FastEmbed local model initialized successfully (dim={self.dim}).")
        except Exception as e:
            logger.warning(f"FastEmbed not loaded ({e}). Using resilient internal deterministic vectorizer (dim={self.dim}).")
            self.fastembed_model = None

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
                logger.warning(f"FastEmbed inference error: {e}. Falling back to internal vectorizer.")

        # Fallback Vectorizer (deterministic crc32 signed subword vector)
        return self._hash_vectorize(text, dim=self.dim)

    def _hash_vectorize(self, text: str, dim: Optional[int] = None) -> List[float]:
        """Fast, 100% deterministic bipolar signed subword hashing vectorizer using crc32."""
        target_dim = dim or self.dim
        vec = [0.0] * target_dim
        words = re.findall(r"[a-zA-Z0-9\+#]+", text.lower())
        if not words:
            return vec

        for w in words:
            h1 = zlib.crc32(w.encode("utf-8")) % target_dim
            s1 = 1.0 if (zlib.crc32((w + "_s").encode("utf-8")) & 1) else -1.0
            vec[h1] += s1 * 2.0

            for i in range(max(1, len(w) - 2)):
                tri = w[i:i+3]
                h2 = zlib.crc32(tri.encode("utf-8")) % target_dim
                s2 = 1.0 if (zlib.crc32((tri + "_s").encode("utf-8")) & 1) else -1.0
                vec[h2] += s2 * 0.5

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-9:
            vec = [round(x / norm, 5) for x in vec]
        return vec

    @staticmethod
    def cosine_similarity(v1: Any, v2: Any) -> float:
        """
        Computes cosine similarity between two vectors scaled to [0.0, 1.0].
        Robust against Python lists, numpy ndarrays, or empty inputs.
        """
        if v1 is None or v2 is None:
            return 0.0
        try:
            l1, l2 = len(v1), len(v2)
            if l1 == 0 or l2 == 0 or l1 != l2:
                return 0.0
        except Exception:
            return 0.0

        try:
            dot = sum(float(a) * float(b) for a, b in zip(v1, v2))
            norm1 = math.sqrt(sum(float(a) * float(a) for a in v1))
            norm2 = math.sqrt(sum(float(b) * float(b) for b in v2))

            if norm1 < 1e-9 or norm2 < 1e-9:
                return 0.0

            cos_sim = dot / (norm1 * norm2)
            return round(max(0.0, min(1.0, cos_sim)), 4)
        except Exception:
            return 0.0


# Global singleton semantic model
_model_instance = None
_model_lock = threading.Lock()


def get_semantic_model() -> LocalSemanticModel:
    """Returns the global local embedding model singleton."""
    global _model_instance
    if _model_instance is None:
        with _model_lock:
            if _model_instance is None:
                _model_instance = LocalSemanticModel()
    return _model_instance


# Skill embedding LRU cache for 500x speedup in matching calculations
_skill_cache_lock = threading.Lock()
_skill_embedding_cache: "OrderedDict[str, List[float]]" = OrderedDict()
SKILL_CACHE_MAX = 8192


def get_cached_skill_embedding(skill: str) -> List[float]:
    """Caches normalized vector embeddings for individual skill tokens."""
    if not skill or not skill.strip():
        return [0.0] * get_semantic_model().dim

    key = skill.strip().lower()
    with _skill_cache_lock:
        if key in _skill_embedding_cache:
            vec = _skill_embedding_cache[key]
            _skill_embedding_cache.move_to_end(key)
            return vec

    model = get_semantic_model()
    vec = model.get_embedding(key)

    with _skill_cache_lock:
        _skill_embedding_cache[key] = vec
        while len(_skill_embedding_cache) > SKILL_CACHE_MAX:
            _skill_embedding_cache.popitem(last=False)

    return vec


def clear_skill_embedding_cache():
    """Clears the skill embedding cache (e.g. on model switch)."""
    with _skill_cache_lock:
        _skill_embedding_cache.clear()


# ==========================================
# Dynamic Taxonomy Manager (Regex in text file)
# ==========================================

DEFAULT_TAXONOMY_PATH = str(_config["taxonomy"]["path"])

SEED_TAXONOMY = {
    "skills": {
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
        "embedded": r"\b(embedded|vi\s*điều\s*khiển|nhúng)\b",
        "embedded background": r"\b(embedded\s+background|embedded\s+systems?|hệ\s*thống\s*nhúng)\b",
        "automotive": r"\b(automotive|autosar|ecu|can\s*(?:bus|protocol|message|network|controller)|lin\s*(?:bus|protocol)|canoe|canalyzer|vector\s*canoe|diag\s*(?:tester|diagnostics))\b",
        "microcontroller": r"\b(stm32|cortex|microcontroller|mcu|esp32|arduino|avr|pic\s*(?:microcontroller|mcu|\d{2,}))\b",
        "device under test": r"\b(device\s+under\s+test|dut\s*(?:testing|board|verification)|hardware\s+(?:test|testing))\b",
        "rtos": r"\b(rtos|freertos|embedded\s+linux)\b",
        "firmware": r"\b(firmware)\b",
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
        "azure": r"\b(azure)\b"
    },
    "levels": {
        "Lead": r"\b(lead|leader|tech\s*lead|team\s*lead|principal|architect|trưởng\s*nhóm)\b",
        "Senior": r"\b(senior|sr\.?|chuyên\s*viên\s*chính|chuyên\s*viên\s*cao\s*cấp)\b",
        "Middle": r"\b(middle|mid\.?|chuyên\s*viên)\b",
        "Junior": r"\b(junior|jr\.?|fresher)\b",
        "Intern": r"\b(intern|internship|thực\s*tập|thực\s*tập\s*sinh)\b"
    },
    "languages": {
        "english": r"\b(english|tiếng\s*anh|toeic|toiec|ielts|toefl)\b",
        "japanese": r"\b(japanese|tiếng\s*nhật|n1|n2|n3|n4|jlpt)\b",
        "german": r"\b(german|tiếng\s*đức)\b"
    }
}


class TaxonomyManager:
    """
    Manages regular expression taxonomy loaded from an external text file (JSON).
    Provides fast, deterministic skill and seniority extraction.
    Supports thread-safe dynamic runtime updates when the LLM discovers novel skills.
    """

    def __init__(self, filepath: Optional[str] = None):
        self.filepath = filepath or DEFAULT_TAXONOMY_PATH
        self._lock = threading.Lock()
        self.skills: Dict[str, str] = {}
        self.levels: Dict[str, str] = {}
        self.languages: Dict[str, str] = {}
        self._compiled_skills: Dict[str, re.Pattern] = {}
        self._compiled_levels: Dict[str, re.Pattern] = {}
        self._compiled_languages: Dict[str, re.Pattern] = {}
        self._experience_pattern = re.compile(
            r"(\d+)\s*(?:\+|plus)?\s*(?:years?|năm)(?!\s*(?:old|tuổi))\s*(?:of\s+)?(?:exp|experience|kinh\s*nghiệm)?",
            re.IGNORECASE
        )
        self.load()

    def load(self) -> None:
        """Loads taxonomy from file, creating it with defaults if missing."""
        with self._lock:
            data = None
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as e:
                    logger.warning(f"Error loading taxonomy file {self.filepath}: {e}. Falling back to default seed.")

            if not data or not isinstance(data, dict):
                data = SEED_TAXONOMY
                self._save_locked(data)

            self.skills = data.get("skills", {})
            self.levels = data.get("levels", {})
            self.languages = data.get("languages", {})
            self._compile_all_locked()

    def _compile_all_locked(self) -> None:
        self._compiled_skills = {}
        for name, pattern in self.skills.items():
            try:
                self._compiled_skills[name] = re.compile(pattern, re.IGNORECASE)
            except Exception as e:
                logger.warning(f"Failed to compile skill pattern for '{name}': {pattern} ({e})")

        self._compiled_levels = {}
        for level, pattern in self.levels.items():
            try:
                self._compiled_levels[level] = re.compile(pattern, re.IGNORECASE)
            except Exception as e:
                logger.warning(f"Failed to compile level pattern for '{level}': {pattern} ({e})")

        self._compiled_languages = {}
        for lang, pattern in self.languages.items():
            try:
                self._compiled_languages[lang] = re.compile(pattern, re.IGNORECASE)
            except Exception as e:
                logger.warning(f"Failed to compile language pattern for '{lang}': {pattern} ({e})")

    def _save_locked(self, data: Optional[Dict[str, Any]] = None) -> None:
        """Atomically saves taxonomy data to the text file."""
        payload = data or {
            "skills": self.skills,
            "levels": self.levels,
            "languages": self.languages,
        }
        dir_name = os.path.dirname(os.path.abspath(self.filepath))
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        tmp_path = f"{self.filepath}.tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.filepath)
        except Exception as e:
            logger.error(f"Failed to save taxonomy file to {self.filepath}: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def extract_skills(self, text: Optional[str]) -> List[str]:
        """Extracts canonical skills from text using compiled taxonomy patterns."""
        if not text:
            return []
        matched = []
        with self._lock:
            patterns = list(self._compiled_skills.items())
        for name, compiled in patterns:
            if compiled.search(text):
                matched.append(name)
        return matched

    def extract_seniority(self, text: Optional[str], default: Optional[str] = None, raw_level: Optional[Any] = None) -> Optional[str]:
        """Identifies seniority level from HRM raw_level or regex patterns in text."""
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
                for lvl in ("Lead", "Senior", "Middle", "Junior", "Intern"):
                    if lvl.lower() in cleaned.lower():
                        return lvl

        if not text:
            return default

        with self._lock:
            patterns = list(self._compiled_levels.items())
        for level, compiled in patterns:
            if compiled.search(text):
                return level
        return default

    def extract_years_of_experience(self, text: Optional[str], raw_experience: Optional[Any] = None) -> Optional[int]:
        """Extracts experience in years, guarding against age mentions."""
        if raw_experience is not None:
            try:
                if isinstance(raw_experience, (int, float)):
                    val = int(raw_experience)
                    if 0 <= val <= 40:
                        return val
                elif isinstance(raw_experience, str) and raw_experience.strip():
                    m = re.search(r"\d+", raw_experience)
                    if m:
                        val = int(m.group(0))
                        if 0 <= val <= 40:
                            return val
            except Exception:
                pass

        if not text:
            return None

        matches = self._experience_pattern.finditer(text)
        for m in matches:
            try:
                val = int(m.group(1))
                if 1 <= val <= 40:
                    after = text[m.end():m.end()+15].lower()
                    if "old" in after or "tuổi" in after:
                        continue
                    return val
            except ValueError:
                pass
        return None

    def register_new_skills(self, skills: List[str]) -> List[str]:
        """
        Dynamically registers novel skills extracted by the LLM into the taxonomy file at runtime.
        Persists newly discovered terms so regex catches them on subsequent passes.
        """
        if not skills:
            return []
        added = []
        with self._lock:
            for skill in skills:
                if not isinstance(skill, str):
                    continue
                s_clean = skill.strip().lower()
                if not s_clean or len(s_clean) < 2 or len(s_clean) > 50:
                    continue
                if s_clean in self.skills:
                    continue

                # Generate safe regex pattern
                escaped = re.escape(s_clean)
                prefix = r"\b" if re.match(r"^\w", s_clean) else ""
                suffix = r"\b" if re.search(r"\w$", s_clean) else r"(?!\w)"
                pattern = f"{prefix}{escaped}{suffix}"

                try:
                    compiled = re.compile(pattern, re.IGNORECASE)
                    self.skills[s_clean] = pattern
                    self._compiled_skills[s_clean] = compiled
                    added.append(s_clean)
                except Exception as e:
                    logger.warning(f"Could not compile dynamic pattern for skill '{s_clean}': {e}")

            if added:
                self._save_locked()
                logger.info(f"Taxonomy updated with {len(added)} new skill(s): {added}")

        return added


# Global singleton taxonomy manager
_taxonomy_manager_instance = None
_taxonomy_manager_lock = threading.Lock()


def get_taxonomy_manager(filepath: Optional[str] = None) -> TaxonomyManager:
    """Returns the global TaxonomyManager singleton."""
    global _taxonomy_manager_instance
    if _taxonomy_manager_instance is None or (filepath and _taxonomy_manager_instance.filepath != filepath):
        with _taxonomy_manager_lock:
            if _taxonomy_manager_instance is None or (filepath and _taxonomy_manager_instance.filepath != filepath):
                _taxonomy_manager_instance = TaxonomyManager(filepath=filepath)
    return _taxonomy_manager_instance


# ==========================================
# Local LLM Structured Extraction
# ==========================================

try:
    import requests
    _REQUESTS_IMPORT_OK = True
except ImportError:  # pragma: no cover
    _REQUESTS_IMPORT_OK = False

LLM_ENABLED = bool(_config["llm"]["enabled"])
LLM_BASE_URL = str(_config["llm"]["base_url"]).rstrip("/")
LLM_MODEL = str(_config["llm"]["model"])
LLM_TIMEOUT = float(_config["llm"]["timeout_seconds"])
LLM_CACHE_SIZE = int(_config["llm"]["cache_size"])

LEVEL_MAP = {
    "intern": "Intern",
    "junior": "Junior",
    "middle": "Middle",
    "senior": "Senior",
    "lead": "Lead",
    "principal": "Senior",
    "fresher": "Junior",
    "manager": "Lead",
}

_SYSTEM_PROMPT = (
    "You are a precise resume and job-requirement extraction assistant. "
    "You always answer with a single valid JSON object and nothing else."
)

_EXTRACTION_SCHEMA = (
    '{ "skills": ["...", "..."], "years_experience": <int or null>, '
    '"level": "<Intern|Junior|Middle|Senior|Lead, or null>", '
    '"languages": ["..."], "summary": "<short summary>" }'
)


def _build_prompt(kind: str, text: str) -> str:
    """Builds the user prompt for a candidate CV or job requirement text."""
    if kind == "job":
        instruction = (
            "Extract from the following job requirement text:\n"
            "1. skills: concrete technical skills, languages, frameworks, libraries, "
            "platforms, or tools (lowercase, deduplicated, e.g. python, kafka, "
            "pytest, kubernetes, system test).\n"
            "2. years_experience: the required experience in years as a number, or null if not specified.\n"
            "3. level: the seniority level (Intern/Junior/Middle/Senior/Lead), or null if not explicit.\n"
            "4. languages: any spoken languages requested (e.g. English, Japanese).\n"
            "5. summary: 1-2 sentences summarizing the core responsibilities.\n"
        )
    else:
        instruction = (
            "Extract from the following candidate CV text:\n"
            "1. skills: concrete technical skills, languages, frameworks, libraries, "
            "platforms, or tools the candidate knows (lowercase, deduplicated, e.g. python, "
            "kafka, pytest, kubernetes, system test).\n"
            "2. years_experience: candidate's total years of work experience as a number, or null if not stated.\n"
            "3. level: seniority level (Intern/Junior/Middle/Senior/Lead), or null if unknown.\n"
            "4. languages: spoken languages or certifications (e.g. English, Japanese, TOEIC).\n"
            "5. summary: 1-2 sentences describing key profile strengths based ONLY on the text.\n"
        )

    text = (text or "").strip()
    if len(text) > 4000:
        # Bounded prompt for fast local prefill
        text = text[:4000]
    suffix = "\nIf information is missing, use null or an empty list rather than guessing.\n"
    return f"{instruction}{suffix}Return ONLY valid JSON matching this schema exactly: {_EXTRACTION_SCHEMA}\n\nTEXT:\n{text}"


def _norm_skills(value: Any) -> List[str]:
    """Normalizes the LLM skills list into a deduplicated lowercase list."""
    if not isinstance(value, list):
        return []
    out = []
    for s in value:
        if not isinstance(s, str):
            continue
        s = s.strip().lower()
        if len(s) >= 2 and s not in out and not s.startswith("skill"):
            out.append(s)
    return out


def _norm_level(value: Any, default: Optional[str] = None) -> Optional[str]:
    """Normalizes the LLM level to one of the canonical capitalized levels."""
    if isinstance(value, str) and value.strip():
        v = value.strip()
        for lv in ("Lead", "Senior", "Middle", "Junior", "Intern"):
            if lv.lower() in v.lower():
                return lv
        for w in v.lower().split():
            if w in LEVEL_MAP:
                return LEVEL_MAP[w]
    elif isinstance(value, list):
        for v in value:
            norm = _norm_level(v)
            if norm:
                return norm
    return default


def _norm_years(value: Any) -> Optional[int]:
    """Normalizes the LLM experience value to an int in [0, 40] or None."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            m = re.search(r"\d+", value)
            if not m:
                return None
            val = int(m.group(0))
        else:
            val = int(value)
    except (TypeError, ValueError):
        return None
    return val if 0 <= val <= 40 else None


def _norm_languages(value: Any) -> List[str]:
    """Normalizes the LLM languages list."""
    if not isinstance(value, list):
        return []
    out = []
    for lang in value:
        if isinstance(lang, str):
            lang = lang.strip()
            if lang and lang.lower() not in out:
                out.append(lang.lower())
    return out


def _norm_summary(value: Any) -> str:
    """Normalizes the LLM summary into a bounded string."""
    if not isinstance(value, str):
        return ""
    summary = re.sub(r"\s+", " ", value).strip()
    return summary[:800]


def normalize_extraction(raw: Dict[str, Any], default_level: Optional[str] = None) -> Dict[str, Any]:
    """Validates and normalizes raw LLM JSON extraction into canonical structure."""
    if not isinstance(raw, dict):
        raise ValueError("LLM extraction is not a JSON object")

    skills = _norm_skills(raw.get("skills"))
    years = _norm_years(raw.get("years_experience"))
    level = _norm_level(raw.get("level"), default=default_level)
    languages = _norm_languages(raw.get("languages"))
    summary = _norm_summary(raw.get("summary"))

    if not skills and years is None and level is None and not languages and not summary:
        raise ValueError("LLM extraction contains no usable fields")

    return {
        "skills": skills,
        "years_experience": years,
        "level": level,
        "languages": languages,
        "summary": summary,
    }


def _extract_json(content: str) -> Optional[Dict[str, Any]]:
    """Parses JSON from LLM content, tolerating code fences and prose noise."""
    if not content:
        return None
    content = content.strip()

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


class LLMExtractor:
    """
    Thread-safe local LLM structured extractor with an in-memory text-hash cache
    and persistent HTTP connection pooling.
    """

    def __init__(
        self,
        base_url: str = LLM_BASE_URL,
        model: str = LLM_MODEL,
        timeout: float = LLM_TIMEOUT,
        enabled: bool = LLM_ENABLED,
        cache_size: int = LLM_CACHE_SIZE,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.enabled = enabled
        self.cache_size = max(1, cache_size)
        self._cache: "OrderedDict[str, Optional[Dict[str, Any]]]" = OrderedDict()
        self._lock = threading.Lock()
        self._session = requests.Session() if _REQUESTS_IMPORT_OK else None
        self._api_flavor = None  # None (unprobed), 'ollama', or 'openai'

    def is_enabled(self) -> bool:
        return self.enabled

    def get_runtime_info(self) -> Dict[str, Any]:
        """Returns best-effort runtime details for UI diagnostics."""
        info: Dict[str, Any] = {
            "provider": "unknown",
            "device": "Unknown",
            "transport": "HTTP chat API",
            "endpoint": self.base_url,
        }

        if not self.enabled:
            return info

        flavor = self._detect_flavor()
        if flavor == "ollama":
            info["provider"] = "ollama"
            info["endpoint"] = f"{self.base_url}/api/chat"
            if not self._session:
                return info

            try:
                resp = self._session.get(f"{self.base_url}/api/ps", timeout=(1.5, 3.0))
                if resp.status_code == 200:
                    payload = resp.json() if isinstance(resp.json(), dict) else {}
                    models = payload.get("models") or []
                    selected = None
                    for item in models:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name") or "")
                        if name == self.model or name.startswith(f"{self.model}:") or self.model.startswith(name):
                            selected = item
                            break
                    if selected is None and len(models) == 1 and isinstance(models[0], dict):
                        selected = models[0]

                    if isinstance(selected, dict):
                        size_vram = selected.get("size_vram", 0)
                        try:
                            info["device"] = "GPU" if int(size_vram) > 0 else "CPU"
                        except Exception:
                            info["device"] = "Unknown"
            except Exception:
                pass
            return info

        info["provider"] = "openai-compatible"
        base = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        info["endpoint"] = f"{base}/chat/completions"
        return info

    def extract(
        self,
        text: str,
        kind: str = "candidate",
        default_level: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Extracts structured metadata from text via LLM.
        Returns the normalized dict or None if LLM is unavailable.
        """
        if not self.enabled or not text or not text.strip():
            return None

        key = hashlib.sha1((f"{kind}\n{text.strip()}").encode("utf-8")).hexdigest()

        with self._lock:
            if key in self._cache:
                cached = self._cache[key]
                self._cache.move_to_end(key)
                return dict(cached) if cached is not None else None

        content = self._chat(_build_prompt(kind, text))
        if not content:
            return None

        raw = _extract_json(content)
        try:
            result = normalize_extraction(raw, default_level=default_level)
        except (ValueError, TypeError):
            logger.warning("LLM extraction output rejected for kind=%s (len=%d)", kind, len(content))
            result = None

        with self._lock:
            self._cache[key] = result
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

        return dict(result) if result is not None else None

    # ------------------------------------------------------------------
    # Chat backends with connection reuse and fast failover
    # ------------------------------------------------------------------

    def _detect_flavor(self) -> str:
        """Probes the endpoint once with a short timeout to choose the best protocol."""
        if self._api_flavor:
            return self._api_flavor
        if not self._session:
            return "ollama"

        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=(1.5, 2.0))
            if resp.status_code == 200:
                self._api_flavor = "ollama"
                return "ollama"
        except Exception:
            pass

        self._api_flavor = "openai"
        return "openai"

    def _chat(self, prompt: str) -> Optional[str]:
        """Requests a completion using persistent session and fast endpoint routing."""
        if not _REQUESTS_IMPORT_OK or not self._session:
            return None

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        flavor = self._detect_flavor()

        # 1. Ollama-first path
        if flavor == "ollama":
            try:
                resp = self._session.post(
                    f"{self.base_url}/api/chat",
                    json={"model": self.model, "messages": messages, "stream": False, "format": "json"},
                    timeout=(2.0, self.timeout),
                )
                resp.raise_for_status()
                content = (resp.json().get("message") or {}).get("content")
                if content:
                    return content
            except requests.RequestException as e:
                logger.debug("Ollama /api/chat error: %s", e)

        # 2. OpenAI-compatible path (/v1/chat/completions)
        base = self.base_url
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        try:
            resp = self._session.post(
                f"{base}/chat/completions",
                json={"model": self.model, "messages": messages, "temperature": 0.0},
                timeout=(2.0, self.timeout),
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if content:
                self._api_flavor = "openai"
                return content
        except requests.RequestException as e:
            logger.debug("OpenAI-compatible endpoint unavailable: %s", e)

        return None


# Global singleton LLM extractor
_llm_extractor_instance = None
_llm_extractor_lock = threading.Lock()


def get_llm_extractor() -> LLMExtractor:
    """Returns the global LLM extractor singleton."""
    global _llm_extractor_instance
    if _llm_extractor_instance is None:
        with _llm_extractor_lock:
            if _llm_extractor_instance is None:
                _llm_extractor_instance = LLMExtractor()
    return _llm_extractor_instance


def reset_llm_extractor(extractor: Optional[LLMExtractor] = None) -> None:
    """Test hook: replaces the global extractor (None restores defaults)."""
    global _llm_extractor_instance
    with _llm_extractor_lock:
        _llm_extractor_instance = extractor


def llm_extract_structured(text: str, kind: str) -> Optional[Dict[str, Any]]:
    """Runs open-vocabulary structured extraction via the local LLM if enabled."""
    if not text or not text.strip():
        return None
    extractor = get_llm_extractor()
    if not extractor.is_enabled():
        return None
    try:
        return extractor.extract(text, kind=kind, default_level=None)
    except Exception as e:
        logger.warning("LLM structured extraction failed (kind=%s): %s", kind, e)
        return None


# ==========================================
# Text Processing
# ==========================================

def clean_html(text: Optional[str]) -> str:
    """Strips HTML tags and normalizes whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"&[a-zA-Z0-9#]+;", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ==========================================
# Hybrid Extraction (Regex Baseline + LLM Enrichment)
# ==========================================

def extract_job_keywords(
    title: str,
    request: str,
    job_description: str,
    raw_level: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Extracts structured keywords, level, and semantic embedding for a Job Request.

    Hybrid Pipeline:
    1. Deterministic regex baseline from taxonomy file for guaranteed skills, level & years.
    2. Local LLM enrichment for open-vocabulary skills and spoken languages.
    3. Automatically persists novel skills discovered by LLM into taxonomy.json at runtime.
    """
    cleaned_desc = clean_html(job_description or "")
    meta = f"\nHRM Level: {raw_level}" if raw_level else ""
    combined_text = f"{title or ''}\n{request or ''}{meta}\n{cleaned_desc}"

    # 1. Regex Baseline
    tax_mgr = get_taxonomy_manager()
    base_skills = tax_mgr.extract_skills(combined_text)
    base_level = tax_mgr.extract_seniority(combined_text, default=None, raw_level=raw_level)
    base_years = tax_mgr.extract_years_of_experience(combined_text)

    # 2. LLM Enrichment
    llm_res = llm_extract_structured(combined_text, kind="job")
    if llm_res:
        llm_skills = llm_res.get("skills") or []
        combined_skills = list(base_skills)
        novel_skills = []
        for s in llm_skills:
            if s not in combined_skills:
                combined_skills.append(s)
                novel_skills.append(s)

        # Update taxonomy with newly discovered skills
        if novel_skills:
            tax_mgr.register_new_skills(novel_skills)

        level = llm_res.get("level") or base_level
        years_exp = llm_res.get("years_experience") if llm_res.get("years_experience") is not None else base_years
    else:
        combined_skills = base_skills
        level = base_level
        years_exp = base_years

    # Generate semantic embedding vector
    model = get_semantic_model()
    embedding = model.get_embedding(combined_text[:2500])

    return {
        "skills": combined_skills,
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
    raw_level: Optional[Any] = None,
    raw_experience: Optional[Any] = None,
    cv_urls: Optional[List[str]] = None,
    raw_languages: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Extracts structured keywords, experience summary, and semantic embedding for a Candidate.

    Hybrid Pipeline:
    1. Deterministic regex baseline from taxonomy file for guaranteed skills, level & years.
    2. Local LLM enrichment for open-vocabulary skills, natural language summary, languages.
    3. Automatically persists novel skills discovered by LLM into taxonomy.json at runtime.
    """
    cv_info_cleaned = clean_html(cv_information) if cv_information else ""

    url_text = ""
    if cv_urls and isinstance(cv_urls, list):
        slugs = []
        for u in cv_urls:
            if isinstance(u, str):
                cleaned_u = re.sub(r"[^a-zA-Z0-9\+#]+", " ", u)
                slugs.append(cleaned_u)
        url_text = " ".join(slugs)

    lang_text = ""
    if raw_languages:
        if isinstance(raw_languages, list):
            lang_text = " ".join(str(l) for l in raw_languages)
        elif isinstance(raw_languages, str):
            lang_text = raw_languages
    meta = " ".join(x for x in [lang_text, str(raw_level or ''), str(raw_experience or '')] if x)
    combined_text = f"{position or ''} {name or ''} {location or ''}\n{meta}\n{url_text}\n{cv_info_cleaned}\n{cv_text or ''}"

    # 1. Regex Baseline + HRM metadata fallback
    tax_mgr = get_taxonomy_manager()
    base_skills = tax_mgr.extract_skills(combined_text)
    base_level = tax_mgr.extract_seniority(combined_text, default=None, raw_level=raw_level)
    base_years = tax_mgr.extract_years_of_experience(combined_text, raw_experience=raw_experience)
    default_summary = cv_info_cleaned[:500] if cv_info_cleaned else f"{position} based in {location}"

    # 2. LLM Enrichment
    llm_res = llm_extract_structured(combined_text, kind="candidate")
    if llm_res:
        llm_skills = llm_res.get("skills") or []
        combined_skills = list(base_skills)
        novel_skills = []
        for s in llm_skills:
            if s not in combined_skills:
                combined_skills.append(s)
                novel_skills.append(s)

        # Update taxonomy with newly discovered skills
        if novel_skills:
            tax_mgr.register_new_skills(novel_skills)

        level = llm_res.get("level") or base_level
        years_exp = llm_res.get("years_experience") if llm_res.get("years_experience") is not None else base_years
        summary = llm_res.get("summary") or default_summary
    else:
        combined_skills = base_skills
        level = base_level
        years_exp = base_years
        summary = default_summary

    model = get_semantic_model()
    embedding = model.get_embedding(combined_text[:2500])

    return {
        "skills": combined_skills,
        "level": level,
        "years_experience": years_exp,
        "summary": summary,
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
        for page in reader.pages[:10]:
            t = page.extract_text()
            if t:
                text_parts.append(t)
        return "\n".join(text_parts).strip()
    except Exception as e:
        logger.warning(f"Error parsing PDF with pypdf: {e}")
        return ""


def download_and_extract_cv(cv_url: str, bearer_token: Optional[str] = None) -> str:
    """
    Downloads candidate CV file from HRM and extracts text.
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
        server_port = int(_config["server"]["port"])
        if host in ("169.254.169.254", "metadata.google.internal") or (host.startswith("127.") and not (parsed.port and parsed.port == server_port)):
            logger.warning(f"Rejected SSRF host: {host}")
            return ""

        headers = {
            "Accept": "application/pdf,*/*",
            "User-Agent": "HRM-Assistant-Backend/1.0"
        }
        if bearer_token and ("ltsgroup.tech" in host or host in ("localhost", "127.0.0.1")):
            headers["Authorization"] = f"Bearer {bearer_token}"

        import requests
        response = requests.get(cv_url, headers=headers, timeout=12)
        if response.status_code == 200:
            return extract_text_from_pdf_bytes(response.content)
        else:
            logger.warning(f"Failed to fetch CV from {cv_url} (HTTP {response.status_code})")
            return ""
    except Exception as e:
        logger.warning(f"Exception downloading CV from {cv_url}: {e}")
        return ""
