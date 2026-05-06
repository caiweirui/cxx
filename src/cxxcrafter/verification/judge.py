from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

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

def _join_messages(*parts: str) -> str:
    msgs = [str(p).strip() for p in parts if str(p).strip()]
    return " | ".join(msgs)

@dataclass
class ConsistencyResult:
    passed: bool = False
    score: float = 0.0
    status: str = "unknown"
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ProductCheckResult:
    passed: bool = False
    score: float = 0.0
    status: str = "unknown"
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class TestCaseResult:
    command: str
    passed: bool = False
    status: str = "unknown"
    message: str = ""
    log_path: str = ""
    exit_code: Optional[int] = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

@dataclass
class TestRunResult:
    passed: bool = False
    score: float = 0.0
    status: str = "unknown"
    skipped: bool = False
    total: int = 0
    passed_count: int = 0
    failed_count: int = 0
    cases: List[TestCaseResult] = field(default_factory=list)
    log_paths: List[str] = field(default_factory=list)
    message: str = ""

class ConsistencyChecker:
    """
    静态一致性验证：
    - Dockerfile / 构建计划 / 项目快照是否匹配
    - build/test/runtime 命令是否符合 build system
    """

    def check(
        self,
        *,
        snapshot: Any,
        build_plan: Any,
        dockerfile_path: str = "",
    ) -> ConsistencyResult:
        snapshot = _to_dict(snapshot)
        plan = _to_dict(build_plan)
        dockerfile_text = _read_text(dockerfile_path)

        build_system = str(snapshot.get("build_system", "") or "unknown").lower()
        source_root_rel = str(snapshot.get("source_root_rel", ".") or ".").strip() or "."

        build_commands = _as_str_list(plan.get("build_commands", []))
        test_commands = _as_str_list(plan.get("test_commands", []))
        runtime_command = str(plan.get("runtime_command", "") or "").strip()
        copy_paths = _as_str_list(plan.get("copy_paths", []))

        issues: List[str] = []
        warnings: List[str] = []
        details: Dict[str, Any] = {
            "build_system": build_system,
            "source_root_rel": source_root_rel,
            "copy_paths": copy_paths,
            "build_commands": build_commands,
            "test_commands": test_commands,
            "runtime_command": runtime_command,
            "dockerfile_exists": bool(dockerfile_text.strip()),
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

        if not re.search(r"^\s*FROM\s+\S+", dockerfile_text, re.M):
            issues.append("Dockerfile 缺少 FROM 指令。")

        if "COPY" not in dockerfile_text and "ADD" not in dockerfile_text:
            warnings.append("Dockerfile 中未发现 COPY/ADD 指令。")

        if "RUN" not in dockerfile_text:
            warnings.append("Dockerfile 中未发现 RUN 指令。")

        low = dockerfile_text.lower()
        if "apt-get install" in low and "apt-get update" not in low:
            warnings.append("检测到 apt-get install，但未发现 apt-get update。")

        # build system 规则
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

        elif build_system == "node":
            if not any(("npm install" in c.lower()) or ("npm ci" in c.lower()) for c in build_commands):
                warnings.append("Node 项目未发现 npm install/npm ci。")
            if test_commands and not any("npm test" in c.lower() for c in test_commands):
                warnings.append("Node 项目的测试命令不明显。")

        elif build_system == "python":
            if not any(("pip install" in c.lower()) or ("python3 -m pip" in c.lower()) for c in build_commands):
                warnings.append("Python 项目未发现 pip 安装命令。")
            if test_commands and not any(("pytest" in c.lower()) or ("unittest" in c.lower()) for c in test_commands):
                warnings.append("Python 项目的测试命令不明显。")

        elif build_system == "meson":
            if not any("meson" in c.lower() for c in build_commands):
                issues.append("Meson 项目缺少 meson 相关构建命令。")

        elif build_system == "autotools":
            if not any(("configure" in c.lower()) or ("autogen" in c.lower()) for c in build_commands):
                issues.append("Autotools 项目缺少 configure/autogen 类命令。")

        else:
            if not build_commands:
                issues.append("未发现构建命令。")

        # 运行命令 / 测试命令合理性
        if runtime_command and runtime_command.lower().startswith("echo "):
            warnings.append("runtime_command 过于简单，可能不足以验证功能。")

        if not runtime_command and build_system not in {"library"} and test_commands:
            warnings.append("存在测试命令，但 runtime_command 为空。")

        score = 1.0
        score -= 0.25 * len(issues)
        score -= 0.05 * len(warnings)
        score = _clamp(score)

        passed = len(issues) == 0
        status = "passed" if passed and not warnings else ("warning" if passed else "failed")

        return ConsistencyResult(
            passed=passed,
            score=score,
            status=status,
            issues=issues,
            warnings=warnings,
            details=details,
        )

class ProductChecker:
    """
    产物/结果级验证：
    - 构建日志是否显示真实产物生成
    - 是否像“真构建”
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
        r"successfully built",
        r"successfully installed",
        r"building wheel",
        r"created wheel",
        r"compile",
        r"linking",
        r"ctest",
        r"running tests",
        r"npm run build",
        r"npm build",
    ]

    def check(
        self,
        *,
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
            "build_success": bool(build_result.get("success", False)),
            "runtime_command": str(plan.get("runtime_command", "") or "").strip(),
            "test_commands": _as_str_list(plan.get("test_commands", [])),
            "build_commands": _as_str_list(plan.get("build_commands", [])),
        }

        if not bool(build_result.get("success", False)):
            issues.append("build_result 显示构建未成功，无法通过产物验证。")
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

        if build_system == "cmake":
            if not any("built target" in m or "linking" in m for m in markers_found):
                warnings.append("CMake 构建日志中缺少 Built target / Linking 证据。")
            if "cmake --build" not in dockerfile_lower:
                warnings.append("Dockerfile 中未明显看到 cmake --build。")

        elif build_system == "make":
            if "make" not in dockerfile_lower and not any("compile" in m for m in markers_found):
                warnings.append("Make 项目缺少明显 make 构建证据。")

        elif build_system == "python":
            if not any("successfully installed" in m or "building wheel" in m for m in markers_found):
                warnings.append("Python 项目未看到安装/打包成功证据。")

        elif build_system == "node":
            if not any("npm" in m for m in markers_found):
                warnings.append("Node 项目未看到 npm 相关产物证据。")

        elif build_system in {"meson", "autotools"}:
            if not markers_found:
                warnings.append(f"{build_system} 项目未看到明显构建产物证据。")

        if runtime_command and not markers_found:
            warnings.append("存在 runtime_command，但日志缺少明显编译/链接产物证据。")

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

class TestRunner:
    """
    运行动态验证命令：
    - runtime smoke test
    - test suite
    """

    def __init__(self, docker_executor: Any) -> None:
        self.docker_executor = docker_executor

    def _invoke_verify(
        self,
        *,
        image_tag: str,
        command: str,
        log_path: str,
        timeout_seconds: Optional[float],
    ) -> Dict[str, Any]:
        """
        尽量兼容不同 executor.verify 签名。
        """
        candidates = [
            {"image_tag": image_tag, "run_command": command, "log_path": log_path, "timeout_seconds": timeout_seconds},
            {"image_tag": image_tag, "command": command, "log_path": log_path, "timeout_seconds": timeout_seconds},
            {"image_tag": image_tag, "run_command": command, "log_path": log_path},
            {"image_tag": image_tag, "command": command, "log_path": log_path},
        ]

        last_exc: Optional[Exception] = None
        for kwargs in candidates:
            try:
                result = self.docker_executor.verify(**kwargs)
                return _to_dict(result)
            except TypeError as e:
                last_exc = e
                continue
            except Exception as e:
                last_exc = e
                break

        return {
            "success": False,
            "status": "error",
            "message": str(last_exc) if last_exc else "verify failed",
            "log_path": log_path,
        }

    def run_commands(
        self,
        *,
        image_tag: str,
        commands: Sequence[str],
        log_dir: str,
        timeout_seconds: Optional[float] = 600,
        stage_name: str = "test",
    ) -> TestRunResult:
        cmd_list = [c.strip() for c in _as_str_list(commands) if c and str(c).strip()]

        if not image_tag or not cmd_list:
            return TestRunResult(
                passed=False,
                score=0.0,
                status="skipped",
                skipped=True,
                total=0,
                passed_count=0,
                failed_count=0,
                cases=[],
                log_paths=[],
                message="No image tag or no commands provided.",
            )

        Path(log_dir).mkdir(parents=True, exist_ok=True)

        cases: List[TestCaseResult] = []
        log_paths: List[str] = []
        passed_count = 0
        failed_count = 0

        for idx, cmd in enumerate(cmd_list, 1):
            log_path = str(Path(log_dir) / f"{stage_name}_{idx:02d}.log")
            data = self._invoke_verify(
                image_tag=image_tag,
                command=cmd,
                log_path=log_path,
                timeout_seconds=timeout_seconds,
            )

            success = bool(data.get("success", False))
            status = str(data.get("status", "passed" if success else "failed"))
            message = str(data.get("message", "") or "")
            exit_code = data.get("exit_code")
            timed_out = bool(data.get("timed_out", False))
            duration_seconds = float(data.get("duration_seconds", 0.0) or 0.0)

            case = TestCaseResult(
                command=cmd,
                passed=success,
                status=status,
                message=message,
                log_path=str(data.get("log_path", log_path) or log_path),
                exit_code=exit_code if isinstance(exit_code, int) else None,
                timed_out=timed_out,
                duration_seconds=duration_seconds,
                raw=data,
            )
            cases.append(case)
            log_paths.append(case.log_path)

            if success:
                passed_count += 1
            else:
                failed_count += 1
                break

        total = len(cmd_list)
        score = _clamp(passed_count / total if total else 0.0)
        passed = failed_count == 0 and total > 0
        status = "passed" if passed else "failed"
        if passed_count == 0 and failed_count == 0:
            status = "skipped"

        message = "All commands passed." if passed else (cases[-1].message if cases else "No test executed.")

        return TestRunResult(
            passed=passed,
            score=score,
            status=status,
            skipped=False,
            total=total,
            passed_count=passed_count,
            failed_count=failed_count,
            cases=cases,
            log_paths=log_paths,
            message=message,
        )

class VerificationJudge:
    """
    多维度验证总裁决：
    1. 静态一致性验证
    2. smoke test
    3. 测试套件验证
    4. 产物/日志验证
    """

    def __init__(
        self,
        docker_executor: Any,
        consistency_checker: Optional[ConsistencyChecker] = None,
        product_checker: Optional[ProductChecker] = None,
        test_runner: Optional[TestRunner] = None,
    ) -> None:
        self.docker_executor = docker_executor
        self.consistency_checker = consistency_checker or ConsistencyChecker()
        self.product_checker = product_checker or ProductChecker()
        self.test_runner = test_runner or TestRunner(docker_executor)

    def evaluate(
        self,
        *,
        snapshot: Any,
        build_plan: Any,
        build_result: Any,
        dockerfile_path: str,
        build_log_path: str,
        image_tag: str,
        verify_timeout_seconds: Optional[float] = 600,
        log_dir: str = "./data/build_logs",
        project_name: str = "",
        enable_verification: bool = True,
    ) -> Dict[str, Any]:
        snapshot_dict = _to_dict(snapshot)
        plan_dict = _to_dict(build_plan)
        build_result_dict = _to_dict(build_result)

        if not enable_verification:
            return {
                "success": False,
                "status": "skipped",
                "skipped": True,
                "message": "verification disabled",
                "score": 0.0,
                "final_verdict": {
                    "verdict": "disabled",
                    "confidence": 0.0,
                    "reason": "verification disabled",
                },
                "stages": {},
                "log_path": "",
                "log_paths": {},
            }

        if not image_tag:
            return {
                "success": False,
                "status": "skipped",
                "skipped": True,
                "message": "image_tag is empty, verification skipped",
                "score": 0.0,
                "final_verdict": {
                    "verdict": "skipped",
                    "confidence": 0.0,
                    "reason": "image_tag is empty",
                },
                "stages": {},
                "log_path": "",
                "log_paths": {},
            }

        dockerfile_text = _read_text(dockerfile_path)
        build_log_text = _read_text(build_log_path)

        consistency = self.consistency_checker.check(
            snapshot=snapshot_dict,
            build_plan=plan_dict,
            dockerfile_path=dockerfile_path,
        )

        product = self.product_checker.check(
            snapshot=snapshot_dict,
            build_plan=plan_dict,
            build_result=build_result_dict,
            build_log_text=build_log_text,
            dockerfile_text=dockerfile_text,
        )

        runtime_command = str(plan_dict.get("runtime_command", "") or "").strip()
        test_commands = plan_dict.get("test_commands", []) or []

        smoke_result = self.test_runner.run_commands(
            image_tag=image_tag,
            commands=[runtime_command] if runtime_command else [],
            log_dir=log_dir,
            timeout_seconds=verify_timeout_seconds,
            stage_name=f"{project_name}_smoke" if project_name else "smoke",
        )

        test_result = self.test_runner.run_commands(
            image_tag=image_tag,
            commands=test_commands,
            log_dir=log_dir,
            timeout_seconds=verify_timeout_seconds,
            stage_name=f"{project_name}_tests" if project_name else "tests",
        )

        dynamic_skipped = bool(smoke_result.skipped and test_result.skipped)

        # 评分权重：静态 30%，产物 25%，smoke 25%，测试 20%
        score = (
            0.30 * consistency.score
            + 0.25 * product.score
            + 0.25 * smoke_result.score
            + 0.20 * test_result.score
        )
        score = _clamp(score)

        dynamic_failed = False
        failing_log_path = ""

        if not smoke_result.skipped and not smoke_result.passed:
            dynamic_failed = True
            failing_log_path = smoke_result.log_paths[0] if smoke_result.log_paths else ""
        if not test_result.skipped and not test_result.passed:
            dynamic_failed = True
            failing_log_path = test_result.log_paths[0] if test_result.log_paths else failing_log_path

        success = bool(consistency.passed and product.passed and not dynamic_failed)
        if dynamic_skipped and consistency.passed and product.passed:
            success = True

        if success:
            status = "passed"
        else:
            status = "failed"

        issues: List[str] = []
        issues.extend(consistency.issues)
        issues.extend(product.issues)

        warnings: List[str] = []
        warnings.extend(consistency.warnings)
        warnings.extend(product.warnings)

        message = _join_messages(
            f"consistency={consistency.status}",
            f"product={product.status}",
            f"smoke={smoke_result.status}",
            f"tests={test_result.status}",
        )
        if not success:
            if issues:
                message = _join_messages(message, issues[0])
            elif warnings:
                message = _join_messages(message, warnings[0])

        final_verdict = {
            "verdict": "passed" if success else "failed",
            "confidence": round(score, 4),
            "reason": message,
        }

        return {
            "success": success,
            "status": status,
            "skipped": False,
            "score": round(score, 4),
            "message": message,
            "log_path": failing_log_path or build_log_path,
            "log_paths": {
                "build_log_path": build_log_path,
                "dockerfile_path": dockerfile_path,
                "smoke_logs": smoke_result.log_paths,
                "test_logs": test_result.log_paths,
            },
            "final_verdict": final_verdict,
            "stages": {
                "consistency": asdict(consistency),
                "product": asdict(product),
                "smoke": asdict(smoke_result),
                "tests": asdict(test_result),
            },
            "issues": issues,
            "warnings": warnings,
            "project": project_name,
            "image_tag": image_tag,
        }