"""Settings shared by the CLI and web service, overridable via environment variables."""
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional
import requests


def _get_env_path(name: str, default: str) -> Path:
    val = os.getenv(name)
    if val:
        return Path(val).resolve()
    return (Path(__file__).resolve().parent.parent / default).resolve()


@dataclass
class Settings:
    ollama_host: str = os.getenv('SENSAI_OLLAMA_HOST') or os.getenv('OLLAMA_HOST', 'http://localhost:11434')
    default_model: str = os.getenv('SENSAI_MODEL') or os.getenv('OLLAMA_MODEL', 'llama3.2:1b')
    mode: str = os.getenv('SENSAI_MODE', 'ollama')  # 'ollama' or 'stub'
    sessions_db: Path = _get_env_path('SENSAI_DB', 'sensai_sessions.db')
    events_db: Path = _get_env_path('SENSAI_EVENTS_DB', 'sensai_events.db')
    prompts_db: Path = _get_env_path('SENSAI_PROMPTS_DB', 'sensai_prompts.db')
    artifacts_db: Path = _get_env_path('SENSAI_ARTIFACTS_DB', 'sensai_artifacts.db')
    snapshots_db: Path = _get_env_path('SENSAI_SNAPSHOTS_DB', 'sensai_snapshots.db')
    workspace_dir: Optional[Path] = Path(os.getenv('SENSAI_WORKSPACE')).resolve() if os.getenv('SENSAI_WORKSPACE') else None
    retrieval_collection: str = os.getenv('SENSAI_RETRIEVAL_COLLECTION', 'default')
    retrieval_model: str = os.getenv('SENSAI_EMBEDDING_MODEL', 'nomic-embed-text')
    retrieval_chunk_size: int = int(os.getenv('SENSAI_CHUNK_SIZE', '20'))
    retrieval_chunk_overlap: int = int(os.getenv('SENSAI_CHUNK_OVERLAP', '5'))
    retrieval_top_k: int = int(os.getenv('SENSAI_RETRIEVAL_TOP_K', '5'))
    retrieval_rerank_model: str = os.getenv('SENSAI_RERANK_MODEL', 'qwen3:0.6b')
    retrieval_rerank_threshold: float = float(os.getenv('SENSAI_RERANK_THRESHOLD', '3.0'))
    retrieval_rerank_top_n: int = int(os.getenv('SENSAI_RERANK_TOP_N', '10'))
    retrieval_rewrite_model: str = os.getenv('SENSAI_REWRITE_MODEL', 'qwen3:0.6b')
    semantic_cache_enabled: bool = os.getenv('SENSAI_SEMANTIC_CACHE_ENABLED', 'true').lower() in ('1', 'true', 'yes', 'on')
    semantic_cache_db: Path = _get_env_path('SENSAI_SEMANTIC_CACHE_DB', 'retrieval/semantic_cache.sqlite3')
    semantic_cache_threshold: float = float(os.getenv('SENSAI_SEMANTIC_CACHE_THRESHOLD', '0.92'))
    semantic_cache_embedding_model: str = os.getenv('SENSAI_SEMANTIC_CACHE_EMBEDDING_MODEL', 'nomic-embed-text')
    semantic_cache_max_age_seconds: Optional[float] = (float(os.environ['SENSAI_SEMANTIC_CACHE_MAX_AGE_SECONDS']) if os.getenv('SENSAI_SEMANTIC_CACHE_MAX_AGE_SECONDS') else None)

    @property
    def clean_ollama_host(self) -> str:
        return self.ollama_host.rstrip('/')

    _last_alive_check: float = 0.0
    _last_alive_result: bool = False

    def check_ollama_alive(self, timeout: float = 0.15) -> bool:
        """Check if the configured Ollama server is reachable, cached for 3 seconds."""
        import time
        now = time.time()
        if now - self._last_alive_check < 3.0:
            return self._last_alive_result

        self._last_alive_check = now
        try:
            resp = requests.get(f"{self.clean_ollama_host}/api/version", timeout=timeout)
            self._last_alive_result = (resp.status_code == 200)
        except Exception:
            try:
                resp = requests.get(self.clean_ollama_host, timeout=timeout)
                self._last_alive_result = (resp.status_code == 200)
            except Exception:
                self._last_alive_result = False
        return self._last_alive_result


settings = Settings()
