# MeshAgent 复现与调试记录

本文档记录了从原始 MeshAgent 项目（Azure）迁移至 DeepSeek + ChromaDB 过程中，实验复现阶段遇到的所有问题、原因及解决方案。

---

## 1. 环境与基础设施

### 1.1 日志目录缺失

**现象**：`FileNotFoundError: logs/debug/baseline_static.jsonl`

**原因**：`logs/debug/` 目录不存在，脚本中的 `open(OUTPUT_JSONL_PATH, 'w')` 无法创建文件。

**解决**：创建 `logs/debug/` 目录，并通过 `.gitkeep` + `.gitignore` exception 规则保证 clone 后目录存在。

**涉及文件**：所有实验脚本、`.gitignore`

---

### 1.2 API Key 未配置

**现象**：`requests.exceptions.HTTPError: 401 Unauthorized`

**原因**：`.env` 中的 API Key 为占位符。

**解决**：在根目录创建 `.env` 文件，填入真实的 DeepSeek 和 DashScope API Key。

**涉及文件**：`.env`（需手动创建）

---

### 1.3 .env.template 混淆

**现象**：用户不清楚 `.env` 和 `.env.template` 的区别。

**原因**：`.env.template` 只是一个模板文件，不会被代码读取。`.env` 才是实际生效的配置。

**解决**：删除 `.env.template`，在 README 中明确说明 `.env` 需要手动创建，并给出完整内容格式。

---

### 1.4 虚拟环境手动激活繁琐

**现象**：每次进入项目目录需手动 `source .venv/bin/activate`。

**解决**：安装 direnv + 创建 `.envrc` 文件。进目录自动激活，离开自动退出。

---

## 2. LLM 相关

### 2.1 LLM 生成代码缺少 import

**现象**：
```
name 'copy' is not defined
name 'combinations' is not defined
```

**原因**：LLM（DeepSeek-v4-flash）生成的 `process_graph()` 函数体内使用了 `copy.deepcopy` 或 `itertools.combinations`，但没有写 `import` 语句。函数体被 `exec()` 独立执行，无法解析未 import 的名字。

**影响**：baseline 中 4/21 题因此失败（19.0%），主要出现在需要修改图结构的题目。

**解决**：此类错误由 CoT 自修复（self-debug）处理。将错误信息送回 LLM，要求其修复并重新生成代码。若自修复也失败，该次运行算运行错误（Fail, code cannot run）。

**深层原因**：这类问题在 GPT-4 上较少出现（GPT-4 有更强的代码补全能力），DeepSeek-v4-flash 因模型能力差异更容易遗忘 import。

---

### 2.2 LLM 生成代码原地修改 G 导致 golden answer 崩溃

**现象**：
```
KeyError: 'type'
```
出现在 `ground_truth_process_graph(G)` 调用中。

**原因**：CoT 各步生成的 `process_graph(G)` 会原地修改图 `G`（如 `G.add_node()`、`G.add_edge()`）。LLM 新增的节点可能缺少 `type` 属性。后续 golden answer 在已污染的图上执行，访问不存在的 `type` 键导致崩溃。

**复现条件**：仅出现在 CoT 脚本中（`cot_with_query_specific`、`cot_with_error_check`、`copy_full_cot_with_tools`、`full_cot_with_tools`），因为 CoT 多步执行会逐步修改 G。单步 baseline 不触发。

**解决**：在 golden answer 比对前调用 `getGraphData()` 重新加载干净的图。共修复 6 个 CoT 脚本。

**涉及 commit**：`96a6b73`

---

### 2.3 自修复失败后 ret 为 None 导致类型错误

**现象**：
```
TypeError: 'NoneType' object is not subscriptable
```
出现在 `if ret['type'] == 'graph':` / `if first_step_ret['type'] == 'graph':` 等位置。

**原因**：当 self-debug 所有尝试都失败时，`ret` / `first_step_ret` 保持 `None`。原代码直接访问 `['type']` 而不检查 None。

**复现条件**：遇到 2.1 类错误（缺少 import），且 self-debug 也无法修复时。

**解决**：
1. 最终 `ret`：添加 `if ret is None: continue`，跳过该次 golden answer 比对
2. 中间步骤 `xxx_step_ret`：包裹 `if xxx is not None:` 保护块

**涉及脚本**：`cot_with_query_specific.py`、`cot_with_error_check.py`、`copy_full_cot_with_tools.py`、`full_cot_with_tools.py`、`full_meshagent_benchmark.py`、`full_meshagent_abstention.py`

**涉及 commit**：`e0f3bdb`、`57472c0`

---

## 3. 数据相关

### 3.1 Prompt 文本与 Golden Answer Key 不匹配

**现象**：
```
SystemExit: Un-support ground truth for the current prompt.
```

**原因**：`prompt_golden_ans.json` 的 key 与脚本中的 query 字符串采用精确匹配。脚本中部分 query 文字与 golden answer 的 key 不完全一致：

| 不匹配 | 脚本中的文本 | Golden Answer Key |
|--------|------------|-------------------|
| 1 | `"Add edges too."` | `"Add node type and edges too."` |
| 2 | `"bandwidth on ju1.a2..."` | `"bandwidth on packet switch ju1.a2..."` |
| 3 | `"Remove five PORT nodes from each..."` | `"Remove five PORT nodes (start from p1) from each..."` |

**影响**：导致 `query_specific_constraint_prompt.py` 和 `cot_with_error_check.py` 等脚本在运行到第 2 或第 7 题时中断。

**解决**：批量检查所有 6 个脚本的 120 条 prompt 与 golden answer 的 21 个 key，修复 5 处不匹配。

**涉及 commit**：`2ee2068`、`86e6e00`

---

### 3.2 Stage 1-4 输出到同一文件

**现象**：baseline、query_specific、cot_query_specific、cot_error_check 四个脚本的 `OUTPUT_JSONL_PATH` 都指向同一个文件 `logs/debug/baseline_static.jsonl`（追加模式）。

**解决**：将四个脚本的输出路径改为独立文件：
- `logs/debug/baseline_static.jsonl`
- `logs/debug/query_specific_constraint.jsonl`
- `logs/debug/cot_query_specific.jsonl`
- `logs/debug/cot_error_check.jsonl`

---

## 4. 缺失功能实现

### 4.1 置信度评分与主动拒答

**论文描述**但代码中未实现：
- 语义一致性 C_semantic（多次运行输出的 embedding 余弦相似度）
- 置信度公式 S_confidence = w·C + (1-w)·(1-I/N)
- 主动拒答（S_confidence < 0.7 → Abstain）

**解决**：新建 `full_meshagent_abstention.py`（Stage 7）完整实现论文的 Eq.(2) 置信度公式和 Fig. 9 拒答矩阵。

---

### 4.2 Debug 迭代次数未记录

**现象**：自修复（self-debug/error-reduce）的执行次数未被记录到 JSONL。

**解决**：在 `cot_with_error_check.py`、`copy_full_cot_with_tools.py` 中添加 debug count 追踪，`error_reduce_verify()` 和 `self_debug_execution_error()` 返回额外计数值，写入 JSONL。

---

### 4.3 实验结果分析工具缺失

**现象**：原项目没有数据清洗/分析脚本。

**解决**：新建 `scripts/analyze_results.py`，支持：
- 终端报告（准确率、By Difficulty/Type、Fig 9 拒答矩阵）
- JSON/CSV 导出到 `results/` 目录
- 失败原因分类（11 类：missing_import、type_error 等）
- 跨实验对比 CSV
- Debug 迭代统计
- 置信度统计
