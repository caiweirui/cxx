from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from cxxcrafter.agents.coordinator import CXXCrafterCoordinator

@dataclass
class BatchItemResult:
    index: int
    project_path: str
    project_name: str
    success: bool
    overall_status: str
    summary_path: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

@dataclass
class BatchRunSummary:
    total: int
    succeeded: int
    failed: int
    passed: int
    passed_with_verification_skipped: int
    generated: int
    skipped: int
    timeout: int
    docker_unavailable: int
    exception: int
    items: List[BatchItemResult] = field(default_factory=list)
    created_at: str = ""
    total_duration_seconds: float = 0.0
    consecutive_failures_peak: int = 0
    stopped_early: bool = False
    stop_reason: str = ""

class BatchExecutor:
    """
    批处理执行器：
    - 顺序处理项目
    - 单项失败不会影响其它项目
    - 遇到 Docker 异常可立即停止
    - 连续失败超过阈值可停止
    """

    def __init__(self, coordinator: CXXCrafterCoordinator) -> None:
        self.coordinator = coordinator

    def discover_projects(self, root_dir: str, recursive: bool = False) -> List[str]:
        root = Path(root_dir)
        if not root.exists():
            return []

        candidates: List[str] = []

        if not recursive:
            for child in sorted(root.iterdir()):
                if child.is_dir() and self._looks_like_project(child):
                    candidates.append(str(child))
            return candidates

        seen = set()
        for p in root.rglob("*"):
            if not p.is_dir():
                continue
            if p in seen:
                continue
            if self._looks_like_project(p):
                seen.add(p)
                candidates.append(str(p))

        return sorted(candidates)

    def run(
        self,
        project_paths: Sequence[str],
        output_dir: str,
        log_dir: str,
        enable_build: bool = True,
        enable_verification: bool = True,
        generate_only: bool = False,
        use_cache: bool = True,
        image_tag_prefix: Optional[str] = None,
        build_timeout_seconds: Optional[float] = 1800,
        verify_timeout_seconds: Optional[float] = 600,
        project_timeout_seconds: Optional[float] = None,
        batch_summary_path: Optional[str] = None,
        stop_on_docker_error: bool = True,
        max_consecutive_failures: int = 3,
    ) -> Dict[str, Any]:
        output_dir = str(Path(output_dir).resolve())
        log_dir = str(Path(log_dir).resolve())

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        started_at = time.time()
        items: List[BatchItemResult] = []

        project_paths = [str(Path(p).resolve()) for p in project_paths]
        total = len(project_paths)

        consecutive_failures = 0
        consecutive_failures_peak = 0
        stopped_early = False
        stop_reason = ""

        print("\n" + "=" * 58)
        print("批处理开始")
        print("=" * 58)
        print(f"项目总数: {total}")
        print(f"输出目录: {output_dir}")
        print(f"日志目录: {log_dir}")
        print(f"停止Docker异常: {stop_on_docker_error}")
        print(f"连续失败阈值: {max_consecutive_failures}")
        print("=" * 58 + "\n")

        for idx, project_path in enumerate(project_paths, start=1):
            project_name = Path(project_path).name

            print("\n" + "=" * 60)
            print(f"[{idx}/{total}] 处理项目: {project_path}")
            print("=" * 60 + "\n")

            per_start = time.time()
            summary: Dict[str, Any] = {}

            try:
                image_tag = None
                if image_tag_prefix:
                    safe_name = self._safe_image_name(project_name)
                    image_tag = f"{image_tag_prefix}/{safe_name}:latest"

                summary = self.coordinator.process_project(
                    project_path=project_path,
                    output_dir=output_dir,
                    log_dir=log_dir,
                    enable_build=enable_build,
                    enable_verification=enable_verification,
                    generate_only=generate_only,
                    use_cache=use_cache,
                    image_tag=image_tag,
                    build_timeout_seconds=build_timeout_seconds,
                    verify_timeout_seconds=verify_timeout_seconds,
                    project_timeout_seconds=project_timeout_seconds,
                )

                overall_status = str(summary.get("overall_status", "") or "").lower()
                success = bool(summary.get("success", False))
                summary_path = str(Path(log_dir) / f"{project_name}_summary.json")

                duration = time.time() - per_start
                item = BatchItemResult(
                    index=idx,
                    project_path=project_path,
                    project_name=project_name,
                    success=success,
                    overall_status=overall_status,
                    summary_path=summary_path,
                    error="",
                    duration_seconds=duration,
                    raw=summary,
                )
                items.append(item)

                is_docker_unavailable = self._is_docker_unavailable(summary, item)
                is_timeout = self._is_timeout(summary, item)
                is_generated = self._is_generated(summary, item)
                is_skipped = self._is_skipped(summary, item)
                is_passed = self._is_passed(summary, item)

                if is_passed:
                    consecutive_failures = 0
                elif is_generated:
                    consecutive_failures = 0
                elif is_timeout:
                    consecutive_failures = 0
                elif is_skipped:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    consecutive_failures_peak = max(consecutive_failures_peak, consecutive_failures)

                print(
                    f"✅ 完成: {project_name} | "
                    f"status={overall_status} | "
                    f"success={success} | "
                    f"{duration:.1f}s"
                )

                if stop_on_docker_error and is_docker_unavailable:
                    stopped_early = True
                    stop_reason = f"Docker unavailable detected at project '{project_name}'"
                    print(f"\n🛑 {stop_reason}")
                    break

                if max_consecutive_failures > 0 and consecutive_failures >= max_consecutive_failures:
                    stopped_early = True
                    stop_reason = (
                        f"consecutive failures reached threshold "
                        f"({consecutive_failures}/{max_consecutive_failures})"
                    )
                    print(f"\n🛑 {stop_reason}")
                    break

            except Exception as e:
                duration = time.time() - per_start
                err = f"{type(e).__name__}: {e}"
                item = BatchItemResult(
                    index=idx,
                    project_path=project_path,
                    project_name=project_name,
                    success=False,
                    overall_status="exception",
                    summary_path="",
                    error=err,
                    duration_seconds=duration,
                    raw=summary or {},
                )
                items.append(item)
                consecutive_failures += 1
                consecutive_failures_peak = max(consecutive_failures_peak, consecutive_failures)

                print(f"❌ 失败: {project_name} | {err}")

                if stop_on_docker_error and self._looks_like_docker_error(err):
                    stopped_early = True
                    stop_reason = f"Docker error inferred from exception at project '{project_name}'"
                    print(f"\n🛑 {stop_reason}")
                    break

                if max_consecutive_failures > 0 and consecutive_failures >= max_consecutive_failures:
                    stopped_early = True
                    stop_reason = (
                        f"consecutive failures reached threshold "
                        f"({consecutive_failures}/{max_consecutive_failures})"
                    )
                    print(f"\n🛑 {stop_reason}")
                    break

        total_duration = time.time() - started_at
        summary = self._summarize(
            items=items,
            total_duration=total_duration,
            consecutive_failures_peak=consecutive_failures_peak,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
        )

        if batch_summary_path:
            p = Path(batch_summary_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        print("\n" + "=" * 58)
        print("批处理结束")
        print("=" * 58)
        print(f"总数   : {summary['total']}")
        print(f"成功   : {summary['succeeded']}")
        print(f"失败   : {summary['failed']}")
        print(f"通过   : {summary['passed']}")
        print(f"跳过验 : {summary['passed_with_verification_skipped']}")
        print(f"生成   : {summary['generated']}")
        print(f"跳过   : {summary['skipped']}")
        print(f"超时   : {summary['timeout']}")
        print(f"Docker异常: {summary['docker_unavailable']}")
        print(f"异常   : {summary['exception']}")
        print(f"连续失败峰值: {summary['consecutive_failures_peak']}")
        print(f"提前停止: {summary['stopped_early']}")
        if summary["stopped_early"]:
            print(f"停止原因: {summary['stop_reason']}")
        print(f"耗时   : {total_duration:.1f}s")
        print("=" * 58 + "\n")

        return summary

    def _looks_like_project(self, path: Path) -> bool:
        markers = [
            "CMakeLists.txt",
            "Makefile",
            "makefile",
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "Dockerfile",
            "meson.build",
            "configure.ac",
            "autogen.sh",
        ]
        for m in markers:
            if (path / m).exists():
                return True

        for pat in ("*.cpp", "*.c", "*.cc", "*.hpp", "*.h", "*.py", "*.js", "*.ts"):
            try:
                if any(path.glob(pat)):
                    return True
            except Exception:
                pass

        return False

    def _summarize(
        self,
        items: List[BatchItemResult],
        total_duration: float,
        consecutive_failures_peak: int,
        stopped_early: bool,
        stop_reason: str,
    ) -> Dict[str, Any]:
        succeeded = 0
        failed = 0
        passed = 0
        passed_with_verification_skipped = 0
        generated = 0
        skipped = 0
        timeout = 0
        docker_unavailable = 0
        exception = 0

        for item in items:
            status = (item.overall_status or "").lower()

            if item.success or status == "passed":
                succeeded += 1
            else:
                failed += 1

            if status == "passed":
                passed += 1
            elif status == "passed_with_verification_skipped":
                passed_with_verification_skipped += 1
            elif status == "generated":
                generated += 1
            elif status == "timeout":
                timeout += 1
            elif status == "docker_unavailable":
                docker_unavailable += 1
            elif status == "exception":
                exception += 1

            if "skipped" in status:
                skipped += 1

        result = BatchRunSummary(
            total=len(items),
            succeeded=succeeded,
            failed=failed,
            passed=passed,
            passed_with_verification_skipped=passed_with_verification_skipped,
            generated=generated,
            skipped=skipped,
            timeout=timeout,
            docker_unavailable=docker_unavailable,
            exception=exception,
            items=items,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            total_duration_seconds=total_duration,
            consecutive_failures_peak=consecutive_failures_peak,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
        )

        return asdict(result)

    def _is_passed(self, summary: Dict[str, Any], item: BatchItemResult) -> bool:
        status = self._status_of(summary, item)
        return status in {"passed"}

    def _is_generated(self, summary: Dict[str, Any], item: BatchItemResult) -> bool:
        status = self._status_of(summary, item)
        return status in {"generated"}

    def _is_skipped(self, summary: Dict[str, Any], item: BatchItemResult) -> bool:
        status = self._status_of(summary, item)
        if status in {"passed_with_verification_skipped"}:
            return True
        if "skipped" in status:
            return True
        return False

    def _is_timeout(self, summary: Dict[str, Any], item: BatchItemResult) -> bool:
        status = self._status_of(summary, item)
        if status == "timeout":
            return True

        build_status = str((summary.get("build_result", {}) or {}).get("status", "")).lower()
        ver_status = str((summary.get("verification_result", {}) or {}).get("status", "")).lower()
        return build_status == "timeout" or ver_status == "timeout"

    def _is_docker_unavailable(self, summary: Dict[str, Any], item: BatchItemResult) -> bool:
        status = self._status_of(summary, item)
        if status == "docker_unavailable":
            return True

        build_result = summary.get("build_result", {}) or {}
        ver_result = summary.get("verification_result", {}) or {}

        build_status = str(build_result.get("status", "") or "").lower()
        ver_status = str(ver_result.get("status", "") or "").lower()
        if build_status == "docker_unavailable" or ver_status == "docker_unavailable":
            return True

        text = " ".join(
            [
                str(build_result.get("message", "")),
                str(ver_result.get("message", "")),
                str(item.error or ""),
            ]
        ).lower()

        docker_error_patterns = [
            "dockerdesktoplinuxengine/_ping",
            "500 internal server error for api route and version",
            "cannot connect to the docker daemon",
            "error during connect",
            "is the docker daemon running",
            "failed to connect to the docker daemon",
            "connection refused",
        ]
        return any(p in text for p in docker_error_patterns)

    def _status_of(self, summary: Dict[str, Any], item: BatchItemResult) -> str:
        status = str(summary.get("overall_status", "") or "").lower().strip()
        if not status:
            status = str(item.overall_status or "").lower().strip()
        return status

    @staticmethod
    def _looks_like_docker_error(text: str) -> bool:
        low = (text or "").lower()
        patterns = [
            "dockerdesktoplinuxengine/_ping",
            "500 internal server error for api route and version",
            "cannot connect to the docker daemon",
            "error during connect",
            "is the docker daemon running",
            "failed to connect to the docker daemon",
            "connection refused",
        ]
        return any(p in low for p in patterns)

    @staticmethod
    def _safe_image_name(name: str) -> str:
        return (
            str(name)
            .lower()
            .replace(" ", "_")
            .replace("/", "_")
            .replace("\\", "_")
            .replace(":", "_")
        )