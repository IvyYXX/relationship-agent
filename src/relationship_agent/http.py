"""共享的出站 HTTP 重定向策略；防止凭据被带到未配置的目标地址。

模型接口与学术搜索共用此策略；搜索服务的凭据可能位于查询参数，
模型凭据通常位于请求头。不能依赖对端重定向仍指向可信地址。
"""

from urllib.request import HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    """让重定向作为 HTTP 错误返回，而不是自动转发授权头或查询参数。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """拒绝所有自动重定向，由调用方按供应商错误处理。"""
        return None
