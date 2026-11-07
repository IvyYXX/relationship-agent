"""Storage boundary for the shared workflow; no dependency on a web app or database.

引擎只知道任务、检查点和结果，不直接访问 SQL 表。生产实现是
beta.store.ScopedStore；测试也通过隔离数据库验证相同协议。
"""

from typing import Any, Protocol


class WorkflowStore(Protocol):
    """引擎依赖的存储接口；实现方负责权限、事务和幂等性。"""

    def run(self, run_id: str) -> dict[str, Any]:
        """读取任务输入、冻结配置、状态和结果。"""
        ...

    def latest_report(self, session_id: str) -> dict[str, Any] | None:
        """读取当前范围最近有效的报告，供反馈修正使用。"""
        ...

    def checkpoint(self, run_id: str, state: dict[str, Any]) -> None:
        """保存已完成阶段，使恢复跳过已落盘的模型结果。"""
        ...

    def trace(
        self, run_id: str, event: str, detail: dict[str, Any] | None = None
    ) -> None:
        """保存脱敏执行事件。"""
        ...

    def finish(self, run_id: str, result: dict[str, Any], records: list) -> None:
        """原子提交结果及带来源层级的记忆，重复提交不得重复写入。"""
        ...

    def fail(self, run_id: str, error: str) -> None:
        """保存脱敏失败原因并保留可恢复状态。"""
        ...
