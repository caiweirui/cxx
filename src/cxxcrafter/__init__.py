from .config import CXXCrafterConfig, SUPPORTED_MODELS

__all__ = [
    "CXXCrafterConfig",
    "SUPPORTED_MODELS",
    "CXXCrafter",
    "CXXCrafterCLI",
    "main",
]

def __getattr__(name):
    """
    延迟导入 CLI，避免包初始化时触发循环导入或路径错误。
    这样：
      from cxxcrafter.config import CXXCrafterConfig
    不会因为 __init__.py 里 eager import cli.py 而失败。

    如果外部仍然需要：
      from cxxcrafter import CXXCrafter
    也可以正常工作。
    """
    if name in {"CXXCrafter", "CXXCrafterCLI", "main"}:
        from .cli import CXXCrafter, CXXCrafterCLI, main
        mapping = {
            "CXXCrafter": CXXCrafter,
            "CXXCrafterCLI": CXXCrafterCLI,
            "main": main,
        }
        return mapping[name]

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")