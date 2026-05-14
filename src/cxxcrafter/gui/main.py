# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
import tkinter as tk
from io import StringIO
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Dict, Optional, Sequence
# -*- coding: utf-8 -*-

from tkinter import messagebox

# =========================================================
# 添加 src 到路径
# =========================================================
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

os.environ.setdefault("LLM_USAGE_LOG_PATH", str(PROJECT_ROOT / "logs" / "llm_usage.log"))
os.environ.setdefault("LLM_TRACE_LOG_PATH", str(PROJECT_ROOT / "logs" / "llm_trace.jsonl"))
os.environ.setdefault("AGENT_TRACE_LOG_PATH", str(PROJECT_ROOT / "logs" / "agent_trace.jsonl"))
os.environ.setdefault("OPENAI_BASE_URL", "https://poloapi.top/v1/chat/completions")
os.environ.setdefault("LLM_PROVIDER_MODE", "auto")

from cxxcrafter.cli import (
    AgentRuntimeConfig,
    CXXCrafterCLI,
    CXXCrafterConfig,
)
from cxxcrafter.execution.batch_executor import BatchExecutor
from cxxcrafter.runtime.os_compat import format_os_tip, open_path
from cxxcrafter.utils.batch_metrics import format_summary_text, BatchMetricsCollector, save_summary_json

AGENT_LABELS = [
    ("dependency", "Dependency Agent"),
    ("build", "Build Agent"),
    ("error", "Error Agent"),
    ("repair", "Dockerfile Repair Agent"),
]

def strip_outer_quotes(text: str) -> str:
    if text is None:
        return ""
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].strip()
    return text

def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default

def _safe_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default

def _parse_optional_positive_float(value: Any) -> Optional[float]:
    """
    将输入解析为可选正数浮点数：
    - 空字符串 / None / 0 / 0.0 / 非法值 => None
    - 正数 => float
    """
    try:
        text = str(value).strip()
        if not text:
            return None
        num = float(text)
        if num <= 0:
            return None
        return num
    except Exception:
        return None

def _summary_status(summary: Dict[str, Any]) -> str:
    return str(summary.get("overall_status", "") or "").lower().strip()

def _is_timeout_summary(summary: Dict[str, Any]) -> bool:
    status = _summary_status(summary)
    if status == "timeout":
        return True
    build_result = summary.get("build_result", {}) or {}
    ver_result = summary.get("verification_result", {}) or {}
    return str(build_result.get("status", "")).lower() == "timeout" or str(ver_result.get("status", "")).lower() == "timeout"

def _is_docker_unavailable_summary(summary: Dict[str, Any]) -> bool:
    status = _summary_status(summary)
    if status == "docker_unavailable":
        return True

    build_result = summary.get("build_result", {}) or {}
    ver_result = summary.get("verification_result", {}) or {}

    build_status = str(build_result.get("status", "") or "").lower()
    ver_status = str(ver_result.get("status", "") or "").lower()
    if build_status == "docker_unavailable" or ver_status == "docker_unavailable":
        return True

    text = " ".join([
        str(build_result.get("message", "")),
        str(ver_result.get("message", "")),
        str(summary.get("error", "")),
    ]).lower()

    patterns = [
        "cannot connect to the docker daemon",
        "error during connect",
        "is the docker daemon running",
        "failed to connect to the docker daemon",
        "permission denied while trying to connect to the docker daemon socket",
        "dockerdesktoplinuxengine/_ping",
        "500 internal server error for api route and version",
    ]
    return any(p in text for p in patterns)

class StdoutRedirector(StringIO):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def write(self, string):
        if not string:
            return
        try:
            self.text_widget.after(0, self._append, string)
        except Exception:
            pass

    def _append(self, string):
        try:
            self.text_widget.configure(state="normal")
            self.text_widget.insert(tk.END, string)
            self.text_widget.see(tk.END)
            self.text_widget.configure(state="disabled")
        except Exception:
            pass

    def flush(self):
        pass

def _build_agent_runtime_config(prefix: str, config: Dict[str, Any]) -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        use_separate_config=bool(config.get(f"{prefix}_use_separate_config", False)),
        model_name=strip_outer_quotes(config.get(f"{prefix}_model_name", "")) or None,
        api_key=strip_outer_quotes(config.get(f"{prefix}_api_key", "")) or None,
        base_url=strip_outer_quotes(config.get(f"{prefix}_base_url", "")) or None,
    )

def build_cli_from_config(config: Dict[str, Any]) -> CXXCrafterCLI:
    """
    根据 GUI 配置构造 CLI。
    """
    cfg = CXXCrafterConfig(
        api_key=strip_outer_quotes(config.get("api_key", "")) or None,
        base_url=strip_outer_quotes(config.get("base_url", "")) or None,
        model_name=strip_outer_quotes(config.get("model_name", "")) or None,
        provider_mode=str(config.get("provider_mode", "auto") or "auto").strip() or "auto",
        enable_build=bool(config.get("enable_build", True)),
        enable_verification=bool(config.get("enable_verification", True)),
        generate_only=bool(config.get("generate_only", False)),
        max_repair_rounds=_safe_int(config.get("max_repair_rounds", 2), 2),
        use_cache=bool(config.get("use_cache", True)),
        output_dir=strip_outer_quotes(config.get("output_dir", str(PROJECT_ROOT / "dockerfile_playground"))),
        log_dir=strip_outer_quotes(config.get("log_dir", str(PROJECT_ROOT / "data" / "build_logs"))),
        image_tag=strip_outer_quotes(config.get("image_tag", "")) or None,
        build_timeout_seconds=_safe_float(config.get("build_timeout_seconds", 1800), 1800.0),
        verify_timeout_seconds=_safe_float(config.get("verify_timeout_seconds", 600), 600.0),
        project_timeout_seconds=_parse_optional_positive_float(config.get("project_timeout_seconds", None)),
        enable_rag=bool(config.get("enable_rag", True)),
        use_buildkit=bool(config.get("use_buildkit", True)),
        buildkit_progress=str(config.get("buildkit_progress", "plain") or "plain"),
        default_base_image=str(config.get("default_base_image", "ubuntu:24.04") or "ubuntu:24.04"),
        dependency_agent=_build_agent_runtime_config("dependency", config),
        build_agent=_build_agent_runtime_config("build", config),
        error_agent=_build_agent_runtime_config("error", config),
        repair_agent=_build_agent_runtime_config("repair", config),
    )
    return CXXCrafterCLI(cfg)

