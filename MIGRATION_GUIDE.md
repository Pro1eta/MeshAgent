# MeshAgent 迁移指南：Azure → DeepSeek + Qwen3 + ChromaDB

## 概述

将 MeshAgent 项目的三个应用（malt / CRG / traffic-analysis）从 Azure 服务迁移到：
- **LLM**: Azure OpenAI GPT-4-32k → DeepSeek-v4-pro
- **Embedding**: text-embedding-ada-002 → text-embedding-v4 (Qwen3-Embedding-8B, 1536维)
- **向量检索**: Azure Cognitive Search → ChromaDB (本地)

---

## 0. 环境与依赖

### API Key 配置

在项目根目录创建 `.env`（注意 `.env` 已在 `.gitignore` 中，不会被提交）：

```ini
DEEPSEEK_API_KEY="sk-xxx"
DASHSCOPE_API_KEY="sk-xxx"
DASHSCOPE_EMBEDDING_URL="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
```

同时提供 `.env.template`（只含占位符，可安全提交到 git）：

```ini
DEEPSEEK_API_KEY="your-deepseek-api-key-here"
DASHSCOPE_API_KEY="your-dashscope-api-key-here"
DASHSCOPE_EMBEDDING_URL="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
```

### 新依赖

```bash
pip install chromadb requests
```

### 保持不变的依赖（不可升级）

- `langchain==0.0.350` — 新版本 API 不兼容
- `openai==0.28.1` — `langchain.chat_models.ChatOpenAI` 依赖旧版 SDK

---

## 1. 新建文件

### 1.1 `app-malt/rag_local.py` — ChromaDB 向量检索模块

```python
"""
Local vector database for MALT/CRG/TA RAG using ChromaDB.
Replaces Azure Cognitive Search with ChromaDB persistent client.

Usage:
    from rag_local import init_rag, rag_constraint_search, rag_tool_search
    init_rag()
    constraints = rag_constraint_search(query_embedding, top_k=13)
    tools = rag_tool_search(query_embedding, top_k=1)
"""

import os
import json
import chromadb
from chromadb.config import Settings

CHROMA_DIR = os.path.join(os.path.dirname(__file__), "chroma_rag")
CONSTRAINT_VECTORS_PATH = os.path.join(
    os.path.dirname(__file__), "create_RAG_index", "output", "constraintVectors.json"
)
TOOL_VECTORS_PATH = os.path.join(
    os.path.dirname(__file__), "create_RAG_index", "output", "toolVectors.json"
)

_collections = {}


def _get_client():
    return chromadb.PersistentClient(
        path=CHROMA_DIR,
        settings=Settings(anonymized_telemetry=False),
    )


def init_rag(force_reindex=False):
    client = _get_client()
    existing = client.list_collections()
    existing_names = {c.name for c in existing}

    if "rag_constraints" in existing_names and not force_reindex:
        _collections["constraints"] = client.get_collection("rag_constraints")
    else:
        if "rag_constraints" in existing_names:
            client.delete_collection("rag_constraints")
        _collections["constraints"] = _build_constraint_collection(client)

    if "rag_tools" in existing_names and not force_reindex:
        _collections["tools"] = client.get_collection("rag_tools")
    else:
        if "rag_tools" in existing_names:
            client.delete_collection("rag_tools")
        _collections["tools"] = _build_tool_collection(client)


def _build_constraint_collection(client):
    with open(CONSTRAINT_VECTORS_PATH) as f:
        data = json.load(f)
    collection = client.create_collection(
        name="rag_constraints",
        metadata={"hnsw:space": "cosine"},
    )
    if not data:
        return collection
    collection.add(
        ids=[item["id"] for item in data],
        documents=[item["constraint"] for item in data],
        embeddings=[item["constraintVector"] for item in data],
        metadatas=[{"label": item["label"]} for item in data],
    )
    return collection


def _build_tool_collection(client):
    with open(TOOL_VECTORS_PATH) as f:
        data = json.load(f)
    collection = client.create_collection(
        name="rag_tools",
        metadata={"hnsw:space": "cosine"},
    )
    if not data:
        return collection
    collection.add(
        ids=[item["id"] for item in data],
        documents=[item["tool"] for item in data],
        embeddings=[item["toolVector"] for item in data],
        metadatas=[{"description": item["description"]} for item in data],
    )
    return collection


def rag_constraint_search(query_embedding, top_k=13):
    col = _collections.get("constraints")
    if col is None:
        raise RuntimeError("RAG not initialized. Call init_rag() first.")
    results = col.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents"],
    )
    constraints_list = results["documents"][0] if results["documents"] else []
    return " ".join(constraints_list)


def rag_tool_search(query_embedding, top_k=1):
    col = _collections.get("tools")
    if col is None:
        raise RuntimeError("RAG not initialized. Call init_rag() first.")
    results = col.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "distances"],
    )
    if not results["documents"] or not results["documents"][0]:
        return "no tools available"
    documents = results["documents"][0]
    distances = results["distances"][0]
    tool_list = []
    for doc, dist in zip(documents, distances):
        similarity = 1.0 - dist
        if similarity < 0.7:
            return "no tools available"
        tool_list.append(doc)
    return " ".join(tool_list) if tool_list else "no tools available"
```

