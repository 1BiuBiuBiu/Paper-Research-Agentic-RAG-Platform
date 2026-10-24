# README 界面展示验收记录

日期：2026-10-24（Asia/Shanghai）

应用：0.13.8

范围：README 产品页面展示；不修改应用行为

## 1. 用户确认范围

用户先确认论文库与 Agent 问答两张页面图，随后明确要求使用真实 API 提问，并确认将最终两张图加入 README、提交并推送远程仓库。

## 2. 展示资产

- `docs/images/paper-library.jpg`：当前论文库桌面视图，展示 6 篇真实文献、文件夹导航、元数据、页数和解析状态。
- `docs/images/agent-qa-real-api.jpg`：当前 Agent 平台桌面视图，展示真实提问、DeepSeek Flash 回答、单篇资料范围、实际 Token、执行过程和原文证据入口。

两张图片均由本机运行中的 0.13.8 页面直接截图；README 使用仓库相对路径。原始运行截图位于 Git 忽略的 `output/readme-preview/`，没有提交用户论文、数据库、模型、密钥、日志或临时文件。

## 3. 真实 API 问答证据

- 论文：《Attention Is All You Need》；范围仅包含该文档。
- 问题：“《Attention Is All You Need》中，Transformer base 与 big 模型分别训练了多少步、使用多少块 GPU、训练耗时多久？”
- 模型：`deepseek-flash`；联网关闭。
- 执行：中文问题英译 → 混合检索 60 条 → Cross-Encoder 精排 10 条 → 充分性判分 → 生成。
- 计费调用：2 次；总 Token：4,109（prompt 3,793 / completion 316）；端到端耗时：48.25 秒。
- 回答：base 为 100,000 步、约 12 小时；big 为 300,000 步、3.5 天；均使用一台机器上的 8 块 NVIDIA P100 GPU。
- 引用：3 条，分别来自 PDF 第 7、1、8 页；资料覆盖 1/1，`insufficient_evidence=false`，`used_web=false`。

## 4. 验收口径

- 图片内容与当前页面一致，尺寸为 1440×900，README 相对路径存在。
- 问答不是替身模型、自建答案或离线回放；调用结果由本机生产 API 持久化到明确命名的会话。
- 截图不显示 API Key、`.env`、本地路径、用户身份或未授权数据。
- 本切片只改文档和展示资产，应用版本保持 0.13.8。

## 5. 检查结果

- README 仓库内相对链接：全部存在。
- 图片尺寸：`paper-library.jpg` 与 `agent-qa-real-api.jpg` 均为 1440×900。
- `ruff check`：通过；`ruff format --check`：153 个文件已格式化。
- `mypy`：98 个项目配置范围内的源文件通过。
- `compileall`、`uv pip check`、`uv lock --check`：通过。
- pytest：288 passed / 4 skipped；跳过项均为需显式开启的真实 Docling / BGE / TableFormer 模型测试。
- Windows 环境的 `uv run mypy` / `uv run pytest` 入口出现 `uv trampoline failed to canonicalize script path`，已使用同一 `.venv` 的 `python -m mypy` / `python -m pytest` 完成对应检查；不是类型或测试失败。
