# MeshAgent

> 基于 "[MeshAgent: Enabling Reliable Network Management with Large Language Models](https://doi.org/10.1145/3771567)" 论文的复现与迁移项目。原项目：[Froot-NetSys/MeshAgent](https://github.com/Froot-NetSys/MeshAgent)

**论文信息**：Yajie Zhou, Kevin Hsieh, Sathiya Kumaran Mani, Srikanth Kandula, Zaoxing Liu. *ACM SIGMETRICS 2026*. [[PDF]](https://zaoxing.github.io/papers/2026/SIGMETRICS26_MeshAgent.pdf) [[MSR]](https://www.microsoft.com/en-us/research/publication/meshagent-enabling-reliable-network-management-with-large-language-models/)

## 服务迁移

本项目已从 Azure 服务迁移至国产模型 + 本地向量库：

| 组件 | 旧 | 新 |
|------|----|----|
| LLM | Azure OpenAI GPT-4-32k | DeepSeek-v4-flash |
| Embedding | text-embedding-ada-002 | text-embedding-v4 (Qwen3-Embedding-8B, 1536维) |
| 向量检索 | Azure Cognitive Search | ChromaDB (本地) |
