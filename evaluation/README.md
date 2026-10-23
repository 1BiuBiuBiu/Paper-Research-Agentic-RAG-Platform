# 人工金标准评估（S3.7）

本入口取代旧上游 RAGAs 示例，不依赖自动裁判，不联网、不调用模型。
当前实现的是**已采集结果 + 人工标注的离线评分**，不会自动运行 PDF 解析、检索或回答。
待标注数据与系统输出齐备后再执行。测试全部为合成单元数据，不代表真实论文质量。

## 放置与运行

将标注 JSON 放到 `evaluation/datasets/benchmark.json`（该目录内用户文件被 Git 忽略）。
在项目根目录运行：

```powershell
uv run python -m evaluation.evaluate --dataset evaluation/datasets/benchmark.json --output output/evaluation/report.json
```

数据合同见 `dataset.schema.json`；模板见 `template.json`。模板是空的，直接运行会报错。
报告保留数据 SHA256、run_id、provenance、逐条原始判分及指标分母；所有分数范围 0..1。
未提供的评估组、零分母返回 null；不会生成简历中的虚构成绩。
provenance 至少记录：每篇 document_id 对应原 PDF SHA256 / 解析修订 / 索引来源指纹、
模型及 revision、系统 Git commit、实际文档范围快照、采集日期。不同版本应分开 run_id。

## headings 数组

一行一个候选，使用 PDF 从 1 开始的物理页。字段：
`document_id, text, page, is_real_section_heading, level, heading_type, predicted_heading, predicted_level`。
真实章节 level 为 1..4；非章节为 null；系统未预测则 predicted_level=null。
保留论文题名、目录条目、图表标题等负样本。候选集必须包含**所有人工正例与所有系统预测**，
包括未匹配的误报和漏检，完成核对后才能设置 heading_candidates_complete=true。
文本匹配及同页重复标题对齐由人工完成，不能只标系统成功提取的行。
Heading F1 使用汇总 TP/FP/FN 的 micro F1。Level Accuracy 仅在 TP 上计算，错层仍为标题 TP。
不计算页段、父子包含、章节归属或完整章节树。

## retrieval 数组

每个问题字段：`question_id, question, evidence_complete, gold_evidence, retrieved_evidence, ideal_relevances`。
采集**原始问题第一轮 Cross-Encoder 精排后、LLM 过滤前**的 Top 5；保持原顺序，不混入改写轮或网页结果。
每轮最多 20 候选；本评分器不改变现有检索链路。

注：当前实现每轮精排 10 条（`RESEARCH_RERANK_TOP_N`），两轮并集进账本；评分器仍按本协议的 @5 计分。需要 @10 的正式字段时，要先把 `Retrieved.rank`、`ideal_relevances` 的 k 参数化（评分器、schema、测试一起改），见 [S3.10 六项指标](../references/S3.10六项指标.md) 里“协议 @5 与实现 @10”的对照。

gold_evidence 每项包含 `evidence_id, document_id, page, element_type, table_label（可省略）,
evidence_text, relevance`。relevance=2 表示直接答案证据，1 表示相关不足；evidence_id 由标注者稳定分配。
表格行、图表趋势、不同方法比较等按实际所需证据单元标注，不按块数量重复扩充金标准。

retrieved_evidence 每项包含 `rank, source_id, relevance, matched_gold_ids`。
rank 必须连续从 1 开始，最多 5；source_id 不重复；匹配金标准按内容人工确认，
不依赖字符串相等或系统引用 ID，相同证据被多个块命中只计一次。
`matched_gold_ids` 为该块实际覆盖的金标准 ID，未命中填 []；相关性必须人工填 0/1/2。

evidence_complete=true 才进入 Recall@5（命中直接证据数 / 全部直接证据数）；
false 只进入 Hit@5（至少命中一条直接证据）。分别报告问题数，不能混合成 Recall。
必须有直接证据才能进入上述分母；无答案题单列数量，并进入最终回答评估。

ideal_relevances 是完整人工相关性池的评分列表，至少包含所有金标准及本轮结果的相关证据；
同一证据单元去重。nDCG 使用 gain=2^relevance-1、log2(rank+1)，IDCG 从该池降序取 5。
不能只把检索命中的结果当作理想池，否则遗漏金标准会虚高。
IDCG=0 的题从 nDCG 均值中排除并单列计数，其他题宏平均。

## answers 数组

每项字段：`question_id, question, answer, answerable, answer_correct, required_components_complete,
citations_support_answer, scope_leakage, correct_abstention, citations`。
每条 citations 包含 `citation_id, supports_answer`，必须覆盖实际回答中的全部引用实例，
不能只标白名单内引用。ID 存在不等于支持结论；逐条人工审查原始证据。

事实、数值、单位、数据集、模型、比较条件的正确性共同体现在 answer_correct；
必要信息完整、引用支持、无越界也必须满足。有答案题还必须有引用且每条均支持。
无答案题还要求 correct_abstention=true；有答案题不要求该字段为 true。
grounded_answer_success 由代码推导，不接受手写成功标签覆盖。
Citation Precision 汇总全部引用（micro），无引用返回 null；Scope Leakage Rate 按问题数统计。
人工判分必须检查实际范围和指纹；评分器无法仅凭布尔标签证明真实来源有效。

建议问题覆盖实验数值、方法比较、消融、超参数、表格、图趋势、无答案及相似论文干扰。
查询改写、联网、调用次数和耗时可留在单独运行记录，不作为本次六项主指标。

## 多文件 pilot 真实采集

`structure_gold.jsonl / rag_questions.jsonl / dataset_summary.json` 与两篇 PDF 位于 datasets。
`uv run python -m evaluation.collect_pilot` 核验 PDF 指纹，使用实际解析、索引和精排模块；
`uv run python -m evaluation.pilot_headings` 按同文档同页一对一匹配，阈值冻结为 0.90。
真实数据本次所有匹配均为规范化后完全相同，未使用模糊匹配提高结果。
标题主口径按提供的协议逐篇 Macro F1，区别于通用评分器的 micro F1；两者均留存。
`uv run python -m evaluation.collect_answers` **仅在允许发送论文片段和计费 API 调用后运行**，
本轮已由用户显式授权；只用 DeepSeek Flash，联网强制关闭，最多 8 题，每题沿用生产流程的调用边界。
该脚本保存每题结果，重跑跳过已有记录（包括失败），不自动重复计费。
结果固定保存在 `output/evaluation/pilot-2026-10-23/`，不混入数据库和论文库。
最终检索结果取最后一轮 LLM 过滤后序列，另保留第一轮过滤前 Top5，不能混用。
数据协议中金标准穷尽与人工二审尚未完成；本轮模型辅助审校只作试评估，不能称为人工终审。

## 回答审校口径（2026-10-23 勘误）

按问题实际要求判定语义完整性，接受等价表达。required_answer_components需区分必需事实与参考答案的补充解释；不能逐字匹配。
同一报告尺度下省略百分号、不额外解释量纲、未给题外基线比较，不单独判错；数值尺度、单位或条件实际错误仍失败。
本轮四题判分修正为成功，原输出不变；judgments和summary旧版在output/review_history中保留。后续独立测试前冻结此口径。

## 仅回放章节规则

`uv run python -m evaluation.replay_headings --source output/evaluation/pilot-2026-10-23 --output output/evaluation/heading-rules-0.11.2`

读取保留的真实 result.json 和原金标准，核对原 PDF SHA256，输出到必须不存在的新目录；manifest 记录输入指纹。此入口只重算标题，不运行模型、不改论文库、原标签及旧报告。原协议待裁决项继续单列排除。新一期回放更换 output 路径。
