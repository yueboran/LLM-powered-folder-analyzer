from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from folder_agent.models import FolderAnalysis, FolderNode
from folder_agent.scanner import format_bytes, top_children


RISK_RE = re.compile(r"(?:风险等级|风险级别)[：:\s`*]*([低中高])")


def serialize_node(node: FolderNode) -> dict[str, Any]:
    """将目录树节点序列化为可供前端直接渲染的 JSON 结构。"""

    return {
        "name": node.name,
        "path": str(node.path),
        "sizeBytes": node.size_bytes,
        "sizeHuman": format_bytes(node.size_bytes),
        "childCount": len(node.children),
        "children": [serialize_node(child) for child in node.children],
    }


def parse_risk_level(summary: str) -> str:
    """从模型 Markdown 中提取风险等级。"""

    match = RISK_RE.search(summary)
    if not match:
        return "unknown"
    return {"低": "low", "中": "medium", "高": "high"}.get(match.group(1), "unknown")


def parse_suggestion_type(summary: str, folder_path: str) -> str:
    """根据路径与分析正文粗分清理建议类型。"""

    text = f"{folder_path}\n{summary}".lower()
    checks = [
        ("cache", ["cache", "缓存", "temp", "tmp", "gpucache", "code cache"]),
        ("logs", ["log", "日志", "crashdump", "crash"]),
        ("dev_deps", ["node_modules", "uv", "pip", "maven", "gradle", "yarn", "npm", "python"]),
        ("ide_index", ["jetbrains", "index", "索引", "idea", "androidstudio", "pycharm"]),
        ("app_data", ["chrome", "edge", "firefox", "google", "mozilla", "browser", "user data", "wechat"]),
        ("installers", ["updater", "update", "安装包", "补丁", "installer"]),
        ("user_data", ["document", "download", "照片", "视频", "用户数据", "workspace", "project"]),
    ]
    for label, keywords in checks:
        if any(keyword in text for keyword in keywords):
            return label
    return "other"


def parse_title(summary: str, folder_path: Path) -> str:
    """优先从 Markdown 一级标题中提取章节标题。"""

    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return folder_path.name or str(folder_path)


def parse_summary(summary: str) -> str:
    """生成章节摘要，用于文档流中的导读条。"""

    lines = [line.strip() for line in summary.splitlines() if line.strip()]
    content_lines = [line for line in lines if not line.startswith("#") and not line.startswith(">")]
    bullet_lines = [line[2:].strip() for line in content_lines if line.startswith("- ")]
    paragraph_lines = [line for line in content_lines if not line.startswith("- ") and not line.startswith("```")]
    candidates = bullet_lines + paragraph_lines
    for line in candidates:
        if len(line) >= 12:
            return line[:140]
    if candidates:
        return candidates[0][:140]
    return "未提取到摘要，可展开阅读完整分析。"


def parse_error_message(summary: str) -> str | None:
    """识别失败/降级分析对应的错误摘要。"""

    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith("- 错误:"):
            return stripped.removeprefix("- 错误:").strip().strip("`")
        if stripped.startswith("- 说明:"):
            return stripped.removeprefix("- 说明:").strip()
    return None


def parse_has_error(summary: str) -> bool:
    """判断当前分析是否属于失败或降级块。"""

    flags = ["分析失败", "分析降级", "未获取到模型回复", "timed out", "http error"]
    lower_summary = summary.lower()
    return any(flag.lower() in lower_summary for flag in flags)


def default_candidate(risk_level: str, suggestion_type: str) -> bool:
    """给前端一个保守的默认勾选策略。"""

    return risk_level == "low" and suggestion_type in {"cache", "logs", "installers", "dev_deps"}


def build_snapshot(
    snapshot_id: str,
    root_node: FolderNode,
    analyses: list[FolderAnalysis],
    inaccessible_paths: list[str],
    top_n: int,
    options: dict[str, Any],
    markdown: str,
) -> dict[str, Any]:
    """构建一次扫描快照，供 Web UI 直接消费。"""

    top_nodes = {str(node.path): node for node in top_children(root_node, top_n)}
    analysis_items: list[dict[str, Any]] = []
    cleanup_candidates: list[dict[str, Any]] = []

    for analysis in analyses:
        node = top_nodes.get(str(analysis.folder_path))
        title = parse_title(analysis.llm_summary, analysis.folder_path)
        summary = parse_summary(analysis.llm_summary)
        risk_level = parse_risk_level(analysis.llm_summary)
        suggestion_type = parse_suggestion_type(analysis.llm_summary, str(analysis.folder_path))
        has_error = parse_has_error(analysis.llm_summary)
        error_message = parse_error_message(analysis.llm_summary)
        candidate = {
            "path": str(analysis.folder_path),
            "name": analysis.folder_path.name or str(analysis.folder_path),
            "sizeBytes": analysis.size_bytes,
            "sizeHuman": format_bytes(analysis.size_bytes),
            "riskLevel": risk_level,
            "suggestionType": suggestion_type,
            "selectedByDefault": default_candidate(risk_level, suggestion_type) and not has_error,
        }
        cleanup_candidates.append(candidate)
        analysis_items.append(
            {
                "path": str(analysis.folder_path),
                "name": analysis.folder_path.name or str(analysis.folder_path),
                "title": title,
                "summary": summary,
                "depth": analysis.depth,
                "sizeBytes": analysis.size_bytes,
                "sizeHuman": format_bytes(analysis.size_bytes),
                "childCount": analysis.child_count,
                "riskLevel": risk_level,
                "suggestionType": suggestion_type,
                "selectedByDefault": candidate["selectedByDefault"],
                "hasError": has_error,
                "errorMessage": error_message,
                "rankingSnapshot": [
                    {"name": name, "sizeBytes": size, "sizeHuman": format_bytes(size)}
                    for name, size in analysis.ranking_snapshot
                ],
                "summaryMarkdown": analysis.llm_summary,
                "tree": serialize_node(node) if node is not None else None,
            }
        )

    return {
        "id": snapshot_id,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rootPath": str(root_node.path),
        "topN": top_n,
        "options": options,
        "rootSummary": {
            "sizeBytes": root_node.size_bytes,
            "sizeHuman": format_bytes(root_node.size_bytes),
            "directChildCount": len(root_node.children),
            "inaccessibleCount": len(inaccessible_paths),
        },
        "rootChildren": [
            {
                "path": str(child.path),
                "name": child.name,
                "sizeBytes": child.size_bytes,
                "sizeHuman": format_bytes(child.size_bytes),
                "childCount": len(child.children),
            }
            for child in root_node.children
        ],
        "analyses": analysis_items,
        "cleanupCandidates": cleanup_candidates,
        "inaccessiblePaths": inaccessible_paths,
        "markdown": markdown,
    }


