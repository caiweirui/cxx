# -*- coding: utf-8 -*-
"""
Batch metrics collection and reporting utilities.

用于批量测试完成后，统一计算论文所需指标：
- 构建成功率 SR
- 平均构建耗时 T_avg
- 平均修复轮次 R_avg
- 平均 Token 消耗（M）
- 静态一致性通过率
- 产物证据通过率
- 动态测试通过率
- 人工干预率
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from statistics import mean
from typing import Any, Dict, List, Optional
import json
from pathlib import Path

def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default

def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default

def _get_nested(obj: Any, *keys: str, default=None):
    """
    从 dict / object 中尽量提取字段。
    """
    cur = obj
    for key in keys:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(key, None)
        else:
            cur = getattr(cur, key, None)
    return default if cur is None else cur

@dataclass
class ProjectMetric:
    project_name: str = ""
    success: bool = False
    build_time_sec: float = 0.0
    repair_rounds: int = 0

    # token 统计：建议这里记录“总 token”
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # 验证模块
    static_pass: bool = False
    product_pass: bool = False
    dynamic_pass: bool = False
    final_verify_pass: bool = False

    # 论文里常会需要
    manual_intervention: bool = False
    timeout: bool = False
    skipped: bool = False

    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(cls, result: Any) -> "ProjectMetric":
        """
        兼容不同项目结果结构：
        - dict
        - dataclass/object
        """
        # 优先从 result.result / summary / final_result 中提取
        project_name = _get_nested(result, "project_name", default="")

        success = bool(_get_nested(result, "success", default=False))
        build_time_sec = _to_float(_get_nested(result, "build_time_sec", default=0.0))
        repair_rounds = _to_int(_get_nested(result, "repair_rounds", default=0))

        # token usage：兼容不同字段
        prompt_tokens = _to_int(
            _get_nested(result, "prompt_tokens", default=None)
            if _get_nested(result, "prompt_tokens", default=None) is not None
            else _get_nested(result, "usage", "prompt_tokens", default=0)
        )
        completion_tokens = _to_int(
            _get_nested(result, "completion_tokens", default=None)
            if _get_nested(result, "completion_tokens", default=None) is not None
            else _get_nested(result, "usage", "completion_tokens", default=0)
        )
        total_tokens = _to_int(
            _get_nested(result, "total_tokens", default=None)
            if _get_nested(result, "total_tokens", default=None) is not None
            else _get_nested(result, "usage", "total_tokens", default=0)
        )

        # 验证结果兼容
        verification = _get_nested(result, "verification", default={}) or {}
        stages = verification.get("stages", {}) if isinstance(verification, dict) else {}

        static_pass = bool(
            _get_nested(result, "static_pass", default=None)
            if _get_nested(result, "static_pass", default=None) is not None
            else stages.get("static_consistency", {}).get("passed", False)
        )
        product_pass = bool(
            _get_nested(result, "product_pass", default=None)
            if _get_nested(result, "product_pass", default=None) is not None
            else stages.get("product_check", {}).get("passed", False)
        )
        dynamic_pass = bool(
            _get_nested(result, "dynamic_pass", default=None)
            if _get_nested(result, "dynamic_pass", default=None) is not None
            else stages.get("dynamic_test", {}).get("passed", False)
        )

        final_verify_pass = bool(
            _get_nested(result, "final_verify_pass", default=None)
            if _get_nested(result, "final_verify_pass", default=None) is not None
            else verification.get("success", False)
        )

        manual_intervention = bool(_get_nested(result, "manual_intervention", default=False))
        timeout = bool(_get_nested(result, "timeout", default=False))
        skipped = bool(_get_nested(result, "skipped", default=False))

        extra = {}
        if isinstance(result, dict):
            for k, v in result.items():
                if k not in {
                    "project_name", "success", "build_time_sec", "repair_rounds",
                    "prompt_tokens", "completion_tokens", "total_tokens",
                    "usage", "verification", "static_pass", "product_pass",
                    "dynamic_pass", "final_verify_pass", "manual_intervention",
                    "timeout", "skipped"
                }:
                    extra[k] = v

        return cls(
            project_name=project_name,
            success=success,
            build_time_sec=build_time_sec,
            repair_rounds=repair_rounds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            static_pass=static_pass,
            product_pass=product_pass,
            dynamic_pass=dynamic_pass,
            final_verify_pass=final_verify_pass,
            manual_intervention=manual_intervention,
            timeout=timeout,
            skipped=skipped,
            extra=extra,
        )

@dataclass
class BatchMetricsSummary:
    total_projects: int = 0
    successful_projects: int = 0
    failed_projects: int = 0
    skipped_projects: int = 0
    timeout_projects: int = 0
    manual_intervention_projects: int = 0

    sr: float = 0.0
    t_avg_sec: float = 0.0
    r_avg: float = 0.0
    token_avg_m: float = 0.0
    token_total_m: float = 0.0

    static_pass_rate: float = 0.0
    product_pass_rate: float = 0.0
    dynamic_pass_rate: float = 0.0
    final_verify_pass_rate: float = 0.0

    avg_prompt_tokens_m: float = 0.0
    avg_completion_tokens_m: float = 0.0

    project_details: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class BatchMetricsCollector:
    def __init__(self):
        self.projects: List[ProjectMetric] = []

    def add(self, result: Any):
        self.projects.append(ProjectMetric.from_result(result))

    def extend(self, results: List[Any]):
        for r in results:
            self.add(r)

    def summarize(self) -> BatchMetricsSummary:
        summary = BatchMetricsSummary()
        summary.total_projects = len(self.projects)

        if summary.total_projects == 0:
            return summary

        successful = [p for p in self.projects if p.success]
        failed = [p for p in self.projects if not p.success]
        skipped = [p for p in self.projects if p.skipped]
        timeout = [p for p in self.projects if p.timeout]
        manual = [p for p in self.projects if p.manual_intervention]

        summary.successful_projects = len(successful)
        summary.failed_projects = len(failed)
        summary.skipped_projects = len(skipped)
        summary.timeout_projects = len(timeout)
        summary.manual_intervention_projects = len(manual)

        summary.sr = summary.successful_projects / summary.total_projects

        # 平均构建耗时：通常只对成功项目统计更合理
        if successful:
            summary.t_avg_sec = mean([p.build_time_sec for p in successful])
            summary.r_avg = mean([p.repair_rounds for p in successful])
            summary.token_avg_m = mean([p.total_tokens for p in successful]) / 1_000_000.0
            summary.avg_prompt_tokens_m = mean([p.prompt_tokens for p in successful]) / 1_000_000.0
            summary.avg_completion_tokens_m = mean([p.completion_tokens for p in successful]) / 1_000_000.0
        else:
            summary.t_avg_sec = 0.0
            summary.r_avg = 0.0
            summary.token_avg_m = 0.0
            summary.avg_prompt_tokens_m = 0.0
            summary.avg_completion_tokens_m = 0.0

        summary.token_total_m = sum([p.total_tokens for p in self.projects]) / 1_000_000.0

        summary.static_pass_rate = sum(1 for p in self.projects if p.static_pass) / summary.total_projects
        summary.product_pass_rate = sum(1 for p in self.projects if p.product_pass) / summary.total_projects
        summary.dynamic_pass_rate = sum(1 for p in self.projects if p.dynamic_pass) / summary.total_projects
        summary.final_verify_pass_rate = sum(1 for p in self.projects if p.final_verify_pass) / summary.total_projects

        summary.project_details = [asdict(p) for p in self.projects]
        return summary

def format_summary_text(summary: BatchMetricsSummary, rag_hit_total: int = 0) -> str:
    """
    生成适合弹窗/控制台输出的中文摘要。
    """
    return (
        "📊 批量测试结果\n"
        "━" * 36 + "\n\n"
        f"构建成功率 (SR)        ：{summary.sr:.2%}\n"
        f"平均构建耗时 (T_avg)   ：{summary.t_avg_sec:.2f} s\n"
        f"平均修复轮次 (R_avg)   ：{summary.r_avg:.2f}\n"
        f"平均消耗 Token         ：{summary.token_avg_m:.4f} M\n"
        f"总消耗 Token           ：{summary.token_total_m:.4f} M\n"
        f"RAG 命中次数           ：{rag_hit_total}\n\n"
        f"静态一致性测试         ：{'✅ 通过' if summary.static_pass_rate > 0.5 else '❌ 未通过'} ({summary.static_pass_rate:.2%})\n"
        f"产物测试               ：{'✅ 通过' if summary.product_pass_rate > 0.5 else '❌ 未通过'} ({summary.product_pass_rate:.2%})\n"
        f"动态测试               ：{'✅ 通过' if summary.dynamic_pass_rate > 0.5 else '❌ 未通过'} ({summary.dynamic_pass_rate:.2%})\n\n"
        "━" * 36 + "\n"
        f"总项目数：{summary.total_projects}  |  成功：{summary.successful_projects}  |  失败：{summary.failed_projects}\n"
        f"超时：{summary.timeout_projects}  |  跳过：{summary.skipped_projects}  |  人工干预：{summary.manual_intervention_projects}"
    )

def save_summary_json(summary: BatchMetricsSummary, path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )