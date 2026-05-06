from .dockerfile_generator import DockerfileGenerator

__all__ = [
    "DockerfileGenerator",
    "generate_dockerfile",
]

def __getattr__(name):
    """
    延迟导入，避免 generation_module 在初始化时就强行加载 docker_generator，
    从而引发循环导入或旧路径导入错误。
    """
    if name == "generate_dockerfile":
        from .docker_generator import generate_dockerfile
        return generate_dockerfile

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")