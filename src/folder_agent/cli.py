from __future__ import annotations

"""命令行入口（CLI）。

本模块负责把各个子模块串起来，形成一个可执行的“扫描 -> 递归 TopN 分析 -> 报告输出”流程。

调用流程总览（ASCII 图，交接用）：

    +-------------------+
    | main()            |
    +-------------------+
              |
              v
    +-------------------+        +-------------------+
    | load_env_file()   |        | parse_args()      |
    | 读取 .env 到环境   |        | 解析命令行参数     |
    +-------------------+        +-------------------+
              |                         |
              +-----------+-------------+
                          |
                          v
                +-------------------+
                | build_tree(root)  |
                | 扫描目录树与大小   |
                +-------------------+
                          |
                          v
                +-------------------+
                | collect_analyses  |
                | 每层 TopN         |
                | 可选调用 LLM       |
                +-------------------+
                          |
                          v
                +-------------------+
                | build_report      |
                | 拼装 Markdown      |
                +-------------------+
                          |
                          v
                +-------------------+
                | write_report      |
                | 写入到 output      |
                +-------------------+

模块依赖关系（高层）：
- `config.py`：加载 `.env`
- `scanner.py`：扫描文件夹树、统计大小、渲染树摘要
- `llm.py`：调用硅基流动 API 获取分析结果
- `report.py`：将结果汇总成 Markdown
"""

import argparse
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Callable

from folder_agent.config import load_env_file
from folder_agent.llm import SiliconFlowAnalyzer
from folder_agent.models import FolderAnalysis, FolderNode
from folder_agent.report import build_report, write_report
from folder_agent.scanner import ScanStats, build_tree, top_children


def parse_args() -> argparse.Namespace:
    """定义并解析命令行参数。

    输出：
    - `argparse.Namespace`：包含 `root/output/top_n/max_depth/...` 等字段。

    异常：
    - `SystemExit`：参数错误时 `argparse` 会退出进程（标准行为）。
    """

    parser = argparse.ArgumentParser(
        description="Analyze folder sizes and generate SiliconFlow-powered cleanup report."
    )
    parser.add_argument("--root", required=True, help="Root folder to analyze.")
    parser.add_argument("--output", default="report/analysis_report.md", help="Markdown report output path.")
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Only analyze the top N largest subfolders under the root (first level only).",
    )
    parser.add_argument(
        "--tree-max-chars",
        type=int,
        default=10000,
        help="Max characters of the folder tree included in LLM prompt (safety limit).",
    )
    parser.add_argument("--skip-llm", action="store_true", help="Only scan folders and write ranking report.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress logs to stdout.")
    return parser.parse_args()


def _log(enabled: bool, message: str) -> None:
    """Print one progress line with a timestamp.

    输入：
    - `enabled`：是否输出日志。
    - `message`：日志内容。

    输出：
    - 无。

    异常：
    - 一般不会抛异常；stdout 失败时可能由运行环境抛出 `OSError`。
    """

    if not enabled:
        return
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {message}")


