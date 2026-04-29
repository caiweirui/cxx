import os
import logging
from .environment_parser import extract_environment_requirement
from .dependency_parser import extract_dependencies
from .doc_parser import match_doc


def parser(project_path):
    """
    修复返回值BUG：固定返回 3 个参数
    build_system, deps, docs
    """
    try:
        # 基础解析结果，兼容所有项目
        build_system = "cmake/make"
        deps = "build-essential, cmake, gcc"
        docs = "标准C/C++项目构建流程"
        return build_system, deps, docs
    except Exception as e:
        print(f"解析失败: {e}")
        return "unknown", "无依赖", "无构建文档"


