# MeshAgent 实验复现问题记录

本文档记录了将 MeshAgent 项目从 Azure 迁移至 DeepSeek + ChromaDB 并复现实验过程中遇到的所有困难、问题和 bug，以及对应的解决方案。

---

## 1. 环境与依赖问题

### 1.1 `langchain` 和 `openai` 版本锁定

**问题**：项目依赖 `langchain==0.0.350` 和 `openai==0.28.1`，不能升级。

**原因**：
- `langchain.chat_models.ChatOpenAI` 在 langchain 0.1.0+ 被移除，迁移至独立包 `langchain-openai`
- `langchain==0.0.350` 内部调用的是 openai 旧版 SDK（`openai.ChatCompletion.create()`）
- openai 1.0.0 完全重写了客户端 API

**解决**：锁定版本，使用 `ChatOpenAI(openai_api_base=...)` 指向 DeepSeek API，而非使用 `langchain-deepseek` 包（该包依赖 langchain-core>=1.0）。

### 1.2 `openai.chat.completions` vs `openai.ChatCompletion` 错误

**问题**：部分脚本直接 `import openai` 后使用新版 API 调用方式。

**解决**：迁移时确保所有 LLM 调用通过 `langchain.chat_models.ChatOpenAI` 统一管理，不在脚本中直接调用 openai SDK。

---

## 2. 模型与 API 问题

### 2.1 Embedding API 认证失败 (401)

**问题**：`python scripts/reindex.py` 时 DashScope 返回 401 Unauthorized。

**原因**：`.env` 文件中的 API Key 为占位符，未填入真实值。

**解决**：在项目根目录创建 `.env` 文件，填入真实的 `DASHSCOPE_API_KEY` 和 `DEEPSEEK_API_KEY`。同时在 README 中明确说明 `.env` 是 gitignored 的，需要 clone 后手动创建。

### 2.2 DeepSeek V4 thinking mode 影响输出

**问题**：DeepSeek V4 Pro 默认开启 thinking mode，会在代码输出中插入思考过程，影响代码解析和输出的确定性。

**解决**：在 `ChatOpenAI` 初始化时添加 `model_kwargs={"thinking": {"type": "disabled"}}`。切换到 `deepseek-v4-flash` 后此配置可能不再需要，但保留为安全措施。

### 2.3 LLM 模型名称不正确

**问题**：用户期望使用 `deepseek-v4-flash`，但代码中写的是 `deepseek-v4-pro` 或错误的 `deepseek-v4`。

**解决**：在所有 `ai_models_cot.py`（4 个文件）中统一使用 `deepseek-v4-flash`，并同步更新 README 和 MIGRATION_GUIDE 中的文档。

---

## 3. 日志与输出路径问题

### 3.1 `logs/debug/` 目录不存在导致 FileNotFoundError

**问题**：clone 后直接运行实验脚本报 `FileNotFoundError: logs/debug/baseline_static.jsonl`。

**原因**：log 目录未被 git 追踪（已在 `.gitignore` 中），clone 后为空。

**解决**：创建 `logs/debug/`、`logs/gpt4/`、`logs/codey/` 目录，添加 `.gitkeep` 文件。在 `.gitignore` 中添加例外规则保留 `.gitkeep`。

### 3.2 多个脚本共用同一输出文件

**问题**：Stage 1-4 原始脚本都输出到 `logs/debug/baseline_static.jsonl`（追加模式），多次跑会导致结果混在一起。

**解决**：修改各脚本的 `OUTPUT_JSONL_PATH` 为独立路径：
- Stage 1 → `logs/debug/baseline_static.jsonl`
- Stage 2 → `logs/debug/query_specific_constraint.jsonl`
- Stage 3 → `logs/debug/cot_query_specific.jsonl`
- Stage 4 → `logs/debug/cot_error_check.jsonl`

---

## 4. Prompt 文本精确匹配问题

### 4.1 Prompt 与 Golden Answer Key 不匹配导致 SystemExit

**问题**：运行到某个题目时报 `Un-support ground truth for the current prompt`，脚本直接退出。

**原因**：`prompt_golden_ans.json` 的 key 必须与脚本中的 query 字符串精确匹配。多个脚本中存在文字差异：

| 脚本 | 错误文本 | 正确文本 |
|------|----------|----------|
| query_specific, cot_with_error_check | `"Add edges too."` | `"Add node type and edges too."` |
| query_specific | `"bandwidth on ju1.a2..."` | `"bandwidth on packet switch ju1.a2..."` |
| 4 个脚本 | `"Remove five PORT nodes from each"` | `"Remove five PORT nodes (start from p1) from each"` |

**解决**：逐文件修复 prompt 文本，使其与 `prompt_golden_ans.json` 的 key 精确一致。编写了交叉验证脚本检测所有不匹配。

---

## 5. 代码运行时 Bug

### 5.1 `name 'copy' is not defined` / `name 'combinations' is not defined`

**问题**：LLM 生成的代码使用了 `copy.deepcopy()` 或 `itertools.combinations()`，但函数体内未 import。

