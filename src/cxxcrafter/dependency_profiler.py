import os
import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Set, Tuple

@dataclass
class DependencyProfile:
    project_packages: List[str]
    apt_packages: List[str]
    features: List[str]
    confidence: float
    reason: str
    evidence: Dict[str, List[str]]
    compatibility_mode: bool

class DependencyProfiler:
    """
    依赖画像器：
    - 通过 CMakeLists.txt / 源码内容推断系统依赖
    - 兼容优先：能精确识别就精确识别，识别不到则回退到更稳妥的兼容方案
    """

    SOURCE_EXTENSIONS = {
        ".c", ".cc", ".cpp", ".cxx",
        ".h", ".hh", ".hpp", ".hxx",
        ".ipp", ".inl", ".tpp",
        ".cmake",
    }

    COMMON_PATTERNS = {
        "openssl": [
            r"find_package\s*\(\s*OpenSSL\b",
            r"OpenSSL::",
            r"#include\s*<openssl/",
        ],
        "curl": [
            r"find_package\s*\(\s*CURL\b",
            r"CURL::",
            r"#include\s*<curl/curl\.h>",
        ],
        "icu": [
            r"find_package\s*\(\s*ICU\b",
            r"ICU::",
            r"#include\s*<unicode/",
        ],
        "zlib": [
            r"find_package\s*\(\s*ZLIB\b",
            r"ZLIB::",
            r"#include\s*<zlib\.h>",
        ],
        "protobuf": [
            r"find_package\s*\(\s*Protobuf\b",
            r"protobuf::",
            r"#include\s*<google/protobuf/",
        ],
        "fmt": [
            r"find_package\s*\(\s*fmt\b",
            r"fmt::fmt",
            r"#include\s*<fmt/",
        ],
        "yaml-cpp": [
            r"find_package\s*\(\s*yaml-cpp\b",
            r"yaml-cpp::yaml-cpp",
            r"#include\s*<yaml-cpp/",
        ],
        "sqlite3": [
            r"find_package\s*\(\s*SQLite3\b",
            r"SQLite::SQLite3",
            r"#include\s*<sqlite3\.h>",
        ],
        "eigen3": [
            r"find_package\s*\(\s*Eigen3\b",
            r"Eigen3::Eigen",
            r"#include\s*<Eigen/",
        ],
        "gtest": [
            r"find_package\s*\(\s*GTest\b",
            r"GTest::GTest",
            r"#include\s*<gtest/",
        ],
        "benchmark": [
            r"find_package\s*\(\s*benchmark\b",
            r"benchmark::benchmark",
            r"#include\s*<benchmark/benchmark\.h>",
        ],
        "tbb": [
            r"find_package\s*\(\s*TBB\b",
            r"TBB::tbb",
            r"#include\s*<tbb/",
        ],
        "lz4": [
            r"find_package\s*\(\s*LZ4\b",
            r"LZ4::LZ4",
            r"#include\s*<lz4\.h>",
        ],
        "zstd": [
            r"find_package\s*\(\s*ZSTD\b",
            r"Zstd::Zstd",
            r"#include\s*<zstd\.h>",
        ],
        "brotli": [
            r"find_package\s*\(\s*Brotli\b",
            r"Brotli::",
            r"#include\s*<brotli/",
        ],
    }

    BOOST_COMPONENT_MAP = {
        "system": "libboost-system-dev",
        "filesystem": "libboost-filesystem-dev",
        "thread": "libboost-thread-dev",
        "regex": "libboost-regex-dev",
        "date_time": "libboost-date-time-dev",
        "serialization": "libboost-serialization-dev",
        "program_options": "libboost-program-options-dev",
        "context": "libboost-context-dev",
        "coroutine": "libboost-coroutine-dev",
        "atomic": "libboost-atomic-dev",
        "chrono": "libboost-chrono-dev",
        "locale": "libboost-locale-dev",
        "iostreams": "libboost-iostreams-dev",
        "graph": "libboost-graph-dev",
        "python": "libboost-python-dev",
        "test": "libboost-test-dev",
    }

    BOOST_FALLBACK_PACKAGES = [
        "libboost-system-dev",
        "libboost-filesystem-dev",
        "libboost-thread-dev",
        "libboost-regex-dev",
        "libboost-date-time-dev",
        "libboost-serialization-dev",
    ]

    def __init__(self, project_root: str, compatibility_mode: bool = True, max_depth: int = 6, max_files: int = 120):
        self.project_root = os.path.abspath(project_root)
        self.compatibility_mode = compatibility_mode
        self.max_depth = max_depth
        self.max_files = max_files

    def _walk_limited(self):
        for root, dirs, files in os.walk(self.project_root):
            rel = os.path.relpath(root, self.project_root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > self.max_depth:
                dirs[:] = []
                continue
            yield root, files

    def _read_text(self, path: str, limit_bytes: int = 200_000) -> str:
        try:
            with open(path, "rb") as f:
                raw = f.read(limit_bytes)
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _collect_relevant_files(self) -> Dict[str, str]:
        """
        收集少量关键文件的内容：
        - CMakeLists.txt
        - .cmake
        - 常见源码/头文件
        """
        results = {}
        count = 0

        for root, files in self._walk_limited():
            # 优先收集 CMakeLists.txt
            for name in files:
                if count >= self.max_files:
                    return results
                path = os.path.join(root, name)
                ext = os.path.splitext(name)[1].lower()

                if name == "CMakeLists.txt" or ext == ".cmake" or ext in self.SOURCE_EXTENSIONS:
                    txt = self._read_text(path)
                    if txt:
                        results[path] = txt
                        count += 1

        return results

    @staticmethod
    def _match_any(text: str, patterns: List[str]) -> bool:
        return any(re.search(p, text, flags=re.IGNORECASE | re.DOTALL) for p in patterns)

    @staticmethod
    def _extract_boost_components(cmake_text: str) -> Set[str]:
        """
        尽量从 find_package(Boost COMPONENTS xxx yyy) 中解析组件。
        同时也解析 Boost::filesystem 这种写法。
        """
        components: Set[str] = set()

        # 1) 解析 find_package(Boost ...)
        for m in re.finditer(r"find_package\s*\(\s*Boost\b(.*?)\)", cmake_text, flags=re.IGNORECASE | re.DOTALL):
            body = re.sub(r"[\r\n\t]+", " ", m.group(1))
            body = re.sub(r"\s+", " ", body).strip()

            if re.search(r"\bCOMPONENTS\b", body, flags=re.IGNORECASE):
                after = re.split(r"\bCOMPONENTS\b", body, maxsplit=1, flags=re.IGNORECASE)[1]
                tokens = re.findall(r"[A-Za-z0-9_]+", after)

                stop_words = {"REQUIRED", "QUIET", "EXACT", "OPTIONAL_COMPONENTS", "OPTIONAL", "NAMES", "VERSION"}
                for tok in tokens:
                    if tok.upper() in stop_words:
                        break
                    components.add(tok)

        # 2) 解析 Boost::xxx
        for comp in re.findall(r"Boost::([A-Za-z0-9_]+)", cmake_text):
            components.add(comp)

        return components

    def _boost_component_to_pkg(self, comp: str) -> str:
        key = comp.lower()
        if key in self.BOOST_COMPONENT_MAP:
            return self.BOOST_COMPONENT_MAP[key]
        # 默认把下划线转成连字符
        return f"libboost-{key.replace('_', '-')}-dev"

    def _detect_from_texts(self, cmake_text: str, source_text: str) -> Tuple[Set[str], Set[str], Dict[str, List[str]]]:
        """
        返回：
        - features
        - project_packages
        - evidence
        """
        features: Set[str] = set()
        packages: Set[str] = set()
        evidence: Dict[str, List[str]] = {}

        # 1) Boost 处理最复杂：优先解析明确组件；否则回退兼容方案
        boost_components = self._extract_boost_components(cmake_text)
        boost_header_found = bool(re.search(r"#include\s*<boost/", source_text, flags=re.IGNORECASE)) or \
                             bool(re.search(r"\bBOOST_[A-Z0-9_]+\b", source_text))

        if boost_components:
            features.add("boost")
            boost_pkgs = []
            for comp in sorted(boost_components):
                boost_pkgs.append(self._boost_component_to_pkg(comp))
            packages.update(boost_pkgs)
            evidence["boost"] = [f"Boost components: {', '.join(sorted(boost_components))}"]
        elif re.search(r"find_package\s*\(\s*Boost\b", cmake_text, flags=re.IGNORECASE) or boost_header_found:
            features.add("boost")
            if self.compatibility_mode:
                packages.add("libboost-all-dev")
                evidence["boost"] = ["Boost usage detected; fallback to libboost-all-dev for compatibility"]
            else:
                packages.update(self.BOOST_FALLBACK_PACKAGES)
                evidence["boost"] = ["Boost usage detected; fallback to a conservative Boost subset"]

        # 2) 其他常见依赖
        feature_to_packages = {
            "openssl": ["libssl-dev"],
            "curl": ["libcurl4-openssl-dev"],
            "icu": ["libicu-dev"],
            "zlib": ["zlib1g-dev"],
            "protobuf": ["protobuf-compiler", "libprotobuf-dev"],
            "fmt": ["libfmt-dev"],
            "yaml-cpp": ["libyaml-cpp-dev"],
            "sqlite3": ["libsqlite3-dev"],
            "eigen3": ["libeigen3-dev"],
            "gtest": ["libgtest-dev"],
            "benchmark": ["libbenchmark-dev"],
            "tbb": ["libtbb-dev"],
            "lz4": ["liblz4-dev"],
            "zstd": ["libzstd-dev"],
            "brotli": ["libbrotli-dev"],
        }

        for feature, patterns in self.COMMON_PATTERNS.items():
            if self._match_any(cmake_text, patterns) or self._match_any(source_text, patterns):
                features.add(feature)
                packages.update(feature_to_packages.get(feature, []))
                evidence[feature] = [f"Matched patterns: {feature}"]

        return features, packages, evidence

    def profile(self) -> Dict:
        """
        返回结构示例：
        {
            "project_packages": [...],
            "apt_packages": [...],
            "features": [...],
            "confidence": 0.82,
            "reason": "...",
            "evidence": {...},
            "compatibility_mode": True
        }
        """
        files = self._collect_relevant_files()

        cmake_text = []
        source_text = []

        for path, text in files.items():
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()

            if name == "CMakeLists.txt" or ext == ".cmake":
                cmake_text.append(text)
            elif ext in self.SOURCE_EXTENSIONS:
                source_text.append(text)

        cmake_blob = "\n".join(cmake_text)
        source_blob = "\n".join(source_text)

        features, project_packages, evidence = self._detect_from_texts(cmake_blob, source_blob)

        # 排序、去重，保证输出稳定，利于 Docker 缓存命中
        project_packages = sorted(set(project_packages))
        features = sorted(features)

        # 简单置信度评估
        confidence = 0.25
        confidence += min(0.45, 0.07 * len(features))
        confidence += 0.10 if cmake_blob.strip() else 0.0
        confidence += 0.10 if source_blob.strip() else 0.0
        confidence = min(0.95, confidence)

        if project_packages:
            reason = f"检测到 {len(features)} 类依赖特征，生成 {len(project_packages)} 个项目级系统包"
        else:
            reason = "未检测到明确的项目级系统依赖，使用基础构建工具链即可"

        return asdict(
            DependencyProfile(
                project_packages=project_packages,
                apt_packages=project_packages,
                features=features,
                confidence=round(confidence, 2),
                reason=reason,
                evidence=evidence,
                compatibility_mode=self.compatibility_mode,
            )
        )