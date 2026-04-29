import os
from typing import List

class DocumentProcessor:
    @staticmethod
    def process_build_log(log_path: str) -> List[str]:
        """处理构建日志，提取错误片段"""
        if not os.path.exists(log_path):
            return []
        
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # 简单的错误提取逻辑（可扩展）
        error_keywords = ["error:", "undefined reference", "fatal error", "cannot find"]
        lines = content.split('\n')
        error_lines = [line.strip() for line in lines if any(k in line.lower() for k in error_keywords)]
        
        return error_lines

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 500) -> List[str]:
        """文本分块"""
        words = text.split()
        chunks = []
        current_chunk = []
        
        for word in words:
            current_chunk.append(word)
            if len(' '.join(current_chunk)) >= chunk_size:
                chunks.append(' '.join(current_chunk))
                current_chunk = []
        
        if current_chunk:
            chunks.append(' '.join(current_chunk))
        
        return chunks