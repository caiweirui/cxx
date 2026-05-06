# src/cxxcrafter/execution/__init__.py
from .executor import (
    DockerExecutor,
    BuildExecutionResult,
    VerificationExecutionResult,
)

# 兼容旧代码：如果别处还在用 BuildExecutor
BuildExecutor = DockerExecutor

__all__ = [
    "DockerExecutor",
    "BuildExecutor",
    "BuildExecutionResult",
    "VerificationExecutionResult",
]