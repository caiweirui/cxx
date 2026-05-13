import json
import os
import hashlib
from typing import Any, Optional

class LLMCache:
    def __init__(self, cache_dir: str = "./data/cache"):
        self.cache_dir = os.path.abspath(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    @staticmethod
    def make_key(model: str, system_prompt: str, user_prompt: str, extra: Optional[Any] = None) -> str:
        payload = {
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "extra": extra,
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        path = self._path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("content")
        except Exception:
            return None

    def set(self, key: str, content: str, meta: Optional[dict] = None):
        path = self._path(key)
        data = {
            "content": content,
            "meta": meta or {},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)