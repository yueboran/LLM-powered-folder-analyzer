from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from folder_agent.models import FolderNode


@dataclass(slots=True)
class ScanStats:
    """扫描过程统计信息（用于记录非致命问题）。"""

    inaccessible_paths: list[str] = field(default_factory=list)


def format_bytes(num_bytes: int) -> str:
    """把字节数转换为可读的大小字符串（B/KB/MB/GB/...）。

    输入：
    - `num_bytes`：字节数（通常为非负整数）。

    输出：
    - `str`：形如 `12.34 MB` 的字符串。
    """

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


def build_tree(path: Path, stats: ScanStats | None = None) -> FolderNode:
    """从 `path` 开始构建完整的文件夹树，并递归统计大小。

    输入：
    - `path`：根目录路径（函数内部会调用 `resolve()` 规范化）。
    - `stats`：可选统计对象，用于收集不可访问路径；不传则内部创建一个。

    输出：
    - `FolderNode`：根节点，包含所有子节点与递归累计大小。

    异常：
    - 对于权限/常见 IO 异常：不会抛出，会被记录到 `stats.inaccessible_paths` 并跳过。
    - 极端情况下仍可能抛出 `OSError`（文件系统异常）等；当前实现不强行吞掉所有异常，
      便于交接排查。
    """

    stats = stats or ScanStats()
    return _build_tree(path.resolve(), stats)


def _build_tree(path: Path, stats: ScanStats) -> FolderNode:
    """递归扫描实现（内部函数）。"""

    children: list[FolderNode] = []
    total_size = 0

    try:
        entries = list(path.iterdir())
    except (PermissionError, OSError):
        # 权限/IO 异常不应中断整个扫描：记录并返回一个 0 大小的占位节点。
        stats.inaccessible_paths.append(str(path))
        return FolderNode(path=path, size_bytes=0, children=[])

    for entry in entries:
        try:
            if entry.is_symlink():
                # 跳过符号链接：
                # 1) 避免循环引用导致无限递归；
                # 2) 避免跨磁盘/跨目录导致的重复计量。
                continue
            if entry.is_dir():
                child_node = _build_tree(entry, stats)
                children.append(child_node)
                total_size += child_node.size_bytes
            elif entry.is_file():
                # 普通文件直接计入当前文件夹大小。
                total_size += entry.stat().st_size
        except (PermissionError, OSError):
            # 即使父目录可访问，单个条目也可能不可读：记录并跳过该条目。
            stats.inaccessible_paths.append(str(entry))

    # 预排序：后续“TopN 子文件夹”可以直接切片获取。
    children.sort(key=lambda node: node.size_bytes, reverse=True)
    return FolderNode(path=path, size_bytes=total_size, children=children)


def top_children(node: FolderNode, top_n: int) -> list[FolderNode]:
    """返回某节点下体积最大的 `top_n` 个子文件夹节点。

    输入：
    - `node`：当前节点。
    - `top_n`：数量上限。

    输出：
    - `list[FolderNode]`：按大小降序的子节点切片（长度不超过 `top_n`）。
    """

    return node.children[:top_n]


def render_tree(node: FolderNode, max_depth: int = 3, max_children: int = 50) -> str:
    """渲染一个“压缩版树结构”字符串，用于放进大模型提示词。

    为什么需要压缩：
    - 真实文件系统树可能非常大，直接发送会导致提示词过长、成本上升、甚至被截断。
    - 这里只提供“足够推断用途”的关键信息：大目录与核心子结构。

    输入：
    - `node`：根节点。
    - `max_depth`：最大展开深度（0 表示只显示根节点行）。
    - `max_children`：每层最多展示多少个子文件夹（超出部分会显示省略提示）。

    输出：
    - `str`：多行文本，适合直接拼接进提示词或日志。
    """

    lines = [f"{node.name}/ ({format_bytes(node.size_bytes)})"]

    def _walk(current: FolderNode, prefix: str, depth: int) -> None:
        if depth >= max_depth:
            # 达到最大展开深度后停止向下展开。
            return
        for child in current.children[:max_children]:
            lines.append(f"{prefix}- {child.name}/ ({format_bytes(child.size_bytes)})")
            _walk(child, f"{prefix}  ", depth + 1)
        remaining = len(current.children) - min(len(current.children), max_children)
        if remaining > 0:
            # 明确提示“还有更多子文件夹被省略”，让模型知道摘要并非完整树。
            lines.append(f"{prefix}- ... {remaining} more subfolders omitted")

    _walk(node, "", 0)
    return "\n".join(lines)


def render_tree_full(node: FolderNode, max_chars: int = 300_000) -> tuple[str, bool, int]:
    """渲染（尽量）完整的子文件夹树结构字符串，用于大模型提示词。

    说明：
    - 这里的“结构树”只包含文件夹节点（本项目扫描树不记录文件名列表）。
    - 在极大目录下，完整树可能非常长，容易超过大模型上下文限制并导致请求失败。
      因此提供 `max_chars` 作为安全阈值：达到阈值后提前截断并标注省略。

    输入：
    - `node`：根节点（要渲染其完整子树）。
    - `max_chars`：输出字符串最大字符数上限；设置为 `0`/负数表示不限制（不推荐）。

    输出：
    - `(text, truncated, node_count)`：
      - `text`：多行树文本。
      - `truncated`：是否发生了截断。
      - `node_count`：实际写入输出的节点数量（用于日志/提示词说明）。
    """

    unlimited = max_chars <= 0
    lines: list[str] = []
    truncated = False
    node_count = 0

    # 根节点行
    header = f"{node.name}/ ({format_bytes(node.size_bytes)})"
    lines.append(header)
    node_count += 1

    # 采用显式栈 DFS，避免 Python 递归深度限制。
    # 栈元素： (节点, 深度, 前缀)
    stack: list[tuple[FolderNode, int, str]] = [(node, 0, "")]

    def _would_overflow(next_line: str) -> bool:
        if unlimited:
            return False
        # +1 for newline join later (approx); keep it simple and safe.
        current_len = sum(len(x) + 1 for x in lines)
        return current_len + len(next_line) + 1 > max_chars

    while stack:
        current, depth, prefix = stack.pop()
        # 子节点需要按原本大小降序顺序输出；栈是 LIFO，所以需要反向压栈。
        for child in reversed(current.children):
            line = f"{prefix}- {child.name}/ ({format_bytes(child.size_bytes)})"
            if _would_overflow(line):
                truncated = True
                marker = f"{prefix}- ... （结构树过大，已截断）"
                # 截断标记也必须严格遵守 max_chars；如果放不下，就不再追加任何内容。
                if not _would_overflow(marker):
                    lines.append(marker)
                stack.clear()
                break

            lines.append(line)
            node_count += 1
            # 下一层缩进两个空格，和 render_tree 的格式保持一致
            stack.append((child, depth + 1, f"{prefix}  "))

    return "\n".join(lines), truncated, node_count
