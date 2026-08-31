# PAWE

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

PAWE（Pick A Weekly）是一个可审计、可重放的 A 股 AI 周度研究系统。本仓库以
Apache-2.0 许可证开源，欢迎通过 Issue 和 Pull Request 参与改进；提交前请阅读
[贡献指南](CONTRIBUTING.md)和[安全政策](SECURITY.md)。

系统以规则候选为基线，允许 AI 在硬约束内有限调整，经人工确认后发布目标且最多 5 只、逐只具有约 10% 周内最高涨幅研究情景的观察集合；硬约束后可发布 1～5 只，零只时停止发布，不用不合格标的凑数。每位登录用户还可独立关注最多 5 只沪深 A 股；自选同步进入本人的每日简报和周终复盘，但不参与公共规则、评分或名单生成。每个交易日收盘后为当周实际发布标的生成简报，周终分别评价规则、AI 和人工三套结果。研究情景不构成收益保证或交易指令。

## 重要声明

- PAWE 是研究与工程验证工具，不构成投资建议、收益承诺或交易指令。
- 仓库不包含正式行情快照、个人自选、账户凭据或 OpenAI API Key；运行者必须自行核对外部数据源的授权、条款、访问条件和数据质量。
- 免费公开接口不提供稳定 SLA。任何缺失、冲突、降级或时点不明的数据都不得被静默补全为正式结论。
- 在云端或共享环境部署前，必须配置 HTTPS、强密码、稳定的凭据加密密钥、备份、恢复、监控和访问控制。

当前状态：本机 Web 主流程已经可以从真实沪深冻结快照生成 V9.0 规则名单并进入人工审批；确定性规则、交易日历、主数据、历史分类、版本化前复权日线、行业广度、可重放快照、保守周特征、规则结果持久化、一次性审批/发布事务、登录与用户管理均已接通。管理员可在决策管理人工执行周初、指定交易日日报和指定自然周周终复盘，任务逐阶段更新并保留审计，页面刷新后仍从数据库恢复最新状态。后台周日 18:00 准备下一周输入，工作日 08:30 生成规则版；交易日收盘后由 15:30 单一任务依次拉取正式名单与个人自选行情并生成日报。本机短时休眠导致收盘任务迟到时，Worker 允许在当晚恢复执行；周五缺失可在下一交易周开始前的周末逐日幂等补做，越过正式边界后只允许隔离回溯，单日失败不阻断其他日期。当周最后一个交易日的日报全部齐备后立即评价规则、AI、人工发布版本；复盘先复用日报已落库行情，只为不同版本标的、行业样本和沪深300基准补齐必要数据，不再按固定 18:00 无条件重抓。自动执行失败或未产出时可人工补跑，但仍严格执行原有时点和质量门禁。周终结果包含周内最高、周终收盘、两类回撤、10%触达、沪深300与行业超额，并进入独立“历史数据”页面按周收敛展示，点击交易周后以弹框查看完整归档。主页在本周研究区以“本周观察名单 / 每日简报 / 本周复盘”三项切换下方内容，每日简报可按交易日切换；我的自选继续使用独立弹框；日报只展示所选日期的当日涨跌、相对近5日量能和可追溯消息证据，周内进度与目标触达只属于周终复盘。2026-08-03 至 08-07 已完成严格历史时点真实数据回放：周初只读至 07-31 收盘，五份简报分别只读至各自交易日，周终只读至 08-07；实际事后抓取时间和前复权版本限制均如实标记。AI 调整和完整公告/财务证据仍在推进。当前不是生产系统，不构成投资建议或交易指令。

任务恢复补充：目标交易周在下一交易周首个开市日开始前始终属于当周正式窗口；周初、日报和周终错过计划执行时间后仍可幂等补生成正式结果。补生成不会移动信息边界：周初仍截至首个交易日前一交易日 15:00，日报截至目标交易日 15:00，周终截至最后交易日收盘。worker 重启会补做已到期但未完成的任务；历史页周目录按正式名单、日报、复盘和成功完成的隔离整周回溯取并集。

历史回溯入口：决策管理只在“周初名单”确认弹框中提供一个历史回溯入口。选择已经结束的完整交易周后，系统按周初名单 → 全部交易日日报 → 周终复盘顺序生成整周内容。回溯结果只写入 `replay_*` 隔离表，保留模拟截止、实际运行时间、输入指纹、警告和失败阶段，不写正式决策、审批、发布或日报表；成功完成后按周进入“历史数据”，决策管理不展示回溯内容。

既有正式补生成数据继续保留正式身份；这不再是一次性例外，而是上述统一正式周窗口规则的历史事实。

