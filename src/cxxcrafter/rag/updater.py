from .knowledge_base import KnowledgeBase
import uuid
from datetime import datetime

class KnowledgeUpdater:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def add_success_case(self, error_msg: str, solution: str, project: str):
        """添加成功修复的案例到知识库"""
        error_id = f"case-{uuid.uuid4().hex[:8]}"
        self.kb.add_error_case(
            error_id=error_id,
            error_msg=error_msg,
            solution=solution,
            project=project
        )
        print(f"✅ 案例已添加到知识库 (ID: {error_id})")

    def add_from_log(self, log_path: str, solution: str, project: str):
        """从构建日志中提取错误并添加"""
        from .document_processor import DocumentProcessor
        
        errors = DocumentProcessor.process_build_log(log_path)
        if errors:
            error_text = '\n'.join(errors)
            self.add_success_case(error_text, solution, project)