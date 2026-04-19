# LLM 增强的 Windows 目录分析工具

这是一个面向 Windows 磁盘清理场景的目录分析项目。它会先扫描指定根目录第一层子文件夹的体积，从大到小排序；然后**只挑选根目录第一层 TopN（默认 10 个）**目录，把它们的子文件夹结构树交给大模型进行用途判断、风险评估和清理建议生成，最终输出一份 Markdown 报告，并支持在本地 Web UI 中结构化查看。

项目目标不是“直接替你删文件”，而是先生成一份**可执行前的磁盘清理决策报告**，帮助你判断：

- 这个目录大概率是什么
- 它是缓存、日志、索引、安装包还是用户数据
- 风险是低 / 中 / 高
- 更稳的清理方式是什么

## 当前能力

当前主线版本同时提供两种使用方式：

- CLI：适合批处理、脚本调用、直接生成 Markdown 报告
- 本地 Web UI：适合查看历史快照、按章节阅读分析结果、按风险/建议类型筛选、导出 Markdown/JSON、对比两次扫描结果

核心分析策略：

- 只分析根目录第一层 TopN
- 每个目录只调用一次 LLM
- 不做递归 TopN，避免调用轮次和 token 指数爆炸
- 使用 `--tree-max-chars` 截断过长结构树，控制提示词成本

## 项目结构与版本策略

本仓库采用“同一仓库，多阶段版本管理”的方式维护：

- `main`
  当前主线版本，包含 CLI + LLM 分析 + Markdown 报告 + 本地 Web UI 阅读器
- `legacy/gui-only`
  早期仅 GUI 原型版本

建议标签：

- `v1.0-gui-only`
- `v2.0-llm-webui`

说明：

- 当前工作区已经是 `v2` 主线代码
- `legacy/gui-only` 分支与 `v1.0-gui-only` 标签需要基于旧 GUI 版源码创建
- 如果旧 GUI 版代码尚未导入当前仓库，需要先补齐旧源码，再创建该历史分支和标签

## 环境准备

1. 创建本地虚拟环境：

```powershell
uv venv .venv
```

2. 安装项目：

```powershell
uv pip install --python .venv\Scripts\python.exe -e .
```

如果你本机 `uv` 默认缓存目录权限不稳定，也可以显式指定可写缓存目录：

```powershell
uv pip install --cache-dir J:\1\uv-cache --python .venv\Scripts\python.exe -e .
```

3. 复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

## 环境变量

本项目使用 OpenAI 兼容接口（`/v1/chat/completions`），因此可兼容硅基流动、硅基智能以及其它 OpenAI-compatible 平台。

需要配置：

- `LLM_API_KEY`：API Key
- `LLM_BASE_URL`：接口基础地址，可写 `https://xxx` 或 `https://xxx/v1`
- `LLM_MODEL`：模型名称，例如 `Qwen/Qwen3-32B`

程序会自动补齐 `/v1`，因此不强制要求你在 `LLM_BASE_URL` 中手动写完整路径。

## CLI 用法

推荐入口：

```powershell
.venv\Scripts\python.exe run_folder_agent.py --root "D:\YourFolder" --output ".\report\analysis_report.md"
```

也可以直接使用模块方式：

```powershell
.venv\Scripts\python.exe -m folder_agent.cli --root "D:\YourFolder" --output ".\report\analysis_report.md"
```

常用参数：

- `--top-n`：只分析根目录第一层 TopN，默认 `10`
- `--tree-max-chars`：结构树最大字符数，默认 `10000`
- `--skip-llm`：仅扫描，不调用大模型
- `--quiet`：减少执行日志输出

## Web UI 用法

启动本地面板：

```powershell
.venv\Scripts\python.exe run_folder_panel.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

当前 Web UI 支持：

- 选择根目录并启动扫描 / 分析任务
- 查看执行日志
- 查看历史快照
- 单列文档流方式阅读报告
- 左侧章节导航切换
- 按风险等级、建议类型、最小体积筛选
- 阅读密度切换（完整正文 / 只看摘要）
- 导出 Markdown / JSON
- 对比两次扫描结果

## 报告内容

最终生成的 Markdown 报告通常包含：

- 根目录第一层子文件夹大小排名
- 根目录第一层 TopN 分析结果
- 每个目录的用途判断、判断依据、清理建议、风险等级、建议操作步骤
- 无法访问的路径清单（如有）

报告写入使用 `utf-8-sig`，以尽量避免 Windows / 记事本打开乱码。

## 设计取舍

为什么不做“递归 Top10”？

因为真实目录结构里经常会出现：

- `uv\cache`
- 浏览器缓存目录
- IDE 索引目录
- 包管理器缓存目录

如果每一层都继续递归 Top10，会导致：

- LLM 调用轮次快速膨胀
- 单次输入 token 过长
- 时间成本和费用不可控

因此本项目明确选择：

- 只分析根目录第一层 TopN
- 每个目录只做一轮 LLM 分析

这是为了让项目在真实 Windows 清理场景里真正可跑、可控、可复用。

## 说明

- 对于无权限访问的目录，程序会记录并跳过
- 项目默认不会直接删除目录
- 当前主线版本更偏“分析报告生成器 + 报告阅读器”，而不是全自动清理 Agent
