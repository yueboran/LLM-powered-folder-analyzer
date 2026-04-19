from __future__ import annotations

"""报告生成与写入。

本模块将扫描结果（目录大小排名）与大模型分析结果拼装为一份 Markdown 报告，并落盘写入。
"""

from datetime import datetime
from pathlib import Path

from folder_agent.models import FolderAnalysis, FolderNode
from folder_agent.scanner import format_bytes


def build_report(
    root: FolderNode,
    analyses: list[FolderAnalysis],
    inaccessible_paths: list[str],
    top_n: int,
) -> str:
    """将扫描结果与模型分析结果拼装为最终 Markdown 报告文本。

    输入：
    - `root`：根目录节点（包含根目录的子文件夹列表与大小）。
    - `analyses`：按递归顺序产出的分析结果列表。
    - `inaccessible_paths`：扫描过程中记录的不可访问路径列表。
    - `top_n`：每层分析 TopN 的 N 值（用于报告标题说明）。

    输出：
    - `str`：Markdown 文本（末尾包含换行）。

    异常：
    - 一般不会主动抛异常；但若字段内容异常或编码错误，仍可能抛出异常。
    """

    lines: list[str] = []
    lines.append("# 文件夹分析报告")
    lines.append("")
    lines.append(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 根目录: `{root.path}`")
    lines.append(f"- 根目录总大小: `{format_bytes(root.size_bytes)}`")
    lines.append(f"- 根目录直接子文件夹数量: `{len(root.children)}`")
    lines.append("")
    lines.append("## 根目录子文件夹大小排名")
    lines.append("")

    for idx, child in enumerate(root.children, start=1):
        # 顶层排名：帮助使用者先看到“空间主要被谁占了”，再决定是否细读分析段落。
        lines.append(f"{idx}. `{child.path}` - `{format_bytes(child.size_bytes)}`")

    lines.append("")
    lines.append(f"## 大模型分析结果（根目录第一层 Top {top_n}）")
    lines.append("")

    if not analyses:
        lines.append("未生成任何大模型分析结果。")
    else:
        for index, item in enumerate(analyses, start=1):
            lines.append(f"### {index}. `{item.folder_path}`")
            lines.append("")
            lines.append(f"- 深度: `{item.depth}`")
            lines.append(f"- 文件夹大小: `{format_bytes(item.size_bytes)}`")
            lines.append(f"- 直接子文件夹数量: `{item.child_count}`")
            lines.append("- 当前层 Top 排名快照:")
            if item.ranking_snapshot:
                for rank, size in item.ranking_snapshot:
                    # 把“当层排名快照”贴在分析块旁边，便于理解该文件夹为何被选中分析。
                    lines.append(f"  - `{rank}` - `{format_bytes(size)}`")
            else:
                lines.append("  - 无")
            lines.append("")
            lines.append(item.llm_summary.strip())
            lines.append("")

    if inaccessible_paths:
        lines.append("## 无法访问的路径")
        lines.append("")
        for path in sorted(set(inaccessible_paths)):
            lines.append(f"- `{path}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(output_path: Path, content: str) -> None:
    """把报告写入到磁盘。

    输入：
    - `output_path`：输出文件路径。
    - `content`：Markdown 文本内容（建议已包含末尾换行）。

    输出：
    - 无返回值。写入成功则文件落盘。

    异常：
    - `OSError`：创建目录失败、无权限、磁盘写入失败等。
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Windows 环境（尤其是记事本/部分 PowerShell 场景）对无 BOM 的 UTF-8 识别不稳定；
    # 这里使用 UTF-8 with BOM，减少报告打开时出现乱码的概率。
    output_path.write_text(content, encoding="utf-8-sig")
