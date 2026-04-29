from .knowledge_base import KnowledgeBase
from typing import List, Dict

class Retriever:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def get_solutions(self, error_msg: str) -> List[Dict]:
        """获取相似错误的解决方案"""
        cases = self.kb.query_similar_errors(error_msg)
        
        # 过滤高相似度结果（距离越小越相似）
        relevant_cases = [c for c in cases if c["distance"] < 0.7]
        
        return relevant_cases

    def format_prompt(self, error_msg: str) -> str:
        """格式化检索结果为LLM提示词"""
        cases = self.get_solutions(error_msg)
        
        if not cases:
            return "无历史解决方案参考"
        
        prompt = "以下是相似构建错误的历史解决方案：\n"
        for i, case in enumerate(cases, 1):
            prompt += f"\n案例 {i} (项目: {case['project']}):\n"
            prompt += f"错误: {case['error'][:200]}...\n"
            prompt += f"解决方案: {case['solution']}\n"
        
        return prompt