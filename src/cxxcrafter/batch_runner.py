import os
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

from cxxcrafter.cli import CXXCrafterCLI
from cxxcrafter.config import CXXCrafterConfig

BUILD_MARKERS = [
    "CMakeLists.txt",
    "Makefile",
    "makefile",
    "GNUmakefile",
    "meson.build",
    "configure.ac",
    "configure",
]

def is_project_root(path: str) -> bool:
    """判断一个目录是否像一个 C/C++ 项目根目录。"""
    if not os.path.isdir(path):
        return False

    for marker in BUILD_MARKERS:
        if os.path.isfile(os.path.join(path, marker)):
            return True

    return False

def discover_projects(root_dir: str, max_depth: Optional[int] = None) -> List[str]:
    """
    递归发现项目目录。

    规则：
    - 找到包含 CMakeLists.txt / Makefile / meson.build / configure.ac 等构建文件的目录
    - 默认向下扫描整个目录树
    - max_depth 可用于限制递归深度（相对 root_dir）
    """
    root_dir = os.path.abspath(root_dir)
    projects: List[str] = []
    seen = set()

    if not os.path.isdir(root_dir):
        return projects

    for current_root, dirs, files in os.walk(root_dir):
        rel = os.path.relpath(current_root, root_dir)
        depth = 0 if rel == "." else rel.count(os.sep) + 1

        if max_depth is not None and depth > max_depth:
            dirs[:] = []
            continue

        if any(marker in files for marker in BUILD_MARKERS):
            abs_path = os.path.abspath(current_root)
            if abs_path not in seen:
                seen.add(abs_path)
                projects.append(abs_path)

            # 如果当前目录已经是项目根目录，就不再继续往下扫它的子目录
            dirs[:] = []

    projects.sort()
    return projects

def safe_get(summary: Dict[str, Any], *keys, default=""):
    """从 summary 中安全取字段。"""
    obj = summary
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
        if obj is None:
            return default
    return obj

def format_one_project_result(summary: Dict[str, Any], index: int, total: int) -> str:
    """格式化单个项目的结果，写入 txt。"""
    project_name = summary.get("project", "unknown")
    overall_status = summary.get("overall_status", "unknown")
    success = summary.get("success", False)
    dockerfile_path = summary.get("dockerfile_path", "")
    created_at = summary.get("created_at", "")

    build_result = summary.get("build_result", {}) or {}
    verification_result = summary.get("verification_result", {}) or {}
    parsed_project = summary.get("parsed_project", {}) or {}
    agent_result = summary.get("agent_result", {}) or {}

    build_success = build_result.get("success", False)
    verify_success = verification_result.get("success", False)

    reason = ""
    if overall_status == "failed":
        reason = build_result.get("message", "") or verification_result.get("message", "")
        if not reason:
            reason = build_result.get("error_summary", "") or "unknown error"
    elif overall_status == "skipped":
        reason = build_result.get("message", "") or build_result.get("error_summary", "") or "skipped"

    lines = []
    lines.append("=" * 90)
    lines.append(f"[{index}/{total}] 项目: {project_name}")
    lines.append(f"时间: {created_at}")
    lines.append(f"项目路径: {parsed_project.get('project_path', '')}")
    lines.append(f"Dockerfile路径: {dockerfile_path}")
    lines.append(f"最终状态: {overall_status}")
    lines.append(f"总成功: {success}")
    lines.append(f"构建成功: {build_success}")
    lines.append(f"验证成功: {verify_success}")

    if reason:
        lines.append(f"原因: {reason}")

    lines.append("")
    lines.append("[构建结果]")
    if isinstance(build_result, dict):
        for k, v in build_result.items():
            lines.append(f"  {k}: {v}")

    lines.append("")
    lines.append("[验证结果]")
    if isinstance(verification_result, dict):
        for k, v in verification_result.items():
            lines.append(f"  {k}: {v}")

    lines.append("")
    lines.append("[解析摘要]")
    lines.append(f"  build_system: {parsed_project.get('build_system', '')}")
    lines.append(f"  file_count: {parsed_project.get('file_count', '')}")
    lines.append(f"  dir_count: {parsed_project.get('dir_count', '')}")
    lines.append(f"  cmake_source_dir_rel: {parsed_project.get('cmake_source_dir_rel', '')}")

    lines.append("")
    lines.append("[智能体结果]")
    if isinstance(agent_result, dict):
        # 只写核心信息，避免 txt 太大
        dep = agent_result.get("dependencies", {}) or {}
        cmd = agent_result.get("build_commands", {}) or {}
        rep = agent_result.get("dockerfile_repair", {}) or {}
        err = agent_result.get("error_diagnosis", {}) or {}

        lines.append(f"  dependencies.status: {dep.get('status', '')}")
        lines.append(f"  dependencies.model: {dep.get('model', '')}")
        lines.append(f"  build_commands.status: {cmd.get('status', '')}")
        lines.append(f"  build_commands.model: {cmd.get('model', '')}")
        lines.append(f"  dockerfile_repair.enabled: {rep.get('enabled', False)}")
        lines.append(f"  dockerfile_repair.success: {rep.get('success', False)}")
        lines.append(f"  dockerfile_repair.model: {rep.get('model', '')}")
        lines.append(f"  error_diagnosis.status: {err.get('status', '')}")
        lines.append(f"  error_diagnosis.model: {err.get('model', '')}")

    lines.append("=" * 90)
    lines.append("")
    return "\n".join(lines)

