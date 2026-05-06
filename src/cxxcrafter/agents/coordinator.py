from __future__ import annotations

import json
import os
import re
import time
import inspect
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

from cxxcrafter.agents.build_agent import BuildAgent, BuildPlan
from cxxcrafter.agents.dependency_agent import DependencyAgent, DependencyAnalysis
from cxxcrafter.agents.error_agent import ErrorAgent, FailureAnalysis
from cxxcrafter.agents.dockerfile_repair_agent import DockerfileRepairAgent, RepairPatch
from cxxcrafter.execution.executor import DockerExecutor
from cxxcrafter.generation_module.dockerfile_generator import DockerfileGenerator

try:
    from cxxcrafter.rag.rag_service import RAGService
except Exception:
    RAGService = None  # type: ignore

try:
    from cxxcrafter.verification.judge import VerificationJudge as _VerificationJudge
except Exception:
    _VerificationJudge = None  # type: ignore

T = TypeVar("T")

@dataclass
class ProjectSnapshot:
    project_path: str
    project_name: str
    source_root_rel: str
    build_system: str
    has_cmakelists: bool = False
    has_makefile: bool = False
    has_package_json: bool = False
    has_pyproject: bool = False
    has_requirements: bool = False
    has_dockerfile: bool = False
    files_sample: List[str] = field(default_factory=list)
    rule_apt_packages: List[str] = field(default_factory=list)
    rule_pip_packages: List[str] = field(default_factory=list)
    rule_env: Dict[str, str] = field(default_factory=dict)
    rule_cmake_args: List[str] = field(default_factory=list)
    rule_notes: List[str] = field(default_factory=list)

