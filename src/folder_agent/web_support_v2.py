from __future__ import annotations

"""旧 Web 支撑模块兼容包装。

说明：
- `web_support_v2.py` 已被 `web_support_v3.py` 取代
- 保留该模块是为了兼容旧导入路径
- 当前 Web 相关逻辑请统一维护在 `web_support_v3.py`
"""

from folder_agent.web_support_v3 import (
    build_snapshot,
    compare_snapshots,
    default_candidate,
    generate_cleanup_scripts,
    parse_error_message,
    parse_has_error,
    parse_risk_level,
    parse_suggestion_type,
    parse_summary,
    parse_title,
    serialize_node,
    snapshot_metadata,
)


__all__ = [
    "build_snapshot",
    "compare_snapshots",
    "default_candidate",
    "generate_cleanup_scripts",
    "parse_error_message",
    "parse_has_error",
    "parse_risk_level",
    "parse_suggestion_type",
    "parse_summary",
    "parse_title",
    "serialize_node",
    "snapshot_metadata",
]
