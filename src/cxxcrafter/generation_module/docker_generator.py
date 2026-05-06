import os
from typing import Any, Dict, Optional, Union

try:
    from .dockerfile_generator import DockerfileGenerator
    from ..parser import parse_project
except ImportError:
    from cxxcrafter.generation_module.dockerfile_generator import DockerfileGenerator
    from cxxcrafter.parser import parse_project

def _is_parsed_project(obj: Any) -> bool:
    """
    粗略判断是否已经是 parse_project 的结果。
    """
    return isinstance(obj, dict) and "project_path" in obj

def generate_dockerfile(
    project_or_parsed: Union[str, Dict[str, Any]],
    config: Any = None,
    base_image: str = "ubuntu:22.04",
    agent_result: Optional[Dict[str, Any]] = None,
    output_path: Optional[str] = None,
    compatibility_mode: bool = True,
) -> str:
    """
    兼容式 Dockerfile 生成入口。

    参数：
        project_or_parsed:
            - 如果是 str/path，则作为项目路径，会自动 parse_project()
            - 如果是 dict，则视为 parse_project() 的结果
        config:
            CXXCrafterConfig 或兼容对象
        base_image:
            基础镜像
        agent_result:
            多智能体运行结果
        output_path:
            如果提供，则会把生成结果写入该路径
        compatibility_mode:
            传给 parse_project 的兼容模式

    返回：
        生成的 Dockerfile 文本
    """
    if _is_parsed_project(project_or_parsed):
        parsed_project = project_or_parsed
    else:
        project_path = os.path.abspath(str(project_or_parsed))
        parsed_project = parse_project(
            project_path,
            compatibility_mode=compatibility_mode,
        )

    generator = DockerfileGenerator(
        parsed_project=parsed_project,
        base_image=base_image,
        config=config,
        agent_result=agent_result,
    )

    dockerfile_content = generator.generate()

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(dockerfile_content)

    return dockerfile_content