def write_batch_report(report_dir: str, summaries: List[Dict[str, Any]], root_dir: str) -> str:
    """把所有项目结果写到一个时间命名的 txt 文件中。"""
    os.makedirs(report_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"batch_result_{ts}.txt")

    total = len(summaries)
    passed = sum(1 for s in summaries if s.get("overall_status") == "passed")
    failed = sum(1 for s in summaries if s.get("overall_status") == "failed")
    skipped = sum(1 for s in summaries if s.get("overall_status") == "skipped")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("CXXCrafter 批量测试报告\n")
        f.write("=" * 90 + "\n")
        f.write(f"批量测试时间: {ts}\n")
        f.write(f"扫描根目录: {os.path.abspath(root_dir)}\n")
        f.write(f"项目总数: {total}\n")
        f.write(f"PASSED: {passed}\n")
        f.write(f"FAILED: {failed}\n")
        f.write(f"SKIPPED: {skipped}\n")
        f.write("=" * 90 + "\n\n")

        for idx, summary in enumerate(summaries, 1):
            f.write(format_one_project_result(summary, idx, total))

        f.write("\n")
        f.write("=" * 90 + "\n")
        f.write("汇总统计\n")
        f.write("=" * 90 + "\n")
        f.write(f"总项目数: {total}\n")
        f.write(f"成功: {passed}\n")
        f.write(f"失败: {failed}\n")
        f.write(f"跳过: {skipped}\n")

        success_rate = (passed / total * 100.0) if total else 0.0
        f.write(f"成功率: {success_rate:.2f}%\n")

    return report_path