实验治理旁路已加入 `20260813_0015`，任务取消检查点由 `20260813_0016` 补齐：AI 规则提案只能使用白名单 DSL，不能修改硬约束或执行代码；实验必须按静态校验、walk-forward、影子、人工批准和回滚状态推进。数据源能力矩阵、Parquet 原子特征产物、DuckDB 只读查询及“规则实验”页面已完成基线。实验激活默认关闭，真实批量实验 worker、持续影子周和正式策略升级尚未上线，因此当前正式 V9.0 行为不受影响。

## 环境要求

- Python 3.12
- uv 0.12 或更高
- Node.js 22 或更高
- pnpm 11
- Docker Compose（仅容器集成时需要）

## 初始化

```bash
uv sync --all-groups
pnpm install
```

复制 `.env.example` 为 `.env` 后按需填写。`PAWE_BOOTSTRAP_ADMIN_PASSWORD` 只允许保存在本机且不得提交到 Git；正式部署前必须更换为至少 12 位的强密码。没有 OpenAI API Key 时保持 `PAWE_AI_ENABLED=false`，系统使用确定性 Mock/降级路径。

首次迁移后幂等创建管理员：

```bash
uv run alembic upgrade head
uv run python -m pawe_api.auth.bootstrap
```

默认管理员用户名为 `admin`，可通过 `PAWE_BOOTSTRAP_ADMIN_USERNAME` 修改。管理员拥有完整权限并可新增、停用普通用户；普通用户除管理本人最多 5 只自选外，其他业务内容保持只读。

## 开发命令

```bash
uv run uvicorn pawe_api.main:app --app-dir apps/api --reload
pnpm --filter @pawe/web dev
```

浏览器访问 `http://127.0.0.1:5173`。API 默认监听 `http://127.0.0.1:8000`。

## 检查与测试

```bash
uv run ruff check .
uv run mypy apps/api services/worker
uv run pytest
pnpm lint
pnpm test
pnpm build
```

## 数据与旧资料探测

从沪深交易所年度休市公告交叉校验并幂等写入指定自然周；默认 URL 当前对应 2026 年公告，跨年时必须显式更新来源与年份：

```bash
uv run python scripts/ingest_exchange_calendar.py 2026-08-10
```

只读探测腾讯主源与东方财富备用源，不写业务数据库：

```bash
uv run python scripts/probe_daily_sources.py sz300383 sh600519 --start 2025-02-21 --end 2025-02-28
```

完整抓取股票主数据当前严格要求沪深两市齐备、分页覆盖和上交所科创板交叉列表；北交所仍会尝试，但失败时记录降级并允许沪深原子批次继续。任何沪深拒绝行或来源冲突都会让事务在写入前停止。东方财富路线只作为显式补充，不会自动替代失败的官方市场。日线批处理默认最多 20 只，只有明确添加 `--all` 才处理全部正式范围；东方财富日线失败时按至少 2 秒间隔请求新浪备源，北交所行情适配仍待接入：

```bash
uv run python scripts/probe_official_stock_master.py SSE SZSE BSE
uv run python scripts/ingest_stock_master.py --observed-on 2026-08-09 --source official
uv run python scripts/ingest_stock_master.py --observed-on 2026-08-09 --source eastmoney
uv run python scripts/ingest_daily_bars.py --code 600519 --code 300750 --start 2026-04-01 --end 2026-08-07
uv run python scripts/ingest_daily_bars.py --v9-universe --available-on 2026-08-10 --published-by 2026-08-07 --start 2026-05-01 --end 2026-08-07
uv run python scripts/report_technical_features.py 600519 --as-of 2026-08-07 --cutoff 2026-08-09T23:59:59+08:00 --source eastmoney
uv run python scripts/materialize_technical_snapshot.py --as-of 2026-08-07 --available-on 2026-08-10 --decision-cutoff 2026-08-07T15:00:00+08:00 --fetched-by 2026-08-10T09:20:00+08:00 --code 600519 --code 300750
uv run python scripts/materialize_v9_inputs.py --snapshot-id <snapshot-uuid> --persist
```

不指定 `--code` 时，日线批处理会在 `data/snapshots/` 保存与起止日期绑定的断点文件；重复执行同一命令会从上次股票代码继续，使用 `--retry-failures` 单独重试失败队列。断点在单只股票事务提交后原子更新，即使进程中断，最多只会幂等重做一只。不要并行运行同一时间窗口的两个批次。

