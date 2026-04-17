from __future__ import annotations

"""便捷启动脚本（无需安装包/无需设置 PYTHONPATH）。

为什么需要它：
- 你的环境里 `PYTHONPATH` 可能被其它软件预先设置（例如 Webots），不包含本项目的 `src/`。
- 在没执行 `uv pip install -e .` 的情况下，`python -m folder_agent.cli` 会报找不到模块。

用法示例：
  .venv\\Scripts\\python.exe run_folder_agent.py --root "C:\\Users\\26897\\AppData\\Local" --skip-llm
"""

import sys
from pathlib import Path


def _ensure_src_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    # 确保本项目源码目录优先于系统/其它软件注入的路径。
    sys.path.insert(0, str(src_dir))


def main() -> None:
    _ensure_src_on_syspath()
    from folder_agent.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()

