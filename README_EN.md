# MeshAgent

[中文](./README.md)

> A fork and migration of [Froot-NetSys/MeshAgent](https://github.com/Froot-NetSys/MeshAgent), based on the paper: **"[MeshAgent: Enabling Reliable Network Management with Large Language Models](https://doi.org/10.1145/3771567)"**.

LLM-based code generation and constraint verification agent for graph data. Extracts domain-specific invariants (constraints) to guide LLM code generation and validation, supporting structured querying and automated code generation for network topologies (MALT), configuration rule graphs (CRG), and traffic analysis.

**Paper**: Yajie Zhou, Kevin Hsieh, Sathiya Kumaran Mani, Srikanth Kandula, Zaoxing Liu. *ACM SIGMETRICS 2026*. [[PDF]](https://zaoxing.github.io/papers/2026/SIGMETRICS26_MeshAgent.pdf) [[MSR]](https://www.microsoft.com/en-us/research/publication/meshagent-enabling-reliable-network-management-with-large-language-models/)

## Service Migration

This project has been migrated from Azure services to domestic models + local vector DB:

| Component | Old | New |
|------|----|----|
| LLM | Azure OpenAI GPT-4-32k | DeepSeek-v4-pro |
| Embedding | text-embedding-ada-002 | text-embedding-v4 (Qwen3-Embedding-8B, 1536-dim) |
| Vector Search | Azure Cognitive Search | ChromaDB (local) |

> See [MIGRATION_GUIDE.md](./MIGRATION_GUIDE.md) for detailed migration guide.

## Project Structure

```
.
├── .env                      # API Key config (not committed to git)
├── .env.template             # API Key template
├── LICENSE                   # MIT License
├── requirements.txt          # Python dependencies
├── MIGRATION_GUIDE.md        # Migration guide
├── app-malt/                 # MALT: network topology code generation
├── app-CRG/                  # CRG: config rule graph code generation
└── app-traffic-analysis/     # traffic analysis code generation
```

## Quick Start

### 1. Configure API Keys

```bash
cp .env.template .env
# Edit .env with your DeepSeek and DashScope API keys
```

| Variable | Description | Get From |
|------|------|----------|
| `DEEPSEEK_API_KEY` | DeepSeek LLM API Key | https://platform.deepseek.com/api_keys |
| `DASHSCOPE_API_KEY` | Alibaba Cloud DashScope Embedding API Key | https://dashscope.console.aliyun.com/apiKey |

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

> **Note**: `langchain==0.0.350` and `openai==0.28.1` must stay at these exact versions. Do not upgrade.

### 3. Generate Vector Index (one-time only)

```bash
cd app-malt && python scripts/reindex.py
```

### 4. Run Experiments

```bash
cd app-malt && python baseline_static_prompt.py
```

All scripts must be run from their respective app directory, as they use relative paths (`data/`, `logs/`).

## Dependency Notes

Core dependencies and version constraints:

| Dependency | Version | Notes |
|------|------|------|
| langchain | 0.0.350 | **Do not upgrade** — new versions have incompatible APIs |
| openai | 0.28.1 | **Do not upgrade** — langchain depends on legacy SDK |
| chromadb | latest | Local vector database |
| networkx | latest | Graph data processing |

## Known Issues

- **DeepSeek V4 thinking mode**: Disabled at init via `model_kwargs={"thinking": {"type": "disabled"}}`. Default thinking mode would affect deterministic output.
- **Exact prompt matching**: Scripts use exact string matching to find ground truth. Minor text differences may cause validation failures.
- **Relative path dependency**: Scripts use `data/` and `logs/` relative paths and must be run from the corresponding app directory.

## Citation

```bibtex
@inproceedings{zhou2026meshagent,
  title     = {MeshAgent: Enabling Reliable Network Management with Large Language Models},
  author    = {Yajie Zhou and Kevin Hsieh and Sathiya Kumaran Mani and Srikanth Kandula and Zaoxing Liu},
  booktitle = {ACM SIGMETRICS},
  year      = {2026},
  doi       = {10.1145/3771567},
}
```

## License

This project is forked from [Froot-NetSys/MeshAgent](https://github.com/Froot-NetSys/MeshAgent) and released under the [MIT License](./LICENSE).
