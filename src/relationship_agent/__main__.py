"""python -m relationship_agent 的兼容启动入口。

把 python -m 的调用交给 beta.app.main。和顶层两个启动文件共用同一个
服务入口，避免不同启动方式加载不同页面或数据库逻辑。
"""

from .beta.app import main

if __name__ == "__main__":
    main()
