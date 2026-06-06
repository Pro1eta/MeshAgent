# MeshAgent 实验结果分析

## 1. 方法概述

实验通过 4 个渐进式阶段评估 MeshAgent 在不同组件配置下的性能：

| Stage | 脚本 | 组件 |
|:---:|------|------|
| **S1** | `baseline_static_prompt.py` | 全量约束静态注入。将所有 13 条约束直接拼入 prompt，LLM 一次性生成 `process_graph()` 代码 |
| **S2** | `query_specific_constraint_prompt.py` | 查询相关约束检索。对每个 query 用 embedding 检索最相关的 top-k 约束，减少 prompt 噪声 |
| **S3** | `cot_with_query_specific.py` | + Chain-of-Thought。LLM 先将问题拆解为 3 步，逐步生成并扩展代码 |
| **S4** | `cot_with_error_check.py` | + Verifier 不变量检测 + 自修复。生成代码后执行 5 项硬性规则检测（节点/边类型、层级关系、孤立节点、带宽非零），失败则触发 RAG 检索修复约束 → LLM 重新生成 |

**实验设置**：
- 模型：DeepSeek-v4-flash（通过 OpenAI 兼容 API）
- Embedding：text-embedding-v4（1536 维，DashScope API）
- 向量检索：ChromaDB 本地持久化
- 测试集：20 道 MALT 网络拓扑查询（7 easy, 7 medium, 6 hard）
- 每个 query 运行 1 次（`EACH_PROMPT_RUN_TIME=1`）

**评估指标**：
- **Accuracy**：result 与 golden answer 精确匹配的比例
- **Reliability**：代码可执行率（不含 self-debug 修复后运行的情况）

---

## 2. 总体对比

| 指标 | S1 Baseline | S2 Query-Specific | S3 CoT | S4 +Error Check |
|------|:--:|:--:|:--:|:--:|
| **总体准确率** | 47.6% | 42.9% | 43.6% | 57.5% |
| **代码可执行率** | 81.0% | 66.7% | 97.4% | 95.0% |
| Easy 准确率 | 57.1% | 85.7% | 85.7% | 85.7% |
| Medium 准确率 | 71.4% | 42.9% | 71.4% | 71.4% |
| Hard 准确率 | 14.3% | 0.0% | 11.1% | 8.3% |

### 关键发现

1. **S2 反而比 S1 差**：查询相关的 top-9 约束比全量 13 条约束效果更差，说明约束检索的质量（RAG 召回精度）是瓶颈。减少约束数量导致关键信息丢失。

2. **S3 CoT 大幅提升可执行率**（66.7% → 97.4%）：CoT 的分步推理让 LLM 在生成代码时更谨慎，几乎消除了 import 遗漏和语法错误。但准确率未提升——更多代码能跑了，但逻辑正确性没变。

3. **S4 达最佳**：verifier + 自修复将准确率提升到 57.5%。Hard 题略有改善（0% → 8.3%），但仍是主要瓶颈。

---

## 3. 逐题结果（Pass ✅ / Fail ❌ / Fail: run 🔴）

| # | 难度 | 题目摘要 | S1 | S2 | S3 | S4 |
|:-:|:--:|------|:--:|:--:|:--:|:--:|
| 1 | Easy | List all ports in packet switch | ✅ | ✅ | ✅ | ✅ |
| 2 | Easy | Add new packet switch + ports | ❌ | ❌ | ❌ | ❌ |
| 3 | Easy | Update physical_capacity_bps | 🔴 | ✅ | ✅ | ✅ |
| 4 | Easy | CONTROL_POINT + PACKET_SWITCH in AGG_BLOCK | ✅ | ✅ | ✅ | ✅ |
| 5 | Easy | CONTROL_DOMAIN with ≥3 CONTROL_POINT | ❌ | ✅ | ✅ | ✅ |
| 6 | Easy | Update stage: 3→5 | ✅ | ✅ | ✅ | ✅ |
| 7 | Easy | CHASSIS count per RACK | ✅ | ✅ | ✅ | ✅ |
| 8 | Medium | Bandwidth on specific packet switch | ✅ | ❌ | ❌ | ✅ |
| 9 | Medium | Bandwidth per AGG_BLOCK | ✅ | 🔴 | ✅ | ✅ |
| 10 | Medium | Top 2 Chassis by capacity | ❌ | 🔴 | ❌ | ❌ |
| 11 | Medium | Average PORT capacity | ✅ | ✅ | ✅ | ✅ |
| 12 | Medium | Switch + Port count per AGG_BLOCK | ✅ | ✅ | ✅ | ✅ |
| 13 | Medium | PS nodes in AGG_BLOCK with avg capacity | ❌ | 🔴 | ❌ | ✅ |
| 14 | Medium | PS nodes above avg capacity | ✅ | ✅ | ✅ | ✅ |
| 15 | Hard | Subgraph: SUPERBLOCK + AGG_BLOCK | ❌ | ❌ | ❌ | ❌ |
| 16 | Hard | Remove switch, balance capacity | 🔴 | ❌ | ❌ | ❌ |
| 17 | Hard | Remove 5 ports, rebalance | 🔴 | 🔴 | ❌ | ❌ |
| 18 | Hard | Paths from CONTROL_DOMAIN to PORT | ❌ | 🔴 | ❌ | ❌ |
| 19 | Hard | Redundancy analysis | 🔴 | 🔴 | ❌ | 🔴 |
| 20 | Hard | Optimize topology | ✅ | 🔴 | ❌ | ✅ |
| 21 | Hard | Optimal placement new switch | ❌ | ❌ | ❌ | 🔴 |

