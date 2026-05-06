import os
from typing import Dict, Any

from .build_root_detector import BuildRootDetector
from .dependency_profiler import DependencyProfiler

class ProjectParser:
    """
    项目解析器（优化版）

    作用：
    1. 扫描项目基础信息
    2. 自动识别构建系统 / 构建根目录
    3. 自动推断项目级系统依赖
    4. 将结果写回 parsed_project，供 Dockerfile 生成器使用
    """

    def __init__(self, project_path: str, compatibility_mode: bool = True):
        self.project_path = os.path.abspath(project_path)
        self.compatibility_mode = compatibility_mode

    def _scan_basic_info(self) -> Dict[str, Any]:
        file_count = 0
        dir_count = 0
        extensions = {}

        for root, dirs, files in os.walk(self.project_path):
            dir_count += len(dirs)
            file_count += len(files)
            for f in files:
                ext = os.path.splitext(f)[1].lower() or "[no_ext]"
                extensions[ext] = extensions.get(ext, 0) + 1

        return {
            "project_path": self.project_path,
            "file_count": file_count,
            "dir_count": dir_count,
            "extensions": dict(sorted(extensions.items(), key=lambda x: x[1], reverse=True)),
        }

    def _detect_build_root(self) -> Dict[str, Any]:
        detector = BuildRootDetector(self.project_path)
        return detector.detect()

    def _profile_dependencies(self) -> Dict[str, Any]:
        profiler = DependencyProfiler(
            self.project_path,
            compatibility_mode=self.compatibility_mode,
        )
        return profiler.profile()

    def parse(self) -> Dict[str, Any]:
        """
        返回一个字典，至少包含：
        - project_path
        - build_system
        - build_root_info
        - cmake_source_dir_rel
        - dependency_profile
        """
        basic_info = self._scan_basic_info()
        build_root_info = self._detect_build_root()
        dependency_profile = self._profile_dependencies()

        parsed_project = {
            **basic_info,
            "build_system": build_root_info.get("build_system", "Unknown"),
            "build_root_info": build_root_info,
            "cmake_source_dir_rel": build_root_info.get("source_dir_rel", "."),
            "dependency_profile": dependency_profile,
            "project_packages": dependency_profile.get("project_packages", []),
            "apt_packages": dependency_profile.get("apt_packages", []),
        }

        return parsed_project

def parse_project(project_path: str, compatibility_mode: bool = True) -> Dict[str, Any]:
    """
    函数式入口，兼容原有调用方式。
    """
    parser = ProjectParser(project_path, compatibility_mode=compatibility_mode)
    return parser.parse()