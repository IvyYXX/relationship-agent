"""共享的出站 HTTP 重定向策略；防止凭据被带到未配置的目标地址。"""

from urllib.request import HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    """让重定向作为 HTTP 错误返回，而不是自动转发授权头或查询参数。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """拒绝所有自动重定向，由调用方按供应商错误处理。"""
        return None