> 此文件需要复制到三个 app 目录：`app-malt/`、`app-CRG/`、`app-traffic-analysis/`

### 1.2 `app-malt/scripts/reindex.py` — 向量重生成脚本

```python
"""
Regenerate RAG vectors using text-embedding-v4 (1536-dim) and rebuild ChromaDB.
Usage: python scripts/reindex.py
"""
import os, sys, json, time, requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not DASHSCOPE_API_KEY:
    print("ERROR: DASHSCOPE_API_KEY not set in .env"); sys.exit(1)

DASHSCOPE_URL = os.getenv(
    "DASHSCOPE_EMBEDDING_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings"
)
EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_DIM = 1536

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "create_RAG_index", "output")
CONSTRAINT_INPUT = os.path.join(DATA_DIR, "rag_constraints.json")
TOOL_INPUT = os.path.join(DATA_DIR, "rag_tools.json")
CONSTRAINT_OUTPUT = os.path.join(OUTPUT_DIR, "constraintVectors.json")
TOOL_OUTPUT = os.path.join(OUTPUT_DIR, "toolVectors.json")


def embed_batch(texts):
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": EMBEDDING_MODEL, "input": texts, "dimensions": EMBEDDING_DIM, "encoding_format": "float"}
    resp = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]


def reindex_constraints():
    print("Reindexing constraints...")
    with open(CONSTRAINT_INPUT) as f:
        constraints = json.load(f)
    texts = [c["constraint"] for c in constraints]
    for i in range(0, len(texts), 10):
        batch = texts[i:i+10]
        embeddings = embed_batch(batch)
        for j, emb in enumerate(embeddings):
            constraints[i+j]["constraintVector"] = emb
            constraints[i+j]["labelVector"] = emb
        print(f"  Constraints {i+1}-{min(i+10, len(texts))}/{len(texts)}")
        if i + 10 < len(texts):
            time.sleep(0.5)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(CONSTRAINT_OUTPUT, "w") as f:
        json.dump(constraints, f)


def reindex_tools():
    print("Reindexing tools...")
    with open(TOOL_INPUT) as f:
        tools = json.load(f)
    texts = [t["tool"] for t in tools]
    desc_texts = [t["description"] for t in tools]
    all_embeddings = embed_batch(texts + desc_texts)
    mid = len(texts)
    for i in range(len(tools)):
        tools[i]["toolVector"] = all_embeddings[i]
        tools[i]["descriptionVector"] = all_embeddings[mid + i]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(TOOL_OUTPUT, "w") as f:
        json.dump(tools, f)


def main():
    print(f"Using model: {EMBEDDING_MODEL}, dimension: {EMBEDDING_DIM}")
    reindex_constraints()
    reindex_tools()
    print("\nRebuilding ChromaDB index...")
    from rag_local import init_rag
    init_rag(force_reindex=True)
    print("Done!")


if __name__ == "__main__":
    main()
```

---

## 2. 修改策略：注释旧代码 + 追加新代码

**核心原则：不删除任何原始代码，用注释块包裹旧代码，后面追加新代码。**

每个改动用以下格式标记：