def run_batch(
    project_paths: List[str],
    output_root: str = "./dockerfile_playground",
    logs_root: str = "./data/build_logs",
    base_image: str = "ubuntu:22.04",
    enable_build: bool = True,
    enable_verification: bool = True,
    compatibility_mode: bool = True,
    report_dir: Optional[str] = None,
    config: Optional[CXXCrafterConfig] = None,
) -> str:
    """
    批量执行多个项目，并把所有结果写到一个 txt 报告里。

    参数：
        project_paths: 项目目录列表
        output_root: 每个项目的输出目录根
        logs_root: 每个项目的日志目录根
        base_image: 基础镜像
        enable_build: 是否执行构建
        enable_verification: 是否执行验证
        compatibility_mode: 是否兼容模式
        report_dir: 最终 txt 的保存目录
        config: 可选的全局配置对象
    """
    if config is None:
        config = CXXCrafterConfig()

    # 统一写入报告目录
    if report_dir is None:
        report_dir = os.path.join(output_root, "batch_reports")

    summaries: List[Dict[str, Any]] = []

    for project_path in project_paths:
        abs_project_path = os.path.abspath(project_path)
        if not os.path.isdir(abs_project_path):
            summaries.append({
                "project": os.path.basename(abs_project_path) or abs_project_path,
                "overall_status": "failed",
                "success": False,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "build_result": {
                    "success": False,
                    "message": f"路径不存在或不是目录: {abs_project_path}",
                },
                "verification_result": {},
                "dockerfile_path": "",
                "parsed_project": {"project_path": abs_project_path},
                "agent_result": {},
            })
            continue

        try:
            cli = CXXCrafterCLI(
                project_path=abs_project_path,
                config=config,
                output_root=output_root,
                logs_root=logs_root,
                base_image=base_image,
                enable_build=enable_build,
                enable_verification=enable_verification,
                compatibility_mode=compatibility_mode,
            )
            summary = cli.process_single_project()

            if not isinstance(summary, dict):
                summary = {
                    "project": os.path.basename(abs_project_path),
                    "overall_status": "failed",
                    "success": False,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "build_result": {"success": False, "message": "process_single_project 返回值无效"},
                    "verification_result": {},
                    "dockerfile_path": "",
                    "parsed_project": {"project_path": abs_project_path},
                    "agent_result": {},
                }

            summaries.append(summary)

        except Exception as e:
            summaries.append({
                "project": os.path.basename(abs_project_path) or abs_project_path,
                "overall_status": "failed",
                "success": False,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "build_result": {
                    "success": False,
                    "message": f"运行异常: {e}",
                },
                "verification_result": {},
                "dockerfile_path": "",
                "parsed_project": {"project_path": abs_project_path},
                "agent_result": {},
            })

    report_path = write_batch_report(report_dir, summaries, root_dir=project_paths[0] if project_paths else output_root)
    return report_path

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CXXCrafter 批量测试工具")
    parser.add_argument(
        "--root",
        required=True,
        help="批量扫描根目录，自动发现项目"
    )
    parser.add_argument(
        "--output-root",
        default="./dockerfile_playground",
        help="每个项目输出目录根"
    )
    parser.add_argument(
        "--logs-root",
        default="./data/build_logs",
        help="每个项目日志目录根"
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="最终批量报告 txt 输出目录；默认是 output-root/batch_reports"
    )
    parser.add_argument(
        "--base-image",
        default="ubuntu:22.04",
        help="Docker 基础镜像"
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="只生成 Dockerfile，不执行构建"
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过验证"
    )
    parser.add_argument(
        "--minimal-deps",
        action="store_true",
        help="使用最小依赖模式"
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="扫描项目的最大深度，默认不限制"
    )
    return parser

def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    root_dir = os.path.abspath(args.root)
    projects = discover_projects(root_dir, max_depth=args.max_depth)

    if not projects:
        print(f"未在目录中发现可测试项目: {root_dir}")
        print("请确认目录下存在 CMakeLists.txt / Makefile / meson.build / configure.ac 等构建文件")
        return

    print(f"发现 {len(projects)} 个项目，开始批量执行...")

    report_path = run_batch(
        project_paths=projects,
        output_root=args.output_root,
        logs_root=args.logs_root,
        base_image=args.base_image,
        enable_build=not args.no_build,
        enable_verification=not args.no_verify,
        compatibility_mode=not args.minimal_deps,
        report_dir=args.report_dir,
    )

    print(f"\n批量测试完成，结果已写入：{report_path}")

if __name__ == "__main__":
    main()