# AI 决策闭环规范

> 状态：MVP AI 行为契约。AI 是受约束的决策增强器，不是规则或数据的替代品。

## 1. AI职责

AI负责：比较候选、识别市场与产业证据、形成多空判断、有限调整排序、暴露数据缺口、验证自身输出、为已发布标的生成每日有证据简报、产生待实验的优化提案。

AI不得：扫描规则之外任意点名、突破硬约束、编造数字、直接修改正式规则、自动发布、给出收益保证或交易指令。

## 2. 输入事实底稿

每次分析只接收冻结快照和候选池，至少包含：

- 市场状态与触发证据。
- 候选规则分、特征和落选风险。
- 行业/产业链成组强度。
- 财务、公告、资金、研报和新闻摘要。
- 上周主池、备选和相似市场状态的历史结果。
- 数据来源、时间、质量和缺失项。
- 当前规则、AI权限和输出Schema版本。

原始长文本先由确定性程序裁剪和引用，不能把不受控网页全文直接作为决策指令。

## 3. 默认执行模式

### 快速模式（默认）

1. 研究调用：统一分析市场和候选，输出结构化评分建议。
2. 审计调用：检查证据、矛盾、硬约束、集中度和数据缺口。

### 深度模式（按需）

市场状态、行业轮动、候选多空和风险审计分角色执行，但共享同一事实底稿。多角色输出用于发现分歧，不以角色投票替代证据。

## 4. 权限与决策

- `ai_adjustment` 范围为 -10～+10。
- 最多替换规则基准前5中的2只。
- 换入对象必须来自规则候选池并通过所有硬约束。
- 审计失败、证据缺失或Schema错误时，该调整无效。
- 用户看到规则版与AI版差异并确认；人工修改必须记录原因。

## 5. 结构化输出

每只候选的最小输出：

```json
{
  "stock_code": "002472",
  "ai_adjustment": 6,
  "touch_probability": 0.43,
  "close_positive_probability": 0.59,
  "expected_drawdown_band": [-0.10, -0.06],
  "summary": "机器人传动分支成组转强，位置未明显透支。",
  "bull_factors": ["sector_breadth:robotics_transmission"],
  "bear_factors": ["volume_confirmation_weak"],
  "invalidation_conditions": ["sector_breadth_breakdown"],
  "evidence_ids": ["feature:week:002472", "sector:robotics:week"],
  "data_gaps": [],
  "confidence": "medium"
}
```

组合输出还需包含换入换出、集中风险、低置信度原因和审计结论。

## 6. 自我验证

发布前审计必须回答：

1. 所有数值是否来自证据ID且时点有效？
2. 是否违反V9.0硬约束或市场状态限制？
3. 是否由单一股票、单一新闻或同质分支主导？
4. 多方结论是否有对应反证和失效条件？
5. 数据缺口是否足以降低置信度或阻止调整？
6. 与规则基准的每个差异是否有明确增益假设？

模型不得输出或展示冗长内部推理；系统保存工具调用、证据、结构化判断、差异和验证结果。

## 7. 记忆与学习

记忆分为：决策记忆、结果记忆、错误模式、市场状态案例。下一周只检索与当前状态和候选相关的少量案例，避免全量历史污染判断。

周终按结构化类型归因：市场状态错误、轮动识别过慢、延续过度、过热过滤过松/过严、个股选择错误、催化判断错误、成交确认不足、数据异常、候选覆盖不足、单一锚点扭曲、AI换入错误、人工干预错误。

## 8. 优化治理

AI可提出提示词、模型、权重、特征或规则优化，但只能创建实验版本。升级流程为：历史walk-forward → 实时影子 → 指标门槛 → 用户批准 → 新版本激活。每次升级保留回退版本。

### 8.1 实验规则提案契约

AI提出规则改进时必须输出受限 DSL，而不是可执行代码。提案至少包含：

```json
{
  "schema_version": "1.0",
  "proposal_id": "exp_rule_2026w33_001",
  "base_rule_version": "v9.0",
  "scope": "ranking",
  "hypothesis": "提高非锚点成组强度的排序权重可减少单一锚点失真",
  "conditions": {"all": [{"feature": "sector_breadth_5d", "op": "gte", "value": 0.7}]},
  "changes": [{"parameter": "sector_group_strength_weight", "op": "set", "value": 22}],
  "objective": ["touch_10_rate", "pre_touch_drawdown"],
  "required_features": ["sector_breadth_5d"],
  "expected_effect": "提高横向验证充分的非锚点候选排名",
  "invalidation_conditions": ["anchor_contribution_share_increases"],
  "rollback_version": "v9.0"
}
```

DSL 仅允许注册特征、白名单逻辑组合 `all/any/not`、比较操作 `eq/in/gt/gte/lt/lte/between` 和有边界的参数变更。禁止任意 Python、SQL、Shell、网络调用、动态导入、文件路径或自由表达式。V9.0 的数据有效性、硬约束、规则优先级、最多 5 只、候选不足不凑数、AI 调整幅度和人工审批边界不得被实验 DSL 覆盖。

### 8.2 静态校验与权限

进入回放前必须校验 Schema、参数范围、单位、特征注册状态、所需回看长度、决策时点可用性、未来标签引用、适用范围和回退版本。任何未知字段、越权作用域或时点不明都将提案标记为 `invalid`，不得“尽力解释”后执行。

AI只能创建 `proposed` 提案和补充实验说明，不能自行校验通过、启动正式影子、批准、激活或回滚。用户批准是激活正式规则的唯一入口；服务端还必须重新执行硬约束回归和版本冲突检查。

### 8.3 实验生命周期

正式状态机为：

`proposed → schema_validated → replay_queued → replay_running → replay_passed/replay_rejected → shadow_ready → shadow_running → awaiting_approval → approved → activated → superseded/rolled_back`

`invalid`、`replay_failed`、`shadow_failed` 为终止或待修订状态。每次状态变化记录操作者、输入指纹、指标摘要、原因和时间；重试创建新的运行记录，不覆盖失败证据。实验产物始终与正式决策表隔离，只有 `approved` 且通过激活前门禁的版本才能成为新的唯一正式版本。

## 9. 提供商与降级

后端以 OpenAI Responses API 作为首个正式提供商接口，开发默认模型为 `gpt-5.6-sol`，但必须由环境配置覆盖且不得写死在规则中。确定性 Mock 始终可用；后续 DeepSeek、OpenRouter 或本地 Ollama 只能通过同一结构化提供商契约扩展。

AI不可用时：保留规则基准结果、标记AI降级、要求人工确认；不得用上次输出或无证据文本冒充本周分析。

每日简报中的 AI 不具备名单调整权限。AI不可用时保留确定性行情与规则失效条件检查，摘要状态标记 `ai_degraded`；不得复用前一交易日文本冒充更新。
