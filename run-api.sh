#!/usr/bin/env zsh
# 本脚本只配置当前服务进程；外部已设置的模型参数优先于下面的默认值。
set -euo pipefail
cd "$(dirname "$0")"
export PERSONA_MODE=api
export RELATIONSHIP_MODEL="${RELATIONSHIP_MODEL:-glm-5.3}"
export RELATIONSHIP_PROTOCOL="${RELATIONSHIP_PROTOCOL:-chat-completions}"
export RELATIONSHIP_BASE_URL="${RELATIONSHIP_BASE_URL:-https://open.bigmodel.cn/api/paas/v4}"
export PERSONA_PROVIDER_LABEL="${PERSONA_PROVIDER_LABEL:-GLM}"
api_port="${PERSONA_PORT:-8770}"
# Origin 必须与浏览器实际访问地址一致，用于请求来源检查。此脚本用于本地访问。
export PERSONA_ORIGIN="http://127.0.0.1:$api_port"
if [[ -z "${PERSONA_API_KEY:-}${GLM_API_KEY:-}" ]]; then
  read -rs "PERSONA_API_KEY?粘贴你有权用于此项目的模型 Key（隐藏输入），然后回车："
  export PERSONA_API_KEY
  echo
fi
# 用服务替换 shell 进程，让 Ctrl+C 和退出状态直接作用于服务器。
exec .venv/bin/python persona_app.py --port "$api_port"
