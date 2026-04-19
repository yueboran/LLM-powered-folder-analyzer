from __future__ import annotations

"""旧 Web 入口兼容包装模块。

说明：
- 保留原始导入路径，避免旧脚本失效
- 当前统一转发到 `folder_agent.webapp_v2`
- 后续 Web 主实现请继续维护 `webapp_v2.py`
"""

from folder_agent.webapp_v2 import AppState, JobRecord, PanelHandler, main, parse_args, run_scan_job


__all__ = [
    "AppState",
    "JobRecord",
    "PanelHandler",
    "main",
    "parse_args",
    "run_scan_job",
]


if __name__ == "__main__":
    main()
