# src/cxxcrafter/agents/__init__.py
from .base_agent import BaseAgent
from .dependency_agent import DependencyAgent, DependencyAnalysis
from .build_agent import BuildAgent, BuildPlan
from .error_agent import ErrorAgent, FailureAnalysis
from .dockerfile_repair_agent import DockerfileRepairAgent, RepairPatch
from .coordinator import CXXCrafterCoordinator

# 兼容旧代码
AgentCoordinator = CXXCrafterCoordinator

__all__ = [
    "BaseAgent",
    "DependencyAgent",
    "DependencyAnalysis",
    "BuildAgent",
    "BuildPlan",
    "ErrorAgent",
    "FailureAnalysis",
    "DockerfileRepairAgent",
    "RepairPatch",
    "CXXCrafterCoordinator",
    "AgentCoordinator",
]