"""Public entry point for the checkpointed workflow.

保留历史导入路径，实际阶段执行在 engine.py。这里不维护第二套 Agent，
新增或修复工作流应修改 RelationshipAgent 的实现。
"""

from .engine import RelationshipAgent

__all__ = ["RelationshipAgent"]
