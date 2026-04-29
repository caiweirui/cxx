import os
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from typing import List, Dict
import json

class KnowledgeBase:
    def __init__(self, base_path: str = "./data/knowledge_base"):
        self.base_path = os.path.abspath(base_path)
        os.makedirs(self.base_path, exist_ok=True)
        
        # 本地嵌入模型（无需API）
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        
        # FAISS索引文件路径
        self.index_path = os.path.join(self.base_path, "faiss_index.bin")
        self.metadata_path = os.path.join(self.base_path, "metadata.json")
        
        # 初始化或加载索引
        self.dimension = 384  # all-MiniLM-L6-v2的维度
        self.index = None
        self.metadata = []
        self._load_or_create()

    def _load_or_create(self):
        if os.path.exists(self.index_path) and os.path.exists(self.metadata_path):
            self.index = faiss.read_index(self.index_path)
            with open(self.metadata_path, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)
        else:
            self.index = faiss.IndexFlatL2(self.dimension)

    def _save(self):
        faiss.write_index(self.index, self.index_path)
        with open(self.metadata_path, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

    def add_error_case(self, error_id: str, error_msg: str, solution: str, project: str):
        """添加构建错误案例到知识库"""
        # 向量化
        embedding = self.model.encode([error_msg])[0].astype('float32')
        
        # 添加到FAISS
        self.index.add(np.array([embedding]))
        
        # 保存元数据
        self.metadata.append({
            "id": error_id,
            "error": error_msg,
            "solution": solution,
            "project": project
        })
        
        self._save()
        print(f"✅ 案例已添加到知识库 (ID: {error_id})")

    def query_similar_errors(self, error_msg: str, top_k: int = 3) -> List[Dict]:
        """检索相似错误案例"""
        if self.index.ntotal == 0:
            return []
        
        # 向量化查询
        embedding = self.model.encode([error_msg])[0].astype('float32')
        
        # FAISS检索
        distances, indices = self.index.search(np.array([embedding]), top_k)
        
        # 格式化结果
        cases = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.metadata):
                cases.append({
                    "id": self.metadata[idx]["id"],
                    "error": self.metadata[idx]["error"],
                    "solution": self.metadata[idx]["solution"],
                    "project": self.metadata[idx]["project"],
                    "distance": float(distances[0][i])
                })
        return cases

    def get_all_cases(self) -> int:
        """获取知识库总案例数"""
        return self.index.ntotal