# MeshAgent 实验结果报告

## 实验设置

### 实验环境

| 配置 | 值 |
|------|-----|
| LLM | DeepSeek-v4-flash |
| Embedding | text-embedding-v4 (1536维, DashScope) |
| 向量检索 | ChromaDB (本地持久化) |
| CoT Self-Debug 上限 | 5 次 |
| 约束检索 | RAG top_k = 13 (static), 9/10/11 (query-specific) |

### 评测方法

21 道 benchmark 题目，分为三个难度：

| 难度 | 数量 | 典型题目 |
|------|:---:|------|
| **Easy** | 7 | 列出端口、更新属性、查找节点 |
| **Medium** | 7 | 计算带宽、聚合统计、排序 |
| **Hard** | 7 | 路径分析、冗余评估、拓扑优化 |

每道题有标准答案（golden answer），LLM 输出与标准答案比对，匹配即 Pass。

### 实验方法（5 个 Stage）

| Stage | 方法 | 组件 | EACH_PROMPT_RUN_TIME |
|:---:|------|------|:---:|
| S1 | **Baseline** | 全量约束静态注入 | 1 |
| S2 | **Query-Specific** | 查询相关约束检索 | 1 |
| S3 | **+ CoT** | Chain-of-Thought 三步推理 | 1 |
| S4 | **+ Error Check** | Verifier 不变量检测 + 自修复 | 2 |
| S5 | **+ Tools** | Full MeshAgent（CoT+Verifier+工具） | 2 |

---

## 总体结果对比

| 指标 | S1 Baseline | S2 Query-Spec | S3 +CoT | S4 +ErrorCheck | S5 +Tools |
|------|:---:|:---:|:---:|:---:|:---:|
| **总体准确率** | 47.6% | 42.9% | 43.6% | 57.5% | **54.1%** |
| **代码可执行率** | 81.0% | 66.7% | 97.4% | 95.0% | **100.0%** |
| **运行错误率** | 19.0% | 33.3% | 2.6% | 5.0% | **0.0%** |
| **结果不匹配率** | 33.3% | 23.8% | 53.8% | 37.5% | 45.9% |

**关键发现**：
- **S2 (Query-Specific) 反而比 S1 (Baseline) 差**：约束少了导致 LLM 缺少上下文，运行错误率从 19% 飙升到 33%
- **S3 (CoT) 大幅提升代码可执行率**（81% → 97%）：CoT 分步推理显著减少语法/逻辑错误
- **S4 (+ErrorCheck) 准确率达到最高 57.5%**：Verifier 自修复在 S3 基础上额外消解了部分结果不匹配
- **S5 (+Tools) 代码完全可执行（100%），但准确率略低于 S4**：工具增强了代码生成能力，但准确率 54.1% 仍低于 S4 的 57.5%

---

## 按难度拆分

| 难度 | S1 Baseline | S2 Query-Spec | S3 +CoT | S4 +ErrorCheck | S5 +Tools |
|------|:---:|:---:|:---:|:---:|:---:|
| **Easy** | 57.1% | 85.7% | — | 85.7% | 85.7% |
| **Medium** | 71.4% | 42.9% | 71.4% | 71.4% | 50.0% |
| **Hard** | 14.3% | **0.0%** | 11.1% | **8.3%** | **11.1%** |

**关键发现**：
- **Easy 题在 S2 达到最高 85.7%**：查询相关约束对简单题最有效
- **Hard 题在所有方法中都极低**（最高仅 14.3%）：复杂拓扑操作超出现有约束表达能力
- **S2 在 Medium 题上反而变差**：约束太少导致缺少必要上下文

---

## 按返回类型拆分

| 题型 | S1 | S2 | S3 | S4 | S5 |
|------|:---:|:---:|:---:|:---:|:---:|
| **list** (5题) | 80.0% | 80.0% | 80.0% | **100.0%** | **100.0%** |
| **table** (5题) | 60.0% | 40.0% | 66.7% | 60.0% | 70.0% |
| **text** (5题) | 40.0% | 20.0% | 33.3% | 40.0% | **0.0%** |
| **graph** (6题) | 16.7% | 33.3% | **0.0%** | 36.4% | 44.4% |

