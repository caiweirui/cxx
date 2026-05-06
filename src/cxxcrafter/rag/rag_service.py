from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .document_processor import DocumentProcessor
from .knowledge_base import KnowledgeBase
from .retriever import Retriever
from .updater import KnowledgeUpdater

class RAGService:
    """
    轻量 RAG 服务：
    1. 构建项目文档上下文（README/INSTALL/BUILDING/...）
    2. 根据错误日志检索历史相似案例
    3. 将修复经验写回知识库
    4. 将成功构建经验写回知识库（用于后续检索）
    """

    DOC_NAME_PATTERNS = (
        "README*",
        "README.*",
        "INSTALL*",
        "INSTALL.*",
        "BUILD*",
        "BUILD.*",
        "CONTRIBUTING*",
        "CONTRIBUTING.*",
        "HOWTO*",
        "HOWTO.*",
        "docs/*.md",
        "docs/*.rst",
        "docs/*.txt",
        "docs/**/*README*",
        "docs/**/*INSTALL*",
        "docs/**/*BUILD*",
    )

    DOC_KEYWORDS = (
        "cmake",
        "make",
        "ninja",
        "autotools",
        "configure",
        "pkg-config",
        "build",
        "install",
        "dependency",
        "dependencies",
        "requirements",
        "ubuntu",
        "debian",
        "centos",
        "fedora",
        "linux",
        "qt",
        "x11",
        "xcb",
        "vcpkg",
        "conan",
        "test",
        "ctest",
    )

    def __init__(
        self,
        base_path: str = "./data/knowledge_base",
        enabled: bool = True,
        max_context_chars: int = 6000,
        max_docs: int = 8,
        max_chunks_per_doc: int = 3,
    ) -> None:
        self.enabled = enabled
        self.max_context_chars = max_context_chars
        self.max_docs = max_docs
        self.max_chunks_per_doc = max_chunks_per_doc

        self.kb = KnowledgeBase(base_path=base_path)
        self.retriever = Retriever(self.kb)
        self.updater = KnowledgeUpdater(self.kb)

        self._doc_cache: Dict[str, str] = {}
        self._error_cache: Dict[str, str] = {}

    # ============================================================
    # Public API
    # ============================================================
    def build_project_context(
        self,
        project_path: str,
        files_sample: Optional[Sequence[str]] = None,
    ) -> str:
        """
        为当前项目提取“文档增强上下文”。
        返回给 BuildAgent 使用。
        """
        if not self.enabled or not project_path:
            return ""

        root = Path(project_path).resolve()
        if not root.exists() or not root.is_dir():
            return ""

        cache_key = f"docs::{root}"
        if cache_key in self._doc_cache:
            return self._doc_cache[cache_key]

        candidates = self._discover_doc_files(root, files_sample=files_sample)
        if not candidates:
            self._doc_cache[cache_key] = ""
            return ""

        sections: List[str] = []
        used_chars = 0

        for path in candidates[: self.max_docs]:
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            content = self._normalize_text(content)
            if not content.strip():
                continue

            chunks = DocumentProcessor.chunk_text(content, chunk_size=700)
            if not chunks:
                continue

            ranked = sorted(
                ((self._score_text(chunk), chunk) for chunk in chunks),
                key=lambda x: x[0],
                reverse=True,
            )

            picked = []
            for score, chunk in ranked[: self.max_chunks_per_doc]:
                if score <= 0 and not picked:
                    picked.append(chunk)
                elif score > 0:
                    picked.append(chunk)

            if not picked:
                continue

            rel_name = self._safe_relative_path(path, root)
            doc_section = [f"[DOC] {rel_name}"]
            for i, chunk in enumerate(picked, 1):
                doc_section.append(f"  - snippet {i}: {self._shrink_text(chunk, 900)}")

            block = "\n".join(doc_section).strip()
            if block:
                sections.append(block)
                used_chars += len(block)
                if used_chars >= self.max_context_chars:
                    break

        result = "\n\n".join(sections).strip()
        result = self._shrink_text(result, self.max_context_chars)
        self._doc_cache[cache_key] = result
        return result

    def build_error_context(
        self,
        error_text: str,
        project_path: Optional[str] = None,
        files_sample: Optional[Sequence[str]] = None,
        top_k: int = 5,
    ) -> str:
        """
        为错误分析构建增强上下文：
        1. 历史相似错误案例
        2. 项目本地文档上下文
        """
        if not self.enabled:
            return ""

        cache_key = f"err::{hash((error_text or '')[:3000])}::{project_path or ''}"
        if cache_key in self._error_cache:
            return self._error_cache[cache_key]

        parts: List[str] = []

        doc_context = ""
        if project_path:
            doc_context = self.build_project_context(project_path, files_sample=files_sample)
        if doc_context:
            parts.append("项目文档上下文：\n" + doc_context)

        history_context = ""
        error_text = (error_text or "").strip()
        if error_text:
            try:
                history_context = self.retriever.format_prompt(error_text)
            except Exception:
                history_context = ""

        if history_context:
            parts.append("历史相似错误案例：\n" + history_context)

        result = "\n\n".join(parts).strip()
        result = self._shrink_text(result, self.max_context_chars)
        self._error_cache[cache_key] = result
        return result

    def record_case(self, error_text: str, solution: str, project: str) -> None:
        """
        将一个错误-修复经验写回知识库。
        """
        if not self.enabled:
            return

        error_text = (error_text or "").strip()
        solution = (solution or "").strip()
        project = (project or "").strip()

        if not error_text or not solution:
            return

        try:
            self.updater.add_success_case(
                error_msg=self._shrink_text(error_text, 4000),
                solution=self._shrink_text(solution, 8000),
                project=project or "unknown",
            )
        except Exception:
            pass

    def record_success_case(self, success_signature: str, solution: str, project: str) -> None:
        """
        将一个“成功构建案例”写回知识库。
        注意：仍然复用同一张知识库表，只是前缀做了 SUCCESS_CASE 标记。
        """
        if not self.enabled:
            return

        success_signature = (success_signature or "").strip()
        solution = (solution or "").strip()
        project = (project or "").strip()

        if not success_signature or not solution:
            return

        try:
            self.updater.add_success_case(
                error_msg=self._shrink_text(f"[SUCCESS_CASE]\n{success_signature}", 4000),
                solution=self._shrink_text(solution, 10000),
                project=project or "unknown",
            )
        except Exception:
            pass

    def record_from_log(self, log_path: str, solution: str, project: str) -> None:
        """
        从日志中抽取错误并写回知识库。
        """
        if not self.enabled:
            return

        try:
            self.updater.add_from_log(log_path=log_path, solution=solution, project=project)
        except Exception:
            pass

    # ============================================================
    # Internal helpers
    # ============================================================
    def _discover_doc_files(
        self,
        root: Path,
        files_sample: Optional[Sequence[str]] = None,
    ) -> List[Path]:
        candidates: Dict[str, Path] = {}

        if files_sample:
            for rel in files_sample:
                p = root / rel
                if p.is_file() and self._looks_like_doc(p):
                    candidates[str(p.resolve())] = p

        for pattern in self.DOC_NAME_PATTERNS:
            try:
                for p in root.rglob(pattern):
                    if p.is_file() and self._looks_like_doc(p):
                        candidates[str(p.resolve())] = p
            except Exception:
                continue

        result = list(candidates.values())
        result.sort(key=lambda p: (self._doc_priority(p), len(str(p))))
        return result

    def _looks_like_doc(self, path: Path) -> bool:
        name = path.name.lower()
        suffix = path.suffix.lower()

        if suffix not in {".md", ".rst", ".txt", ".markdown", ""}:
            return False

        if any(part.startswith(".") for part in path.parts):
            return False

        doc_markers = ("readme", "install", "build", "contributing", "howto", "docs")
        return any(marker in name or marker in str(path).lower() for marker in doc_markers)

    def _doc_priority(self, path: Path) -> int:
        name = path.name.lower()
        parent = str(path.parent).lower()
        score = 0

        if "readme" in name:
            score += 100
        if "install" in name:
            score += 90
        if "build" in name:
            score += 80
        if "contributing" in name:
            score += 50
        if "howto" in name:
            score += 40
        if "docs" in parent:
            score += 10

        score -= len(str(path).split("/"))
        return score

    def _score_text(self, text: str) -> int:
        low = (text or "").lower()
        score = 0
        for kw in self.DOC_KEYWORDS:
            if kw in low:
                score += 1
        return score

    def _normalize_text(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        return text

    def _shrink_text(self, text: str, max_chars: int) -> str:
        text = text or ""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    def _safe_relative_path(self, path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root))
        except Exception:
            return path.name