# MeshAgent

[English](./README_EN.md)

基于 LLM 的图数据代码生成与约束验证代理。支持对网络拓扑（MALT）、配置规则图（CRG）及流量分析场景的结构化提问与自动化代码生成。

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
├── .env                      # API Key 配置（不提交到 git）
├── .env.template             # API Key 模板
├── requirements.txt          # Python 依赖
├── MIGRATION_GUIDE.md        # 迁移指南
├── app-malt/                 # MALT：网络拓扑代码生成
├── app-CRG/                  # CRG：配置规则图代码生成
└── app-traffic-analysis/     # 流量分析代码生成
```

## 快速开始

### 1. 配置 API Key

```bash
cp .env.template .env
# 编辑 .env，填入你的 DeepSeek 和 DashScope API Key
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

```bash
cd app-malt && python baseline_static_prompt.py
```

所有脚本必须从对应的 app 目录运行，因为内部使用了 `data/` 和 `logs/` 相对路径。

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
