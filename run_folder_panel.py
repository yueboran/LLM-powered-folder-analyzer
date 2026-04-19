from __future__ import annotations

import sys
from pathlib import Path


def _ensure_src_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    sys.path.insert(0, str(src_dir))


def main() -> None:
    _ensure_src_on_syspath()
    from folder_agent.webapp_v2 import main as web_main

    web_main()


if __name__ == "__main__":
    main()
