import os
import re
from typing import Dict, List, Tuple

DOC_NAMES = [
    "README", "README.md", "README.txt",
    "INSTALL", "INSTALL.md", "INSTALL.txt",
    "BUILDING", "BUILDING.md", "BUILDING.txt",
    "CONTRIBUTING", "CONTRIBUTING.md",
    "docs", "Doc", "doc",
]

def _read_text(path: str, limit: int = 30000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except Exception:
        return ""

def _score_doc(text: str, build_system: str = "") -> int:
    score = 0
    lower = text.lower()
    keywords = [
        "build", "compile", "install", "cmake", "make", "meson",
        "configure", "ninja", "bazel", "autotools", "dependencies",
        "prerequisites", "requirements"
    ]
    for k in keywords:
        if k in lower:
            score += 1

    if build_system and build_system.lower() in lower:
        score += 3
    return score

def _extract_hints(text: str) -> List[str]:
    hints = []
    patterns = [
        r"apt(?:-get)?\s+install\s+([^\n\r;]+)",
        r"dnf\s+install\s+([^\n\r;]+)",
        r"yum\s+install\s+([^\n\r;]+)",
        r"pacman\s+-S\s+([^\n\r;]+)",
        r"brew\s+install\s+([^\n\r;]+)",
        r"cmake\s+[^ \n\r]*",
        r"meson\s+setup\s+[^ \n\r]*",
        r"\.\/configure[^\n\r]*",
        r"make[^\n\r]*",
        r"ninja[^\n\r]*",
        r"bazel\s+build[^\n\r]*",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I | re.M):
            item = m.group(0).strip()
            if item and item not in hints:
                hints.append(item)
    return hints[:30]

def collect_docs(project_dir: str, build_system: str = "") -> Tuple[str, List[str], List[str]]:
    """
    返回:
      docs_text: 选中的文档摘要文本
      selected_files: 文档路径列表
      hints: 提取出的构建提示
    """
    project_dir = os.path.abspath(project_dir)
    candidates = []

    for root, _, files in os.walk(project_dir):
        for file in files:
            low = file.lower()
            if (
                low.startswith("readme") or low.startswith("install") or low.startswith("building")
                or low.startswith("contributing")
                or file in DOC_NAMES
            ):
                path = os.path.join(root, file)
                text = _read_text(path)
                score = _score_doc(text, build_system=build_system)
                candidates.append((score, path, text))

    if not candidates:
        return "", [], []

    candidates.sort(key=lambda x: x[0], reverse=True)
    selected = candidates[:3]

    selected_files = [p for _, p, _ in selected]
    merged_text = "\n\n".join([t for _, _, t in selected])[:25000]
    hints = _extract_hints(merged_text)

    return merged_text, selected_files, hints