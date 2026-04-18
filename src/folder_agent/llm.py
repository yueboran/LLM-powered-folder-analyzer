from __future__ import annotations

"""大模型调用（硅基流动 API）。

本模块负责把扫描出来的“文件夹名称/大小/子结构摘要”组织成提示词，并调用硅基流动
（SiliconFlow）提供的 OpenAI 兼容接口：`POST /chat/completions`。

实现要点：
- 使用 Python 标准库 `urllib` 发起 HTTP 请求，避免引入额外依赖。
- 提示词包含：路径、大小、当前层 TopN 排名、直接子文件夹列表、以及裁剪后的树摘要。
- 对 HTTP/网络/响应异常做显式抛错，便于交接排查。
"""

import json
import os
import socket
from pathlib import Path
from urllib import error, request

from folder_agent.models import FolderNode
from folder_agent.scanner import format_bytes, render_tree, render_tree_full


class SiliconFlowAnalyzer:
    """OpenAI 兼容 Chat 接口的轻量客户端（默认适配硅基流动/硅基智能等平台）。

    兼容策略：
    - 绝大多数第三方“模型中转/聚合/国产平台”都提供 OpenAI 兼容接口（`/v1/chat/completions`）。
    - 本客户端只依赖标准库，通过配置 `base_url/api_key/model` 即可切换服务商。

    环境变量（推荐）：
    - `LLM_API_KEY`：API Key（Bearer Token）
    - `LLM_BASE_URL`：API Base URL（可以不带 `/v1`；内部会自动补齐）
    - `LLM_MODEL`：模型名称（例如 `Qwen/Qwen3-32B`）

    向后兼容：
    - 如果未设置 `LLM_*`，会回退读取 `SILICONFLOW_API_KEY / SILICONFLOW_BASE_URL / SILICONFLOW_MODEL`。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        """创建分析器实例。

        输入：
        - `api_key`：可选，API Key；默认从环境变量 `SILICONFLOW_API_KEY` 读取。
        - `base_url`：可选，API Base URL；默认 `https://api.siliconflow.cn/v1`。
        - `model`：可选，模型名称；默认从环境变量 `SILICONFLOW_MODEL` 读取。

        输出：
        - 无返回值。

        异常：
        - `ValueError`：缺少 `SILICONFLOW_API_KEY`。
        """

        # 允许构造函数显式传参；若不传则从环境变量读取（`.env` 会在 CLI 入口加载）。
        # 优先读取通用 `LLM_*`，未配置时再回退到历史 `SILICONFLOW_*`，避免破坏已有用户配置。
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("SILICONFLOW_API_KEY")
        self.base_url = (
            base_url
            or os.getenv("LLM_BASE_URL")
            or os.getenv("SILICONFLOW_BASE_URL")
            or "https://api.siliconflow.cn/v1"
        )
        self.model = model or os.getenv("LLM_MODEL") or os.getenv("SILICONFLOW_MODEL") or "Qwen/Qwen3-32B"

        if not self.api_key:
            raise ValueError("Missing LLM_API_KEY (or SILICONFLOW_API_KEY). Please configure it in .env.")

    def analyze_folder(
        self,
        folder: FolderNode,
        depth: int,
        ranking_snapshot: list[FolderNode],
        tree_max_chars: int,
    ) -> str:
        """对单个文件夹发起一次大模型分析请求并返回 Markdown 文本。

        输入：
        - `folder`：待分析文件夹节点（包含 `children` 等扫描信息）。
        - `depth`：逻辑深度（根目录为 0，第一层子目录为 1）。
        - `ranking_snapshot`：当前层 TopN 的快照，用于让模型理解“为什么轮到它”。
        - `tree_max_chars`：结构树最大字符数上限（用于避免提示词过长导致请求失败）。

        输出：
        - `str`：模型返回的中文 Markdown 分析文本（包含用途推断与清理建议）。

        异常：
        - 除缺少 API key（构造函数已校验）外，本函数尽量不抛运行时异常；
          若遇到 token 超限/超时/网络波动，会自动缩短结构树并重试，尽量保证仍能产出分析文本。
        """

        rank_lines = [
            f"{idx}. {item.path.name or str(item.path)} - {format_bytes(item.size_bytes)}"
            for idx, item in enumerate(ranking_snapshot, start=1)
        ]

        # 兼容 base_url 既可能是 `https://host/v1`，也可能是 `https://host`。
        base = self.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        endpoint = f"{base}/chat/completions"

        system_msg = {"role": "system", "content": "你输出严谨、可执行、面向清理决策的中文分析报告。"}

        def _build_prompt_full(tree_chars: int) -> str:
            tree_text, tree_truncated, tree_node_count = render_tree_full(folder, max_chars=tree_chars)
            return f"""
你是一个资深 Windows 磁盘清理与目录结构分析专家。请根据下面的目录信息，判断该文件夹可能是什么、作用是什么，以及是否适合清理。

要求：
1. 结合文件夹名称、子文件夹名称、层级结构和大小进行推测。
2. 说明判断依据，避免空泛结论。
3. 对清理建议给出风险等级：低 / 中 / 高。
4. 如果判断不确定，要明确写出“不确定”以及原因。
5. 输出使用中文 Markdown，包含以下小节：
   - 可能用途
   - 判断依据
   - 清理建议
   - 风险等级
   - 建议操作步骤

