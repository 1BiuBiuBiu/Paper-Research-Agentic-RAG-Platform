# Research Agentic RAG Platform

当前应用 **0.13.5**：解析成功后**自动在后台建立索引**（页面显示“索引中”），用户不再手动建索引；稀疏统计（分词、tf、df、倒排）随索引落盘，查询期只对问题分词并按本次选中集合合并 df 算 IDF。检索改为**一个库**：稠密与 BM25 各取全局 Top 60，RRF 后取 60 条精排 10 条（原 12，按覆盖/字节测量下调），不再有每篇深度或每篇保底。索引在进程内缓存（300 MB / 8 组 / 空闲 30 分钟），切范围释放。交给模型前，被选中的表格行/图注会与**引用它的那段原文整段合并**成一条证据（不做字节截断；段落单独被选中时不附加 caption）——不重建索引、不额外调用模型。上下文预算 50,000 字节，并在生成前按非证据部分反推，保证请求不超过服务商 65,536 字节上限。判分改为“本轮文档能否完整回答本轮问题”（`sufficient/covered/missing/Q1`），**精排召回的全部候选都进账本、都交给模型判断**，判分只决定够不够与缺口；不足时**定向补检 1 次**，两轮证据账本累积后再生成。**模型调用上限 3 次**（判分① + 判分② + 生成），中文问题的 1 次英译仅用于精排、不计入上限。最近一次 25 题真实复测：有依据回答 **21/25（84.0%）**、可回答题 18/22、应拒答 3/3，金标准进入交付证据 22/22；正式六项指标为 Heading F1 **91.77%**、Level Accuracy **100%**、Hit@5 **86.36%（19/22）**、nDCG@5 **0.7357**、Grounded Answer Success **84.0%**、Citation Precision **100%（57/57）**。见 [S3.10 六项指标](references/S3.10六项指标.md)。

新增25题问答已实测：Hit@5 **77.27%（17/22）**、有依据回答成功率 **56.00%（14/25）**、已交付引用准确率 **96.97%（32/33）**，越界0/25；含1题引用校验阻断失败。61次模型调用、46,972 tokens，关闭联网。原8题未重跑，历史合并回答21/33。Recall与正式nDCG仍待完善标注；见 [S3.7.4详细结果](references/S3.7.4问答扩充评估.md)。

最新新增五篇章节实测：Macro F1 **90.51%**；含旧样本共七篇为 **93.22%**，存在19个误报、16个漏检，详见 [S3.7.3扩充评估](references/S3.7.3章节扩充评估.md)。评估时应用为 0.11.2，未为此次测试修改识别策略。

S3.7.2（0.11.2）：字母附录层级与出版元信息章节排除已修正；两篇已有真实解析回放的标题 F1、层级准确率均为100%，仅限该试评估样本，见 [验收与限制](references/S3.7.2验收记录.md)。以下表格保留0.11.1原始模型运行结果。

## 实测评估：两篇论文、八个问题（2026-10-23，pilot）

使用 `evaluation/datasets/` 中两篇 PDF（29 页）、86 条标题候选和 8 题（7 可回答、1 无答案）实际运行。
PDF SHA256 与数据清单一致；原 PDF、原金标准和现有论文库未改动。被测逻辑基线为 `d4229e7`，
本轮只增加采集与报告脚本，复用 Docling、BGE-M3、FAISS/BM25、RRF、Cross-Encoder 和 CorrectiveAgent。
用户已显式允许本轮 API 调用；使用 `deepseek-flash`，联网强制关闭。

**这是试评估，数据及本轮 Codex 辅助审校尚未经过独立人工终审，不能直接作为正式简历成绩。**

