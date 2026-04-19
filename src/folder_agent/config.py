from __future__ import annotations

"""配置与环境变量加载。

本模块只做一件事：从本地 `.env` 文件读取配置并写入进程环境变量。

设计取舍：
- 不使用第三方库（例如 python-dotenv），以保证在无外网/无法安装依赖的环境里也能运行。
- 解析器只实现本项目所需的最小语法子集，便于交接与维护。
"""

import os
from pathlib import Path


def load_env_file(dotenv_path: str | Path = ".env") -> None:
    """从本地 `.env` 文件加载简单的 `KEY=VALUE` 环境变量。

    支持语法：
    - 空行忽略
    - 以 `#` 开头的注释行忽略
    - 以第一个 `=` 作为分隔符拆分 key/value
    - value 支持包裹引号（单引号/双引号），会去掉最外层引号

    重要行为：
    - 如果某个 key 已经在 `os.environ` 中存在，本函数不会覆盖它（外部注入优先）。

    输入：
    - `dotenv_path`：`.env` 文件路径（默认当前目录下 `.env`）。

    输出：
    - 无返回值。成功时会把解析到的变量写入 `os.environ`。

    异常：
    - `UnicodeDecodeError`：`.env` 不是 UTF-8 编码且包含非 ASCII 字符时可能触发。
    - `OSError`：读取文件失败（例如权限/文件系统错误）。
      当前实现不捕获该异常，便于交接排查时快速暴露问题。
    """

    path = Path(dotenv_path)
    if not path.exists():
        # `.env` 不存在是允许的：用户也可能通过系统环境变量或 shell 传入配置。
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            # 跳过空行、注释行、以及不包含 `=` 的非法行。
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            # 保留外部注入值的优先级：已存在则不覆盖。
            os.environ[key] = value