当前分析对象：
- 路径: {folder.path}
- 深度: {depth}
- 文件夹大小: {format_bytes(folder.size_bytes)}
- 直接子文件夹数量: {len(folder.children)}

当前层级的 Top 排名：
{chr(10).join(rank_lines) if rank_lines else "(无)"}

当前文件夹的子文件夹结构树：
- 结构树节点数（写入提示词）: {tree_node_count}
- 是否截断: {"是" if tree_truncated else "否"}
{tree_text}
""".strip()

        def _build_prompt_summary() -> str:
            # 更短的兜底提示词：显著减少 token 压力与响应耗时。
            tree_text = render_tree(folder, max_depth=3, max_children=50)
            return f"""
你是一个资深 Windows 磁盘清理与目录结构分析专家。请根据下面的目录信息，判断该文件夹可能是什么、作用是什么，以及是否适合清理。

要求：
1. 结合文件夹名称、子文件夹名称、层级结构和大小进行推测。
2. 说明判断依据，避免空泛结论。
3. 对清理建议给出风险等级：低 / 中 / 高。
4. 如果判断不确定，要明确写出“不确定”以及原因。
5. 输出使用中文 Markdown，包含以下小节：
   - 可能用途
   - 判断依据
   - 清理建议
   - 风险等级
   - 建议操作步骤

当前分析对象：
- 路径: {folder.path}
- 深度: {depth}
- 文件夹大小: {format_bytes(folder.size_bytes)}
- 直接子文件夹数量: {len(folder.children)}

当前层级的 Top 排名：
{chr(10).join(rank_lines) if rank_lines else "(无)"}

当前文件夹的树形结构摘要（已裁剪）：
{tree_text}
""".strip()

        def _is_token_limit(detail: str | None) -> bool:
            if not detail:
                return False
            try:
                obj = json.loads(detail)
            except Exception:
                return False
            return isinstance(obj, dict) and obj.get("code") == 20015

        def _call(prompt_text: str, timeout_s: int) -> tuple[dict | None, str | None, Exception | None]:
            body = {
                "model": self.model,
                "temperature": 0.2,
                "messages": [system_msg, {"role": "user", "content": prompt_text}],
            }
            req = request.Request(
                url=endpoint,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with request.urlopen(req, timeout=timeout_s) as resp:
                    return json.loads(resp.read().decode("utf-8")), None, None
            except error.HTTPError as exc:
                return None, exc.read().decode("utf-8", errors="ignore"), exc
            except error.URLError as exc:
                return None, None, exc
            except (TimeoutError, socket.timeout) as exc:
                return None, None, exc

        def _extract_content(resp: dict | None) -> str:
            if not resp:
                return ""
            choices = resp.get("choices") or []
            if not choices:
                return ""
            return (choices[0].get("message") or {}).get("content", "") or ""

        # 默认超时适当放宽，减少长目录分析超时概率。
        timeout_s = 240
        min_tree_chars = 2_000

        # 第一阶段：使用“完整树（按 tree_max_chars 截断）”请求。
        # 如果服务端返回 token 超限，则继续缩短结构树并重试，确保最终仍能分析。
        last_detail: str | None = None
        tree_chars = max(tree_max_chars, min_tree_chars)
        for _ in range(6):
            prompt_text = _build_prompt_full(tree_chars)
            resp, detail, exc = _call(prompt_text, timeout_s=timeout_s)
            if detail is not None:
                last_detail = detail
                if _is_token_limit(detail) and tree_chars > min_tree_chars:
                    tree_chars = max(min_tree_chars, int(tree_chars * 0.5))
                    continue
                # 其他 HTTP 错误进入兜底摘要树
                break
            if exc is not None:
                # 超时/网络波动：先再试一次同样提示词；失败则进入兜底摘要树
                resp2, detail2, _exc2 = _call(prompt_text, timeout_s=timeout_s)
                if detail2 is not None:
                    last_detail = detail2
                    break
                content2 = _extract_content(resp2)
                if content2:
                    return content2
                break

            content = _extract_content(resp)
            if content:
                return content
            last_detail = "empty content"
            break

        # 第二阶段兜底：使用更短的“摘要树”提示词，降低 token 压力与响应耗时。
        prompt_text = _build_prompt_summary()
        for _ in range(2):
            resp, detail, exc = _call(prompt_text, timeout_s=timeout_s)
            if detail is not None:
                last_detail = detail
                continue
            if exc is not None:
                continue
            content = _extract_content(resp)
            if content:
                return content

        # 最后兜底：不抛异常，返回一段“降级说明”，避免报告里出现硬失败段落。
        return (
            "### 分析降级（未获取到模型回复）\n\n"
            f"- 路径: `{folder.path}`\n"
            "- 说明: 本次调用模型未成功（可能是提示词过长、接口短暂异常或网络波动）。\n\n"
            "建议：\n"
            f"- 继续降低 `--tree-max-chars`（当前={tree_max_chars}），例如 10000 或 5000。\n"
            "- 避开高峰期或稍后重试。\n"
            "- 如环境存在代理，确保 `HTTP_PROXY/HTTPS_PROXY` 不指向无效地址。\n"
            + (f"\n调试信息（简要）: `{last_detail}`\n" if last_detail else "")
        )