| 能力 | 指标 | 本轮结果 | 口径 |
| --- | --- | --- | --- |
| PDF 章节识别 | Macro Heading F1 | **96.81%** | 两篇分别 93.62%、100.00%，逐篇平均 |
| PDF 标题层级 | Level Accuracy | **88.14%（52/59）** | 仅已匹配真标题；排除 1 条待裁决目录标题 |
| 最终证据检索 | Hit@5 | **85.71%（6/7）** | 最后一轮 LLM 过滤后，包含有界改写恢复 |
| 最终证据排序 | nDCG@5 参考池试算 | **80.86%** | 已知金证据 + 返回证据审校池，7 道可回答题宏平均；不是预先人工冻结池的正式成绩 |
| 端到端回答 | Grounded Answer Success Rate | **87.50%（7/8）** | 按问题要求的语义完整性审校，接受等价表达，详见下表 |
| 回答引用 | Citation Precision | **100.00%（13/13）** | 引用支持其相邻实际断言；不表示回答所需信息完整 |
| 范围护栏 | Scope Leakage Rate | **0.00%（0/8）** | 本轮所用证据均在各题允许论文范围内；不是对所有未来请求的保证 |

**Recall@5 不发布**：这 7 道可回答题均为 `direct_evidence_exhaustive=false`，不能将 Hit@5 改名为 Recall@5。
无答案题不进入检索均值，只进入最终拒答评价。排序缺席位置按零增益处理。

标题汇总 TP=59、FP=3、FN=0，Micro F1 为 97.52%。3 个误报为出版附属信息；7 个错层均来自附录字母编号小节。
待裁决的第 15 页目录 `Appendix` 不计入主分数；若按原暂定负例计 FP，Macro F1 为 **96.14%**。
本轮所有标题匹配都是规范化后完全相同，未靠模糊匹配增加 TP。匹配按同文档同页一对一进行，未匹配的额外预测也计 FP。

| 问题 ID（去掉 PILOT- 前缀） | 完整回答成功 | 核对结果 |
| --- | --- | --- |
| MER26-001 | 是 | 两个 WAF 和差值正确，百分点表达明确了百分数尺度 |
| MER26-002 | 是 | 三个数值和输入条件正确；题目未要求额外解释量纲 |
| MER26-003 | 是 | 两类信号的通道、采样率及单位完整 |
| MER26-004 | 是 | 一次改写恢复正确论文的 60.27；报告尺度无歧义，省略百分号不单独判错 |
| RL26-001 | 否 | 只有图注和正文，没有图内 66.49 / 68.39，系统说明证据不足 |
| RL26-002 | 是 | 模型、训练数据、测试集及 68.39 正确；省略百分号不单独判错 |
| RL26-003 | 是 | 已回答从 10 个样本增加后的上升趋势，无需附加题目未要求的基线比较 |
| RL26-004 | 是 | 未给 G=32 编造结果，列出的其他设置数值没有冒充目标答案 |

本报告于 2026-10-23 根据用户反馈修正回答审校口径：以问题实际要求的事实和语义为准，接受等价表达。
不因未写 `%` 或未额外解释“无量纲”判错；但数值尺度错误（如把 0.6839 写作 0.6839%）、指标/单位冲突、条件错误仍应判错。
不把参考答案中的额外解释、题目未要求的基线比较当作强制得分项。旧 3/8 判分过严，已撤回；
四题纠正后为 7/8，仅 RL26-001 因未答出图内指定数值失败。原模型输出未变、未重新调用 API；
旧审校和汇总已备份至本机 `output/evaluation/pilot-2026-10-23/review_history/`。
此次是对已观察结果的判分勘误，应在后续独立测试前冻结修订口径，不能当作模型性能提升。

引用支持与完整回答分开审校：正确陈述证据不足、或正确给出部分信息，也可以有有效引用，但仍未成功回答可回答题。
Figure 3 原页含精确读数，当前文本证据链没有将图内数值交给模型；未用人工看到的图替模型补答。
nDCG 每题的最终 relevance 列表依次为 `[2,2] / [2] / [2] / [2] / [1,1] / [2] / [2]`；
参考池等级依次为 `[2,2] / [2,1] / [2] / [2] / [2,1,1] / [2,1] / [2,2]`。
图内数值证据与仅含图注的文本视图分别记录；计算使用 `2^relevance-1` 和 `log2(rank+1)`。
参考池由本轮审校构建，需独立人工确认去重和等级后才能发布正式 nDCG。