def snapshot_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    """提取快照列表页所需的精简元信息。"""

    return {
        "id": snapshot["id"],
        "createdAt": snapshot["createdAt"],
        "rootPath": snapshot["rootPath"],
        "topN": snapshot["topN"],
        "rootSizeBytes": snapshot["rootSummary"]["sizeBytes"],
        "rootSizeHuman": snapshot["rootSummary"]["sizeHuman"],
        "analysisCount": len(snapshot.get("analyses", [])),
    }


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """比较两次扫描的根目录一层子目录变化。"""

    before_map = {item["path"]: item for item in before.get("rootChildren", [])}
    after_map = {item["path"]: item for item in after.get("rootChildren", [])}
    all_paths = sorted(set(before_map) | set(after_map))
    changes = []
    for path in all_paths:
        before_size = before_map.get(path, {}).get("sizeBytes", 0)
        after_size = after_map.get(path, {}).get("sizeBytes", 0)
        delta = after_size - before_size
        if delta == 0:
            continue
        changes.append(
            {
                "path": path,
                "name": Path(path).name or path,
                "beforeSizeBytes": before_size,
                "beforeSizeHuman": format_bytes(before_size),
                "afterSizeBytes": after_size,
                "afterSizeHuman": format_bytes(after_size),
                "deltaBytes": delta,
                "deltaHuman": format_bytes(abs(delta)),
                "direction": "increase" if delta > 0 else "decrease",
            }
        )

    changes.sort(key=lambda item: abs(item["deltaBytes"]), reverse=True)
    root_delta = after["rootSummary"]["sizeBytes"] - before["rootSummary"]["sizeBytes"]
    return {
        "beforeId": before["id"],
        "afterId": after["id"],
        "rootDeltaBytes": root_delta,
        "rootDeltaHuman": format_bytes(abs(root_delta)),
        "direction": "increase" if root_delta > 0 else "decrease",
        "changes": changes,
    }


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def generate_cleanup_scripts(
    snapshot: dict[str, Any],
    selected_paths: list[str],
    quarantine_root: str,
    output_dir: Path,
) -> dict[str, Any]:
    """生成移动到隔离目录及恢复的 PowerShell 脚本。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest: list[dict[str, str]] = []
    for index, path in enumerate(selected_paths, start=1):
        name = Path(path).name or f"item_{index}"
        manifest.append(
            {
                "source": path,
                "target": str(Path(quarantine_root) / f"{index:02d}_{name}"),
            }
        )

    move_lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$quarantineRoot = {_ps_quote(quarantine_root)}",
        "New-Item -ItemType Directory -Force -Path $quarantineRoot | Out-Null",
    ]
    restore_lines = ["$ErrorActionPreference = 'Stop'"]
    for item in manifest:
        source = _ps_quote(item["source"])
        target = _ps_quote(item["target"])
        move_lines.extend(
            [
                f"if (Test-Path -LiteralPath {source}) {{",
                f"    Move-Item -LiteralPath {source} -Destination {target}",
                "}",
            ]
        )
        restore_lines.extend(
            [
                f"if (Test-Path -LiteralPath {target}) {{",
                f"    Move-Item -LiteralPath {target} -Destination {source}",
                "}",
            ]
        )

    move_script = "\n".join(move_lines) + "\n"
    restore_script = "\n".join(restore_lines) + "\n"
    move_path = output_dir / f"cleanup_{snapshot['id']}_{timestamp}.ps1"
    restore_path = output_dir / f"restore_{snapshot['id']}_{timestamp}.ps1"
    manifest_path = output_dir / f"manifest_{snapshot['id']}_{timestamp}.json"
    move_path.write_text(move_script, encoding="utf-8-sig")
    restore_path.write_text(restore_script, encoding="utf-8-sig")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    return {
        "moveScriptPath": str(move_path),
        "restoreScriptPath": str(restore_path),
        "manifestPath": str(manifest_path),
        "moveScript": move_script,
        "restoreScript": restore_script,
        "manifest": manifest,
    }