def collect_analyses(
    node: FolderNode,
    analyzer: SiliconFlowAnalyzer | None,
    top_n: int,
    tree_max_chars: int,
    log_fn: Callable[[str], None] | None = None,
) -> list[FolderAnalysis]:
    """只分析根目录第一层 TopN 子文件夹（不递归），并返回分析结果列表。

    这是“只分析 10 轮”的策略：
    - 只取 `node`（根目录）的第一层子文件夹按大小排序后的前 `top_n` 个。
    - 对每个目标子文件夹调用一次大模型（或写入占位文本）。
    - 不会再进入该子文件夹继续分析，因此总轮次最多为 `top_n`。

    输入：
    - `node`：根目录节点。
    - `analyzer`：LLM 分析器；`None` 表示跳过模型分析。
    - `top_n`：只分析根目录第一层的 TopN。
    - `tree_max_chars`：结构树最大字符数上限（传给 LLM）。
    - `log_fn`：可选日志函数。

    输出：
    - `list[FolderAnalysis]`：长度不超过 `top_n`。

    异常：
    - `RuntimeError`：LLM 请求失败时会向上抛出。
    """

    if not node.children:
        return []

    current_top = top_children(node, top_n)
    analyses: list[FolderAnalysis] = []

    for child in current_top:
        # 无论是否调用 LLM，都写入一条分析记录：
        # - `--skip-llm` 时是占位文本；
        # - 有 LLM 但无子文件夹时，提示“无法基于子结构推断”；
        # 这样报告结构稳定，便于后续人工补充或二次处理。
        llm_summary = "已跳过大模型分析。"
        if analyzer is not None:
            if log_fn is not None:
                log_fn(f"LLM 分析开始: depth=1 size={child.size_bytes} path={child.path}")
            # `analyze_folder` 内部已经实现 token 超限/超时的自动降级与兜底，不应在这里把异常原文写进报告。
            llm_summary = analyzer.analyze_folder(
                folder=child,
                depth=1,
                ranking_snapshot=current_top,
                tree_max_chars=tree_max_chars,
            )
            if log_fn is not None:
                log_fn(f"LLM 分析完成: path={child.path}")

        analyses.append(
            FolderAnalysis(
                folder_path=child.path,
                depth=1,
                size_bytes=child.size_bytes,
                child_count=len(child.children),
                ranking_snapshot=[(item.path.name or str(item.path), item.size_bytes) for item in current_top],
                llm_summary=llm_summary,
            )
        )

    return analyses


def main() -> None:
    """CLI 主入口。

    输入：
    - 从命令行读取参数（见 `parse_args`）。
    - 从 `.env` 或系统环境变量读取硅基流动配置。

    输出：
    - 在 `--output` 指定路径生成 Markdown 报告文件，并在 stdout 打印输出路径。

    异常：
    - `FileNotFoundError` / `NotADirectoryError`：`--root` 不存在或不是目录。
    - `ValueError`：未配置 `SILICONFLOW_API_KEY` 且未使用 `--skip-llm`。
    - `RuntimeError`：LLM 调用失败（网络/HTTP/响应解析等）。
    - `OSError`：写报告失败（无权限/磁盘错误等）。
    """

    load_env_file()
    args = parse_args()
    log_enabled = not args.quiet
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Root path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Root path is not a directory: {root}")

    _log(log_enabled, f"开始扫描: root={root}")
    stats = ScanStats()
    scan_t0 = perf_counter()
    root_node = build_tree(root, stats)
    scan_s = perf_counter() - scan_t0
    _log(
        log_enabled,
        f"扫描完成: root_size={root_node.size_bytes}B direct_children={len(root_node.children)} "
        f"inaccessible={len(stats.inaccessible_paths)} elapsed={scan_s:.2f}s",
    )

    analyses: list[FolderAnalysis] = []
    if args.skip_llm:
        _log(log_enabled, "已指定 --skip-llm：跳过大模型分析，仅生成大小排名报告。")
    else:
        _log(
            log_enabled,
            f"开始大模型分析（仅根目录第一层 TopN，不递归）: top_n={args.top_n} "
            f"tree_max_chars={args.tree_max_chars}",
        )
        analyzer = SiliconFlowAnalyzer()
        llm_t0 = perf_counter()
        analyses = collect_analyses(
            node=root_node,
            analyzer=analyzer,
            top_n=args.top_n,
            tree_max_chars=args.tree_max_chars,
            log_fn=(lambda m: _log(log_enabled, m)),
        )
        llm_s = perf_counter() - llm_t0
        _log(log_enabled, f"大模型分析完成: items={len(analyses)} elapsed={llm_s:.2f}s")

    report = build_report(
        root=root_node,
        analyses=analyses,
        inaccessible_paths=stats.inaccessible_paths,
        top_n=args.top_n,
    )
    output_path = Path(args.output).expanduser().resolve()
    write_report(output_path, report)
    _log(log_enabled, f"报告已写入: {output_path}")


if __name__ == "__main__":
    main()
