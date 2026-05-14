import os
from dataclasses import dataclass, asdict
from typing import Dict, List

@dataclass
class BuildRootInfo:
    build_system: str
    source_dir: str
    source_dir_rel: str
    confidence: float
    reason: str
    candidates: List[str]

class BuildRootDetector:
    """
    自动识别项目构建根目录。

    支持：
    - CMakeLists.txt
    - Makefile
    - meson.build
    - configure.ac / configure.in / autogen.sh

    返回结果用于：
    - 决定 Dockerfile 里的 cmake -S 路径
    - 决定是否需要切换构建系统模板
    """

    def __init__(self, project_root: str, max_depth: int = 6):
        self.project_root = os.path.abspath(project_root)
        self.max_depth = max_depth

    def _walk_limited(self, max_depth: int = None):
        if max_depth is None:
            max_depth = self.max_depth

        for root, dirs, files in os.walk(self.project_root):
            rel = os.path.relpath(root, self.project_root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > max_depth:
                dirs[:] = []
                continue
            yield root, files

    def _find_dirs_containing(self, filename: str, max_depth: int = None) -> List[str]:
        results = []
        seen = set()
        for root, files in self._walk_limited(max_depth=max_depth):
            if filename in files:
                abspath = os.path.abspath(root)
                if abspath not in seen:
                    results.append(abspath)
                    seen.add(abspath)
        return results

    @staticmethod
    def _relpath_norm(base: str, target: str) -> str:
        rel = os.path.relpath(target, base)
        return "." if rel == "." else rel.replace("\\", "/")

    def _score_cmake_root(self, path: str) -> float:
        """
        给 CMake 根目录打分：
        分数越高越像真正的项目根。
        """
        score = 0.0
        try:
            files = set(os.listdir(path))
        except Exception:
            return -999.0

        # 1. 本目录有 CMakeLists.txt
        if "CMakeLists.txt" in files:
            score += 5.0

        # 2. 常见源码结构
        if any(name in files for name in ["src", "include", "tests", "test", "examples"]):
            score += 2.0

        # 3. 常见辅助目录
        if any(name in files for name in ["cmake", "modules", "third_party", "deps"]):
            score += 1.0

        # 4. README / LICENSE 常常在项目根
        if any(name in files for name in ["README", "README.md", "LICENSE", "LICENSE.txt", "CHANGELOG", "CHANGELOG.md"]):
            score += 1.0

        # 5. 根目录下如果有一些源码文件，也略加分
        if any(name.endswith((".cpp", ".cc", ".c", ".h", ".hpp", ".hh")) for name in files):
            score += 1.0

        # 6. 太深的目录大幅降分，避免选到深层子目录
        rel = os.path.relpath(path, self.project_root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        score -= min(depth * 2.0, 10.0)

        # 7. 惩罚常见非主入口目录名
        rel_text = rel.replace("\\", "/").lower()
        bad_tokens = (
            "docs", "doc", "examples", "example", "tests", "test",
            "thirdparty", "vendor", "external", "submodule", "deps",
            "platform", "android", "ios", "demo", "template",
            "bsp", "sndserv", "open-amp", "rpmsg", "board", "ports",
            "tools", "scripts", "ci", "benchmark", "fuzz",
        )
        if any(x in rel_text for x in bad_tokens):
            score -= 8.0

        return score

    def detect(self) -> Dict:
        """
        返回示例：
        {
            "build_system": "CMake",
            "source_dir": "/abs/path/to/project/subdir",
            "source_dir_rel": "subdir",
            "confidence": 0.87,
            "reason": "...",
            "candidates": [...]
        }
        """
        # 1) 优先找 CMakeLists.txt
        cmake_candidates = self._find_dirs_containing("CMakeLists.txt", max_depth=self.max_depth)
        if cmake_candidates:
            scored = []
            for candidate in cmake_candidates:
                scored.append((self._score_cmake_root(candidate), candidate))
            scored.sort(key=lambda x: x[0], reverse=True)

            best_score, best_path = scored[0]
            rel = self._relpath_norm(self.project_root, best_path)
            confidence = min(0.95, max(0.35, 0.55 + best_score / 10.0))

            return asdict(
                BuildRootInfo(
                    build_system="CMake",
                    source_dir=best_path,
                    source_dir_rel=rel,
                    confidence=round(confidence, 2),
                    reason=f"检测到 {len(cmake_candidates)} 个 CMakeLists.txt，选择评分最高目录: {rel}",
                    candidates=[p.replace("\\", "/") for p in cmake_candidates],
                )
            )

        # 2) 兜底：Makefile
        make_candidates = self._find_dirs_containing("Makefile", max_depth=self.max_depth)
        if make_candidates:
            best_path = make_candidates[0]
            rel = self._relpath_norm(self.project_root, best_path)
            return asdict(
                BuildRootInfo(
                    build_system="Makefile",
                    source_dir=best_path,
                    source_dir_rel=rel,
                    confidence=0.70,
                    reason=f"未找到 CMakeLists.txt，找到 Makefile: {rel}",
                    candidates=[p.replace("\\", "/") for p in make_candidates],
                )
            )

        # 3) 兜底：Meson
        meson_candidates = self._find_dirs_containing("meson.build", max_depth=self.max_depth)
        if meson_candidates:
            best_path = meson_candidates[0]
            rel = self._relpath_norm(self.project_root, best_path)
            return asdict(
                BuildRootInfo(
                    build_system="Meson",
                    source_dir=best_path,
                    source_dir_rel=rel,
                    confidence=0.70,
                    reason=f"未找到 CMakeLists.txt，找到 meson.build: {rel}",
                    candidates=[p.replace("\\", "/") for p in meson_candidates],
                )
            )

        # 4) 兜底：Autotools
        autotools_candidates = []
        for root, files in self._walk_limited(max_depth=self.max_depth):
            if "configure.ac" in files or "configure.in" in files or "autogen.sh" in files:
                autotools_candidates.append(os.path.abspath(root))

        if autotools_candidates:
            best_path = autotools_candidates[0]
            rel = self._relpath_norm(self.project_root, best_path)
            return asdict(
                BuildRootInfo(
                    build_system="Autotools",
                    source_dir=best_path,
                    source_dir_rel=rel,
                    confidence=0.65,
                    reason=f"未找到 CMakeLists.txt，找到 Autotools 构建文件: {rel}",
                    candidates=[p.replace("\\", "/") for p in autotools_candidates],
                )
            )

        # 5) 未识别
        return asdict(
            BuildRootInfo(
                build_system="Unknown",
                source_dir=self.project_root,
                source_dir_rel=".",
                confidence=0.10,
                reason="未识别出明确的构建系统或构建根目录",
                candidates=[],
            )
        )