class CXXCrafterCoordinator:
    """
    轻多智能体重构版：
    - 规则层做硬判断
    - DependencyAgent / BuildAgent / ErrorAgent / RepairAgent 负责分析与修复
    - DockerfileGenerator 确定性渲染
    - ErrorAgent / RepairAgent 负责修复闭环
    - build / verify 增加明确状态与超时控制
    - 成功后可回写 RAG 知识库

    增强点：
    - agent 使用日志
    - RAG 命中日志
    - summary 记录 agent / rag / trace
    """

    def __init__(
        self,
        dependency_agent: DependencyAgent,
        build_agent: BuildAgent,
        error_agent: ErrorAgent,
        repair_agent: DockerfileRepairAgent,
        docker_executor: DockerExecutor,
        dockerfile_generator_factory=None,
        max_repair_rounds: int = 2,
        rag_service: Any = None,
        verification_judge: Any = None,
    ) -> None:
        self.dependency_agent = dependency_agent
        self.build_agent = build_agent
        self.error_agent = error_agent
        self.repair_agent = repair_agent
        self.docker_executor = docker_executor
        self.max_repair_rounds = max(0, int(max_repair_rounds))
        self.dockerfile_generator_factory = dockerfile_generator_factory
        self.rag_service = rag_service
        self.verification_judge = verification_judge

    # -------------------------
    # 公开主入口
    # -------------------------
    def process_project(
        self,
        project_path: str,
        output_dir: str,
        log_dir: str,
        enable_build: bool = True,
        enable_verification: bool = True,
        generate_only: bool = False,
        use_cache: bool = True,
        image_tag: Optional[str] = None,
        build_timeout_seconds: Optional[float] = 1800,
        verify_timeout_seconds: Optional[float] = 600,
        project_timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        project_path = str(Path(project_path).resolve())
        output_dir = str(Path(output_dir).resolve())
        log_dir = str(Path(log_dir).resolve())

        started_at = time.monotonic()

        snapshot = self._snapshot_project(project_path)
        project_name = snapshot.project_name

        project_out_dir = Path(output_dir) / project_name
        project_out_dir.mkdir(parents=True, exist_ok=True)

        dockerfile_path = str(project_out_dir / "Dockerfile")
        build_log_path = str(Path(log_dir) / f"{project_name}_build.log")
        verify_log_path = str(Path(log_dir) / f"{project_name}_verify.log")
        summary_path = str(Path(log_dir) / f"{project_name}_summary.json")

        generator = (
            self.dockerfile_generator_factory(project_path)
            if self.dockerfile_generator_factory
            else DockerfileGenerator(project_path)
        )

        # 规则层基础依赖
        dep_seed = self._rule_dependency_seed(snapshot)
        snapshot.rule_apt_packages = dep_seed["apt_packages"]
        snapshot.rule_pip_packages = dep_seed["pip_packages"]
        snapshot.rule_env = dep_seed["env"]
        snapshot.rule_cmake_args = dep_seed["cmake_args"]
        snapshot.rule_notes = dep_seed["notes"]

        # 运行时诊断：agent / rag 基线信息
        agent_usage = self._build_agent_usage_report()
        rag_usage = self._init_rag_usage_report()
        execution_trace: List[Dict[str, Any]] = []

        print("\n" + "=" * 58)
        print("CXXCrafter 启动")
        print("=" * 58)
        print(f"开始处理项目: {project_path}")
        print()
        self._print_agent_usage_banner(agent_usage)
        self._print_rag_banner(rag_usage)

        # 1) 依赖分析
        dep_analysis, dep_elapsed = self._timed_call(
            "dependency_agent.analyze",
            self.dependency_agent.analyze,
            asdict(snapshot),
        )
        dep_analysis = self._coerce_output(DependencyAnalysis, dep_analysis)
        execution_trace.append(self._trace_event("dependency_agent.analyze", dep_elapsed, success=True))

        self._append_agent_call(agent_usage, "dependency_agent", "analyze", dep_elapsed, dep_analysis)
        self._record_rag_usage_from_object(rag_usage, "dependency_analysis", dep_analysis)

        current_dep_analysis = dep_analysis

        # 2) 构建计划
        build_plan, build_plan_elapsed = self._timed_call(
            "build_agent.plan",
            self.build_agent.plan,
            snapshot=asdict(snapshot),
            deps={
                "apt_packages": dep_analysis.apt_packages,
                "pip_packages": dep_analysis.pip_packages,
                "project_packages": dep_analysis.project_packages,
                "env": dep_analysis.env,
                "cmake_args": dep_analysis.cmake_args,
                "notes": dep_analysis.notes,
                "confidence": dep_analysis.confidence,
            },
        )
        build_plan = self._coerce_output(BuildPlan, build_plan)
        execution_trace.append(self._trace_event("build_agent.plan", build_plan_elapsed, success=True))

        self._append_agent_call(agent_usage, "build_agent", "plan", build_plan_elapsed, build_plan)
        self._record_rag_usage_from_object(rag_usage, "build_plan", build_plan)

        current_plan = build_plan

        # 3) 确定性生成 Dockerfile
        dockerfile_text = generator.render(current_dep_analysis, current_plan, asdict(snapshot))
        dockerfile_path = generator.save(dockerfile_text, dockerfile_path)

        # 4) 仅生成模式：直接返回
        if generate_only or (not enable_build and not enable_verification):
            summary = self._make_summary(
                snapshot=snapshot,
                dockerfile_path=dockerfile_path,
                build_result={
                    "success": False,
                    "status": "skipped",
                    "message": "build disabled",
                },
                verification_result={
                    "success": False,
                    "status": "skipped",
                    "skipped": True,
                    "message": "verification disabled",
                    "final_verdict": {
                        "verdict": "disabled",
                        "confidence": 0,
                        "reason": "verification disabled",
                    },
                },
                overall_status="generated",
                success=True,
                dep_analysis=current_dep_analysis,
                build_plan=current_plan,
                repair_round=0,
                attempts_used=0,
                attempt_history=[],
                agent_usage=agent_usage,
                rag_usage=rag_usage,
                execution_trace=execution_trace,
            )
            summary["rag_recorded"] = False
            summary["rag_record_type"] = "none"
            self._save_json(summary_path, summary)
            print(f"✅ Dockerfile 生成完成：{dockerfile_path}")
            self._print_final_summary(snapshot.project_name, summary)
            return summary

        # 如果禁用 build，则无法做 verification，直接视为生成完成
        if not enable_build:
            summary = self._make_summary(
                snapshot=snapshot,
                dockerfile_path=dockerfile_path,
                build_result={
                    "success": False,
                    "status": "skipped",
                    "message": "build disabled",
                },
                verification_result={
                    "success": False,
                    "status": "skipped",
                    "skipped": True,
                    "message": "verification skipped because build is disabled",
                    "final_verdict": {
                        "verdict": "disabled",
                        "confidence": 0.0,
                        "reason": "verification skipped because build is disabled",
                    },
                },
                overall_status="generated",
                success=True,
                dep_analysis=current_dep_analysis,
                build_plan=current_plan,
                repair_round=0,
                attempts_used=0,
                attempt_history=[],
                agent_usage=agent_usage,
                rag_usage=rag_usage,
                execution_trace=execution_trace,
            )
            summary["rag_recorded"] = False
            summary["rag_record_type"] = "none"
            self._save_json(summary_path, summary)
            print(f"✅ Dockerfile 生成完成：{dockerfile_path}")
            self._print_final_summary(snapshot.project_name, summary)
            return summary

        # 5) 构建 / 验证 / 修复闭环
        current_dockerfile = dockerfile_path
        current_text = dockerfile_text

        max_attempts = self.max_repair_rounds + 1
        attempt_history: List[Dict[str, Any]] = []
        final_build_result: Dict[str, Any] = {
            "success": False,
            "status": "not_started",
            "message": "not started",
        }
        final_verification_result: Dict[str, Any] = {
            "success": False,
            "status": "not_started",
            "message": "not started",
            "skipped": False,
            "final_verdict": {
                "verdict": "unknown",
                "confidence": 0.0,
                "reason": "not started",
            },
        }

        for attempt_idx in range(max_attempts):
            if project_timeout_seconds is not None:
                elapsed = time.monotonic() - started_at
                if elapsed > project_timeout_seconds:
                    final_build_result = {
                        "success": False,
                        "status": "timeout",
                        "message": f"project timeout after {project_timeout_seconds} seconds before attempt {attempt_idx}",
                    }
                    final_verification_result = {
                        "success": False,
                        "status": "skipped",
                        "skipped": True,
                        "message": "project timeout before verification",
                        "final_verdict": {
                            "verdict": "timeout",
                            "confidence": 0.0,
                            "reason": "project timeout",
                        },
                    }
                    execution_trace.append(
                        self._trace_event(
                            "project_timeout",
                            0.0,
                            success=False,
                            extra={"attempt_index": attempt_idx, "project_timeout_seconds": project_timeout_seconds},
                        )
                    )
                    break

            is_initial = attempt_idx == 0
            repair_round_used = attempt_idx

            print(f"✅ Dockerfile 生成完成：{current_dockerfile}")
            if is_initial:
                print(f"[初始生成] 1/{max_attempts}")
            else:
                print(f"[修复轮次] {repair_round_used}/{self.max_repair_rounds}")

            print("➡️ 开始构建 ...")
            build_result = self.docker_executor.build(
                dockerfile_path=current_dockerfile,
                context_dir=project_path,
                image_tag=image_tag,
                log_path=build_log_path,
                timeout_seconds=build_timeout_seconds,
            )
            build_dict = self._result_to_dict(build_result)
            final_build_result = build_dict
            execution_trace.append(
                self._trace_event(
                    "docker_build",
                    float(getattr(build_result, "duration_seconds", 0.0) or 0.0),
                    success=bool(build_dict.get("success", False)),
                    extra={
                        "attempt_index": attempt_idx,
                        "status": build_dict.get("status", ""),
                        "timed_out": build_dict.get("timed_out", False),
                        "log_path": build_dict.get("log_path", build_log_path),
                    },
                )
            )

            attempt_record: Dict[str, Any] = {
                "attempt_index": attempt_idx,
                "is_initial": is_initial,
                "repair_round_used": repair_round_used,
                "dockerfile_path": current_dockerfile,
                "build_result": build_dict,
            }

            if build_dict.get("success", False):
                print("✅ 构建完成")

                # build 成功后，进入多维度验证
                if enable_verification:
                    judge = self._get_verification_judge()
                    if judge is None:
                        final_verification_result = {
                            "success": False,
                            "status": "skipped",
                            "skipped": True,
                            "message": "verification judge not available",
                            "final_verdict": {
                                "verdict": "skipped",
                                "confidence": 0.0,
                                "reason": "verification judge not available",
                            },
                        }
                        attempt_record["status"] = "build_passed_verification_skipped"
                        attempt_record["verification_result"] = final_verification_result
                        attempt_history.append(attempt_record)
                        execution_trace.append(
                            self._trace_event(
                                "verification_skipped",
                                0.0,
                                success=True,
                                extra={"reason": "verification judge not available"},
                            )
                        )
                        break

                    verification_result, judge_elapsed = self._timed_call(
                        "verification_judge.evaluate",
                        judge.evaluate,
                        snapshot=asdict(snapshot),
                        build_plan=current_plan,
                        build_result=build_dict,
                        dockerfile_path=current_dockerfile,
                        build_log_path=build_log_path,
                        image_tag=build_dict.get("image_tag", ""),
                        verify_timeout_seconds=verify_timeout_seconds,
                        log_dir=log_dir,
                        project_name=project_name,
                        enable_verification=True,
                    )
                    verification_result = self._result_to_dict(verification_result)
                    final_verification_result = verification_result
                    attempt_record["verification_result"] = verification_result

                    self._append_agent_call(
                        agent_usage,
                        "error_agent",
                        "judge_context",
                        judge_elapsed,
                        verification_result,
                    )
                    execution_trace.append(
                        self._trace_event(
                            "verification_judge.evaluate",
                            judge_elapsed,
                            success=bool(verification_result.get("success", False)),
                            extra={
                                "status": verification_result.get("status", ""),
                                "timed_out": verification_result.get("timed_out", False),
                                "log_path": verification_result.get("log_path", verify_log_path),
                            },
                        )
                    )

                    if verification_result.get("success", False):
                        attempt_record["status"] = "passed"
                        attempt_history.append(attempt_record)
                        break

                    attempt_record["status"] = "verification_failed"
                    attempt_history.append(attempt_record)

                    if attempt_idx >= self.max_repair_rounds:
                        break

                    failure_source = (
                        self._read_text_file(verification_result.get("log_path"))
                        or verification_result.get("message", "")
                    )

                    failure, error_elapsed = self._timed_call(
                        "error_agent.analyze",
                        self.error_agent.analyze,
                        build_log=failure_source,
                        dockerfile_text=current_text,
                        snapshot=asdict(snapshot),
                    )
                    failure = self._coerce_output(FailureAnalysis, failure)
                    self._append_agent_call(agent_usage, "error_agent", "analyze", error_elapsed, failure)
                    self._record_rag_usage_from_object(rag_usage, f"repair_round_{attempt_idx}_failure_analysis", failure)
                    execution_trace.append(
                        self._trace_event(
                            "error_agent.analyze",
                            error_elapsed,
                            success=True,
                            extra={"attempt_index": attempt_idx},
                        )
                    )

                    patch, repair_elapsed = self._timed_call(
                        "repair_agent.suggest_patch",
                        self.repair_agent.suggest_patch,
                        snapshot=asdict(snapshot),
                        current_plan=current_plan,
                        failure=asdict(failure),
                    )
                    patch = self._coerce_output(RepairPatch, patch)
                    self._append_agent_call(agent_usage, "repair_agent", "suggest_patch", repair_elapsed, patch)
                    self._record_rag_usage_from_object(rag_usage, f"repair_round_{attempt_idx}_patch", patch)
                    execution_trace.append(
                        self._trace_event(
                            "repair_agent.suggest_patch",
                            repair_elapsed,
                            success=True,
                            extra={"attempt_index": attempt_idx},
                        )
                    )

                    heuristic_patch = self._infer_patch_from_text(
                        text=failure_source,
                        dockerfile_text=current_text,
                        current_plan=current_plan,
                    )

                    merged_patch = self._merge_patches(patch, heuristic_patch)

                    current_plan, current_dep_analysis = self._apply_patch(
                        plan=current_plan,
                        deps=current_dep_analysis,
                        patch=merged_patch,
                        failure=failure,
                    )
                    current_text = generator.render(current_dep_analysis, current_plan, asdict(snapshot))
                    current_dockerfile = generator.save(current_text, dockerfile_path)
                    continue

                # build 成功且不需要验证
                final_verification_result = {
                    "success": False,
                    "status": "skipped",
                    "skipped": True,
                    "message": "verification disabled",
                    "final_verdict": {
                        "verdict": "disabled",
                        "confidence": 0.0,
                        "reason": "verification disabled",
                    },
                }
                attempt_record["status"] = "build_passed_verification_skipped"
                attempt_record["verification_result"] = final_verification_result
                attempt_history.append(attempt_record)
                execution_trace.append(
                    self._trace_event(
                        "verification_skipped",
                        0.0,
                        success=True,
                        extra={"reason": "verification disabled"},
                    )
                )
                break

            # build 失败：记录并尝试修复
            print("❌ 构建失败")
            attempt_record["status"] = "build_failed"
            attempt_history.append(attempt_record)

            if attempt_idx >= self.max_repair_rounds:
                break

            failure_source = self._read_text_file(build_dict.get("log_path")) or build_dict.get("message", "")
            failure, error_elapsed = self._timed_call(
                "error_agent.analyze",
                self.error_agent.analyze,
                build_log=failure_source,
                dockerfile_text=current_text,
                snapshot=asdict(snapshot),
            )
            failure = self._coerce_output(FailureAnalysis, failure)
            self._append_agent_call(agent_usage, "error_agent", "analyze", error_elapsed, failure)
            self._record_rag_usage_from_object(rag_usage, f"repair_round_{attempt_idx}_failure_analysis", failure)
            execution_trace.append(
                self._trace_event(
                    "error_agent.analyze",
                    error_elapsed,
                    success=True,
                    extra={"attempt_index": attempt_idx},
                )
            )

            patch, repair_elapsed = self._timed_call(
                "repair_agent.suggest_patch",
                self.repair_agent.suggest_patch,
                snapshot=asdict(snapshot),
                current_plan=current_plan,
                failure=asdict(failure),
            )
            patch = self._coerce_output(RepairPatch, patch)
            self._append_agent_call(agent_usage, "repair_agent", "suggest_patch", repair_elapsed, patch)
            self._record_rag_usage_from_object(rag_usage, f"repair_round_{attempt_idx}_patch", patch)
            execution_trace.append(
                self._trace_event(
                    "repair_agent.suggest_patch",
                    repair_elapsed,
                    success=True,
                    extra={"attempt_index": attempt_idx},
                )
            )

            heuristic_patch = self._infer_patch_from_text(
                text=failure_source,
                dockerfile_text=current_text,
                current_plan=current_plan,
            )

            merged_patch = self._merge_patches(patch, heuristic_patch)

            current_plan, current_dep_analysis = self._apply_patch(
                plan=current_plan,
                deps=current_dep_analysis,
                patch=merged_patch,
                failure=failure,
            )
            current_text = generator.render(current_dep_analysis, current_plan, asdict(snapshot))
            current_dockerfile = generator.save(current_text, dockerfile_path)

        # 6) 最终汇总
        attempts_used = len(attempt_history)
        repair_round_used = max(0, attempts_used - 1)

        success = False
        overall_status = "failed"

        if enable_build:
            success = bool(final_build_result.get("success", False))
            if success and enable_verification:
                if final_verification_result.get("skipped", False):
                    success = True
                    overall_status = "passed"
                else:
                    success = bool(final_verification_result.get("success", False))
                    overall_status = (
                        "passed"
                        if success
                        else ("timeout" if final_verification_result.get("timed_out", False) else "failed")
                    )
            elif success and not enable_verification:
                overall_status = "passed"
            else:
                overall_status = "failed"
        else:
            success = True
            overall_status = "generated"

        if final_build_result.get("status") == "timeout":
            overall_status = "timeout"
            success = False

        # 汇总 rag / agent / trace
        self._finalize_rag_usage(rag_usage)
        summary = self._make_summary(
            snapshot=snapshot,
            dockerfile_path=current_dockerfile,
            build_result=final_build_result,
            verification_result=final_verification_result,
            overall_status=overall_status,
            success=success,
            dep_analysis=current_dep_analysis,
            build_plan=current_plan,
            repair_round=repair_round_used,
            attempts_used=attempts_used,
            attempt_history=attempt_history,
            agent_usage=agent_usage,
            rag_usage=rag_usage,
            execution_trace=execution_trace,
        )

        # ============================================================
        # 成功后回写 RAG（只在真实 build + verification 成功时回写）
        # ============================================================
        rag_recorded = self._maybe_record_success_case(
            snapshot=snapshot,
            dep_analysis=current_dep_analysis,
            build_plan=current_plan,
            build_result=final_build_result,
            verification_result=final_verification_result,
            dockerfile_path=current_dockerfile,
            enable_build=enable_build,
            enable_verification=enable_verification,
        )
        summary["rag_recorded"] = rag_recorded
        summary["rag_record_type"] = "success" if rag_recorded else "none"

        self._save_json(summary_path, summary)
        self._print_final_summary(snapshot.project_name, summary)
        return summary

    # -------------------------
    # 规则层
    # -------------------------
    def _snapshot_project(self, project_path: str) -> ProjectSnapshot:
        root = Path(project_path)
        project_name = root.name

        has_cmakelists = False
        has_makefile = False
        has_package_json = False
        has_pyproject = False
        has_requirements = False
        has_dockerfile = False
        files_sample: List[str] = []

        source_root_rel = "."
        build_system = "unknown"

        cmake_candidates = []
        for p in root.rglob("*"):
            if len(files_sample) < 120 and p.is_file():
                files_sample.append(str(p.relative_to(root)))

            if p.name == "CMakeLists.txt":
                has_cmakelists = True
                cmake_candidates.append(p.parent)
            elif p.name in ("Makefile", "makefile"):
                has_makefile = True
            elif p.name == "package.json":
                has_package_json = True
            elif p.name == "pyproject.toml":
                has_pyproject = True
            elif p.name == "requirements.txt":
                has_requirements = True
            elif p.name.lower() == "dockerfile":
                has_dockerfile = True

        if has_cmakelists:
            build_system = "cmake"
            cmake_dir = sorted(cmake_candidates, key=lambda p: len(str(p).split(os.sep)))[0]
            source_root_rel = str(cmake_dir.relative_to(root)) if cmake_dir != root else "."
        elif has_makefile:
            build_system = "make"
            source_root_rel = "."
        elif has_package_json:
            build_system = "node"
            source_root_rel = "."
        elif has_pyproject or has_requirements:
            build_system = "python"
            source_root_rel = "."

        return ProjectSnapshot(
            project_path=str(root),
            project_name=project_name,
            source_root_rel=source_root_rel,
            build_system=build_system,
            has_cmakelists=has_cmakelists,
            has_makefile=has_makefile,
            has_package_json=has_package_json,
            has_pyproject=has_pyproject,
            has_requirements=has_requirements,
            has_dockerfile=has_dockerfile,
            files_sample=files_sample,
        )

    def _rule_dependency_seed(self, snapshot: ProjectSnapshot) -> Dict[str, Any]:
        apt_packages = []
        pip_packages = []
        env = {}
        cmake_args = []
        notes = []

        if snapshot.build_system == "cmake":
            apt_packages += ["cmake", "build-essential", "ninja-build", "pkg-config"]
            cmake_args += ["-DCMAKE_BUILD_TYPE=Release"]
            notes.append("Detected CMake project")
        elif snapshot.build_system == "make":
            apt_packages += ["build-essential", "make", "pkg-config"]
            notes.append("Detected Makefile project")
        elif snapshot.build_system == "node":
            apt_packages += ["nodejs", "npm"]
            notes.append("Detected Node project")
        elif snapshot.build_system == "python":
            apt_packages += ["python3", "python3-pip", "python3-venv"]
            notes.append("Detected Python project")

        if snapshot.has_dockerfile:
            notes.append("Existing Dockerfile found in project tree")

        return {
            "apt_packages": sorted(set(apt_packages)),
            "pip_packages": sorted(set(pip_packages)),
            "env": env,
            "cmake_args": cmake_args,
            "notes": notes,
        }

    # -------------------------
    # agent / rag diagnostics
    # -------------------------
    def _build_agent_usage_report(self) -> Dict[str, Any]:
        return {
            "dependency_agent": self._describe_agent_runtime(self.dependency_agent),
            "build_agent": self._describe_agent_runtime(self.build_agent),
            "error_agent": self._describe_agent_runtime(self.error_agent),
            "repair_agent": self._describe_agent_runtime(self.repair_agent),
        }

    def _describe_agent_runtime(self, agent: Any) -> Dict[str, Any]:
        cfg = self._extract_agent_config(agent)
        return {
            "class": agent.__class__.__name__ if agent is not None else "None",
            "model_name": cfg.get("model_name"),
            "api_key_set": bool(cfg.get("api_key_set", False)),
            "base_url": cfg.get("base_url") or "",
            "temperature": cfg.get("temperature"),
            "rag_attached": bool(cfg.get("rag_attached", False)),
            "enabled": True,
            "calls": [],
            "total_call_seconds": 0.0,
            "call_count": 0,
        }

    def _extract_agent_config(self, agent: Any) -> Dict[str, Any]:
        """
        从 agent 实例中尽可能提取当前有效配置。
        不依赖具体实现，只做容错式读取。
        """
        if agent is None:
            return {}

        candidate_cfg = None
        for attr in ("config", "runtime_config", "_config", "cfg"):
            try:
                if hasattr(agent, attr):
                    candidate_cfg = getattr(agent, attr)
                    if candidate_cfg is not None:
                        break
            except Exception:
                pass

        def pick(*names: str) -> Any:
            # 先看 agent 本体
            for name in names:
                try:
                    if hasattr(agent, name):
                        v = getattr(agent, name)
                        if v is not None:
                            return v
                except Exception:
                    pass
            # 再看嵌套 config
            if candidate_cfg is not None:
                if isinstance(candidate_cfg, dict):
                    for name in names:
                        if name in candidate_cfg and candidate_cfg[name] is not None:
                            return candidate_cfg[name]
                else:
                    for name in names:
                        try:
                            if hasattr(candidate_cfg, name):
                                v = getattr(candidate_cfg, name)
                                if v is not None:
                                    return v
                        except Exception:
                            pass
            return None

        model_name = pick("model_name", "model", "llm_model", "model_id")
        api_key = pick("api_key", "api_token", "key")
        base_url = pick("base_url", "url", "endpoint")
        temperature = pick("temperature", "temp")
        rag_attached = pick("rag_service", "rag", "retriever")

        return {
            "model_name": model_name,
            "api_key_set": bool(api_key),
            "base_url": base_url,
            "temperature": temperature,
            "rag_attached": rag_attached is not None,
        }

    def _init_rag_usage_report(self) -> Dict[str, Any]:
        return {
            "enabled": self.rag_service is not None,
            "service_class": self.rag_service.__class__.__name__ if self.rag_service is not None else None,
            "stages": {},
            "hit_stage_count": 0,
            "hit_doc_count_total": 0,
            "hit_stages": [],
            "miss_stages": [],
            "last_hit_stage": None,
            "last_context_length": 0,
        }

    def _print_agent_usage_banner(self, agent_usage: Dict[str, Any]) -> None:
        print("[Agent] 运行时配置：")
        for role, info in agent_usage.items():
            print(
                f"  - {role}: class={info.get('class')} | "
                f"model={self._mask_value(info.get('model_name'))} | "
                f"api_key={'set' if info.get('api_key_set') else 'unset'} | "
                f"base_url={self._mask_value(info.get('base_url')) or 'unset'} | "
                f"rag={'on' if info.get('rag_attached') else 'off'}"
            )
        print()

    def _print_rag_banner(self, rag_usage: Dict[str, Any]) -> None:
        print("[RAG] 运行时状态：")
        print(f"  - enabled: {rag_usage.get('enabled', False)}")
        print(f"  - service_class: {rag_usage.get('service_class') or 'None'}")
        print()

    def _record_rag_usage_from_object(self, rag_usage: Dict[str, Any], stage: str, obj: Any) -> None:
        context = self._extract_rag_context(obj)
        if context["hit"]:
            rag_usage["stages"][stage] = context
            rag_usage["hit_stage_count"] += 1
            rag_usage["hit_doc_count_total"] += int(context.get("doc_count", 0))
            rag_usage["hit_stages"].append(stage)
            rag_usage["last_hit_stage"] = stage
            rag_usage["last_context_length"] = int(context.get("context_length", 0))
            print(
                f"[RAG][{stage}] 命中 {context.get('doc_count', 0)} 份文档 | "
                f"context_len={context.get('context_length', 0)}"
            )
            for idx, snippet in enumerate(context.get("preview", [])[:3], start=1):
                print(f"[RAG][{stage}] preview {idx}: {snippet}")
            print()
        else:
            rag_usage["stages"][stage] = context
            rag_usage["miss_stages"].append(stage)
            if rag_usage.get("enabled", False):
                print(f"[RAG][{stage}] 未检测到显式 rag_docs_context，但 RAG 服务处于启用状态。\n")

    def _extract_rag_context(self, obj: Any) -> Dict[str, Any]:
        """
        从 dataclass / dict / 普通对象中提取 rag 证据。
        重点找：
        - raw.rag_docs_context
        - rag_docs_context
        - rag_context
        """
        payload = self._to_dict(obj)
        raw = payload.get("raw", {}) if isinstance(payload, dict) else {}

        context = None
        source = None

        if isinstance(raw, dict):
            for key in ("rag_docs_context", "rag_context", "retrieved_docs", "docs_context"):
                if raw.get(key):
                    context = raw.get(key)
                    source = f"raw.{key}"
                    break

        if context is None and isinstance(payload, dict):
            for key in ("rag_docs_context", "rag_context", "retrieved_docs", "docs_context"):
                if payload.get(key):
                    context = payload.get(key)
                    source = key
                    break

        if context is None and obj is not None:
            for key in ("rag_docs_context", "rag_context", "retrieved_docs", "docs_context"):
                try:
                    if hasattr(obj, key):
                        v = getattr(obj, key)
                        if v:
                            context = v
                            source = key
                            break
                except Exception:
                    pass

        if not context:
            return {
                "hit": False,
                "doc_count": 0,
                "context_length": 0,
                "preview": [],
                "source": None,
            }

        if not isinstance(context, str):
            try:
                context = json.dumps(context, ensure_ascii=False, indent=2)
            except Exception:
                context = str(context)

        doc_count = len(re.findall(r"\[DOC\]", context))
        preview = self._make_context_preview(context)

        return {
            "hit": True,
            "doc_count": doc_count if doc_count > 0 else 1,
            "context_length": len(context),
            "preview": preview,
            "source": source,
        }

    def _make_context_preview(self, context: str, max_items: int = 3, max_len: int = 260) -> List[str]:
        lines = [line.rstrip() for line in str(context).splitlines()]
        candidates: List[str] = []
        for line in lines:
            s = line.strip()
            if not s:
                continue
            candidates.append(s)
            if len(candidates) >= max_items * 2:
                break

        if not candidates:
            candidates = [str(context)[:max_len]]

        previews: List[str] = []
        for item in candidates[:max_items]:
            item = item.replace("\t", " ")
            if len(item) > max_len:
                item = item[:max_len - 3] + "..."
            previews.append(item)
        return previews

    def _finalize_rag_usage(self, rag_usage: Dict[str, Any]) -> None:
        if rag_usage.get("hit_stage_count", 0) == 0:
            rag_usage["last_hit_stage"] = None

    def _append_agent_call(self, agent_usage: Dict[str, Any], agent_key: str, call_name: str, seconds: float, result: Any) -> None:
        if agent_key not in agent_usage:
            return

        info = agent_usage[agent_key]
        info["call_count"] = int(info.get("call_count", 0)) + 1
        info["total_call_seconds"] = float(info.get("total_call_seconds", 0.0)) + float(seconds)

        summary = self._summarize_result_for_trace(result)
        info.setdefault("calls", []).append(
            {
                "call": call_name,
                "seconds": float(seconds),
                "summary": summary,
            }
        )

    def _summarize_result_for_trace(self, result: Any) -> Dict[str, Any]:
        payload = self._to_dict(result)
        summary: Dict[str, Any] = {}

        if isinstance(payload, dict):
            for key in ("status", "success", "timed_out", "verdict", "confidence", "project_family", "feature_tags"):
                if key in payload:
                    summary[key] = payload[key]

            # 额外记录一些对排查有用的字段
            for key in ("message", "reason", "final_verdict"):
                if key in payload:
                    summary[key] = payload[key]

            if "raw" in payload and isinstance(payload["raw"], dict):
                raw = payload["raw"]
                if "rag_docs_context" in raw:
                    summary["rag_context_detected"] = True
                    summary["rag_context_length"] = len(str(raw.get("rag_docs_context", "")))
                if "notes" in raw:
                    summary["raw_notes"] = raw.get("notes")

        else:
            summary["type"] = type(result).__name__

        return summary

    def _trace_event(self, name: str, seconds: float, success: bool, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        event = {
            "event": name,
            "seconds": float(seconds),
            "success": bool(success),
        }
        if extra:
            event.update(extra)
        return event

    def _timed_call(self, label: str, func, *args, **kwargs):
        started = time.monotonic()
        result = func(*args, **kwargs)
        elapsed = time.monotonic() - started
        return result, elapsed

    # -------------------------
    # 成功后回写 RAG
    # -------------------------
    def _maybe_record_success_case(
        self,
        snapshot: ProjectSnapshot,
        dep_analysis: DependencyAnalysis,
        build_plan: BuildPlan,
        build_result: Dict[str, Any],
        verification_result: Dict[str, Any],
        dockerfile_path: str,
        enable_build: bool,
        enable_verification: bool,
    ) -> bool:
        """
        只在真实 build + verification 成功时回写成功案例。
        """
        if self.rag_service is None:
            return False

        if not enable_build or not enable_verification:
            return False

        if not bool(build_result.get("success", False)):
            return False

        if not bool(verification_result.get("success", False)):
            return False

        success_signature = self._build_success_signature(
            snapshot=snapshot,
            dep_analysis=dep_analysis,
            build_plan=build_plan,
            build_result=build_result,
            verification_result=verification_result,
            dockerfile_path=dockerfile_path,
        )
        solution_text = self._build_success_solution(
            snapshot=snapshot,
            dep_analysis=dep_analysis,
            build_plan=build_plan,
            build_result=build_result,
            verification_result=verification_result,
            dockerfile_path=dockerfile_path,
        )

        try:
            self.rag_service.record_success_case(
                success_signature=success_signature,
                solution=solution_text,
                project=snapshot.project_name,
            )
            return True
        except Exception:
            return False

    def _build_success_signature(
        self,
        snapshot: ProjectSnapshot,
        dep_analysis: DependencyAnalysis,
        build_plan: BuildPlan,
        build_result: Dict[str, Any],
        verification_result: Dict[str, Any],
        dockerfile_path: str,
    ) -> str:
        tags = ",".join((dep_analysis.feature_tags or [])[:20])
        apt = ",".join((dep_analysis.apt_packages or [])[:20])
        pip = ",".join((dep_analysis.pip_packages or [])[:20])
        build_cmds = " ; ".join((build_plan.build_commands or [])[:5])
        test_cmds = " ; ".join((build_plan.test_commands or [])[:5])

        lines = [
            f"project={snapshot.project_name}",
            f"build_system={snapshot.build_system}",
            f"family={dep_analysis.project_family}",
            f"tags={tags}",
            f"source_root={snapshot.source_root_rel}",
            f"apt={apt}",
            f"pip={pip}",
            f"cmake_args={','.join(dep_analysis.cmake_args or [])}",
            f"build_commands={build_cmds}",
            f"test_commands={test_cmds}",
            f"runtime_command={build_plan.runtime_command}",
            f"dockerfile_path={dockerfile_path}",
            f"build_success={build_result.get('success', False)}",
            f"verification_success={verification_result.get('success', False)}",
            f"verification_status={verification_result.get('status', '')}",
        ]
        return "\n".join(lines)

    def _build_success_solution(
        self,
        snapshot: ProjectSnapshot,
        dep_analysis: DependencyAnalysis,
        build_plan: BuildPlan,
        build_result: Dict[str, Any],
        verification_result: Dict[str, Any],
        dockerfile_path: str,
    ) -> str:
        payload = {
            "project": snapshot.project_name,
            "project_path": snapshot.project_path,
            "build_system": snapshot.build_system,
            "source_root_rel": snapshot.source_root_rel,
            "project_family": dep_analysis.project_family,
            "feature_tags": dep_analysis.feature_tags,
            "dependency_analysis": asdict(dep_analysis),
            "build_plan": asdict(build_plan),
            "build_result": {
                "success": build_result.get("success", False),
                "status": build_result.get("status", ""),
                "message": build_result.get("message", ""),
            },
            "verification_result": {
                "success": verification_result.get("success", False),
                "status": verification_result.get("status", ""),
                "message": verification_result.get("message", ""),
                "exit_code": verification_result.get("exit_code"),
            },
            "dockerfile_path": dockerfile_path,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # -------------------------
    # failure heuristics
    # -------------------------
    def _infer_patch_from_text(self, text: str, dockerfile_text: str, current_plan: BuildPlan) -> RepairPatch:
        text = text or ""
        dockerfile_text = dockerfile_text or ""
        low = text.lower()

        patch = RepairPatch()

        def add_apt(pkg: str) -> None:
            if pkg and pkg not in patch.add_apt_packages:
                patch.add_apt_packages.append(pkg)

        def add_note(note: str) -> None:
            if note and note not in patch.notes:
                patch.notes.append(note)

        if any(
            s in low
            for s in [
                "502 bad gateway",
                "failed to fetch",
                "temporary failure resolving",
                "could not resolve",
                "repository is not signed",
                "inrelease",
                "the repository",
                "certificate verification failed",
            ]
        ):
            add_note("APT repository/network failure detected. Use mirror + retries and keep apt steps minimal.")
            if "apt_source_mirror" not in dockerfile_text.lower() and "acquire::retries" not in dockerfile_text.lower():
                add_note("Re-render Dockerfile with mirrored apt sources and retry settings.")

        if re.search(r"(cmake: command not found|cmake.*not found)", low):
            add_apt("cmake")
        if re.search(r"(ninja: command not found|ninja-build.*not found)", low):
            add_apt("ninja-build")
        if re.search(r"(pkg-config: command not found|pkg-config.*not found)", low):
            add_apt("pkg-config")
        if re.search(r"(make: command not found|make.*not found)", low):
            add_apt("make")
        if re.search(r"(g\+\+: command not found|gcc: command not found|cc: command not found|c\+\+: command not found)", low):
            add_apt("build-essential")
        if re.search(r"(python3: command not found|python.*not found)", low):
            add_apt("python3")
            add_apt("python3-pip")
            add_apt("python3-venv")

        if "no rule to make target 'test'" in low or 'no rule to make target "test"' in low or "target 'test' not found" in low:
            bad_tests = [cmd for cmd in current_plan.test_commands if self._looks_like_test_target(cmd)]
            patch.remove_test_commands = self._merge_str_lists(patch.remove_test_commands, bad_tests)
            add_note("Remove invalid test target; use ctest only if tests are configured.")

        if "ctest" in low and ("failed" in low or "error" in low):
            add_note("CTest failed; check whether tests are actually enabled and runtime dependencies are installed.")

        if "undefined reference" in low or "ld:" in low or "linker command failed" in low:
            add_note("Linker failure detected; missing system libraries or wrong link order may be the cause.")

        patch.add_apt_packages = self._merge_str_lists(patch.add_apt_packages, [])
        patch.notes = self._merge_str_lists(patch.notes, [])
        return patch

    @staticmethod
    def _looks_like_test_target(cmd: str) -> bool:
        c = (cmd or "").lower()
        if "ctest" in c:
            return True
        if re.search(r"\btest\b", c):
            return True
        return False

    def _merge_patches(self, a: RepairPatch, b: RepairPatch) -> RepairPatch:
        return RepairPatch(
            add_apt_packages=self._merge_str_lists(a.add_apt_packages, b.add_apt_packages),
            add_pip_packages=self._merge_str_lists(a.add_pip_packages, b.add_pip_packages),
            add_preinstall_commands=self._merge_str_lists(a.add_preinstall_commands, b.add_preinstall_commands),
            add_build_commands=self._merge_str_lists(a.add_build_commands, b.add_build_commands),
            add_test_commands=self._merge_str_lists(a.add_test_commands, b.add_test_commands),
            remove_build_commands=self._merge_str_lists(a.remove_build_commands, b.remove_build_commands),
            remove_test_commands=self._merge_str_lists(a.remove_test_commands, b.remove_test_commands),
            replace_base_image=a.replace_base_image or b.replace_base_image,
            notes=self._merge_str_lists(a.notes, b.notes),
            confidence=max(a.confidence, b.confidence),
            raw={**b.raw, **a.raw},
        )

    # -------------------------
    # patch / merge
    # -------------------------
    def _apply_patch(
        self,
        plan: BuildPlan,
        deps: DependencyAnalysis,
        patch: RepairPatch,
        failure: FailureAnalysis,
    ) -> Tuple[BuildPlan, DependencyAnalysis]:
        new_deps = replace(
            deps,
            apt_packages=self._merge_str_lists(deps.apt_packages, patch.add_apt_packages, failure.add_apt_packages),
            pip_packages=self._merge_str_lists(deps.pip_packages, patch.add_pip_packages, failure.add_pip_packages),
            notes=self._merge_str_lists(deps.notes, patch.notes, failure.suggested_actions, failure.likely_causes),
            confidence=max(deps.confidence, patch.confidence, failure.confidence),
            raw={**deps.raw, **patch.raw, **failure.raw},
        )

        new_preinstall = list(plan.preinstall_commands)
        new_build = list(plan.build_commands)
        new_test = list(plan.test_commands)
        new_notes = list(plan.notes)

        def add_unique(target: List[str], items: List[str]) -> None:
            for item in items:
                item = str(item).strip()
                if item and item not in target:
                    target.append(item)

        def remove_matching(target: List[str], patterns: List[str]) -> List[str]:
            if not patterns:
                return target
            out = []
            for cmd in target:
                matched = False
                for pat in patterns:
                    p = str(pat).strip()
                    if not p:
                        continue
                    if cmd == p or p in cmd:
                        matched = True
                        break
                    try:
                        if re.search(p, cmd, re.I):
                            matched = True
                            break
                    except re.error:
                        pass
                if not matched:
                    out.append(cmd)
            return out

        new_base_image = patch.replace_base_image or failure.change_base_image or plan.base_image

        for pkg in patch.add_apt_packages + failure.add_apt_packages:
            cmd = f"apt-get install -y {pkg}"
            if cmd not in new_preinstall:
                new_preinstall.append(cmd)

        for pkg in patch.add_pip_packages + failure.add_pip_packages:
            cmd = f"python3 -m pip install --no-cache-dir {pkg}"
            if cmd not in new_preinstall:
                new_preinstall.append(cmd)

        add_unique(new_preinstall, patch.add_preinstall_commands)
        add_unique(new_build, patch.add_build_commands)
        add_unique(new_build, failure.update_build_commands)
        add_unique(new_test, patch.add_test_commands)

        new_build = remove_matching(new_build, patch.remove_build_commands)
        new_test = remove_matching(new_test, patch.remove_test_commands)

        add_unique(new_notes, patch.notes)
        add_unique(new_notes, failure.suggested_actions)
        add_unique(new_notes, failure.likely_causes)

        new_plan = replace(
            plan,
            base_image=new_base_image,
            preinstall_commands=new_preinstall,
            build_commands=new_build,
            test_commands=new_test,
            notes=new_notes,
            confidence=max(plan.confidence, patch.confidence, failure.confidence),
            raw={**plan.raw, **patch.raw, **failure.raw},
        )

        return new_plan, new_deps

    def _merge_str_lists(self, *lists: Any) -> List[str]:
        out: List[str] = []
        seen = set()

        for seq in lists:
            if not seq:
                continue

            if isinstance(seq, (str, bytes)):
                seq = [seq]

            for item in seq:
                s = str(item).strip()
                if not s:
                    continue
                if s in seen:
                    continue
                seen.add(s)
                out.append(s)

        return out

    # -------------------------
    # summary / i/o
    # -------------------------
    def _make_summary(
        self,
        snapshot: ProjectSnapshot,
        dockerfile_path: str,
        build_result: Dict[str, Any],
        verification_result: Dict[str, Any],
        overall_status: str,
        success: bool,
        dep_analysis: DependencyAnalysis,
        build_plan: BuildPlan,
        repair_round: int,
        attempts_used: int,
        attempt_history: List[Dict[str, Any]],
        agent_usage: Dict[str, Any],
        rag_usage: Dict[str, Any],
        execution_trace: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "project": snapshot.project_name,
            "project_path": snapshot.project_path,
            "source_root": snapshot.project_path,
            "dockerfile_path": dockerfile_path,
            "overall_status": overall_status,
            "success": success,
            "repair_round": repair_round,
            "attempts_used": attempts_used,
            "attempt_history": attempt_history,
            "build_result": build_result,
            "verification_result": verification_result,
            "snapshot": asdict(snapshot),
            "dependency_analysis": asdict(dep_analysis),
            "build_plan": asdict(build_plan),
            "agent_usage": agent_usage,
            "llm_usage": agent_usage,
            "rag_usage": rag_usage,
            "execution_trace": execution_trace,
            "runtime_diagnostics": {
                "agent_count": len(agent_usage or {}),
                "rag_enabled": rag_usage.get("enabled", False),
                "rag_hit_stage_count": rag_usage.get("hit_stage_count", 0),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            },
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _save_json(self, path: str, data: Dict[str, Any]) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _print_final_summary(self, project_name: str, summary: Dict[str, Any]) -> None:
        build_result = summary.get("build_result", {}) or {}
        verify_result = summary.get("verification_result", {}) or {}
        rag_usage = summary.get("rag_usage", {}) or {}

        print("\n" + "=" * 60)
        print("最终汇总")
        print("=" * 60)
        print(f"项目          : {project_name}")
        print(f"构建成功      : {build_result.get('success', False)}")
        print(f"构建状态      : {build_result.get('status', 'unknown')}")
        print(f"验证成功      : {verify_result.get('success', False)}")
        print(f"验证状态      : {verify_result.get('status', 'unknown')}")
        print(f"修复轮次      : {summary.get('repair_round', 0)}")
        print(f"尝试次数      : {summary.get('attempts_used', 0)}")
        print(f"最终状态      : {str(summary.get('overall_status', '')).upper()}")
        print(f"RAG 命中阶段数: {rag_usage.get('hit_stage_count', 0)}")
        print(f"RAG 命中文档数: {rag_usage.get('hit_doc_count_total', 0)}")
        print("=" * 60 + "\n")

    # -------------------------
    # helpers
    # -------------------------
    def _result_to_dict(self, result: Any) -> Dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        if is_dataclass(result):
            return asdict(result)
        if hasattr(result, "__dict__"):
            return dict(result.__dict__)
        return {"value": str(result)}

    def _coerce_output(self, cls: Type[T], value: Any) -> T:
        """
        尽量把 agent 输出转成目标 dataclass 实例。
        兼容：
        - 本来就是 dataclass
        - dict
        - 带 __dict__ 的对象
        """
        if isinstance(value, cls):
            return value

        if is_dataclass(value):
            try:
                data = asdict(value)
                return cls(**data)  # type: ignore[misc]
            except Exception:
                pass

        if isinstance(value, dict):
            try:
                return cls(**value)  # type: ignore[misc]
            except Exception:
                pass

            try:
                sig = inspect.signature(cls)
                params = sig.parameters
                if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                    return cls(**value)  # type: ignore[misc]
                filtered = {k: v for k, v in value.items() if k in params}
                return cls(**filtered)  # type: ignore[misc]
            except Exception:
                pass

        if hasattr(value, "__dict__"):
            try:
                data = dict(value.__dict__)
                return cls(**data)  # type: ignore[misc]
            except Exception:
                pass

        # 最后兜底：直接尝试无参构造
        try:
            return cls()  # type: ignore[misc]
        except Exception as e:
            raise TypeError(f"Cannot coerce {type(value).__name__} to {cls.__name__}: {e}") from e

    def _get_verification_judge(self) -> Any:
        if self.verification_judge is not None:
            return self.verification_judge
        if _VerificationJudge is None:
            return None
        try:
            self.verification_judge = _VerificationJudge(docker_executor=self.docker_executor)
        except Exception:
            self.verification_judge = None
        return self.verification_judge

    def _read_text_file(self, path: Any) -> str:
        if not path:
            return ""
        try:
            p = Path(str(path))
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
        return ""

    def _mask_value(self, value: Any) -> str:
        if value is None:
            return ""
        s = str(value)
        if not s:
            return ""
        if len(s) <= 8:
            return "***"
        return s[:4] + "..." + s[-4:]