```python
# =====================================================================
# ORIGINAL: [描述] (commented out for migration)
# =====================================================================
# [原始代码，每行前面加 #]
# =====================================================================

# =====================================================================
# NEW: [描述]
# =====================================================================
[新代码]
# =====================================================================
```

---

## 3. 逐文件修改说明

### 3.1 LLM 初始化文件

**影响文件：**
- `app-malt/ai_models_cot.py`
- `app-malt/copy_ai_models_cot.py`
- `app-CRG/ai_models_cot.py`
- `app-traffic-analysis/ai_models_cot.py`

**改 1：注释掉 Google 导入（消除 FutureWarning）**

找到：
```python
from langchain.llms import VertexAI
import google.generativeai as genai
```
包裹进 `# ORIGINAL` 注释块。

**改 2：修正 `load_dotenv()` 路径**

```python
# 旧
load_dotenv()

# 新
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
```

**改 3：替换 LLM 初始化**

注释掉 Azure / Google LLM 初始化代码块，替换为：

```python
# =====================================================================
# ORIGINAL: Azure OpenAI GPT-4-32k / Google VertexAI (commented out for migration)
# =====================================================================
# [原始 LLM 初始化代码]
# =====================================================================

# =====================================================================
# NEW: DeepSeek-v4-pro via OpenAI-compatible API
# =====================================================================
from langchain.chat_models import ChatOpenAI

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

llm = ChatOpenAI(
    model='deepseek-v4-pro',
    openai_api_base='https://api.deepseek.com/v1',
    openai_api_key=DEEPSEEK_API_KEY,
    temperature=0.0,
    max_tokens=4000,
    model_kwargs={"thinking": {"type": "disabled"}},
)
# =====================================================================
```

> **注意**：`copy_ai_models_cot.py` 原本用 Google VertexAI `code-bison`，同样替换为 DeepSeek。

---

### 3.2 Helper 文件

**影响文件：**
- `app-malt/helper.py`
- `app-CRG/helper.py`
- `app-traffic-analysis/helper.py`

**改 1：修正 `load_dotenv()` 路径**

```python
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
```

**改 2：注释掉 Azure 导入**

包裹 `from azure.core.credentials ...` 到 `from azure.search.documents.models ...` 进注释块。

**改 3：替换 `generate_embeddings()` 函数**

注释掉旧的（使用 `openai.Embedding.create`），追加新的：

```python
# =====================================================================
# ORIGINAL: OpenAI text-embedding-ada-002 (commented out for migration)
# =====================================================================
# def generate_embeddings(text):
#     response = openai.Embedding.create(
#         input=text, engine="text-embedding-ada-002")
#     embeddings = response['data'][0]['embedding']
#     return embeddings
# =====================================================================

# =====================================================================
# NEW: text-embedding-v4 (Qwen3-Embedding-8B, 1536-dim) via DashScope
# =====================================================================
import requests

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_URL = os.getenv(
    "DASHSCOPE_EMBEDDING_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings"
)

def generate_embeddings(text):
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "text-embedding-v4",
        "input": text,
        "dimensions": 1536,
        "encoding_format": "float",
    }
    resp = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]
# =====================================================================
```

**改 4：注释掉 `extract_constraints()` 和 `extract_tools()` 函数**

这两个是 Azure Cognitive Search 专用的结果解析器，ChromaDB 不需要。

---

### 3.3 实验脚本

**影响文件：**
- `app-malt/baseline_static_prompt.py`
- `app-malt/query_specific_constraint_prompt.py`
- `app-malt/cot_with_query_specific.py`
- `app-malt/cot_with_error_check.py`
- `app-malt/full_cot_with_tools.py`
- `app-malt/copy_full_cot_with_tools.py`

#### A. 修改 import

将 `from helper import ...` 中的 `extract_constraints` 替换为 `generate_embeddings`，移除 `extract_tools`（如有）。

添加：
```python
from rag_local import init_rag, rag_constraint_search
```
如果该脚本使用了 tool search（`full_cot_with_tools.py`、`copy_full_cot_with_tools.py`），再加：
```python
, rag_tool_search
```

#### B. 注释掉 Azure 配置块

