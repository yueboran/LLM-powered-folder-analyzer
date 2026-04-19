from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class FolderNode:
    """扫描树上的一个文件夹节点。

    字段：
    - `path`：该文件夹的绝对路径。
    - `size_bytes`：该文件夹的递归总大小（包含所有子孙文件夹中的文件）。
    - `children`：子文件夹节点列表。

    约定：
    - `children` 会在扫描阶段按 `size_bytes` 从大到小排序，方便后续 TopN 选取。
    """

    path: Path
    size_bytes: int
    children: list["FolderNode"] = field(default_factory=list)

    @property
    def name(self) -> str:
        """返回用于展示的文件夹名称。

        说明：
        - 大多数情况下 `Path.name` 足够；
        - 但某些根路径（例如盘符根目录）可能返回空名称，此时回退为完整路径字符串。

        返回：
        - `str`：用于展示的名称。
        """

        return self.path.name or str(self.path)


@dataclass(slots=True)
class FolderAnalysis:
    """单个文件夹的分析结果（用于报告汇总）。

    说明：
    - 扫描阶段先构建完整的目录树（`FolderNode`）。
    - 分析阶段按“每层 TopN”选择文件夹并调用大模型。
    - 每次大模型返回后，保存为一个 `FolderAnalysis`，最终交给报告模块拼装输出。

    字段：
    - `folder_path`：被分析的文件夹路径。
    - `depth`：分析深度（根目录为 0，第一层子目录为 1，以此类推）。
    - `size_bytes`：该文件夹的递归大小（字节）。
    - `child_count`：直接子文件夹数量（用于提示词与报告展示）。
    - `ranking_snapshot`：当前层 TopN 的快照（列表项是 `(名称, size_bytes)`）。
    - `llm_summary`：大模型返回的 Markdown 文本（或跳过分析的占位文本）。
    """

    folder_path: Path
    depth: int
    size_bytes: int
    child_count: int
    ranking_snapshot: list[tuple[str, int]]
    llm_summary: str
