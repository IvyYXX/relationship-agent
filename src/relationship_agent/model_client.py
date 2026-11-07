"""Public model adapter entry points.

这里只重导出公共类型。真实 HTTP 协议在 providers.py，离线样例在 demo.py；
调用方通过统一的 complete_json 接口使用它们。
"""

from .providers import APIClient, ModelError, build_model_client
from .demo import MockModelClient

__all__ = ["APIClient", "ModelError", "MockModelClient", "build_model_client"]