def run_single_project(
    cli: CXXCrafterCLI,
    project_path: str,
    output_dir: str,
    log_dir: str,
    enable_build: bool,
    enable_verification: bool,
    generate_only: bool,
    use_cache: bool,
    build_timeout_seconds: float,
    verify_timeout_seconds: float,
    project_timeout_seconds: Optional[float],
) -> Dict[str, Any]:
    return cli.process_project(
        project_path=project_path,
        output_dir=output_dir,
        log_dir=log_dir,
        enable_build=enable_build,
        enable_verification=enable_verification,
        generate_only=generate_only,
        use_cache=use_cache,
        build_timeout_seconds=build_timeout_seconds,
        verify_timeout_seconds=verify_timeout_seconds,
        project_timeout_seconds=project_timeout_seconds,
    )

def run_batch_projects(
    cli: CXXCrafterCLI,
    project_paths: Sequence[str],
    output_dir: str,
    log_dir: str,
    enable_build: bool,
    enable_verification: bool,
    generate_only: bool,
    use_cache: bool,
    build_timeout_seconds: float,
    verify_timeout_seconds: float,
    project_timeout_seconds: Optional[float],
    stop_on_docker_error: bool,
    max_consecutive_failures: int,
) -> Dict[str, Any]:
    coordinator = cli.create_coordinator()
    batch = BatchExecutor(coordinator)
    return batch.run(
        project_paths=project_paths,
        output_dir=output_dir,
        log_dir=log_dir,
        enable_build=enable_build,
        enable_verification=enable_verification,
        generate_only=generate_only,
        use_cache=use_cache,
        image_tag_prefix="cxxcrafter",
        build_timeout_seconds=build_timeout_seconds,
        verify_timeout_seconds=verify_timeout_seconds,
        project_timeout_seconds=project_timeout_seconds,
        stop_on_docker_error=stop_on_docker_error,
        max_consecutive_failures=max_consecutive_failures,
    )

class CXXCrafterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CXXCrafter - GUI")
        self.root.geometry("1180x860")
        self.root.minsize(1040, 720)

        self.running = False
        self.worker_thread = None
        self.status_var = tk.StringVar(value="就绪")

        self._build_vars()
        self._build_ui()

        self.redirector = StdoutRedirector(self.log_text)
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr
        sys.stdout = self.redirector
        sys.stderr = self.redirector

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._append_line(format_os_tip())
        self._append_line("")

    def _build_vars(self):
        self.project_path_var = tk.StringVar()
        self.repo_list_var = tk.StringVar()

        # 全局配置
        self.global_api_key_var = tk.StringVar(value="")
        self.global_base_url_var = tk.StringVar(value="")
        self.global_model_var = tk.StringVar(value="gpt-4o")
        self.global_provider_mode_var = tk.StringVar(value="auto")

        self.output_dir_var = tk.StringVar(value=str(PROJECT_ROOT / "dockerfile_playground"))
        self.logs_dir_var = tk.StringVar(value=str(PROJECT_ROOT / "data" / "build_logs"))
        self.default_base_image_var = tk.StringVar(value="ubuntu:24.04")
        self.buildkit_progress_var = tk.StringVar(value="plain")

        self.enable_build_var = tk.BooleanVar(value=True)
        self.enable_verify_var = tk.BooleanVar(value=True)
        self.generate_only_var = tk.BooleanVar(value=False)
        self.use_cache_var = tk.BooleanVar(value=True)
        self.use_buildkit_var = tk.BooleanVar(value=True)
        self.enable_rag_var = tk.BooleanVar(value=True)

        self.max_repair_rounds_var = tk.StringVar(value="2")
        self.build_timeout_seconds_var = tk.StringVar(value="1800")
        self.verify_timeout_seconds_var = tk.StringVar(value="600")
        self.project_timeout_seconds_var = tk.StringVar(value="")
        self.max_consecutive_failures_var = tk.StringVar(value="3")
        self.stop_on_docker_error_var = tk.BooleanVar(value=True)

        self.batch_summary_path_var = tk.StringVar(value="")

        # 四个智能体：是否使用独立配置 + 独立 model/key/url
        self.agent_vars: Dict[str, Dict[str, tk.Variable]] = {}
        for key, _label in AGENT_LABELS:
            self.agent_vars[key] = {
                "use_separate": tk.BooleanVar(value=False),
                "model": tk.StringVar(value=""),
                "api_key": tk.StringVar(value=""),
                "base_url": tk.StringVar(value=""),
            }

    def _build_ui(self):
        tab = ttk.Notebook(self.root)
        tab.pack(fill="both", expand=True)

        self.tab_config = ttk.Frame(tab)
        self.tab_run = ttk.Frame(tab)
        self.tab_result = ttk.Frame(tab)
        self.tab_about = ttk.Frame(tab)

        tab.add(self.tab_config, text="配置")
        tab.add(self.tab_run, text="运行")
        tab.add(self.tab_result, text="结果")
        tab.add(self.tab_about, text="关于")

        self._init_config_tab()
        self._init_run_tab()
        self._init_result_tab()
        self._init_about_tab()

    def _build_cli_config(self) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {
            "api_key": self.global_api_key_var.get().strip(),
            "base_url": self.global_base_url_var.get().strip(),
            "model_name": self.global_model_var.get().strip(),
            "provider_mode": self.global_provider_mode_var.get().strip() or "auto",
            "enable_build": bool(self.enable_build_var.get()),
            "enable_verification": bool(self.enable_verify_var.get()),
            "generate_only": bool(self.generate_only_var.get()),
            "max_repair_rounds": _safe_int(self.max_repair_rounds_var.get(), 2),
            "use_cache": bool(self.use_cache_var.get()),
            "use_buildkit": bool(self.use_buildkit_var.get()),
            "buildkit_progress": self.buildkit_progress_var.get().strip() or "plain",
            "output_dir": self.output_dir_var.get().strip(),
            "log_dir": self.logs_dir_var.get().strip(),
            "default_base_image": self.default_base_image_var.get().strip() or "ubuntu:24.04",
            "enable_rag": bool(self.enable_rag_var.get()),
            "build_timeout_seconds": _safe_float(self.build_timeout_seconds_var.get(), 1800.0),
            "verify_timeout_seconds": _safe_float(self.verify_timeout_seconds_var.get(), 600.0),
            "project_timeout_seconds": _parse_optional_positive_float(self.project_timeout_seconds_var.get()),
        }

        for key, _label in AGENT_LABELS:
            cfg[f"{key}_use_separate_config"] = bool(self.agent_vars[key]["use_separate"].get())
            cfg[f"{key}_model_name"] = strip_outer_quotes(self.agent_vars[key]["model"].get().strip())
            cfg[f"{key}_api_key"] = strip_outer_quotes(self.agent_vars[key]["api_key"].get().strip())
            cfg[f"{key}_base_url"] = strip_outer_quotes(self.agent_vars[key]["base_url"].get().strip())

        return cfg

    def _build_cli(self) -> CXXCrafterCLI:
        return build_cli_from_config(self._build_cli_config())

    def _set_running_ui(self, running: bool):
        self.running = running
        self.start_btn.configure(state="disabled" if running else "normal")
        self.status_var.set("运行中" if running else "就绪")

    def _append_line(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text.rstrip() + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _format_agent_summary(self) -> str:
        lines = []
        lines.append("智能体配置")
        lines.append("=" * 60)
        lines.append(f"全局模型: {self.global_model_var.get().strip() or 'gpt-4o'}")
        lines.append(f"全局分组: {self.global_provider_mode_var.get().strip() or 'auto'}")
        lines.append(f"全局 API Key: {'已设置' if self.global_api_key_var.get().strip() else '未设置'}")
        lines.append(f"全局 Base URL: {self.global_base_url_var.get().strip() or '未设置'}")
        lines.append("")
        for key, label in AGENT_LABELS:
            use_sep = bool(self.agent_vars[key]["use_separate"].get())
            model = self.agent_vars[key]["model"].get().strip() or "(继承全局)"
            api_key = "已设置" if self.agent_vars[key]["api_key"].get().strip() else "(继承全局)"
            base_url = self.agent_vars[key]["base_url"].get().strip() or "(继承全局)"
            lines.append(f"{label}:")
            lines.append(f"  独立配置: {'是' if use_sep else '否'}")
            lines.append(f"  模型: {model}")
            lines.append(f"  API Key: {api_key}")
            lines.append(f"  Base URL: {base_url}")
            lines.append("")
        return "\n".join(lines)

    # -----------------------------
    # 配置 Tab
    # -----------------------------
    def _init_config_tab(self):
        outer = ttk.Frame(self.tab_config, padding=12)
        outer.pack(fill="both", expand=True)

        global_frame = ttk.LabelFrame(outer, text="全局配置", padding=12)
        global_frame.pack(fill="x", pady=(0, 10))

        items = [
            ("API Key", self.global_api_key_var, True),
            ("Base URL", self.global_base_url_var, False),
            ("模型名称", self.global_model_var, False),
            ("分组选择", self.global_provider_mode_var, False),
            ("输出目录", self.output_dir_var, False),
            ("日志目录", self.logs_dir_var, False),
            ("默认基础镜像", self.default_base_image_var, False),
            ("BuildKit 模式", self.buildkit_progress_var, False),
            ("最大修复轮次", self.max_repair_rounds_var, False),
            ("构建超时(秒)", self.build_timeout_seconds_var, False),
            ("验证超时(秒)", self.verify_timeout_seconds_var, False),
            ("项目总超时(秒)", self.project_timeout_seconds_var, False),
            ("连续失败阈值", self.max_consecutive_failures_var, False),
        ]

        for row, (label, var, secret) in enumerate(items):
            ttk.Label(global_frame, text=f"{label}：").grid(row=row, column=0, sticky="w", pady=5)
            entry = ttk.Entry(global_frame, textvariable=var, width=60, show="*" if secret else "")
            entry.grid(row=row, column=1, sticky="ew", pady=5)

        option_row = ttk.Frame(global_frame)
        option_row.grid(row=len(items), column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(option_row, text="启用构建", variable=self.enable_build_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_row, text="启用验证", variable=self.enable_verify_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_row, text="仅生成模式", variable=self.generate_only_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_row, text="启用缓存", variable=self.use_cache_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_row, text="启用 BuildKit", variable=self.use_buildkit_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_row, text="启用 RAG", variable=self.enable_rag_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_row, text="Docker 异常立即停止", variable=self.stop_on_docker_error_var).pack(side="left")

        global_frame.columnconfigure(1, weight=1)

        agents_frame = ttk.LabelFrame(outer, text="多智能体配置（可选独立配置；未勾选则继承全局）", padding=12)
        agents_frame.pack(fill="x", pady=(0, 10))

        header = ttk.Frame(agents_frame)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="智能体", width=24).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="独立配置", width=12).grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="模型", width=32).grid(row=0, column=2, sticky="w")
        ttk.Label(header, text="API Key", width=32).grid(row=0, column=3, sticky="w")
        ttk.Label(header, text="Base URL", width=42).grid(row=0, column=4, sticky="w")

        body = ttk.Frame(agents_frame)
        body.pack(fill="x", expand=True)

        for row, (agent_key, agent_label) in enumerate(AGENT_LABELS):
            self._create_agent_row(body, row, agent_key, agent_label)

        note = ttk.Label(
            agents_frame,
            text="说明：若某个智能体不勾选“独立配置”，则自动继承全局模型 / API Key / Base URL / 分组。",
            foreground="gray",
        )
        note.pack(anchor="w", pady=(8, 0))

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="恢复默认", command=self._reset_defaults).pack(side="left")
        ttk.Button(btn_row, text="查看系统兼容性", command=self._show_os_tip).pack(side="left", padx=8)
        ttk.Button(btn_row, text="查看智能体摘要", command=self._show_agent_summary).pack(side="left", padx=8)

    def _create_agent_row(self, parent, row: int, agent_key: str, agent_label: str):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="ew", pady=5)
        frame.columnconfigure(2, weight=1)
        frame.columnconfigure(3, weight=1)
        frame.columnconfigure(4, weight=1)

        ttk.Label(frame, text=agent_label, width=24).grid(row=0, column=0, sticky="w", padx=(0, 8))

        use_separate = self.agent_vars[agent_key]["use_separate"]
        model_var = self.agent_vars[agent_key]["model"]
        api_key_var = self.agent_vars[agent_key]["api_key"]
        base_url_var = self.agent_vars[agent_key]["base_url"]

        ttk.Checkbutton(frame, variable=use_separate).grid(row=0, column=1, sticky="w", padx=(0, 8))

        ttk.Entry(frame, textvariable=model_var, width=32).grid(row=0, column=2, sticky="ew", padx=(0, 8))
        ttk.Entry(frame, textvariable=api_key_var, show="*", width=34).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        ttk.Entry(frame, textvariable=base_url_var, width=44).grid(row=0, column=4, sticky="ew")

    def _reset_defaults(self):
        self.global_api_key_var.set("")
        self.global_base_url_var.set("")
        self.global_model_var.set("gpt-4o")
        self.global_provider_mode_var.set("auto")
        self.output_dir_var.set(str(PROJECT_ROOT / "dockerfile_playground"))
        self.logs_dir_var.set(str(PROJECT_ROOT / "data" / "build_logs"))
        self.default_base_image_var.set("ubuntu:24.04")
        self.buildkit_progress_var.set("plain")
        self.max_repair_rounds_var.set("2")
        self.build_timeout_seconds_var.set("1800")
        self.verify_timeout_seconds_var.set("600")
        self.project_timeout_seconds_var.set("")
        self.max_consecutive_failures_var.set("3")
        self.enable_build_var.set(True)
        self.enable_verify_var.set(True)
        self.generate_only_var.set(False)
        self.use_cache_var.set(True)
        self.use_buildkit_var.set(True)
        self.enable_rag_var.set(True)
        self.stop_on_docker_error_var.set(True)

        for key, _label in AGENT_LABELS:
            self.agent_vars[key]["use_separate"].set(False)
            self.agent_vars[key]["model"].set("")
            self.agent_vars[key]["api_key"].set("")
            self.agent_vars[key]["base_url"].set("")

        messagebox.showinfo("成功", "已恢复默认配置。")

    def _show_os_tip(self):
        messagebox.showinfo("系统兼容性", format_os_tip())

    def _show_agent_summary(self):
        try:
            messagebox.showinfo("智能体摘要", self._format_agent_summary())
        except Exception as e:
            messagebox.showerror("错误", f"无法生成智能体摘要：{e}")

    # -----------------------------
    # 运行 Tab
    # -----------------------------
    def _init_run_tab(self):
        outer = ttk.Frame(self.tab_run, padding=12)
        outer.pack(fill="both", expand=True)

        path_frame = ttk.LabelFrame(outer, text="任务输入", padding=12)
        path_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(path_frame, text="单个项目路径：").grid(row=0, column=0, sticky="w", pady=5)
        row0 = ttk.Frame(path_frame)
        row0.grid(row=0, column=1, sticky="ew", pady=5)
        row0.columnconfigure(0, weight=1)
        ttk.Entry(row0, textvariable=self.project_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(row0, text="浏览...", command=self._browse_project_path).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(path_frame, text="项目列表文件：").grid(row=1, column=0, sticky="w", pady=5)
        row1 = ttk.Frame(path_frame)
        row1.grid(row=1, column=1, sticky="ew", pady=5)
        row1.columnconfigure(0, weight=1)
        ttk.Entry(row1, textvariable=self.repo_list_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(row1, text="浏览...", command=self._browse_repo_list).grid(row=0, column=1, padx=(8, 0))

        option_frame = ttk.LabelFrame(outer, text="运行参数", padding=12)
        option_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(option_frame, text="连续失败阈值：").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(option_frame, textvariable=self.max_consecutive_failures_var, width=10).grid(row=0, column=1, sticky="w", pady=5)

        ttk.Label(option_frame, text="批处理摘要路径：").grid(row=0, column=2, sticky="e", pady=5)
        rowb = ttk.Frame(option_frame)
        rowb.grid(row=0, column=3, sticky="ew", pady=5)
        rowb.columnconfigure(0, weight=1)
        ttk.Entry(rowb, textvariable=self.batch_summary_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(rowb, text="浏览...", command=self._browse_batch_summary).grid(row=0, column=1, padx=(8, 0))

        ttk.Checkbutton(option_frame, text="停止于 Docker 异常", variable=self.stop_on_docker_error_var).grid(row=1, column=0, sticky="w", pady=5)
        ttk.Label(option_frame, text="构建超时(秒)：").grid(row=1, column=1, sticky="e", pady=5)
        ttk.Entry(option_frame, textvariable=self.build_timeout_seconds_var, width=10).grid(row=1, column=2, sticky="w", pady=5)

        ttk.Label(option_frame, text="验证超时(秒)：").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(option_frame, textvariable=self.verify_timeout_seconds_var, width=10).grid(row=2, column=1, sticky="w", pady=5)

        ttk.Label(option_frame, text="项目总超时(秒)：").grid(row=2, column=2, sticky="e", pady=5)
        ttk.Entry(option_frame, textvariable=self.project_timeout_seconds_var, width=10).grid(row=2, column=3, sticky="w", pady=5)

        control_frame = ttk.Frame(outer)
        control_frame.pack(fill="x", pady=(0, 10))

        self.start_btn = ttk.Button(control_frame, text="开始运行", command=self._start_run)
        self.start_btn.pack(side="left")
        ttk.Button(control_frame, text="停止运行", command=self._stop_run).pack(side="left", padx=8)
        ttk.Button(control_frame, text="清空日志", command=self._clear_log).pack(side="left", padx=8)
        ttk.Label(control_frame, textvariable=self.status_var).pack(side="right")

        log_frame = ttk.LabelFrame(outer, text="运行日志", padding=8)
        log_frame.pack(fill="both", expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, wrap="word", height=22)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _browse_project_path(self):
        path = filedialog.askdirectory(title="选择项目目录")
        if path:
            self.project_path_var.set(path)
            self.repo_list_var.set("")

    def _browse_repo_list(self):
        path = filedialog.askopenfilename(
            title="选择项目列表文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if path:
            self.repo_list_var.set(path)
            self.project_path_var.set("")

    def _browse_batch_summary(self):
        path = filedialog.asksaveasfilename(
            title="选择批处理摘要保存位置",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")]
        )
        if path:
            self.batch_summary_path_var.set(path)

    def _stop_run(self):
        self.running = False
        self._append_line("\n⏹️ 已请求停止，当前任务会在当前轮次结束后停止。\n")
        self.status_var.set("停止请求已发送")

    def _start_run(self):
        if self.running:
            messagebox.showwarning("提示", "任务正在运行中。")
            return

        project_path = strip_outer_quotes(self.project_path_var.get().strip())
        repo_list = strip_outer_quotes(self.repo_list_var.get().strip())

        if not project_path and not repo_list:
            messagebox.showerror("错误", "请提供单个项目路径或项目列表文件。")
            return

        self._clear_log()
        self._append_line("==================================================")
        self._append_line("开始执行任务...")
        self._append_line("==================================================")

        self._set_running_ui(True)
        self.worker_thread = threading.Thread(
            target=self._run_backend,
            args=(project_path, repo_list),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_backend(self, project_path: str, repo_list: str):
        try:
            cli = self._build_cli()

            output_dir = strip_outer_quotes(self.output_dir_var.get().strip() or str(PROJECT_ROOT / "dockerfile_playground"))
            log_dir = strip_outer_quotes(self.logs_dir_var.get().strip() or str(PROJECT_ROOT / "data" / "build_logs"))
            build_timeout_seconds = _safe_float(self.build_timeout_seconds_var.get(), 1800.0)
            verify_timeout_seconds = _safe_float(self.verify_timeout_seconds_var.get(), 600.0)

            project_timeout_seconds = _parse_optional_positive_float(self.project_timeout_seconds_var.get())

            max_consecutive_failures = _safe_int(self.max_consecutive_failures_var.get(), 100)
            stop_on_docker_error = bool(self.stop_on_docker_error_var.get())

            self._append_line(f"验证开关: {self.enable_verify_var.get()}")
            self._append_line(f"启用构建: {self.enable_build_var.get()}")
            self._append_line(f"启用 RAG: {self.enable_rag_var.get()}")
            self._append_line(f"输出目录: {output_dir}")
            self._append_line(f"日志目录: {log_dir}")
            self._append_line(f"构建超时(秒): {build_timeout_seconds}")
            self._append_line(f"验证超时(秒): {verify_timeout_seconds}")
            self._append_line(f"项目总超时(秒): {self.project_timeout_seconds_var.get().strip()}")
            self._append_line(f"连续失败阈值: {max_consecutive_failures}")
            self._append_line(f"停止于 Docker 异常: {stop_on_docker_error}")
            self._append_line("")
            self._append_line(self._format_agent_summary())
            self._append_line("")

            total = 0
            success_count = 0
            failure_count = 0
            passed_with_skip_count = 0
            generated_count = 0
            timeout_count = 0
            docker_unavailable_count = 0
            consecutive_failures = 0
            consecutive_failures_peak = 0

            # 指标收集器
            metrics_collector = BatchMetricsCollector()
            rag_hit_total = 0
            all_summaries = []

            if repo_list:
                repo_list_path = Path(repo_list)
                if not repo_list_path.exists():
                    raise FileNotFoundError(f"项目列表文件不存在: {repo_list}")

                repos = [line.strip() for line in repo_list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                total = len(repos)
                self._append_line(f"共读取到 {total} 个项目。")

                for idx, repo in enumerate(repos, 1):
                    if not self.running:
                        self._append_line("已停止，退出批处理。")
                        break

                    repo_path = str(Path(repo).resolve())
                    self._append_line("")
                    self._append_line("=" * 60)
                    self._append_line(f"[{idx}/{total}] 处理项目: {repo_path}")
                    self._append_line("=" * 60)

                    if not Path(repo_path).exists():
                        self._append_line(f"❌ 项目路径不存在: {repo_path}")
                        failure_count += 1
                        consecutive_failures += 1
                        consecutive_failures_peak = max(consecutive_failures_peak, consecutive_failures)
                        if max_consecutive_failures > 0 and consecutive_failures >= max_consecutive_failures:
                            self._append_line(f"🛑 连续失败达到阈值 ({consecutive_failures}/{max_consecutive_failures})，停止批处理。")
                            break
                        continue

                    summary = run_single_project(
                        cli=cli,
                        project_path=repo_path,
                        output_dir=output_dir,
                        log_dir=log_dir,
                        enable_build=bool(self.enable_build_var.get()),
                        enable_verification=bool(self.enable_verify_var.get()),
                        generate_only=bool(self.generate_only_var.get()),
                        use_cache=bool(self.use_cache_var.get()),
                        build_timeout_seconds=build_timeout_seconds,
                        verify_timeout_seconds=verify_timeout_seconds,
                        project_timeout_seconds=project_timeout_seconds,
                    )

                    overall_status = _summary_status(summary)
                    success = bool(summary.get("success", False))

                    if success or overall_status in {"passed", "generated", "passed_with_verification_skipped"}:
                        success_count += 1
                        if overall_status == "generated":
                            generated_count += 1
                        if overall_status == "passed_with_verification_skipped":
                            passed_with_skip_count += 1
                        consecutive_failures = 0
                    else:
                        failure_count += 1
                        consecutive_failures += 1
                        consecutive_failures_peak = max(consecutive_failures_peak, consecutive_failures)

                    if _is_timeout_summary(summary):
                        timeout_count += 1
                    if _is_docker_unavailable_summary(summary):
                        docker_unavailable_count += 1

                    self._append_line(
                        f"✅ 完成: {Path(repo_path).name} | status={overall_status} | success={success}"
                    )

                    # 收集项目指标
                    all_summaries.append(summary)
                    _pm = {
                        "project_name": Path(repo_path).name,
                        "success": success,
                        "build_time_sec": 0,
                        "repair_rounds": int((summary or {}).get("repair_round", 0) or 0),
                        "skipped": overall_status in ("skipped", "generated"),
                        "timeout": overall_status == "timeout",
                    }
                    # token 用量
                    _au = (summary or {}).get("agent_usage") or (summary or {}).get("llm_usage") or {}
                    _tp = _tc = _tt = 0
                    if isinstance(_au, dict):
                        for _an, _ai in _au.items():
                            if isinstance(_ai, dict):
                                _u = _ai.get("usage") or {}
                                _tp += int(_u.get("prompt_tokens", 0) or 0)
                                _tc += int(_u.get("completion_tokens", 0) or 0)
                                _tt += int(_u.get("total_tokens", 0) or 0)
                    _pm["prompt_tokens"] = _tp
                    _pm["completion_tokens"] = _tc
                    _pm["total_tokens"] = _tt
                    # 验证结果
                    _vr = (summary or {}).get("verification_result") or {}
                    _st = _vr.get("stages") or {}
                    _pm["static_pass"] = bool((_st.get("consistency") or {}).get("passed", False))
                    _pm["product_pass"] = bool((_st.get("product") or {}).get("passed", False))
                    _pm["dynamic_pass"] = bool((_st.get("smoke") or _st.get("tests") or {}).get("passed", False))
                    _pm["final_verify_pass"] = bool(_vr.get("success", False))
                    metrics_collector.add(_pm)
                    # RAG 命中
                    _ru = (summary or {}).get("rag_usage") or (summary or {}).get("runtime_diagnostics") or {}
                    rag_hit_total += int(_ru.get("hit_stage_count", 0) or 0)

                    if stop_on_docker_error and _is_docker_unavailable_summary(summary):
                        self._append_line("🛑 检测到 Docker 异常，立即停止整个批处理。")
                        break

                    if max_consecutive_failures > 0 and consecutive_failures >= max_consecutive_failures:
                        self._append_line(f"🛑 连续失败达到阈值 ({consecutive_failures}/{max_consecutive_failures})，停止批处理。")
                        break

            else:
                if not project_path:
                    raise ValueError("单个项目路径不能为空。")

                project_path = str(Path(project_path).resolve())
                if not Path(project_path).exists():
                    raise FileNotFoundError(f"项目路径不存在: {project_path}")

                total = 1
                summary = run_single_project(
                    cli=cli,
                    project_path=project_path,
                    output_dir=output_dir,
                    log_dir=log_dir,
                    enable_build=bool(self.enable_build_var.get()),
                    enable_verification=bool(self.enable_verify_var.get()),
                    generate_only=bool(self.generate_only_var.get()),
                    use_cache=bool(self.use_cache_var.get()),
                    build_timeout_seconds=build_timeout_seconds,
                    verify_timeout_seconds=verify_timeout_seconds,
                    project_timeout_seconds=project_timeout_seconds,
                )

                overall_status = _summary_status(summary)
                success = bool(summary.get("success", False))

                if success or overall_status in {"passed", "generated", "passed_with_verification_skipped"}:
                    success_count = 1
                else:
                    failure_count = 1

                if overall_status == "generated":
                    generated_count = 1
                if overall_status == "passed_with_verification_skipped":
                    passed_with_skip_count = 1
                if _is_timeout_summary(summary):
                    timeout_count = 1
                if _is_docker_unavailable_summary(summary):
                    docker_unavailable_count = 1

                self._append_line(
                    f"✅ 完成: {Path(project_path).name} | status={overall_status} | success={success}"
                )

                # 单项目也收集指标
                all_summaries.append(summary)
                _pm = {
                    "project_name": Path(project_path).name,
                    "success": success,
                    "build_time_sec": 0,
                    "repair_rounds": int((summary or {}).get("repair_round", 0) or 0),
                    "skipped": overall_status in ("skipped", "generated"),
                    "timeout": overall_status == "timeout",
                }
                _au = (summary or {}).get("agent_usage") or (summary or {}).get("llm_usage") or {}
                _tp = _tc = _tt = 0
                if isinstance(_au, dict):
                    for _an, _ai in _au.items():
                        if isinstance(_ai, dict):
                            _u = _ai.get("usage") or {}
                            _tp += int(_u.get("prompt_tokens", 0) or 0)
                            _tc += int(_u.get("completion_tokens", 0) or 0)
                            _tt += int(_u.get("total_tokens", 0) or 0)
                _pm["prompt_tokens"] = _tp
                _pm["completion_tokens"] = _tc
                _pm["total_tokens"] = _tt
                _vr = (summary or {}).get("verification_result") or {}
                _st = _vr.get("stages") or {}
                _pm["static_pass"] = bool((_st.get("consistency") or {}).get("passed", False))
                _pm["product_pass"] = bool((_st.get("product") or {}).get("passed", False))
                _pm["dynamic_pass"] = bool((_st.get("smoke") or _st.get("tests") or {}).get("passed", False))
                _pm["final_verify_pass"] = bool(_vr.get("success", False))
                metrics_collector.add(_pm)
                _ru = (summary or {}).get("rag_usage") or (summary or {}).get("runtime_diagnostics") or {}
                rag_hit_total += int(_ru.get("hit_stage_count", 0) or 0)

            self._append_line("")
            self._append_line("==================================================")
            self._append_line(
                f"任务完成：总数={total}，成功={success_count}，失败={failure_count}，"
                f"生成={generated_count}，跳过验证={passed_with_skip_count}，"
                f"超时={timeout_count}，Docker异常={docker_unavailable_count}"
            )
            self._append_line("==================================================")

            if self.batch_summary_path_var.get().strip():
                p = Path(self.batch_summary_path_var.get().strip())
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    json.dumps(
                        {
                            "total": total,
                            "success": success_count,
                            "failed": failure_count,
                            "generated": generated_count,
                            "passed_with_verification_skipped": passed_with_skip_count,
                            "timeout": timeout_count,
                            "docker_unavailable": docker_unavailable_count,
                            "consecutive_failures_peak": consecutive_failures_peak,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

            def finish_ui():
                self.status_var.set("完成")
                self._set_running_ui(False)

                # 计算并显示批量测试指标
                metrics_text = ""
                try:
                    metrics_summary = metrics_collector.summarize()
                    metrics_text = format_summary_text(metrics_summary, rag_hit_total=rag_hit_total)
                    # 保存指标 JSON
                    try:
                        _mjp = Path(log_dir) / "batch_metrics.json"
                        save_summary_json(metrics_summary, str(_mjp))
                    except Exception:
                        pass
                except Exception:
                    pass

                if metrics_text and total > 1:
                    # 批量模式：弹出指标报告
                    messagebox.showinfo("📊 批量测试结果", metrics_text)
                elif failure_count == 0:
                    messagebox.showinfo("完成", "任务执行完成。")
                else:
                    messagebox.showwarning("完成", f"任务执行完成，但有 {failure_count} 个失败项。")
                self._refresh_project_list()

            self.root.after(0, finish_ui)

        except Exception as e:
            tb = traceback.format_exc()
            self.root.after(0, lambda: self._append_line(tb))
            self.root.after(0, lambda: self.status_var.set("异常"))
            self.root.after(0, lambda: messagebox.showerror("错误", f"任务运行失败：{e}"))
            self.root.after(0, lambda: self._set_running_ui(False))
        finally:
            self.running = False

    # -----------------------------
    # 结果 Tab
    # -----------------------------
    def _init_result_tab(self):
        outer = ttk.Frame(self.tab_result, padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.LabelFrame(outer, text="输出目录", padding=12)
        top.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.output_dir_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="刷新项目列表", command=self._refresh_project_list).pack(side="left", padx=8)
        ttk.Button(row, text="打开目录", command=self._open_output_dir).pack(side="left")

        mid = ttk.LabelFrame(outer, text="生成的 Dockerfile", padding=12)
        mid.pack(fill="both", expand=True)

        select_row = ttk.Frame(mid)
        select_row.pack(fill="x", pady=(0, 8))

        ttk.Label(select_row, text="项目：").pack(side="left")
        self.selected_project_var = tk.StringVar(value="")
        self.project_combo = ttk.Combobox(select_row, textvariable=self.selected_project_var, state="readonly", width=45)
        self.project_combo.pack(side="left", padx=8)
        ttk.Button(select_row, text="加载 Dockerfile", command=self._load_dockerfile).pack(side="left")

        self.df_text = scrolledtext.ScrolledText(mid, wrap="none")
        self.df_text.pack(fill="both", expand=True)
        self.df_text.configure(state="disabled")

    def _refresh_project_list(self):
        out_dir = strip_outer_quotes(self.output_dir_var.get().strip())
        if not out_dir or not Path(out_dir).exists():
            self.project_combo["values"] = []
            return []

        projects = sorted([d.name for d in Path(out_dir).iterdir() if d.is_dir()])
        self.project_combo["values"] = projects
        if projects and not self.selected_project_var.get():
            self.selected_project_var.set(projects[0])
        return projects

    def _load_dockerfile(self):
        project = self.selected_project_var.get().strip()
        if not project:
            messagebox.showwarning("提示", "请先选择项目。")
            return

        df_path = Path(strip_outer_quotes(self.output_dir_var.get().strip())) / project / "Dockerfile"
        if not df_path.exists():
            messagebox.showwarning("提示", "该项目没有生成 Dockerfile。")
            return

        content = df_path.read_text(encoding="utf-8", errors="ignore")
        self.df_text.configure(state="normal")
        self.df_text.delete("1.0", tk.END)
        self.df_text.insert(tk.END, content)
        self.df_text.configure(state="disabled")

    def _open_output_dir(self):
        path = strip_outer_quotes(self.output_dir_var.get().strip() or str(PROJECT_ROOT / "dockerfile_playground"))
        if not Path(path).exists():
            messagebox.showwarning("提示", "输出目录不存在。")
            return
        open_path(path)

    # -----------------------------
    # 关于
    # -----------------------------
    def _init_about_tab(self):
        outer = ttk.Frame(self.tab_about, padding=20)
        outer.pack(fill="both", expand=True)

        text = (
            "CXXCrafter - GUI 版本\n\n"
            "保留能力：\n"
            "1. 使用大模型辅助 Dockerfile 生成\n"
            "2. 通过 RAG 增强检索辅助生成 / 构建 / 验证\n"
            "3. 具备多维度验证\n"
            "4. 支持多智能体独立配置\n"
            "5. 支持批处理\n"
            "6. 支持 Linux / Windows\n"
            "7. 支持 Docker 异常快速停止\n"
            "8. 支持连续失败阈值\n"
            "9. 支持分组选择（auto / az / 自定义）\n"
        )
        ttk.Label(outer, text=text, justify="left", font=("Microsoft YaHei UI", 11)).pack(anchor="w")

    def on_close(self):
        try:
            sys.stdout = self._old_stdout
            sys.stderr = self._old_stderr
        except Exception:
            pass
        self.root.destroy()
    
    def run_batch_and_popup(coordinator, project_paths, output_dir):
        collector = BatchMetricsCollector()
        results = []

        for p in project_paths:
            try:
                r = coordinator.process_project(p)
                results.append(r)
                collector.add(r)
            except Exception as e:
                fail_r = {
                    "project_name": p,
                    "success": False,
                    "build_time_sec": 0.0,
                    "repair_rounds": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "manual_intervention": True,
                    "timeout": False,
                    "skipped": False,
                    "error": str(e),
                }
                results.append(fail_r)
                collector.add(fail_r)

        summary = collector.summarize()
        save_summary_json(summary, f"{output_dir}/summary.json")

        text = format_summary_text(summary, rag_hit_total=0)
        messagebox.showinfo("📊 批量测试结果", text)

        return summary, results

# =========================================================
# Headless 批处理入口
# =========================================================
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CXXCrafter GUI / Batch Entry")
    parser.add_argument("--headless", action="store_true", help="不启动 GUI，直接运行批处理或单项目")
    parser.add_argument("--project", type=str, default="", help="单个项目目录")
    parser.add_argument("--repo-list", type=str, default="", help="项目列表文件，每行一个项目路径")
    parser.add_argument("--output-dir", type=str, default=str(PROJECT_ROOT / "dockerfile_playground"))
    parser.add_argument("--log-dir", type=str, default=str(PROJECT_ROOT / "data" / "build_logs"))

    parser.add_argument("--api-key", type=str, default="")
    parser.add_argument("--base-url", type=str, default="")
    parser.add_argument("--model-name", type=str, default="gpt-4o")
    parser.add_argument("--provider-mode", type=str, default="auto")

    parser.add_argument("--enable-build", action="store_true", default=True)
    parser.add_argument("--disable-build", action="store_true")
    parser.add_argument("--enable-verification", action="store_true", default=True)
    parser.add_argument("--disable-verification", action="store_true")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--use-cache", action="store_true", default=True)
    parser.add_argument("--no-cache", action="store_true")

    parser.add_argument("--use-buildkit", action="store_true", default=True)
    parser.add_argument("--no-buildkit", action="store_true")
    parser.add_argument("--buildkit-progress", type=str, default="plain")
    parser.add_argument("--default-base-image", type=str, default="ubuntu:24.04")
    parser.add_argument("--max-repair-rounds", type=int, default=2)

    parser.add_argument("--build-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--verify-timeout-seconds", type=float, default=600.0)

    # 默认 None，表示未启用项目总超时
    parser.add_argument("--project-timeout-seconds", type=float, default=None)

    parser.add_argument("--stop-on-docker-error", action="store_true", default=True)
    parser.add_argument("--no-stop-on-docker-error", action="store_true")
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--batch-summary-path", type=str, default="")
    return parser

def run_headless(args: argparse.Namespace) -> Dict[str, Any]:
    config = {
        "api_key": strip_outer_quotes(args.api_key),
        "base_url": strip_outer_quotes(args.base_url),
        "model_name": args.model_name,
        "provider_mode": strip_outer_quotes(args.provider_mode) or "auto",
        "enable_build": bool(not args.disable_build),
        "enable_verification": bool(not args.disable_verification),
        "generate_only": bool(args.generate_only),
        "max_repair_rounds": int(args.max_repair_rounds),
        "use_cache": bool(not args.no_cache),
        "use_buildkit": bool(not args.no_buildkit),
        "buildkit_progress": args.buildkit_progress or "plain",
        "output_dir": strip_outer_quotes(args.output_dir),
        "log_dir": strip_outer_quotes(args.log_dir),
        "default_base_image": args.default_base_image or "ubuntu:24.04",
        "enable_rag": True,

        "dependency_use_separate_config": False,
        "build_use_separate_config": False,
        "error_use_separate_config": False,
        "repair_use_separate_config": False,

        "project_timeout_seconds": args.project_timeout_seconds,
    }

    cli = build_cli_from_config(config)

    if args.repo_list:
        repo_list_path = Path(strip_outer_quotes(args.repo_list))
        if not repo_list_path.exists():
            raise FileNotFoundError(f"项目列表文件不存在: {repo_list_path}")

        project_paths = [line.strip() for line in repo_list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        summary = run_batch_projects(
            cli=cli,
            project_paths=project_paths,
            output_dir=strip_outer_quotes(args.output_dir),
            log_dir=strip_outer_quotes(args.log_dir),
            enable_build=bool(not args.disable_build),
            enable_verification=bool(not args.disable_verification),
            generate_only=bool(args.generate_only),
            use_cache=bool(not args.no_cache),
            build_timeout_seconds=float(args.build_timeout_seconds),
            verify_timeout_seconds=float(args.verify_timeout_seconds),
            project_timeout_seconds=(float(args.project_timeout_seconds) if args.project_timeout_seconds and args.project_timeout_seconds > 0 else None),
            stop_on_docker_error=bool(not args.no_stop_on_docker_error),
            max_consecutive_failures=int(args.max_consecutive_failures),
        )
    elif args.project:
        summary = run_single_project(
            cli=cli,
            project_path=str(Path(strip_outer_quotes(args.project)).resolve()),
            output_dir=strip_outer_quotes(args.output_dir),
            log_dir=strip_outer_quotes(args.log_dir),
            enable_build=bool(not args.disable_build),
            enable_verification=bool(not args.disable_verification),
            generate_only=bool(args.generate_only),
            use_cache=bool(not args.no_cache),
            build_timeout_seconds=float(args.build_timeout_seconds),
            verify_timeout_seconds=float(args.verify_timeout_seconds),
            project_timeout_seconds=(float(args.project_timeout_seconds) if args.project_timeout_seconds and args.project_timeout_seconds > 0 else None),
        )
    else:
        raise ValueError("headless 模式下必须提供 --project 或 --repo-list")

    if args.batch_summary_path:
        p = Path(strip_outer_quotes(args.batch_summary_path))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary



def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.headless or args.project or args.repo_list:
        try:
            summary = run_headless(args)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            raise

    root = tk.Tk()
    app = CXXCrafterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()