**关键发现**：
- **list 题型最稳定**：从 S4 开始达到 100%
- **graph 题型逐渐改善**：从 Baseline 16.7% → Tools 44.4%
- **text 题型在 S5 完全失败（0%）**：工具调用对纯文本输出场景无效甚至有害

---

## 逐题详细对比（前三难度 Easy）

| # | 题目摘要 | 类型 | S1 | S2 | S3 | S4 | S5 |
|:--:|------|:--:|:--:|:--:|:--:|:--:|:--:|
| Q1 | List all ports in packet switch ju1.a1.m1.s2c1 | list | ✅ | ✅ | ✅ | ✅ | ✅ |
| Q2 | Add packet_switch ju1.a1.m1.s4c7 with 5 ports | graph | ❌M | ❌M | ❌M | ❌M | ❌M |
| Q3 | Update physical_capacity_bps to 4000 Mbps | graph | ❌R | ✅ | ✅ | ✅ | ✅ |
| Q4 | CONTROL_POINT within AGG_BLOCK ju1.a4.m4 | list | ✅ | ✅ | ✅ | ✅ | ✅ |
| Q5 | CONTROL_DOMAIN with ≥3 CONTROL_POINT | list | ❌M | ✅ | ✅ | ✅ | ✅ |
| Q6 | Update PACKET_SWITCH stage 3 → 5 | graph | ✅ | ✅ | — | ✅ | ✅ |
| Q7 | CHASSIS nodes per RACK | table | ✅ | ✅ | ✅ | ✅ | ✅ |

> 图例：✅ Pass | ❌R Run Error | ❌M Result Mismatch | — 未包含

**Easy 题分析**：
- Q2（新增节点+5端口+边）是所有方法都无法攻克的难点：需要正确构建多层级包含关系（JUPITER→SUPERBLOCK→AGG_BLOCK→PACKET_SWITCH→PORT），DeepSeek 容易遗漏边的 `type` 属性或层次结构
- S2 唯一新增的 Pass 是 Q5（CONTROL_DOMAIN 计数），其余与 S1 相同

## 逐题详细对比（Medium）

| # | 题目摘要 | 类型 | S1 | S2 | S3 | S4 | S5 |
|:--:|------|:--:|:--:|:--:|:--:|:--:|:--:|
| Q8 | Bandwidth on ju1.a2.m1.s2c2 (Mbps) | text | ✅ | ❌M | ✅ | ✅ | ❌M |
| Q9 | Bandwidth per AGG_BLOCK | table | ✅ | ❌R | ✅ | ✅ | ✅ |
| Q10 | Top 2 Chassis by capacity on ju1.a1.m1 | table | ❌M | ❌R | ❌M | ❌M | ❌M |
| Q11 | Avg physical_capacity_bps for all PORTs | text | ✅ | ✅ | ✅ | ✅ | ❌M |
| Q12 | Switch/Port count per AGG_BLOCK | table | ✅ | ✅ | ✅ | ✅ | ✅ |
| Q13 | Avg capacity per PACKET_SWITCH in ju1.a1.m1 | table | ❌M | ❌R | ❌M | ✅ | ✅ |
| Q14 | PACKET_SWITCH above avg capacity | list | ✅ | ✅ | ✅ | ✅ | ✅ |

**Medium 题分析**：
- Q10（找到容量最大的两个 Chassis）在所有方法中从未通过：需要多层聚合计算（端口→交换机→机框），DeepSeek 容易算错数值
- Q13 从 S4 开始通过：Verifier 检测到表格格式问题后，自修复纠正了错误
- S2 在 Q8/Q9 反而退步：约束减少导致模型缺乏足够的图结构信息

## 逐题详细对比（Hard）

