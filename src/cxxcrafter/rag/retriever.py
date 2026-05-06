from typing import List, Dict

from .knowledge_base import KnowledgeBase

class Retriever:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def get_solutions(self, error_msg: str) -> List[Dict]:
        cases = self.kb.query_similar_errors(error_msg, top_k=5)
        cases = sorted(cases, key=lambda x: x.get("distance", 999.0))
        return [c for c in cases if c.get("distance", 999.0) < 1.5]

    def format_prompt(self, error_msg: str) -> str:
        cases = self.get_solutions(error_msg)

        if not cases:
            return "无历史解决方案参考"

        prompt = "以下是相似构建错误的历史解决方案：\n"
        for i, case in enumerate(cases, 1):
            prompt += f"\n案例 {i} (项目: {case['project']}):\n"
            prompt += f"错误: {case['error'][:200]}...\n"
            prompt += f"解决方案: {case['solution']}\n"
        return prompt