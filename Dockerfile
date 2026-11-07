FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# 只复制安装所需源码；.dockerignore 进一步排除密钥、聊天库和本机环境。
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[beta]' && useradd --create-home --uid 10001 persona && mkdir /backups && chown persona /backups
COPY scripts ./scripts
# 服务以非 root 用户运行；数据库通过环境配置连接，不写进镜像。
USER persona
# EXPOSE 仅声明容器内部端口；公开访问由 compose 的 Caddy 代理负责。
EXPOSE 8770
CMD ["python", "-m", "relationship_agent.beta.app", "--host", "0.0.0.0", "--port", "8770"]
