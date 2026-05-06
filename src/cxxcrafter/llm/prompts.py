import json
from typing import Dict, Any

def _pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)

def dependency_system_prompt() -> str:
    return (
        "你是专业的 C/C++ 项目依赖解析专家。"
        "任务：分析项目的依赖关系，识别缺失依赖、版本约束、安装源。"
        "输出必须是严格 JSON。"
    )

def dependency_user_prompt(context: Dict[str, Any]) -> str:
    return f"""
分析以下 C/C++ 项目的依赖：

项目路径：
{context.get("project_path", "")}

构建系统：
{context.get("build_system", "")}

文档内容：
{(context.get("docs", "") or "")[:3000]}

静态依赖线索：
{_pretty(context.get("deps", {}))}

请输出 JSON，格式如下：
{{
  "dependencies": [
    {{
      "name": "libssl-dev",
      "version_constraint": "",
      "source": "apt|vcpkg|source|system",
      "reason": "..."
    }}
  ],
  "notes": ["..."]
}}
只返回 JSON，不要解释文本。
""".strip()

def build_system_prompt() -> str:
    return (
        "你是专业的 C/C++ 构建系统适配专家。"
        "任务：根据构建系统类型和依赖信息生成准确的构建命令序列。"
        "输出必须是严格 JSON。"
    )

def build_user_prompt(context: Dict[str, Any]) -> str:
    return f"""
为以下 C/C++ 项目生成构建命令：

项目路径：
{context.get("project_path", "")}

构建系统：
{context.get("build_system", "")}

依赖信息：
{_pretty(context.get("dependencies", {}))}

项目文档摘要：
{(context.get("docs", "") or "")[:2500]}

请输出 JSON，格式如下：
{{
  "commands": [
    "apt-get update",
    "apt-get install -y ..."
  ],
  "build_entry": "cmake|make|autotools|meson|bazel",
  "reason": "..."
}}
只返回 JSON，不要解释文本。
""".strip()

def error_system_prompt() -> str:
    return (
        "你是专业的 C/C++ 构建错误诊断专家。"
        "任务：分析构建错误日志，定位根因，生成修复方案。"
        "输出必须是严格 JSON。"
    )

def error_user_prompt(context: Dict[str, Any]) -> str:
    return f"""
分析以下 C/C++ 构建错误：

错误日志：
{(context.get("error_log", "") or "")[:4000]}

构建上下文：
{_pretty(context.get("build_context", {}))}

历史解决方案参考：
{context.get("rag_context", "")}

请输出 JSON，格式如下：
{{
  "error_type": "dependency_missing|network_error|linker_error|build_script_error|permission_error|unknown",
  "root_cause": "...",
  "fix_commands": ["..."],
  "confidence": 0.0
}}
只返回 JSON，不要解释文本。
""".strip()

def dockerfile_system_prompt() -> str:
    return (
        "你是专业的 C/C++ Dockerfile 生成助手。"
        "任务：根据项目解析结果和智能体建议生成可构建的 Dockerfile。"
        "只输出纯净 Dockerfile，不要解释，不要 Markdown 代码围栏。"
    )

def dockerfile_user_prompt(context: Dict[str, Any]) -> str:
    return f"""
你需要为一个 C/C++ 开源项目生成 Dockerfile。

基础镜像：
{context.get("base_image", "ubuntu:22.04")}

项目路径：
{context.get("project_path", "")}

构建系统：
{context.get("build_system", "Unknown")}

构建根目录信息：
{_pretty(context.get("build_root_info", {}))}

依赖画像：
{_pretty(context.get("dependency_profile", {}))}

依赖智能体输出：
{_pretty(context.get("dependency_result", {}))}

构建智能体输出：
{_pretty(context.get("build_result", {}))}

错误诊断输出（如有）：
{_pretty(context.get("error_result", {}))}

要求：
1. 生成适用于 Ubuntu 的 Dockerfile
2. 安装必要的构建工具和依赖
3. 使用正确的构建根目录
4. 生成可执行的构建命令
5. 尽量兼容复杂项目
6. 不要输出解释，只输出 Dockerfile 内容
""".strip()