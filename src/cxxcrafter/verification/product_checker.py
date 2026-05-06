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
class ProductCheckResult:
    passed: bool = False
    score: float = 0.0
    status: str = "unknown"
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

class ProductChecker:
    """
    产物/结果级验证：
    - 构建日志是否显示真的生成了目标
    - 是否像“真实构建”而不是空成功
    - 是否与项目类型一致
    """

    BUILD_MARKERS = [
        r"built target",
        r"linking cxx executable",
        r"linking cxx shared library",
        r"linking c executable",
        r"linking c shared library",
        r"generating done",
        r"finished building",
        r"compile",
        r"linking",
        r"successfully built",
        r"successfully installed",
        r"building wheel",
        r"created wheel",
        r"npm run build",
        r"npm build",
        r"ctest",
        r"running tests",
    ]

    def check(
        self,
        snapshot: Any,
        build_plan: Any,
        build_result: Any,
        build_log_text: str,
        dockerfile_text: str = "",
    ) -> ProductCheckResult:
        snapshot = _to_dict(snapshot)
        plan = _to_dict(build_plan)
        build_result = _to_dict(build_result)

        issues: List[str] = []
        warnings: List[str] = []
        details: Dict[str, Any] = {
            "build_system": str(snapshot.get("build_system", "") or "unknown").lower(),
            "runtime_command": str(plan.get("runtime_command", "") or "").strip(),
            "test_commands": _as_str_list(plan.get("test_commands", [])),
            "build_commands": _as_str_list(plan.get("build_commands", [])),
            "build_success": bool(build_result.get("success", False)),
        }

        if not bool(build_result.get("success", False)):
            issues.append("build_result 表示构建未成功，无法通过产物验证。")
            return ProductCheckResult(
                passed=False,
                score=0.0,
                status="failed",
                issues=issues,
                warnings=warnings,
                details=details,
            )

        log_text = (build_log_text or "").lower()
        dockerfile_lower = (dockerfile_text or "").lower()
        build_system = details["build_system"]
        runtime_command = details["runtime_command"]
        test_commands = details["test_commands"]

        markers_found: List[str] = []
        for marker in self.BUILD_MARKERS:
            if re.search(marker, log_text, re.I):
                markers_found.append(marker)

        details["markers_found"] = markers_found
        details["marker_count"] = len(markers_found)

        if not markers_found:
            warnings.append("构建日志中未发现明显的产物生成/编译链接关键字。")

        # build system 相关产物证据
        if build_system == "cmake":
            if not any("built target" in m or "linking" in m for m in markers_found):
                warnings.append("CMake 构建日志中缺少 Built target / Linking 证据。")
            if "cmake --build" not in dockerfile_lower:
                warnings.append("Dockerfile 中未明显看到 cmake --build。")

        elif build_system == "make":
            if "make" not in dockerfile_lower and not any("compile" in m for m in markers_found):
                warnings.append("Make 项目缺少明显的 make 构建证据。")

        elif build_system == "python":
            if not any("successfully installed" in m or "building wheel" in m for m in markers_found):
                warnings.append("Python 项目未看到安装/打包成功证据。")

        elif build_system == "node":
            if not any("npm" in m for m in markers_found):
                warnings.append("Node 项目未看到 npm 相关产物证据。")

        elif build_system in {"meson", "autotools"}:
            if not markers_found:
                warnings.append(f"{build_system} 项目未看到明显构建产物证据。")

        # 运行命令存在但没有任何产物线索 -> 提醒
        if runtime_command and not markers_found:
            warnings.append("存在 runtime_command，但日志缺少明显编译/链接产物证据。")

        # 测试命令存在但日志里完全没有测试迹象
        if test_commands and not any("test" in m for m in markers_found):
            warnings.append("存在测试命令，但日志里未发现测试运行迹象。")

        score = 1.0
        score -= 0.12 * len(warnings)
        score -= 0.35 * len(issues)
        score = _clamp(score)

        passed = len(issues) == 0
        status = "passed" if passed and not warnings else ("warning" if passed else "failed")

        return ProductCheckResult(
            passed=passed,
            score=score,
            status=status,
            issues=issues,
            warnings=warnings,
            details=details,
        )