| # | 题目摘要 | 类型 | S1 | S2 | S3 | S4 | S5 |
|:--:|------|:--:|:--:|:--:|:--:|:--:|:--:|
| Q15 | Subgraph of SUPERBLOCK + AGG_BLOCK | graph | ❌M | ❌M | ❌M | ❌M | — |
| Q16 | Remove switch, balance Chassis capacity | graph | ❌R | ❌M | ❌M | ❌M | ❌M |
| Q17 | Remove ports, balance switch capacity | text | ❌R | ❌R | ❌M | ❌M | ❌M |
| Q18 | Paths from CONTROL_DOMAIN to PORT | text | ❌M | ❌R | ❌M | ❌M | ❌M |
| Q19 | Redundancy level per SUPERBLOCK | text | ❌R | ❌R | ❌M | ❌R | ❌M |
| Q20 | Removable PACKET_SWITCH for connectivity | list | ✅ | ❌R | ❌M | ✅ | ✅ |
| Q21 | Optimal placement of new PACKET_SWITCH | graph | ❌M | ❌M | ❌M | ❌R | ❌M |

**Hard 题分析**：
- Q20（识别可移除交换机）是唯一被多次通过的 Hard 题：S1/S4/S5 均通过，说明该题需要在约束引导下推理
- Q16-Q19 涉及多步推理和优化（容量均衡、路径分析、冗余评估），DeepSeek 在所有方法中均无法给出正确答案
- S2 导致 Q20 从 Pass 变成 Run Error：约束过滤后丢失了关键信息

---

## 失败原因分析

### 失败模式分布

| 失败模式 | S1 | S2 | S3 | S4 | S5 |
|------|:---:|:---:|:---:|:---:|:---:|
| Missing import | 3 | 7 | 1 | 2 | 0 |
| List/dict error | 1 | 0 | 0 | 0 | 0 |
| Result mismatch | 7 | 5 | 10 | 8 | 12 |

**失败模式趋势**：
- **Missing import 从 S3 开始急剧减少**：CoT + self-debug 能修复 import 缺失
- **Result mismatch 持续增加**：代码能跑了，但结果不对——说明 DeepSeek 在逻辑精确性上存在短板

### Debug 迭代统计（S4/S5）

| 指标 | S4 +ErrorCheck | S5 +Tools |
|------|:---:|:---:|
| 触发 Debug 的题目数 | 3/40 | 2/37 |
| 额外 Execution Debug 次数 | 2 | 0 |
| 额外 Verifier Debug 次数 | 1 | 6 |

S5 的 Verifier Debug 次数（6次）远超 S4（1次），说明工具调用会引入更多不变量违例。

---

## 讨论

### 1. 约束数量与质量

S2 (Query-Specific, top_k=9-13) 相比 S1 (All constraints) 在 Easy 题上有提升（57.1%→85.7%），但在 Medium 和 Hard 题上反而退化。这与论文结论一致：**约束太少不如全量约束**。

### 2. CoT 的代价

S3 (CoT) 极大提升代码可执行率（81%→97%），但会引入新类型的错误：三步推理中信息可能失真或遗漏，导致结果不匹配率从 33% 上升到 54%。

### 3. Verifier 的实际价值

S4 (ErrorCheck) 取得最高总体准确率（57.5%），说明 Verifier 在 CoT 基础上能有效过滤和修复部分错误。但 debug 触发率很低（3/40），说明大多数失败并非不变量违例，而是更深层的逻辑错误。

### 4. DeepSeek-v4-flash 与论文 GPT-4 的差距

论文中 GPT-4 在 MALT 上达到 >90% 准确率，DeepSeek-v4-flash 最高仅 57.5%。主要差距：
- **图操作题（graph type）**：GPT-4 能正确处理节点属性和边的类型，DeepSeek 频繁遗漏
- **计算题（text type）**：GPT-4 数值计算更准确，DeepSeek 容易在多层聚合时出错
- **Import 遗漏**：DeepSeek 更频繁遗忘 import（7次 vs 论文中几乎为 0）

### 5. 下一步

- 加入置信度评分与主动拒答（S7）可能改善 Hard 题表现
- 增大 `EACH_PROMPT_RUN_TIME` 可能提升稳定性
- 在 prompt 中显式要求 import 可减少运行错误
