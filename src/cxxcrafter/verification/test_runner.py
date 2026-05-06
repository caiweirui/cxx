from __future__ import annotations

import os
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

def _clamp(v: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, v))

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

class TestRunner:
    """
    运行动态验证命令：
    - runtime smoke test
    - test suite
    """
    def __init__(self, docker_executor: Any) -> None:
        self.docker_executor = docker_executor

    def run_commands(
        self,
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
            try:
                raw = self.docker_executor.verify(
                    image_tag=image_tag,
                    run_command=cmd,
                    log_path=log_path,
                    timeout_seconds=timeout_seconds,
                )
                data = _to_dict(raw)
            except Exception as e:
                data = {
                    "success": False,
                    "status": "error",
                    "message": str(e),
                    "log_path": log_path,
                }

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
                # 失败即停止，避免浪费时间
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