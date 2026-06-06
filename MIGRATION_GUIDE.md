# MeshAgent 迁移提示词：Azure → DeepSeek + Qwen3 + ChromaDB

> **用途**：在原始 [Froot-NetSys/MeshAgent](https://github.com/Froot-NetSys/MeshAgent) 项目上执行本提示词，即可复现完整的迁移和实验环境。
>
> **适用对象**：AI Agent（已具备代码读写能力）。
>
> **前置条件**：已 clone 原项目，Python 3.11+ 虚拟环境就绪。

## 概述

将 MeshAgent 项目从 Azure 服务迁移至国产模型 + 本地向量库：

| 组件 | 旧 | 新 |
|------|----|----|
| LLM | Azure OpenAI GPT-4-32k | DeepSeek-v4-flash |
| Embedding | text-embedding-ada-002 | text-embedding-v4（1536 维） |
| 向量检索 | Azure Cognitive Search | ChromaDB（本地持久化） |

---

## 执行指令

请对原 MeshAgent 项目执行以下所有修改。修改策略：**注释原代码 + 追加新代码**，不删除原始代码。用以下格式标记：

```python
# =====================================================================
# ORIGINAL: [描述] (commented out for migration)
# =====================================================================
# [原始代码]
# =====================================================================

# =====================================================================
# NEW: [描述]
# =====================================================================
[新代码]
# =====================================================================
```

---

## 第一步：环境与依赖

### 1.1 创建 `.env` 文件（项目根目录）

```ini
DEEPSEEK_API_KEY="sk-xxx"
DASHSCOPE_API_KEY="sk-xxx"
DASHSCOPE_EMBEDDING_URL="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
```

### 1.2 更新 `requirements.txt`

```txt
openai==0.28.1
langchain==0.0.350
langchain-community==0.0.2
requests
python-dotenv
chromadb
networkx
prototxt-parser
pandas
numpy
jsonlines
scikit-learn
tenacity
azure-core
azure-search-documents
Faker
matplotlib
```

> ⚠️ `langchain==0.0.350` 和 `openai==0.28.1` 必须锁定此版本，不可升级。

### 1.3 创建日志目录

```bash
mkdir -p app-malt/logs/debug app-malt/logs/gpt4 app-malt/logs/codey
mkdir -p app-CRG/logs/debug
mkdir -p app-traffic-analysis/logs/debug
```

在每个 `logs/debug/`、`logs/gpt4/`、`logs/codey/` 下添加 `.gitkeep` 文件。

### 1.4 更新 `.gitignore`

新增以下规则：
```
**/chroma_rag/
**/create_RAG_index/output/
.omo/
.omo/**
.envrc
```

同时为每个 `logs/*/*.gitkeep` 添加例外规则，示例：
```
app-malt/logs/*
!app-malt/logs/debug/
app-malt/logs/debug/*
!app-malt/logs/debug/.gitkeep
!app-malt/logs/gpt4/
app-malt/logs/gpt4/*
!app-malt/logs/gpt4/.gitkeep
# ... 同样模式覆盖 app-CRG 和 app-traffic-analysis
```

---

## 第二步：新建文件

### 2.1 `app-malt/rag_local.py`（同时复制到 `app-CRG/` 和 `app-traffic-analysis/`）

实现 ChromaDB 本地向量检索，提供 `init_rag()`、`rag_constraint_search()`、`rag_tool_search()` 三个函数。

从 `create_RAG_index/output/constraintVectors.json` 和 `toolVectors.json` 读取向量构建索引。使用 cosine 距离。

### 2.2 `app-malt/scripts/reindex.py`

使用 DashScope API（`text-embedding-v4`，1536 维）重新生成约束和工具的 embedding，写入 `create_RAG_index/output/`。

### 2.3 `app-malt/scripts/analyze_results.py`

实验结果分析脚本。支持：
- 终端报告（准确率、按难度/题型拆分、Fig 9 拒答矩阵）
- 导出 summary JSON、per-query JSON、跨实验 CSV
- 失败原因分类（11 类）
- Debug 迭代计数统计
- 置信度指标（S_confidence、C_semantic）

### 2.4 新增实验脚本

| 脚本 | 描述 |
|------|------|
| `app-malt/full_meshagent_benchmark.py` | Stage 6：完整 CoT + verifier + 工具，使用 benchmark 题，无拒答 |
| `app-malt/full_meshagent_abstention.py` | Stage 7：Stage 6 + 置信度评分 + 主动拒答 |

---

## 第三步：修改 LLM 初始化

修改以下 4 个文件中的 LLM 初始化代码：

- `app-malt/ai_models_cot.py`
- `app-malt/copy_ai_models_cot.py`
- `app-CRG/ai_models_cot.py`
- `app-traffic-analysis/ai_models_cot.py`

### 3.1 注释掉 Google VertexAI 导入

```python
# from langchain.llms import VertexAI
# import google.generativeai as genai
```

### 3.2 修正 `load_dotenv()` 路径

```python
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
```

### 3.3 替换 LLM 初始化

注释掉 Azure OpenAI GPT-4-32k 和 Google VertexAI 的 LLM 初始化代码块，替换为：

```python
from langchain.chat_models import ChatOpenAI

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

llm = ChatOpenAI(
    model='deepseek-v4-flash',
    openai_api_base='https://api.deepseek.com/v1',
    openai_api_key=DEEPSEEK_API_KEY,
    temperature=0.0,
    max_tokens=4000,
    model_kwargs={"thinking": {"type": "disabled"}},
)
```

> 注意：`copy_ai_models_cot.py` 原使用 Google VertexAI `code-bison`，同样替换为以上代码。

---

## 第四步：修改 helper.py

修改以下 3 个文件：
- `app-malt/helper.py`
- `app-CRG/helper.py`
- `app-traffic-analysis/helper.py`

### 4.1 修正 `load_dotenv()` 路径

同上，`load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))`

### 4.2 注释掉 Azure 导入

包裹 `from azure.core.credentials ...` 至 `from azure.search.documents.models import Vector` 进注释块。

### 4.3 替换 `generate_embeddings()` 函数

注释掉旧的 `openai.Embedding.create()`，追加：

```python
import requests

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_URL = os.getenv(
    "DASHSCOPE_EMBEDDING_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings"
)

def generate_embeddings(text):
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "text-embedding-v4", "input": text, "dimensions": 1536, "encoding_format": "float"}
    resp = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]
```

### 4.4 注释掉 `extract_constraints()` 和 `extract_tools()`

这两个是 Azure Cognitive Search 专用的结果解析器，ChromaDB 不需要。

---

## 第五步：修改实验脚本（app-malt）

### 5.1 修改 import

在所有 6 个实验脚本中：
- 将 `from helper import extract_constraints` 替换为 `from helper import generate_embeddings`
- 添加 `from rag_local import init_rag, rag_constraint_search`
- 对于使用工具搜索的脚本（`full_cot_with_tools.py`、`copy_full_cot_with_tools.py`），添加 `rag_tool_search`

### 5.2 注释掉 Azure 配置块

包裹以下内容进 `# ORIGINAL` 注释块：
- `from tenacity import retry ...` 到所有 Azure 相关导入
- Azure 环境变量读取（`service_endpoint`、`credential` 等）
- 本地定义的 `generate_embeddings()`（因为已在 helper.py 中定义）
- 本地定义的 `rag_vector_search()` / `rag_constraint_search()` / `rag_tool_search()`

> ⚠️ 不要把 `EACH_PROMPT_RUN_TIME`、`OUTPUT_JSONL_PATH`、`MODEL_SOURCE` 等全局配置变量注释掉。

### 5.3 修改 `userQuery()` 函数

在函数开头（加载 golden answers 之前）加上：
```python
init_rag()
```

将搜索调用改为：
```python
constraints_found = rag_constraint_search(generate_embeddings(each_prompt))
tool_found = rag_tool_search(generate_embeddings(each_prompt))
```

### 5.4 修改各脚本输出路径

将每个脚本的 `OUTPUT_JSONL_PATH` 改为独立路径：

| 脚本 | 输出路径 |
|------|------|
| `baseline_static_prompt.py` | `logs/debug/baseline_static.jsonl` |
| `query_specific_constraint_prompt.py` | `logs/debug/query_specific_constraint.jsonl` |
| `cot_with_query_specific.py` | `logs/debug/cot_query_specific.jsonl` |
| `cot_with_error_check.py` | `logs/debug/cot_error_check.jsonl` |
| `full_cot_with_tools.py` | `logs/gpt4/srikanth_queries_2.jsonl` |
| `copy_full_cot_with_tools.py` | `logs/codey/full_cot_tool.jsonl` |

### 5.5 统一 `MODEL_SOURCE`

`copy_full_cot_with_tools.py` 原为 `MODEL_SOURCE = "GOOGLE"`，改为 `MODEL_SOURCE = "OPENAI"`。

---

## 第六步：Bug 修复

> ⚠️ 以下修复在原项目中也存在，切换到 DeepSeek 后更容易触发。

### 6.1 Prompt 文本精确匹配

所有实验脚本中的 `prompt_list` 字符串必须与 `golden_answer_generator/prompt_golden_ans.json` 的 key 精确一致。检查并修正以下已知不匹配：

1. `"Add edges too"` → `"Add node type and edges too"`
2. `"bandwidth on ju1.a2"` → `"bandwidth on packet switch ju1.a2"`
3. `"Remove five PORT nodes from each"` → `"Remove five PORT nodes (start from p1) from each"`

### 6.2 Golden Answer 图污染

在所有 CoT 脚本（`cot_with_query_specific.py`、`cot_with_error_check.py`、`copy_full_cot_with_tools.py`、`full_cot_with_tools.py`）中，golden answer 对比前重新加载图：

```python
_, G = getGraphData()  # 添加这一行
exec(goldenAnswerCode)
ground_truth_ret = eval("ground_truth_process_graph(G)")
```

### 6.3 None 守卫

在所有 CoT 脚本中添加以下保护：

**最终 ret 判空**：
```python
if ret is None:
    continue
```

**CoT step 1 中间结果判空**：
```python
if first_step_ret is None:
    continue
```

**CoT steps 2/3 中间结果判空**：
```python
if second_step_ret is not None:
    if second_step_ret['type'] == 'graph':
        # ...
```

### 6.4 self_debug_execution_error 返回值

在 `cot_with_error_check.py` 和 `copy_full_cot_with_tools.py` 中，将 `self_debug_execution_error` 和 `error_reduce_verify` 的返回值从 2 元组改为 3 元组，增加 debug 迭代计数。相应更新所有调用点和 JSONL 写入逻辑。

### 6.5 删除死代码

删除 `full_meshagent_abstention.py` 中未被调用的 `compare_with_golden` 函数。

---

## 第七步：文档与配置

### 7.1 创建 `results/` 目录

在项目根目录创建 `results/`，添加 `.gitkeep`。分析脚本运行后自动在其中创建每实验独立子目录。

### 7.2 创建 `notes/` 目录

添加 `notes/troubleshooting.md`（问题记录）和 `notes/experiment_results.md`（实验结果）。

### 7.3 更新 README

- 中英双语（`README.md` 中文，`README_EN.md` 英文）
- 包含论文引用、原项目归因、MIT License
- 完整实验流程（Stage 1-7）
- 数据分析阶段

### 7.4 创建 LICENSE

MIT License，版权署名原论文作者。

---

## 第八步：运行前准备

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 API Key（编辑 .env 填入真实 key）

# 生成向量索引（只需一次）
cd app-malt && python scripts/reindex.py
```

---

## 第九步：运行实验

```bash
cd app-malt

# 按顺序运行
python baseline_static_prompt.py
python scripts/analyze_results.py logs/debug/baseline_static.jsonl

python query_specific_constraint_prompt.py
python scripts/analyze_results.py logs/debug/query_specific_constraint.jsonl

python cot_with_query_specific.py
python scripts/analyze_results.py logs/debug/cot_query_specific.jsonl

python cot_with_error_check.py
python scripts/analyze_results.py logs/debug/cot_error_check.jsonl

python full_meshagent_benchmark.py
python scripts/analyze_results.py logs/debug/full_meshagent_benchmark.jsonl

python full_meshagent_abstention.py
python scripts/analyze_results.py logs/debug/full_meshagent_abstention.jsonl
```

查看 `results/all_experiments.csv` 获得跨实验对比表。

---

## 已知限制

| 限制 | 说明 |
|------|------|
| **不升级 langchain/openai** | 必须保持 `0.0.350` / `0.28.1` |
| **不使用 langchain-deepseek** | 与 langchain 0.0.350 不兼容 |
| **相对路径依赖** | 所有脚本必须从 `app-malt/` 目录运行 |
| **Prompt 精确匹配** | 任何文字差异都会导致 SystemExit |
| **Golden answer 只在 MALT 可用** | CRG 和 traffic-analysis 的 golden answer 未适配 |

## 常见问题

详见 `notes/troubleshooting.md`，记录了迁移和实验过程中遇到的 15 个问题及其解决方案。
