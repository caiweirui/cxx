from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

def _to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"value": str(obj)}

def _as_str_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x) for x in value if str(x).strip()]
    return [str(value)]

def _read_text(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

def _clamp(v: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, v))

@dataclass
class ConsistencyResult:
    passed: bool = False
    score: float = 0.0
    status: str = "unknown"
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

class ConsistencyChecker:
    """
    静态一致性验证：
    - Dockerfile 基础结构
    - 构建系统与命令匹配
    - 依赖/测试/运行命令的合理性
    """

    def check(
        self,
        dockerfile_path: str,
        build_plan: Any,
        snapshot: Any,
    ) -> ConsistencyResult:
        plan = _to_dict(build_plan)
        snapshot = _to_dict(snapshot)
        dockerfile_text = _read_text(dockerfile_path)

        issues: List[str] = []
        warnings: List[str] = []
        details: Dict[str, Any] = {
            "dockerfile_path": dockerfile_path,
            "dockerfile_exists": bool(dockerfile_text),
            "base_image": str(plan.get("base_image", "") or ""),
            "build_system": str(snapshot.get("build_system", "") or "unknown").lower(),
            "source_root_rel": str(snapshot.get("source_root_rel", ".") or "."),
            "copy_paths": _as_str_list(plan.get("copy_paths", [])),
            "build_commands": _as_str_list(plan.get("build_commands", [])),
            "test_commands": _as_str_list(plan.get("test_commands", [])),
            "runtime_command": str(plan.get("runtime_command", "") or "").strip(),
        }

        if not dockerfile_text.strip():
            issues.append("Dockerfile 不存在或为空。")
            return ConsistencyResult(
                passed=False,
                score=0.0,
                status="failed",
                issues=issues,
                warnings=warnings,
                details=details,
            )

        # 基础结构检查
        if not re.search(r"^\s*FROM\s+\S+", dockerfile_text, re.M):
            issues.append("Dockerfile 缺少 FROM 指令。")

        if "RUN" not in dockerfile_text:
            warnings.append("Dockerfile 中未发现 RUN 指令。")

        if "COPY" not in dockerfile_text and "ADD" not in dockerfile_text:
            warnings.append("Dockerfile 中未发现 COPY/ADD 指令。")

        if "apt-get install" in dockerfile_text.lower() and "apt-get update" not in dockerfile_text.lower():
            warnings.append("检测到 apt-get install，但未发现 apt-get update。")

        # 构建系统匹配
        build_system = details["build_system"]
        build_commands = details["build_commands"]
        test_commands = details["test_commands"]
        runtime_command = details["runtime_command"]

        if build_system == "cmake":
            if not any("cmake -s" in c.lower() and "-b" in c.lower() for c in build_commands):
                issues.append("CMake 项目缺少 cmake -S/-B 配置命令。")
            if not any("cmake --build" in c.lower() for c in build_commands):
                issues.append("CMake 项目缺少 cmake --build 命令。")
            if test_commands and not any("ctest" in c.lower() for c in test_commands):
                warnings.append("CMake 项目存在测试命令，但未使用 ctest。")

        elif build_system == "make":
            if not any(re.search(r"\bmake\b", c, re.I) for c in build_commands):
                issues.append("Makefile 项目缺少 make 构建命令。")

        elif build_system == "python":
            if not any(("pip install" in c.lower()) or ("python3 -m pip" in c.lower()) for c in (build_commands + _as_str_list(plan.get("preinstall_commands", [])))):
                warnings.append("Python 项目未发现明显的 pip 安装命令。")
            if test_commands and not any(("pytest" in c.lower()) or ("unittest" in c.lower()) for c in test_commands):
                warnings.append("Python 项目的测试命令不明显。")

        elif build_system == "node":
            if not any(("npm install" in c.lower()) or ("npm ci" in c.lower()) for c in (build_commands + _as_str_list(plan.get("preinstall_commands", [])))):
                warnings.append("Node 项目未发现明显的 npm install/npm ci 命令。")
            if test_commands and not any("npm test" in c.lower() for c in test_commands):
                warnings.append("Node 项目的测试命令不明显。")

        elif build_system == "meson":
            if not any("meson" in c.lower() for c in build_commands):
                issues.append("Meson 项目缺少 meson 相关构建命令。")

        elif build_system == "autotools":
            if not any(("configure" in c.lower()) or ("autogen" in c.lower()) for c in build_commands):
                issues.append("Autotools 项目缺少 configure/autogen 类命令。")

        else:
            # 通用项目至少应有构建命令
            if not build_commands:
                issues.append("未发现构建命令。")

        # 测试与运行一致性
        has_test_files = any(
            token in " ".join(snapshot.get("files_sample", []) or []).lower()
            for token in ["test", "tests", "gtest", "catch2", "doctest"]
        )

        if has_test_files and not test_commands:
            warnings.append("项目存在测试线索，但未生成测试命令。")

        if runtime_command and runtime_command.lower().startswith("echo "):
            warnings.append("runtime_command 过于简单，可能不足以验证功能。")

        if not runtime_command and build_system not in {"library"} and has_test_files:
            warnings.append("检测到测试线索，但 runtime_command 为空。")

        # 计算分数
        score = 1.0
        score -= 0.25 * len(issues)
        score -= 0.05 * len(warnings)
        score = _clamp(score)

        passed = len(issues) == 0
        status = "passed" if passed and not warnings else ("warning" if passed else "failed")

        details.update(
            {
                "issues_count": len(issues),
                "warnings_count": len(warnings),
            }
        )

        return ConsistencyResult(
            passed=passed,
            score=score,
            status=status,
            issues=issues,
            warnings=warnings,
            details=details,
        )