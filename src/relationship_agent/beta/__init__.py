"""Persona AI invite beta: authenticated, tenant-scoped application.

beta 是沿用的包名，当前网页入口均指向这里。app 负责 HTTP，service 负责
排队执行，store 负责带用户范围的数据操作；不要由前端决定资源权限。
"""