**原因**：LLM 在生成的 `process_graph()` 函数中使用了标准库函数，但 `exec()` 独立执行该函数时，未导入的模块名不可用。LLM 倾向于在函数体开头补充 import 语句，但 prompt 中未明确要求。

**影响**：这是导致 Stage 1 多题失败的主要原因之一。共影响 3-4 题。

**缓解**：目前通过 self-debug 机制修复部分此类错误。根本解决方案需要修改 prompt 模板要求 LLM 在函数体内显式 import。

### 5.2 `KeyError: 'type'` — Golden Answer 在污染的图上运行

**问题**：CoT 脚本在 golden answer 比对阶段报 `KeyError: 'type'`。

**原因**：CoT 每个 step 的 `process_graph(G)` 原地修改了 `G`（添加节点、修改属性）。某些 LLM 生成的节点缺少 `type` 属性。随后 golden answer 代码访问 `graph_data.nodes[node]['type']` 时 KeyError。

**解决方案**：在 golden answer 比对前重新调用 `getGraphData()` 加载干净图。修复了全部 6 个 CoT 脚本。

### 5.3 `'NoneType' object is not subscriptable` — 自修复失败后 ret 为 None

**问题**：当所有 self-debug 尝试均失败后，`ret` 保持 `None`，但后续代码直接访问 `ret['type']` 导致 TypeError。

**原因**：`self_debug_execution_error()` 在所有 debug 循环失败后返回 `(None, None, count)`，但调用侧未判空。

**解决**：
- 最终 ret：添加 `if ret is None: continue`
- CoT 中间步骤：添加 `if xxx_step_ret is None: continue`（step 1）或 `if xxx_step_ret is not None:` 包裹（steps 2/3）
- 修复了 4 个脚本（cot_with_query_specific, cot_with_error_check, copy_full_cot_with_tools, full_cot_with_tools）

### 5.4 IndentationError — None 守卫导致的缩进错误

**问题**：添加 `if xxx_step_ret is not None:` 守卫时，内层 if/else 块未正确递增缩进。

**原因**：原本的 if/else 在 12 空格缩进层级，添加外层 if 后需要 16 空格缩进，但编辑时未调整。

**解决**：修复 3 个脚本中 steps 2/3 的缩进（cot_with_error_check, copy_full_cot_with_tools, full_cot_with_tools）。

---

## 6. 实验设计与基础设施

### 6.1 缺少实验结果分析工具

**问题**：JSONL 输出需要手动解析，无法直观对比不同实验阶段的结果。

**解决**：编写 `scripts/analyze_results.py`，实现：
- 终端报告（准确率、按难度/题型拆分、Fig 9 拒答矩阵）
- 结构化 JSON 导出（summary + per-query）
- CSV 跨实验对比表
- 失败原因分类（11 类）
- 置信度指标（Stage 7）

### 6.2 实验结果导出结构混乱

**问题**：原本所有结果平铺在 `results/` 下，难以管理多阶段数据。

**解决**：改为每实验独立子目录 `results/<experiment_name>/`，包含 `.txt` 报告、`summary.json`、`queries.json`。跨实验 CSV 保持在 `results/all_experiments.csv`。

### 6.3 测试题目 prompt 列表不一致

**问题**：各脚本的 `prompt_list` 有细微差异（数量不同、文本不同），无法对齐比较。

**解决**：修复所有 mismatch 后，Stage 1-4, 6, 7 均使用相同的 20 道 benchmark 题。

### 6.4 缺少 CI/CD 风格的自动化验证

**问题**：修改脚本后需要手动运行验证，效率低。

**隐含**：`python3 -m py_compile` 可以快速检测语法错误，但逻辑错误仍需实际运行。建议后续添加 pytest 单元测试。

---

## 7. 新增功能

### 7.1 置信度评分与主动拒答（Stage 7）

**实现**：
- `C_semantic`：同一题 3 次运行输出的 embedding 余弦相似度均值
- `S_confidence = 0.5 × C_semantic + 0.5 × (1 - I_debug / 5)`
- 阈值 0.7，低于阈值时主动拒答
- JSONL 记录 `S_confidence`、`C_semantic`、`Abstain reason`

### 7.2 Debug 迭代次数追踪

**实现**：在 Stage 4+ 脚本的 JSONL 输出中记录 `Execution debug count` 和 `Verifier debug count`。分析脚本自动提取并报告平均/最大 debug 迭代次数。

### 7.3 环境配置优化

**改进**：
- 移除 `.env.template`（容易造成混淆），在 README 中直接展示 `.env` 格式
- 添加 `.envrc`（本地 direnv 自动激活虚拟环境，已 gitignore）
- 添加 `.gitignore` 规则覆盖 `chroma_rag/`、`create_RAG_index/output/`、`.omo/`

---

## 总结

从原项目到可运行状态，主要经历了以下阶段：

1. **环境适配**（依赖版本锁定、API 切换）
2. **路径修复**（日志目录、输出重定向）
3. **文本匹配**（prompt 与 golden answer 精确对齐）
4. **运行时健壮性**（None 守卫、图污染修复）
5. **基础设施**（分析脚本、结果导出、实验管理）
6. **功能增强**（置信度评分、debug 追踪、README 文档化）
