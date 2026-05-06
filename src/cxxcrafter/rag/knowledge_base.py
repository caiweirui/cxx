import os
import json
from typing import List, Dict

import numpy as np

try:
    import faiss
except Exception:
    faiss = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

class KnowledgeBase:
    def __init__(self, base_path: str = "./data/knowledge_base"):
        self.base_path = os.path.abspath(base_path)
        os.makedirs(self.base_path, exist_ok=True)

        self.index_path = os.path.join(self.base_path, "faiss_index.bin")
        self.metadata_path = os.path.join(self.base_path, "metadata.json")
        self.dimension = 384

        self.model = None
        if SentenceTransformer is not None:
            try:
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                self.model = None

        self.index = None
        self.metadata: List[Dict] = []
        self._load_or_create()

    def _load_or_create(self):
        if faiss is None:
            self.index = None
            self.metadata = self._load_metadata()
            return

        if os.path.exists(self.index_path) and os.path.exists(self.metadata_path):
            try:
                self.index = faiss.read_index(self.index_path)
                self.metadata = self._load_metadata()
                return
            except Exception:
                pass

        self.index = faiss.IndexFlatL2(self.dimension)
        self.metadata = []

    def _load_metadata(self) -> List[Dict]:
        if not os.path.exists(self.metadata_path):
            return []
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self):
        if faiss is not None and self.index is not None:
            try:
                faiss.write_index(self.index, self.index_path)
            except Exception:
                pass
        with open(self.metadata_path, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

    def _embed(self, text: str) -> np.ndarray:
        text = text or ""
        if self.model is not None:
            try:
                vec = self.model.encode([text])[0].astype("float32")
                return vec
            except Exception:
                pass

        import hashlib
        vec = np.zeros(self.dimension, dtype="float32")
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
        for i, b in enumerate(digest):
            vec[i % self.dimension] += float(b) / 255.0
        return vec

    def add_error_case(self, error_id: str, error_msg: str, solution: str, project: str):
        embedding = self._embed(error_msg)
        if faiss is not None and self.index is not None:
            self.index.add(np.array([embedding], dtype="float32"))

        self.metadata.append({
            "id": error_id,
            "error": error_msg,
            "solution": solution,
            "project": project,
        })
        self._save()

    def query_similar_errors(self, error_msg: str, top_k: int = 3) -> List[Dict]:
        if not self.metadata:
            return []

        if faiss is None or self.index is None or self.index.ntotal == 0:
            target = error_msg.lower()
            scored = []
            for item in self.metadata:
                err = item.get("error", "").lower()
                score = 0
                for token in target.split()[:20]:
                    if token and token in err:
                        score += 1
                scored.append((score, item))
            scored.sort(key=lambda x: x[0], reverse=True)
            result = []
            for score, item in scored[:top_k]:
                result.append({
                    "id": item["id"],
                    "error": item["error"],
                    "solution": item["solution"],
                    "project": item["project"],
                    "distance": float(max(0, 10 - score)),
                })
            return result

        embedding = self._embed(error_msg)
        distances, indices = self.index.search(np.array([embedding], dtype="float32"), top_k)
        cases = []
        for i, idx in enumerate(indices[0]):
            if 0 <= idx < len(self.metadata):
                item = self.metadata[idx]
                cases.append({
                    "id": item["id"],
                    "error": item["error"],
                    "solution": item["solution"],
                    "project": item["project"],
                    "distance": float(distances[0][i]),
                })
        return cases

    def get_all_cases(self) -> int:
        return len(self.metadata)