前复权日线按内容保存历史版本。`--v9-universe` 只处理在评价入口日有效、且在决策截止日已经公开的唯一正式主领域股票；缺失或冲突分类不会被默认值补齐。技术特征只读取快照锁定前已经抓取、且交易日不晚于决策截止日的版本，不会用后续公司行为修订后的价格覆盖原周度输入。腾讯与新浪日期、成交量和复权口径均一致时可达到 `verified`；若仅前复权因子造成价格差异，则使用腾讯价格和新浪成交额，固定标记 `single_source` 并保留降级原因；成交量或日期冲突仍排除。分类行情快照保存最近61个交易日的来源记录、分类时间边界、行业广度、波动率分位和派生指标；受限代码或 `--limit` 运行只能预览，只有 `--all --persist` 且正式分类范围零失败时才允许写入锁定快照。`materialize_v9_inputs.py` 将该快照转为保守 V9 输入：缺少公告、财务、催化或历史结果时一律不加分；缺少上一周状态数据时保留前态并显式降级，不推断新状态。

官方领域分类按“中上协行业底座 → 中证/国证主题证据 → PAWE 唯一主领域”分层保存。先预览，确认数量后显式加入 `--persist`：

```bash
uv run python scripts/ingest_classifications.py capco \
  --pdf /path/to/capco-industry.pdf \
  --evidence-url https://sp.capco.org.cn/path/to/capco-industry.pdf \
  --published-at 2026-04-03 \
  --valid-from 2026-04-03
uv run python scripts/ingest_classifications.py csi --all
uv run python scripts/ingest_classifications.py resolve --as-of 2026-08-09
```

`valid-from` 不得早于公开日期；官网动态样本文件只从实际抓取日开始用于回放。东方财富概念不具备正式领域授予权。同一证据层级命中多个领域时保持 `conflicted` 并排除，不按名称或模型常识猜测。

把旧项目 Markdown 的解析结果幂等写入隔离暂存表；所有记录保持 `legacy_unverified`，不得直接进入正式决策链：

```bash
uv run python scripts/stage_legacy_markdown.py /path/to/pick_a_weekly
```

用腾讯前复权日线复算一周，或可恢复地复算全部已暂存周；声明基准与 V9 首个交易日开盘基准会分开保存，单源匹配仍不等同于双源正式验证：

```bash
uv run python scripts/verify_legacy_week.py 2025-02-17
uv run python scripts/verify_legacy_batch.py
uv run python scripts/attribute_legacy_replay.py
uv run python scripts/report_legacy_replay_inventory.py
```

批处理默认跳过已处理记录，可用 `--start`、`--end`、`--limit` 缩小范围；只有调查后才应使用 `--force` 重算。
归因脚本不改变原验证状态；回放清单按整份发布名单保持原子性，默认只输出汇总，添加 `--details` 可查看逐周实验臂。

## 目录

- `apps/api`：FastAPI、契约和业务用例。
- `apps/web`：React Web。
- `services/worker`：周初、每日简报和周终任务入口。
- `packages/contracts`：前端共享类型。
- `tests`：确定性回归测试。
- `docs`：正式需求、规则、数据、AI 和评估契约。

完整设计入口见 [docs/README.md](docs/README.md)。

## 开源协作

- 许可证：[Apache License 2.0](LICENSE)
- 贡献与本地验证：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全问题报告：[SECURITY.md](SECURITY.md)
- 版本记录：[CHANGELOG.md](CHANGELOG.md)
- 正式产品、规则、数据和架构文档：[docs/README.md](docs/README.md)

请勿在 Issue、Pull Request、日志或截图中提交 API Key、密码、令牌、个人自选、完整账户信息或未经授权的第三方数据。外部贡献不得绕过 V9.0 硬约束、时点隔离、审批发布和审计要求。

AI 工作台提供周初候选分析、周终复盘解读、错误归因和规则迭代四项能力。用户可保存本人 OpenAI API Key；密钥只在服务端使用 AES-GCM 加密保存，前端只返回末四位提示，个人凭据优先于系统凭据。生产环境必须显式设置稳定的 `PAWE_AI_CREDENTIAL_ENCRYPTION_KEY`。没有个人或系统凭据时只写 `skipped/degraded` 审计，不生成 Mock 结论或改变正式名单；Mock 仅可由测试显式注入。错误归因使用 docs/03 的正式策略 taxonomy；规则迭代只能创建 `proposed` DSL 实验提案，不能直接验证、审批、激活或修改正式规则。调用审计只保存 provider、模型、指纹、schema/policy、结构化输出和警告，不保存完整提示词、API Key 或内部推理。

## 本机 Web 验证

启动容器后访问 `http://127.0.0.1:4173`。管理员可进入“决策管理”查看当前工作周版本，并人工触发周初名单、日报或周终复盘；人工任务与自动任务使用同一正式逻辑和审计链。周五收盘后和周末仍指向刚结束的交易周，直到下一交易周首个开市日才切换。后台按计划准备并执行，遗漏任务在正式周窗口内可补生成；所有补生成继续遵守原始数据截止点和实际获取时间审计。同一规则来源一经批准即关闭重复审批入口；只有完成审批和正式发布的数据库决策才会出现在驾驶舱。