运行合计 **18 次模型调用、15,189 tokens**（供应商返回用量），未估算费用。相似论文干扰题发生 1 次改写；
第一轮过滤前 Top5 与最后一轮过滤后结果分开留存，不把二者混算。

本机完整证据保存在 `output/evaluation/pilot-2026-10-23/`：
`run_manifest.json`（输入指纹/版本）、`heading_report.json`（逐标题匹配）、
`retrieval_outputs.json`（第一轮候选/精排）、`agent_outputs.json`（真实回答/最终证据/轨迹）、
`judgments.json`（逐题审校依据）、`summary_report.json`（汇总及证据 SHA256）。
这些文件含论文片段，保持 Git 忽略；仓库只提交方法和结果摘要。

复现采集步骤见 [评估说明](evaluation/README.md)，检查与限制见 [本轮验收](references/S3.7.1验收记录.md)。

科研论文工作台。基于 [Enterprise-Agentic-RAG-Platform](https://github.com/ara-5/Enterprise-Agentic-RAG-Platform) 的固定提交 `f165bf62ca6cf2df84ce0829f470823bdfa08bdd` 进行迁移，保留上游 Git 历史与未迁移模块。

当前应用版本 **0.7.1 / S3.3.1**。左侧主导航为论文库 / Agent 平台。论文库内为文件夹和完整属性列表，标题直接进入 PDF / 解析对照；阅读区采用单屏双栏，右下角可翻页或输入页码；Agent 平台支持持久对话、多篇 / 文件夹范围与可配置模型服务，消息内部滚动，右侧问题导航可快速定位。见 [单屏工作台验收](references/S3.2验收记录.md)、[会话验收](references/S3.0验收记录.md)、[统一论文库验收](references/S2.1.2验收记录.md)、[PDF 方案与限制](references/论文PDF结构解析方案.md)、[版本历史](CHANGELOG.md)。

当前实现 **S0 解析 + 混合检索 + 分类 + 持久会话 / 多篇范围 / 自定义连接**：使用本地 BGE-M3 与 FAISS/BM25，模型 Key 仅在服务端。真实问答需 Key 和有效索引；LangGraph 路由、联网与视觉问答仍待后续，见 [迁移状态](references/功能迁移清单.md)。`parsed` 只表示解析完成，索引另有状态和版本。

## 已可用

- 导入页支持多选或拖入多个 PDF，加入队列后逐篇上传并解析；显示各篇结果、重复记录及失败原因，可继续处理失败项。单批最多 20 篇 / 200 MiB，每篇仍受 50 MiB / 500 页限制；队列属于当前浏览器会话，已入库记录持久保留。清空队列不删除论文。
- 右侧逐页解析在原有公式位置展示已保存的公式区域原图；没有公式区域时保留提取文字。尚未启用公式转 LaTeX 模型，原图预览不代表精确数学识别。
- API 密钥入口：设置 → 服务商 → 选择服务商 → 打开本机密钥输入窗口；也可双击根目录“配置模型密钥.cmd”设置内置 DeepSeek。密码式窗口只在 Windows 本机出现，密钥保存到服务端 `.env`，不经网页或命令行参数。保存后等待当前任务结束，再停止并重新启动平台生效。新服务商先保存连接配置，再选择该服务商配置密钥；启动进程的同名环境变量仍优先于 `.env`。

- 自建分类文件夹、唯一归属与移动；每篇自动创建 data/papers/{document_id}/ 独立子目录，原件 / 解析 / 图表共同管理。全部论文为汇总视图，不额外存副本。
- 单篇文字 PDF 上传与去重、同名不同内容保留、解析状态与失败重试。
- Docling 本地 CPU 版面解析，双栏正文顺序、重复页眉页脚排除、原始块与清洗坐标追溯。
- 标题、作者、DOI、摘要、期刊/会议及年份候选；人工修正与来源标记，SQLite 持久化。
- 图区域渲染（包括检测到的矢量图）、图注与正文引用关联；原始 PDF 与原页/图片预览。
- 显式单篇 / 多篇 / 整文件夹范围，或通用对话；摘要帮助定位，按问题检索原文，引用含 chunk_id、页码、坐标和摘录。会话持久保存，范围变化隔离旧历史。
- DeepSeek Flash/Pro 切换，单次最多一次外部调用；引用越界/缺失、超时、限流、余额与鉴权失败有明确错误，不自动换模型或付费重试。
- 带来源和年份的 JIF 手工录入。没有数据源不猜值；会议/预印本为不适用，变更期刊会清除旧指标。

## 日常打开（Windows，环境已安装）

若重启后页面提示无法连接本机 API，先等待启动窗口显示“平台已启动”，再点击页面的“重新连接本机服务”或刷新。0.7.1 起页面访问回环地址的 API / 图片明确绕过环境及 Windows 系统代理，避免启动器健康正常但页面收到代理的 502；不需要重新输入 Key。密钥“已配置”只代表本机已读取，不代表远程生成已验证。见 [连接修复记录](references/S3.3.1验收记录.md)。

在项目文件夹中双击 **启动平台.cmd**。自动在后台启动本机 API 和页面，均就绪后打开默认浏览器。启动窗口可以关闭；结束使用时双击 **停止平台.cmd**。

- 地址为 http://127.0.0.1:8501/，仅监听本机。重复启动复用本项目健康服务，不启动第二份；其他程序占用端口时显示错误，不关闭该程序。
- 停止入口只关闭此启动器创建且 PID / 创建时间 / 项目解释器均核对一致的进程树。原先手动运行的服务须在其窗口按 Ctrl+C。
- 日志与进程状态在 output/launcher/8000-8501/，不进入 Git。启动失败保留错误窗口，本次新启动的服务回收，原服务保留。
- 不需管理员权限；不自动安装依赖、下载模型或调用外部模型。缺少 .venv 时按下节安装一次，再双击启动。修改本机 .env 后需重启后端。
- 当前提供双击启动入口，尚未制作独立安装包或自带 Python 的 exe。

## 安装与启动（Windows PowerShell）

若启动时报“api 服务提前退出”，先查看 `output/launcher/8000-8501/` 最新的 `api.err.log`。若含 `No Python at ...`，说明 `.venv` 引用的基础 Python 已不存在，常见于移动目录或迁移电脑；不是 API 端口故障。先备份 `.venv/pyvenv.cfg`，再执行以下命令恢复同版本解释器并保留已有包，最后重新双击启动入口：

```powershell
uv python install 3.12.7
uv venv --allow-existing --python 3.12.7 .venv
uv pip check
uv lock --check --offline
```

恢复后如仍有缺包或导入错误，按锁文件执行 `uv sync --locked`，不要改用系统 Python 3.9 / 3.13。运行环境不随项目文件复制自动变得可移植。本次恢复证据见 [2026-10-22 启动环境恢复](references/2026-10-22启动环境恢复.md)。

Python **3.12.7**；推荐 uv **0.13.3**。`uv.lock` 锁定 Windows 依赖，已验证安装的 Torch 为 CPU 构建；Linux/Docker 环境留待部署切片验证。不要再使用上游旧依赖环境启动当前入口。

```powershell
python -m pip install uv==0.13.3
uv sync --locked
# 终端一：单进程本机 API（不要增加 workers）
uv run uvicorn app.api:app --host 127.0.0.1 --port 8000
# 终端二：中文界面
uv run streamlit run app/main.py
```

访问 [工作台](http://127.0.0.1:8501) / [API 文档](http://127.0.0.1:8000/docs)。界面绑定本机，未引入账号系统。不要将此运行配置直接暴露公网。

如本目录已有 `.venv`，本次环境中也可直接使用 `.venv/Scripts/python -m uvicorn ...` 和 `.venv/Scripts/python -m streamlit ...`。

首次解析会从 Hugging Face 下载 Docling 的本地版面模型；解析不发送论文到远程服务。只有提交问答时，服务端才向所选 DeepSeek 模型发送问题、有限摘要及相关原文片段；不默认发送完整 PDF。为适应部分 Windows 网络，解析 worker 默认关闭 Xet 下载通道，使用 HTTPS。断网部署须先下载本地模型；配置 `DOCLING_ARTIFACTS_PATH` 指向官方工具准备的 artifacts 目录。缓存已下载的模型不会重复下载。

```powershell
uv run docling-tools models download --help
# 根据帮助下载需要的 layout artifacts，再设置相应路径
$env:DOCLING_ARTIFACTS_PATH = '你的本地模型目录'
```

API 读取本机 `.env`，进程环境变量优先。前端只访问本机 API，不读取 Key；`.env.example` 中保持空 Key，不要把真实凭据写入示例文件。修改 Key 后重启 API。其他设置也可使用 PowerShell：

```powershell
$env:RESEARCH_DATA_DIR = 'E:/paper-library'
$env:RESEARCH_MAX_UPLOAD_MB = '50'
$env:RESEARCH_MAX_PAGES = '500'
$env:RESEARCH_PARSE_TIMEOUT = '300'
```

原文、版本化页面/图片、SQLite 位于 `data/`（或指定目录），已被 Git 忽略。默认单任务解析，忙时返回可重试状态；CPU 首次载入模型较慢。PDF 解析默认超时 300 秒；本地向量进程默认超时 600 秒；DeepSeek 总调用期限 90 秒。UI 对应等待上限为 360/660/750 秒。首次模型载入较慢，后续性能优化在容量切片完成。

## DeepSeek Flash / Pro 配置

Agent 平台左侧可创建项目、在项目内新建对话，并通过“对话选项”将现有对话移动到项目或未分组。项目只组织对话，不复制论文，也不自动扩大资料范围。空项目可以归档 / 恢复。

对话输入框下方直接选择“具体模型 ID · 服务商”，不用先选 API 服务再选模型。设置 → 模型页签可保存新对话默认模型；服务商页签负责添加 / 修改连接，凭据引用与 JSON 参数在高级配置。已有对话优先上一轮所用模型。

内置服务保持以下配置；其他服务在「设置 → 服务商」中添加 Base URL 和模型 ID，密钥变量名在高级连接配置。当前支持 Chat Completions 兼容协议，外部服务使用 HTTPS，本机地址可 HTTP。保存连接不会自动访问供应商。

例如在本机 .env 中填写 `RESEARCH_MODEL_KEY_LAB`，页面只填写变量名；不要把变量值输入页面或提交 Git。无 Key 的本机接口可取消「需要 API Key」。配置是否存在与远程是否可调用是两回事。

在 Agent 平台新建对话，打开资料范围跨文件夹多选论文或选一个文件夹；解析完成的论文会自动在后台建索引，页面显示“索引中”。每轮最多 100 篇，发送相关证据不等于读完整个范围；可以继续追问，刷新 / 重启后从对话记录恢复。旧范围消息可见，但不会送入新范围的模型上下文。聊天失败可显式重试，不自动产生额外外部调用。

API：GET/POST /connections，PUT /connections/{id}；GET/POST /conversations，GET/PATCH /conversations/{id}，POST /conversations/{id}/turns。每轮传 request_id、expected_revision、question、scope、connection_id、model；scope 示例为 `{"kind":"documents","document_ids":["文档 UUID"]}`，文件夹为 `{"kind":"folder","folder_id":"分类 UUID"}`，通用为 `{"kind":"chat"}`。旧 /query 仍是原有单篇 DeepSeek 契约。

本机 `.env`（已忽略，切勿提交）：

```dotenv
DEEPSEEK_API_KEY=在本机填写，不要发到聊天
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT=90
DEEPSEEK_MAX_OUTPUT_TOKENS=2048
RESEARCH_CONTEXT_BYTES=50000
RESEARCH_CANDIDATES=60
RESEARCH_RERANK_TOP_N=10
RESEARCH_AGENT_MAX_CALLS=3
RESEARCH_INDEX_CACHE_MB=300
RESEARCH_AUTO_INDEX=1
```

在官方接口中，Flash 使用 `deepseek-flash`，Pro 使用 `deepseek-v4-pro`；[官方调用文档](https://api-docs.deepseek.com/zh-cn/)与[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)。当前默认关闭思考模式、非流式 JSON 回答，前端显示服务端实际返回的模型名。供应商的别名可能随其发布更新，应用不将 Flash/Pro 偷换成另一模型。没有配置 Key 时仍能解析和建立本地索引，问答按钮禁用。

先下载固定的多语言 BGE-M3 权重（约 2.3 GB，无论文外发）：

```powershell
uv run python -m scripts.download_embedding
# 若常规大文件传输失败，使用系统 curl 续传并核对官方 SHA256
uv run python -m scripts.download_embedding --direct-https
```

模型固定为 BAAI/bge-m3、revision `5617a9f61b028005a4858fdac845db406aefb181`；实际向量计算离线运行，不在请求期间下载模型。索引各自属于一篇文档，以稠密文件与 `bm25.json` 的校验和、文本/模型指纹共同验证完整性，构建失败不会激活半成品或覆盖其他论文；索引失败与解析失败分开记录，可单独重试。

Agent 操作：成功解析论文（自动排队建索引）→ 新建项目或未分组对话 → 选择论文 / 文件夹 → 在输入框下选择模型 → 提问 / 继续追问。旧 /query 仍提供单篇无状态兼容接口。未选、无效、未解析、索引未就绪的范围明确拒绝，不隐式查全库；证据不足明确返回不足与缺口，不再用改写循环反复检索。

请求示例（ID 使用实际论文 UUID）：

```json
{"question":"本文的主要实验证据是什么？","scope":{"type":"documents","ids":["替换为论文UUID"]},"model":"flash"}
```

来源标记为 `[E1]` 等，服务器只接受本次实际提供的证据 ID。上下文默认最多 24,000 UTF-8 字节，整次模型请求最多 65,536 字节；这是明确的字节边界，不能当作精确 token 估算。实际 token 用量仅使用供应商返回值，没有值则显示不可用；不编造费用。来源/格式校验不能替代对回答事实的人工核对。

## 使用与 API

在论文库左侧新建 / 重命名课题文件夹，右侧列出所选文件夹文献；在整理操作中选目标并移动单篇或多篇。新论文默认未分类，移回未分类不删除文件。空文件夹可归档 / 恢复，含论文时须先移动。分类 / 搜索先于内部加载，移动不触发重解析或重建索引。每篇一个分类，尚无嵌套 / 多标签；Agent 的项目独立组织对话。

- GET/POST /categories，PATCH /categories/{id}，POST /categories/{id}/archive 或 restore。
- POST /documents/move：document_ids（1–100 UUID）与 category_id；显式 null 表示未分类。全批验证后事务移动，失败不部分生效。
- GET /documents 可带 category_id / uncategorized / q / include_samples；SQLite 自动增量迁移到 schema v3，旧文档 JSON 不重写。

上传 PDF 后，点击列表中的论文单元格，查看「原文与解析 / 论文信息 / 研究图表 / 期刊指标」。当前文件名与记录始终显示；原页与解析正文并列，可切换页码。演示样本默认隐藏，可手动显示。研究图表默认仅展示匹配编号图注的区域，出版标志 / 小图标与其他图像保留在独立组。提取字段为候选，请核对。扫描件、损坏或加密 PDF 会明确拒绝；不把扫描页空结果当成功。

已有论文可使用「更新提取规则」，无需重新上传。服务先核对原 PDF SHA256，保存完整旧记录至 data/papers/{id}/history，再从保留块更新标题、元数据和图片分组；保留人工确认字段，不改原 PDF。来源修订后旧索引会判定过期，启动或下次解析完成时自动重建，也可手动重试。每次验证通过的切片同步三份根文档与 CHANGELOG，并独立本地 Git 提交；不将用户数据、备份、Key 提交或擅自推送。

- `POST /documents` 或兼容入口 `POST /ingest`：multipart 字段 `file`；返回 document、duplicate。解析失败记录的 status 为 failed，支持后续重试。
- `GET /documents?offset=0&limit=50`：分页列表；`GET /documents/{id}`：详情。
- `POST /documents/{id}/retry`：仅重试 failed 文档。
- `POST /documents/{id}/refresh-extraction`：已解析文档按新规则更新字段和图像分组；原文校验、先备份、保留人工字段，失败不激活。
- `POST /documents/{id}/reparse`：重新运行原 PDF 的版面 / 表格识别，成功前保留有效旧记录，人工字段保留，生成 history 快照。
- `GET /documents/{id}/exports/docling_json` 或 `markdown`：下载所属记录的结构 JSON 或阅读版 Markdown；导出不含重复嵌入图片，原图独立保存。
- `PATCH /documents/{id}/metadata`：只更新传入字段，保存人工来源。
- `PUT /documents/{id}/metric`：year、value、source，要求已确认期刊名称/类型。
- `GET /documents/{id}/original`、`GET /documents/{id}/assets/{asset}`：仅访问所属记录的原文/白名单资产。
- `GET /health`：健康与配置状态；`rag_ready` 只在当前进程成功完成外部模型请求后变为 true，不代替每篇索引检查。
- `GET /models`：Flash/Pro 白名单、配置状态和最近是否有成功请求；不返回 Key。
- `GET/POST /documents/{id}/index`：查看索引状态（`building/ready/failed/stale/missing`，含索引错误码）或**同步**重建所选论文索引；解析成功会自动排队建立，POST 保留为手动建立／重试入口。
- `GET /agent/status`：精排就绪、联网配置、调用上限（3 次）与进程内索引缓存统计。
- `POST /query`：单篇范围和模型选择；返回回答、原文引用、实际模型、真实 usage（缺失为 null）、耗时、上下文字节数。

应用使用生成的 UUID 路径落盘；浏览器传来的 Windows 路径可能先由 multipart 库规范化为文件名，这不影响服务器存储路径。跨站写请求和不可信 Host 被拒绝；分块请求同样受总大小限制。Key 只在本机服务端 .env 或环境变量中配置；页面不提供密钥录入或回显。校验失败不回显请求原始内容，外部模型错误不透传服务端响应正文。

## 验证

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
uv run python -m compileall -q app
uv pip check
# 真正运行本地 Docling 模型的测试（需模型下载完成）
$env:RUN_DOCLING_TESTS = '1'
uv run pytest -q -m docling
# 真正运行固定 BGE-M3，验证中英文和跨语言召回
$env:RUN_EMBEDDING_TESTS = '1'
uv run pytest -q -m embedding
```

快速测试使用解析/向量/模型替身验证接口、存储和范围；DeepSeek 适配器使用 httpx.MockTransport，既不联网也不计费。`docling` 标记的测试才验证真实 PDF 双栏顺序、页眉过滤且保留脚注、标题/作者/DOI、矢量/位图、图注/正文关联及重启一致性。夹具由 `scripts/make_fixture.py` 自建，无用户论文内容。生成命令：`uv run python -m scripts.make_fixture`。

已保留的旧 `app/agent.py`、`app/rag_qa.py`、`app/ui.py`、`ingestion/`、`embeddings/`、`vectorstore/`、`evaluation/` 不属于当前启动路径，S1 检索已在 app/retrieval 中适配，其余依赖和行为将在各自切片迁移；不要直接运行旧脚本。CI 已调整为 Windows 上的快速质量检查（未在 GitHub 运行）；旧 Docker 暂未适配当前版本，尚不能视为可用构建，后续切片有明确验收。

## 数据恢复与限制

停止 API 后备份整个 `data/`，包括 SQLite 与所有 papers 资产；恢复时一起还原到同一路径。不要只复制 SQLite 或其中某张图片。启动会把中断的解析任务标为 failed，可以重试；失败版本不会替换其他论文的有效文件。当前不提供批量删库命令。

当前不承诺任意版面均正确，不做整篇 OCR、公式/表格数值重建、模型识图或外部书目/JIF 自动获取。元数据候选识别仍可能遗漏复杂作者/期刊样式；未匹配字段可人工修正。真实用户样本、千篇容量及性能尚需后续专项验证。

准备文档：[PRD](PRD.md)、[架构](架构.md)、[项目状态](项目状态.md)、[解析方案](references/科研解析与元数据方案.md)、[DeepSeek 配置](references/DeepSeek接入说明.md)、[S1 验收](references/S1验收记录.md)。

### 0.8.0 纠错检索与联网补充

论文问答现经一个库的混合检索（稠密全局 Top 60 + BM25 全局 Top 60 → RRF 60）、Cross-Encoder 精排 10 条、判分与生成分离；第 1 轮判分不足时用 Q1 定向补检一次，第 2 轮只判新文档，最后用两轮证据账本并集生成，仍缺则明列缺口。通用对话直接生成。提问过程中页面按 0.4 秒轮询显示实时步骤（范围、英译、召回、精排、判分结论、定向补检、联网、生成），结束后仍可展开执行过程与网页来源；**最多 3 次计入上限的模型调用**（判分① + 判分② + 生成），中文问题另有 1 次英译只用于精排且不计入上限，英文问题不产生该调用。

本机精排模型需要单独下载一次：`uv run python -m scripts.download_reranker`。固定模型只在本机 CPU 离线推理，缺失时明确提示。0.12.0 起中文问题先由 DeepSeek 译成英文检索式再送精排（译文只用于打分，不改变检索问句），译文缺失原问句数字时回退中文；重放见 [S3.8 验收](references/S3.8验收记录.md)，跨领域与中文论文语料仍待真实数据评估。

可选搜索：设置 → 服务商 → 配置 API 密钥，选择 Tavily 联网搜索，打开本机窗口输入后重启平台。变量名为 `TAVILY_API_KEY`；未配置时仍可基于本地证据回答，不足时说明缺口。联网只发送检索问题，不发送论文正文；网页来源为搜索摘要，不是全文。旧 `/query` 默认 `allow_web=false`，Agent 资料范围中可逐轮切换。

已执行本机精排和合成控制流测试，未执行付费 LLM/Tavily 真实请求；验收记录见 [S3.4](references/S3.4验收记录.md)。

### 0.9.0 资料范围选择

Agent 输入区点击“资料范围”，按论文库方式在左侧浏览文件夹、右侧勾选文献。每页 8 篇，上方可选本页、整个文件夹或所有文件夹。翻页和切换文件夹保留选中；单篇可跨文件夹叠加，后台按 ID 去重。整组选中后仍可取消其中某篇，作为本轮排除项；也可清空全部选择。点击“完成选择”返回对话，当前范围显示有效文献数量。单轮仍最多 100 篇，超限会提示，不自动截断。

0.9.1 起，未设置资料范围的新对话默认全部论文，输入区显示数量；已有手动范围继续沿用，显式清空不会自动恢复全库。全库仍默认排除演示样本、检查索引并保留单轮 100 篇边界。

### 0.10.0 本地版本管理与连接测试

本项目已初始化 Git `main`，0.9.1 基线提交 `9cb0525` / 标签 `v0.9.1`，后续切片独立提交；运行 `git log --oneline --decorate` 查看版本。没有推送远程。`.env`、用户论文、运行数据与模型不纳入 Git，回退代码不会自动回退这些运行数据。

设置 → 服务商 → 测试已有服务，可选 DeepSeek、兼容 API 或 Tavily。自定义表单可“测试当前配置”或“测试并保存服务商”；每个所填模型一次短请求，全部通过才保存，失败保留原配置。测试可能按供应商计费，只有明确点击才发送固定公开文本，不发送论文。缺凭据时可先“保存为停用（不测试）”，在本机窗口配置密钥、重启，再勾选启用并测试保存。测试通过只证明当次可用，配置/密钥变更后重新测试。

## S3.7 人工金标准评估 · 0.11.0 · 2026-10-23

已实现三组离线评分：标题 F1 / TP 层级准确率、Recall@5（不完整金标准改用 Hit@5）/ nDCG@5、有依据回答成功率 / 引用准确率；范围越界率为护栏。数据放在 `evaluation/datasets/benchmark.json`，模板、数据合同和命令见 [评估说明](evaluation/README.md)。当前评分输入为已采集系统结果及人工标注，未自动运行模型或采集检索轨迹；不以合成测试作为真实论文成绩。见 [验收](references/S3.7验收记录.md)。
