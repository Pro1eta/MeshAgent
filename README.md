# MeshAgent

[English](./README_EN.md)

> 基于 "[MeshAgent: Enabling Reliable Network Management with Large Language Models](https://doi.org/10.1145/3771567)" 论文的复现与迁移项目。原项目：[Froot-NetSys/MeshAgent](https://github.com/Froot-NetSys/MeshAgent)

基于 LLM 的图数据代码生成与约束验证代理。通过提取领域不变性约束（invariants）引导 LLM 生成并验证代码，支持对网络拓扑（MALT）、配置规则图（CRG）及流量分析场景的结构化提问与自动化代码生成。

**论文信息**：Yajie Zhou, Kevin Hsieh, Sathiya Kumaran Mani, Srikanth Kandula, Zaoxing Liu. *ACM SIGMETRICS 2026*. [[PDF]](https://zaoxing.github.io/papers/2026/SIGMETRICS26_MeshAgent.pdf) [[MSR]](https://www.microsoft.com/en-us/research/publication/meshagent-enabling-reliable-network-management-with-large-language-models/)

## 服务迁移

本项目已从 Azure 服务迁移至国产模型 + 本地向量库：

| 组件 | 旧 | 新 |
|------|----|----|
| LLM | Azure OpenAI GPT-4-32k | DeepSeek-v4-pro |
| Embedding | text-embedding-ada-002 | text-embedding-v4 (Qwen3-Embedding-8B, 1536维) |
| 向量检索 | Azure Cognitive Search | ChromaDB (本地) |

> 详细迁移指南见 [MIGRATION_GUIDE.md](./MIGRATION_GUIDE.md)

## 项目结构

```
.
├── .env                      # API Key 配置（需自行创建，已 gitignore）
├── LICENSE                   # MIT License
├── requirements.txt          # Python 依赖
├── MIGRATION_GUIDE.md        # 迁移指南
├── results/                  # 清洗后的实验数据（自动导出）
├── app-malt/                 # MALT：网络拓扑代码生成
├── app-CRG/                  # CRG：配置规则图代码生成
└── app-traffic-analysis/     # 流量分析代码生成
```

## 快速开始

### 1. 创建 `.env` 并配置 API Key

> ⚠️ `.env` **不在仓库中**（已 gitignore）。clone 后需在项目根目录手动创建。

在项目根目录新建 `.env` 文件，内容如下（替换为你的真实 key）：

```ini
DEEPSEEK_API_KEY="sk-你的deepseek-api-key"
DASHSCOPE_API_KEY="sk-你的dashscope-api-key"
DASHSCOPE_EMBEDDING_URL="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
```

| 变量 | 说明 | 获取地址 |
|------|------|----------|
| `DEEPSEEK_API_KEY` | DeepSeek LLM API Key | https://platform.deepseek.com/api_keys |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope Embedding API Key | https://dashscope.console.aliyun.com/apiKey |

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

> **注意**：`langchain==0.0.350` 和 `openai==0.28.1` 必须保持此版本，不可升级。

### 3. 生成向量索引（仅需执行一次）

```bash
cd app-malt && python scripts/reindex.py
```

### 4. 运行实验

实验按渐进式模块叠加设计，每个脚本在上一个基础上增加一个组件：

```bash
cd app-malt

# Stage 1: 全量约束静态注入（Baseline）
python baseline_static_prompt.py

# Stage 2: 查询相关约束检索
python query_specific_constraint_prompt.py

# Stage 3: + Chain-of-Thought 推理
python cot_with_query_specific.py

# Stage 4: + Verifier 不变量检测 + 自修复
python cot_with_error_check.py

# Stage 5: + 工具调用（Full MeshAgent）
python full_cot_with_tools.py

# （可选）Stage 5 替代版（Google VertexAI 原版适配）
python copy_full_cot_with_tools.py

# Stage 6: + 置信度评分 + 主动拒答（Full MeshAgent + Abstention）
# 需要 ≥ 3 次重复运行来评估语义一致性 (EACH_PROMPT_RUN_TIME=3)
python full_meshagent_abstention.py
```

> 所有脚本必须从 `app-malt/` 目录运行（内部使用 `data/`、`logs/` 相对路径）。

### 5. 清洗与分析

```bash
cd app-malt

# 每个实验跑完后执行：
python scripts/analyze_results.py logs/debug/baseline_static.jsonl
python scripts/analyze_results.py logs/debug/query_specific_constraint.jsonl
python scripts/analyze_results.py logs/debug/cot_query_specific.jsonl
python scripts/analyze_results.py logs/debug/cot_error_check.jsonl
python scripts/analyze_results.py logs/gpt4/srikanth_queries_2.jsonl
python scripts/analyze_results.py logs/codey/full_cot_tool.jsonl
python scripts/analyze_results.py logs/debug/full_meshagent_abstention.jsonl
```

每次运行输出：
- 终端报告（准确率、Fig 9 拒答指标、失败原因分类）
- `results/{实验名}_summary.json` — 结构化指标
- `results/{实验名}_queries.json` — 逐题明细
- `results/all_experiments.csv` — 跨实验对比表（自动追加）

## 依赖说明

核心依赖及其版本约束：

| 依赖 | 版本 | 说明 |
|------|------|------|
| langchain | 0.0.350 | **不可升级**，新版本 API 不兼容 |
| openai | 0.28.1 | **不可升级**，langchain 依赖旧版 SDK |
| chromadb | latest | 本地向量数据库 |
| networkx | latest | 图数据处理 |

## 已知问题

- **DeepSeek V4 thinking mode**：已在初始化时通过 `model_kwargs={"thinking": {"type": "disabled"}}` 关闭，否则默认思考模式会影响确定性输出
- **Prompt 文本精确匹配**：脚本通过精确字符串匹配查找 ground truth，细微差异可能导致验证失败
- **相对路径依赖**：脚本使用 `data/` 和 `logs/` 相对路径，必须从对应 app 目录运行

## 论文引用

```bibtex
@inproceedings{zhou2026meshagent,
  title     = {MeshAgent: Enabling Reliable Network Management with Large Language Models},
  author    = {Yajie Zhou and Kevin Hsieh and Sathiya Kumaran Mani and Srikanth Kandula and Zaoxing Liu},
  booktitle = {ACM SIGMETRICS},
  year      = {2026},
  doi       = {10.1145/3771567},
}
```

## 协议

本项目基于 [Froot-NetSys/MeshAgent](https://github.com/Froot-NetSys/MeshAgent) fork，沿用 [MIT License](./LICENSE) 开源。