将以下内容包裹进 `# ORIGINAL` 注释块：
- `from tenacity import retry ...` 到 `from azure.search.documents.models import Vector`
- Azure 环境变量读取（`service_endpoint = ...` 到 `credential = ...`）
- 本地定义的 `generate_embeddings()` 函数
- 本地定义的 `rag_vector_search()` / `rag_constraint_search()` / `rag_tool_search()` 函数

**⚠️ 但不要把以下全局配置变量注释掉**（它们不是 Azure 专属的）：
```python
EACH_PROMPT_RUN_TIME = 1
OUTPUT_JSONL_PATH = 'logs/debug/xxx.jsonl'
DEBUG_LOOP_TOTAL = N
MODEL_SOURCE = "OPENAI"
```
确保这些变量在注释块**之外**、`def userQuery` 函数**之前**。

#### C. 修改 `userQuery()` 函数

在函数开头（加载 golden answers 之前）加上：
```python
init_rag()
```

将搜索调用从：
```python
constraints_found = rag_vector_search(each_prompt)
constraints_found = rag_constraint_search(each_prompt)
```
改为：
```python
constraints_found = rag_constraint_search(generate_embeddings(each_prompt))
constraints_found = rag_constraint_search(generate_embeddings(each_prompt), top_k=9)
```

将工具搜索从：
```python
tool_found = rag_tool_search(each_prompt)
```
改为：
```python
tool_found = rag_tool_search(generate_embeddings(each_prompt))
```

将 Verifier 错误搜索从：
```python
rag_constraint_search(str(verifier_error), num_extraction=2)
```
改为：
```python
rag_constraint_search(generate_embeddings(str(verifier_error)), top_k=2)
```

#### D. 各脚本的 `top_k` 默认值

| 脚本 | 约束搜索 top_k |
|------|---------------|
| baseline_static_prompt | 默认(13) |
| query_specific_constraint_prompt | 9 |
| cot_with_query_specific | 10 |
| cot_with_error_check | 默认(11) |
| full_cot_with_tools | 默认(13) |
| copy_full_cot_with_tools | 默认(10) |

#### E. `copy_full_cot_with_tools.py` 特殊处理

原代码使用 Google VertexAI 输出格式，`MODEL_SOURCE = "GOOGLE"`。因已切换到 DeepSeek，改为：
```python
MODEL_SOURCE = "OPENAI"
```
（`diff_model_source_output_format` 函数依赖此变量判断 LLM 输出格式）

---

## 4. 运行前准备

```bash
# 1. 创建日志目录
mkdir -p app-malt/logs/debug

# 2. 创建 scripts 目录
mkdir -p app-malt/scripts

# 3. 生成向量 + 构建 ChromaDB 索引（只需跑一次）
cd app-malt && python scripts/reindex.py
```

---

## 5. 验证

```bash
cd app-malt && python baseline_static_prompt.py
```

预期输出：
```
Query:  List all ports contained in packet switch ju1.a1.m1.s2c1. Return a list.
Constraints:  packet switch nodes also have switch location attribute...
Find the prompt in the list.
Calling model
model returned
def process_graph(graph_data):
    ...
Pass the test!
=========Current query process is done!=========
Total test times:  1
Testing accuracy:  1.0
```

---

## 6. 已知陷阱

| 陷阱 | 说明 |
|------|------|
| **不要升级 langchain** | 必须保持 `0.0.350`，新版本 API 不兼容 |
| **不要升级 openai** | 必须保持 `0.28.1`，`langchain.chat_models.ChatOpenAI` 依赖旧版 SDK |
| **不要用 `langchain-deepseek`** | 依赖 `langchain-core>=1.0`，与 `0.0.350` 冲突。用 `ChatOpenAI` + `openai_api_base` 即可 |
| **关闭 DeepSeek thinking mode** | `model_kwargs={"thinking": {"type": "disabled"}}`，否则 V4 默认思考模式影响确定性输出 |
| **Prompt 文本必须精确匹配** | 脚本通过精确字符串匹配查找 ground truth，出现细微差异（如 `"with 5 ports"` vs `"with 5 ports, each port has..."`) 就会报 `Un-support ground truth` 退出 |
| **必须从 app 目录运行** | 脚本使用相对路径 `data/` 和 `logs/` |
| **`.env` 位置** | 放在项目根目录，`load_dotenv()` 需要找到它 |