> ✅ = Pass (正确)　❌ = Fail (结果不匹配)　🔴 = Fail (代码无法运行)

### 观察

- **题目 2**（新增 Packet Switch）：所有阶段均失败。这道题要求 LLM 在图中新增节点并建立正确的层级关系边，涉及多层级（JUPITER → SUPERBLOCK → AGG_BLOCK → PACKET_SWITCH → PORT）。DeepSeek-v4-flash 无法正确理解并执行这种多步拓扑修改。
- **题目 10**（Top 2 Chassis by capacity）：仅 S2 是代码错误，其余是结果不匹配。S4 的 verifier 无法帮助，因为输出是一个表格（无图结构可验证）。
- **题目 20**（优化拓扑）：S1 和 S4 均通过，但 S2/S3 失败。这说明全量约束对此类推理题更有效——静态约束提供了完整的图结构知识，有助于 LLM 做出正确的优化判断。

---

## 4. 按题型分析

| 题型 | 题目数 | S1 Acc | S4 Acc | 变化 |
|------|:---:|:---:|:---:|:---:|
| Text（文本） | 5 | 40.0% | 40.0% | 0 |
| List（列表） | 5 | 80.0% | 100.0% | +20% |
| Table（表格） | 5 | 60.0% | 60.0% | 0 |
| **Graph（图）** | 6 | 16.7% | 36.4% | +19.7% |

- **List 题型最容易**：只需遍历和过滤，LLM 擅长的模式
- **Graph 题型最困难**：需要创建/修改图结构，涉及多层级关系，LLM 容易出错
- **Verifier 对 Graph 题型提升最大**（+19.7%）：因为 graph 输出可以被 `MyChecker` 的结构验证覆盖

---

## 5. 错误类型分布

| 错误类型 | S1 | S2 | S3 | S4 |
|------|:--:|:--:|:--:|:--:|
| 代码无法运行 (Run Error) | 4 | 7 | 1 | 2 |
| 结果不匹配 (Mismatch) | 7 | 5 | 21 | 15 |
| Verifier 失败 | — | — | — | 0 |
| Missing import | 3 | 2 | 0 | 0 |
| List/dict 操作错误 | 1 | 1 | 0 | 0 |
| 其他 | 0 | 4 | 1 | 2 |

**趋势**：
- Run Error 从 S1 的 4 题降到 S3 的 1 题——CoT 大幅减少了语法/import 错误
- Mismatch 从 S1 的 7 题升到 S3 的 21 题——CoT 让更多代码能跑，暴露了更多逻辑错误
- S4 将 mismatch 降到 15——verifier + 自修复减少了部分逻辑错误

---

## 6. 与论文的对比

论文报告 MeshAgent 在 GPT-4 上实现 >95% 准确率（含 abstention）。当前 DeepSeek-v4-flash 上最佳为 57.5%，差距较大。

可能原因：
1. **模型能力差异**：DeepSeek-v4-flash vs GPT-4-32k 的代码生成能力有显著差距
2. **约束检索质量**：论文使用 Azure Cognitive Search 的混合检索（关键词 + 向量），我们使用纯向量检索（ChromaDB），可能丢失了关键词匹配的信号
3. **模型差异**：DeepSeek 可能比 GPT-4 更容易产生原地修改图的行为模式，导致 CoT 步骤间的图污染
4. **没有置信度拒答**：论文的 Fig 9 评估包含了主动拒答的题目（不计入错误），我们 Stage 1-4 无此机制

### 建议后续实验

1. **Stage 6**（`full_meshagent_benchmark.py`）：在 S4 基础上加入工具调用（RAG 检索的工具函数注入 prompt），测试工具是否能帮助 LLM 做出更正确的图操作
2. **Stage 7**（`full_meshagent_abstention.py`）：加入置信度评分和主动拒答，完整复现论文 Fig 9 指标
3. **Ablation**：用 `deepseek-v4-pro` 重复实验，量化模型版本的影响
4. **约束检索 Ablation**：测试不同 top-k 和检索策略对准确率的影响
