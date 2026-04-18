# “LLM 增强的目录分析工具（LLM-powered folder analyzer）

这个项目会扫描指定根目录下的文件夹大小，按从大到小排序；然后**只挑选根目录第一层**体积最大的前 N 个子文件夹（默认 10 个）交给大模型进行分析，输出清理建议与风险提示。

大模型分析输入包含：文件夹路径、大小、TopN 排名上下文、以及该文件夹的（尽量）完整子文件夹结构树。当结构树过大导致提示词超出`max_prompt_tokens` 限制时，程序会自动缩小结构树并重试；单个目录分析失败也不会中断整次 TopN 分析。

最终输出一个 Markdown 报告，包含：

- 根目录下子文件夹大小排名
- 根目录第一层 Top N 文件夹的大模型分析结果
- 无法访问的路径清单（如有）

## 环境准备

1. 使用 `uv` 创建本地虚拟环境：

```powershell
uv venv .venv
```

2. 安装项目（可选，但推荐；这样可以用 `-m folder_agent.cli`）：

```powershell
# 如果用户目录下 uv 缓存权限受限，建议指定一个可写 cache-dir
uv pip install --cache-dir J:\1\uv-cache --python .venv\Scripts\python.exe -e .
```

3. 复制环境变量模板并填入API Key：

```powershell
Copy-Item .env.example .env
```

## 运行

推荐方式（无需安装包/无需设置 `PYTHONPATH`）：

```powershell
.venv\Scripts\python.exe run_folder_agent.py --root "D:\YourFolder" --output ".\report\analysis_report.md"
```

常用参数：

- `--top-n`：只分析根目录第一层 Top N，默认 `10`
- `--tree-max-chars`：发给大模型的结构树最大字符数上限（安全阈值），默认 `10000`
- `--skip-llm`：仅生成大小报告，不调用大模型
- `--quiet`：不输出执行过程日志

## 环境变量

推荐（通用 OpenAI 兼容配置，适配硅基流动/硅基智能/其它兼容平台）：

- `LLM_API_KEY`：API Key（Bearer Token）
- `LLM_BASE_URL`：API Base URL（可写 `https://xxx/v1` 或 `https://xxx`，程序会自动补齐 `/v1`）
- `LLM_MODEL`：模型名称（例如 `Qwen/Qwen3-32B`）


## 说明

- 对于无权限访问的目录，程序会记录并跳过。
- 报告为 Markdown 格式，便于后续继续整理或导出。
- 报告写入使用 `UTF-8 with BOM`（`utf-8-sig`）编码，避免 Windows/记事本打开乱码。
