import os
from typing import Dict, Any, Tuple

from .utils.build_system_parser import detect_build_system
from .dependency_parser import extract_dependencies
from .document_parser import collect_docs
from .environment_parser import detect_environment

def parser(project_path: str) -> Tuple[str, Dict[str, Any], str]:
    """
    兼容原有接口：
        return build_system, deps, docs

    其中：
        build_system -> 主构建系统字符串
        deps         -> 结构化依赖信息 dict
        docs         -> 文档摘要字符串
    """
    project_path = os.path.abspath(project_path)

    build_info = detect_build_system(project_path)
    build_system = build_info.get("primary", "Unknown")
    env_info = detect_environment(project_path)

    docs_text, selected_files, doc_hints = collect_docs(project_path, build_system=build_system)
    dependencies = extract_dependencies(project_path, docs_text=docs_text)

    deps = {
        "build_system": build_info,
        "environment": env_info,
        "dependencies": dependencies,
        "doc_hints": doc_hints,
        "selected_docs": selected_files,
    }

    # 给下游一个更适合 prompt 的文档摘要
    docs_summary = docs_text
    if not docs_summary:
        docs_summary = "无可用 README/INSTALL/BUILDING 文档摘要"

    return build_system, deps, docs_summary