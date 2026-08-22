import { type FormEvent, type KeyboardEvent as ReactKeyboardEvent, useEffect, useState } from "react";
import type { Confidence, DailyBrief, DailyBriefItem, ErrorAttribution, MarketState, ReplayRun, ReplayStage, StockSearchResult, WatchlistDailyBrief, WatchlistItem, WatchlistWeeklyReview, WeekSummary, WeeklyDecisionItem, WeeklyReview, WeeklyReviewItem } from "@pawe/contracts";
import { activeDecisionWeekId, naturalWeekId, naturalWeekIdFromDateId, replayStageItemSummary, selectPrimaryReviewVersion, shanghaiDateId, weeklyReviewTargetWeekId, weeklySelectionDeadlinePassed } from "./week";

type Role = "admin" | "viewer";
type User = {
  id: string;
  username: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
};
type DecisionType = "rule" | "ai" | "published";
type DecisionVersion = {
  week_id: string;
  decision_type: DecisionType;
  version: number;
  status: string;
  fingerprint: string;
  source_type: string | null;
  source_version: number | null;
  items: Array<{ stock_code: string; stock_name: string; rank: number }>;
};
type WeeklyJob = {
  id: string;
  job_type: "weekly_selection" | "daily_brief" | "weekly_review" | "replay";
  week_id: string;
  mode?: "formal" | "replay";
  replay_stage?: ReplayStage | null;
  trade_date?: string | null;
  replay_run_id?: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  error_code: string | null;
  error_message: string | null;
  progress_percent: number;
  details: {
    events?: Array<{ stage: string; percent: number; at: string; message: string }>;
    candidate_count?: number;
    baseline_count?: number;
    reused?: boolean;
  };
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};
type ArchivedBrief = DailyBrief & { archive_source: "formal" | "historical_replay" };
type ReplayBriefPayload = { daily_briefs: DailyBrief[] };
type ReplayEligibility = { week_id: string; stage: ReplayStage; trade_dates: string[]; formal_available: boolean; replay_available: boolean; reason: string };
type TaskExecution = { mode: "formal" | "replay"; weekId: string; tradeDate?: string; fillMissing?: boolean };
type ExperimentSummary = {
  id: string;
  proposal_id: string;
  version: number;
  status: string;
  baseline_rule_version: string;
  candidate_rule_version: string;
  rollback_version: string;
  status_reason: string | null;
  updated_at: string;
};
type SourceCapability = {
  source_id: string;
  adapter_version: string;
  dataset: string;
  market_coverage: Record<string, unknown>;
  formal_eligibility: "formal" | "research_only" | "disabled";
  quality: string;
  fallback_priority: number;
  terms_reviewed_at: string | null;
  last_failure_reason: string | null;
};
type FeatureArtifact = {
  id: string;
  partition_key: string;
  feature_version: string;
  row_count: number;
  quality: string;
  status: "building" | "published" | "failed" | "cancelled";
  created_at: string;
};
type AICapability = "weekly_selection" | "weekly_review" | "error_attribution" | "rule_evolution";
type AIConnection = {
  connected: boolean;
  source: "personal_api_key" | "system_api_key" | "none";
  provider: "openai";
  key_hint: string | null;
  model: string;
  capabilities: Record<AICapability, boolean>;
  updated_at: string | null;
};
type AIAudit = {
  id: string;
  invocation_id: string;
  capability: AICapability;
  subject_type: string;
  subject_id: string;
  validation: { status?: string };
  warnings: string[];
  created_at: string;
};
type AIInvocation = {
  id: string;
  capability: AICapability;
  status: string;
  model: string;
  structured_output: Record<string, unknown> | null;
  error_message: string | null;
  created_at: string;
};
type AIProposal = { attribution_id: string; proposal_id: string | null; status: "proposed" | "rejected"; reason: string | null; created_at: string };

type AppView = "dashboard" | "approval" | "history" | "ai" | "experiments" | "users";

const viewCopy: Record<AppView, { title: string; eyebrow: string; description: string }> = {
  dashboard: {
    title: "本周研究驾驶舱",
    eyebrow: "WEEKLY RESEARCH",
    description: "结果优先，证据按需。这里只展示完成管理员审批并正式发布的本周名单。",
  },
  approval: {
    title: "决策管理",
    eyebrow: "DECISION LEDGER",
    description: "查看规则、AI 与人工版本，完成有理由、可审计、幂等的审批和发布。",
  },
  history: {
    title: "历史数据",
    eyebrow: "HISTORICAL REVIEW",
    description: "按周查看历史名单、逐标的表现、每日日报和周终评价。",
  },
  ai: {
    title: "AI 工作台",
    eyebrow: "BOUNDED AI",
    description: "关联个人 API，执行周初分析、复盘解读、错误归因与受控规则迭代。",
  },
  experiments: {
    title: "规则实验",
    eyebrow: "CONTROLLED EVOLUTION",
    description: "查看规则实验、数据源资格和特征产物；实验不会静默影响正式名单。",
  },
  users: {
    title: "用户管理",
    eyebrow: "ACCESS CONTROL",
    description: "管理员可新增或停用普通用户；普通用户可管理本人自选，其余内容只读。",
  },
};

const appNavigation: Array<{ view: AppView; label: string; icon: string; adminOnly?: boolean }> = [
  { view: "dashboard", label: "本周驾驶舱", icon: "◈" },
  { view: "approval", label: "决策管理", icon: "⌁", adminOnly: true },
  { view: "history", label: "历史数据", icon: "↺" },
  { view: "ai", label: "AI 工作台", icon: "✦" },
  { view: "experiments", label: "规则实验", icon: "⌬", adminOnly: true },
  { view: "users", label: "用户管理", icon: "◎", adminOnly: true },
];

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const pct = (value: number) => `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;

const confidenceLabel: Record<Confidence, string> = {
  high: "高置信度",
  medium: "中置信度",
  low: "低置信度",
};

const marketStateLabel: Record<MarketState, string> = {
  NORMAL: "常态",
  ANCHOR_DISTORTED: "锚点失真",
  SYSTEMIC_RETREAT: "系统性退潮",
  BREADTH_RECOVERY: "广度恢复",
  RECOVERY_CONFIRMED: "恢复确认",
  RECOVERY_FAILED: "恢复失败",
};

const marketStateMeaning: Record<MarketState, string> = {
  NORMAL: "市场广度、核心方向与风险指标没有触发特殊状态，按常态组合约束运行。",
  ANCHOR_DISTORTED: "少数权重或单一锚点主导指数表现，指数看似稳定但多数标的未同步，降低对单一指数信号的依赖。",
  SYSTEMIC_RETREAT: "主池与储备池同步走弱，风险具有系统性，规则会收紧入选与组合暴露。",
  BREADTH_RECOVERY: "上涨覆盖面开始修复，但持续性尚未确认，允许观察恢复候选但维持谨慎。",
  RECOVERY_CONFIRMED: "恢复信号得到连续数据确认，可以按恢复分支提高对有效方向的认可。",
  RECOVERY_FAILED: "此前的恢复没有延续，重新收紧组合，避免把短期反弹误判为趋势恢复。",
};

const confidenceMeaning: Record<Confidence, string> = {
  high: "数据完整、市场状态与候选证据一致，组合容量和约束均正常。它仍不代表收益保证。",
  medium: "主要约束通过，但仍有初始周、证据深度或状态确认方面的限制，需要人工复核。",
  low: "存在数据降级、候选不足、市场状态不稳定或其他重要限制；系统会明确提示并禁止自动发布。",
};

const scoreLabel: Record<string, string> = { price_structure: "价格结构", sector_strength: "方向强度", liquidity: "流动性", market_fit: "市场适配", history: "历史验证", fundamentals: "基本面", risk_quality: "风险与数据质量" };

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|; )pawe_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function api(path: string, init?: RequestInit) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.method && init.method !== "GET" ? { "X-CSRF-Token": csrfToken() } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const knownErrors: Record<string, string> = {
      "Invalid username or password": "用户名或密码错误",
      "Authentication service is temporarily unavailable": "认证服务暂不可用，请稍后重试",
    };
    throw new ApiError(response.status, knownErrors[body?.detail] ?? body?.detail ?? "请求失败，请稍后重试");
  }
  return response.status === 204 ? null : response.json();
}

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [view, setView] = useState<AppView>("dashboard");

  useEffect(() => {
    api("/api/v1/auth/me")
      .then((body) => setUser(body.user))
      .catch(() => setUser(null))
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <LoadingScreen />;
  if (!user) return <LoginScreen onLogin={setUser} />;

  const visibleNavigation = appNavigation.filter((item) => !item.adminOnly || user.role === "admin");
  const activeCopy = viewCopy[view];

  async function logout() {
    await api("/api/v1/auth/logout", { method: "POST" }).catch(() => null);
    setUser(null);
  }

  return (
    <main className="app-shell">
      <aside className="app-sidebar" aria-label="主导航">
        <button type="button" className="app-brand" onClick={() => setView("dashboard")} aria-label="返回主页">
          <span className="app-brand-mark">P</span>
          <span className="app-brand-copy"><strong>PAWE</strong><small>PICK A WEEKLY</small></span>
        </button>
        <nav className="app-nav">
          {visibleNavigation.map((item) => (
            <button
              type="button"
              key={item.view}
              className={view === item.view ? "app-nav-item is-active" : "app-nav-item"}
              aria-current={view === item.view ? "page" : undefined}
              onClick={() => setView(item.view)}
            >
              <span className="app-nav-icon" aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="app-account">
          <span className="app-account-avatar">{user.username.slice(0, 1).toUpperCase()}</span>
          <span className="app-account-copy"><strong>{user.username}</strong><small>{user.role === "admin" ? "管理员" : "普通用户"}</small></span>
          <button type="button" className="app-logout" onClick={() => void logout()}>退出</button>
        </div>
      </aside>

      <section className="app-main">
        <header className="app-topbar">
          <div>
            <p className="app-eyebrow">{activeCopy.eyebrow}</p>
            <h1>{activeCopy.title}</h1>
            <p className="app-description">{activeCopy.description}</p>
          </div>
          <div className="app-topbar-status"><span className="app-status-dot" aria-hidden="true" />本地研究系统</div>
        </header>
        <div className="app-page">
          {view === "history" ? <HistoryCenter /> : view === "ai" ? <AIWorkbench user={user} /> : view === "users" && user.role === "admin" ? <UserManagement currentUser={user} /> : view === "experiments" && user.role === "admin" ? <ExperimentHealthCenter /> : view === "approval" && user.role === "admin" ? <ApprovalCenter /> : <Dashboard />}
        </div>
      </section>
    </main>
  );
}

const aiCapabilityCopy: Record<AICapability, { title: string; description: string; action: string }> = {
  weekly_selection: { title: "周初名单分析", description: "只分析服务端规则候选，最多调整 2 个席位且不能突破硬约束。", action: "运行周初分析" },
  weekly_review: { title: "周终复盘解读", description: "基于已经落库的周终指标生成结构化解读，不改写确定性评价。", action: "解读周终复盘" },
  error_attribution: { title: "错误归因", description: "结合确定性事实与固定分类，形成可人工确认或驳回的错误假设。", action: "生成错误归因" },
  rule_evolution: { title: "规则迭代", description: "仅从已确认归因生成受限规则实验提案，不能直接修改正式 V9 规则。", action: "提出规则迭代" },
};

const attributionTaxonomyLabel: Record<string, string> = {
  market_state_error: "市场状态判断错误", rotation_lag: "轮动识别过慢", continuation_overreach: "延续判断过度",
  overheat_filter_loose: "过热过滤过松", overheat_filter_strict: "过热过滤过严", stock_selection_error: "个股选择错误",
  catalyst_error: "催化判断错误", confirmation_insufficient: "成交确认不足", data_anomaly: "数据异常",
  candidate_coverage_insufficient: "候选覆盖不足", anchor_distortion: "单一锚点扭曲", ai_swap_error: "AI 换入错误",
  human_override_error: "人工干预错误",
};

function AIWorkbench({ user }: { user: User }) {
  const [connection, setConnection] = useState<AIConnection | null>(null);
  const [weekId, setWeekId] = useState(weeklyReviewTargetWeekId());
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gpt-5.6-sol");
  const [audits, setAudits] = useState<AIAudit[]>([]);
  const [attributions, setAttributions] = useState<ErrorAttribution[]>([]);
  const [resolutionReason, setResolutionReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadConnection = () => api("/api/v1/ai/connection").then((row: AIConnection) => {
    setConnection(row);
    setModel(row.model);
  });
  const loadWeek = (targetWeek: string) => Promise.all([
    api(`/api/v1/weeks/${targetWeek}/attributions`).catch(() => []),
    api("/api/v1/ai/audits").catch(() => []),
  ]).then(([attributionRows, auditRows]: [ErrorAttribution[], AIAudit[]]) => {
    setAttributions(attributionRows);
    setAudits(auditRows.slice(0, 12));
  });

  useEffect(() => { void loadConnection().catch((reason) => setError(reason.message)); }, []);
  useEffect(() => { void loadWeek(weekId); }, [weekId]);

  async function saveConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("connection"); setError(""); setNotice("");
    try {
      const row = await api("/api/v1/ai/connection", { method: "POST", body: JSON.stringify({ api_key: apiKey, model }) });
      setConnection(row); setApiKey(""); setNotice("个人 API 凭据已加密保存，后续 AI 任务将优先使用该凭据。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "AI 凭据保存失败"); }
    finally { setBusy(null); }
  }

  async function removeConnection() {
    setBusy("connection"); setError(""); setNotice("");
    try {
      await api("/api/v1/ai/connection", { method: "DELETE" });
      await loadConnection(); setNotice("个人 API 凭据已移除。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "AI 凭据移除失败"); }
    finally { setBusy(null); }
  }

  async function runCapability(capability: AICapability) {
    setBusy(capability); setError(""); setNotice("");
    try {
      const result = await api("/api/v1/ai/tasks", { method: "POST", body: JSON.stringify({ capability, week_id: weekId }) }) as AIInvocation | AIProposal | ErrorAttribution;
      if ("taxonomy" in result) setNotice(`错误归因已生成：${attributionTaxonomyLabel[result.taxonomy] ?? result.taxonomy}。请核对证据后确认或驳回。`);
      else if ("proposal_id" in result) setNotice(result.status === "proposed" ? `规则实验提案已创建：${result.proposal_id}` : `规则迭代未创建：${result.reason ?? "未通过门禁"}`);
      else setNotice(`${aiCapabilityCopy[capability].title}已完成，结果与调用审计均已保存。`);
      await loadWeek(weekId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "AI 任务执行失败"); }
    finally { setBusy(null); }
  }

  async function resolveAttribution(item: ErrorAttribution, action: "confirm" | "reject") {
    if (resolutionReason.trim().length < 8) { setError("确认或驳回理由至少填写 8 个字符。"); return; }
    setBusy(`resolve-${item.id}`); setError(""); setNotice("");
    try {
      await api(`/api/v1/attributions/${item.id}/resolution`, { method: "POST", body: JSON.stringify({ action, reason: resolutionReason }) });
      setResolutionReason(""); setNotice(action === "confirm" ? "归因已确认，可进入规则迭代。" : "归因已驳回，不会作为规则迭代依据。");
      await loadWeek(weekId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "归因处理失败"); }
    finally { setBusy(null); }
  }

  const connected = Boolean(connection?.connected);
  const confirmedAttribution = attributions.some((item) => item.status === "confirmed");
  return (
    <section className="space-y-6 p-6 md:px-10 md:py-7">
      {(error || notice) && <p role={error ? "alert" : "status"} className={`rounded-xl px-4 py-3 text-sm ${error ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-900"}`}>{error || notice}</p>}
      <section className="grid gap-5 xl:grid-cols-[1.05fr_1.95fr]">
        <article className="rounded-2xl border border-black/10 bg-white p-5">
          <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">PERSONAL PROVIDER</p><h2 className="mt-2 text-xl font-semibold">OpenAI API 关联</h2></div><span className={`rounded-full px-3 py-1 text-xs font-semibold ${connected ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>{connected ? "已连接" : "未连接"}</span></div>
          <p className="mt-3 text-sm leading-6 text-slate-600">API Key 只发送到 PAWE 服务端并加密保存，页面不会再次显示完整密钥。ChatGPT 订阅不能直接代替 API 凭据。</p>
          {connection?.source === "personal_api_key" && <p className="mt-3 rounded-xl bg-[#f7f5ef] px-4 py-3 text-sm">个人凭据 {connection.key_hint} · 模型 {connection.model}</p>}
          {connection?.source === "system_api_key" && <p className="mt-3 rounded-xl bg-[#f7f5ef] px-4 py-3 text-sm">正在使用系统管理员配置的 API 凭据 · 模型 {connection.model}</p>}
          <form className="mt-5 space-y-3" onSubmit={saveConnection}>
            <label className="block text-sm font-medium" htmlFor="ai-api-key">OpenAI API Key</label>
            <input className="input" id="ai-api-key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} required minLength={20} placeholder="sk-…" />
            <label className="block text-sm font-medium" htmlFor="ai-model">模型</label>
            <input className="input" id="ai-model" value={model} onChange={(event) => setModel(event.target.value)} required />
            <div className="flex flex-wrap gap-2"><button type="submit" disabled={busy === "connection"} className="rounded-xl bg-[#173f35] px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">保存个人凭据</button>{connection?.source === "personal_api_key" && <button type="button" disabled={busy === "connection"} onClick={() => void removeConnection()} className="rounded-xl border border-red-200 px-4 py-2.5 text-sm font-semibold text-red-700 disabled:opacity-50">移除</button>}</div>
          </form>
        </article>
        <article className="rounded-2xl border border-black/10 bg-white p-5">
          <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">CAPABILITY RUNNER</p><h2 className="mt-2 text-xl font-semibold">指定交易周</h2></div><input aria-label="AI 任务交易周" type="date" value={weekId} onChange={(event) => setWeekId(naturalWeekIdFromDateId(event.target.value))} className="rounded-xl border border-black/15 bg-white px-4 py-2.5 text-sm" /></div>
          <p className="mt-3 text-xs leading-5 text-slate-500">日期会自动按其所在周处理。周初分析和规则迭代仅管理员可执行；所有输出保留模型、提示词、输入指纹和使用量审计。</p>
          <div className="mt-5 grid gap-3 md:grid-cols-2">{(Object.keys(aiCapabilityCopy) as AICapability[]).map((capability) => {
            const adminOnly = capability === "weekly_selection" || capability === "rule_evolution";
            const missingConfirmed = capability === "rule_evolution" && !confirmedAttribution;
            const disabled = !connected || !connection?.capabilities[capability] || (adminOnly && user.role !== "admin") || missingConfirmed || busy !== null;
            return <article key={capability} className="rounded-xl border border-black/10 bg-[#fbfaf7] p-4"><div className="flex items-start justify-between gap-2"><h3 className="font-semibold">{aiCapabilityCopy[capability].title}</h3>{adminOnly && <span className="rounded-full bg-violet-100 px-2 py-1 text-[10px] font-semibold text-violet-800">管理员</span>}</div><p className="mt-2 min-h-12 text-xs leading-5 text-slate-500">{aiCapabilityCopy[capability].description}</p><button type="button" disabled={disabled} onClick={() => void runCapability(capability)} className="mt-3 w-full rounded-lg bg-[#6a5fc1] px-3 py-2 text-xs font-semibold text-white disabled:bg-slate-300">{busy === capability ? "正在执行…" : missingConfirmed ? "需先确认错误归因" : aiCapabilityCopy[capability].action}</button></article>;
          })}</div>
        </article>
      </section>
      <section className="grid gap-5 xl:grid-cols-2">
        <article className="rounded-2xl border border-black/10 bg-white p-5"><h2 className="text-xl font-semibold">错误归因 · {weekId}</h2>{attributions.length === 0 ? <p className="mt-4 rounded-xl border border-dashed border-black/15 p-4 text-sm text-slate-500">该周尚无错误归因。需要先有周终复盘。</p> : <div className="mt-4 space-y-3">{attributions.map((item) => <article key={item.id} className="rounded-xl bg-[#f7f5ef] p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{attributionTaxonomyLabel[item.taxonomy] ?? item.taxonomy}</h3><span className="rounded-full bg-white px-2 py-1 text-xs">{item.status === "confirmed" ? "已确认" : item.status === "rejected" ? "已驳回" : "待复核"} · {item.confidence}</span></div><p className="mt-2 text-sm leading-6 text-slate-600">{item.proposed_hypothesis}</p>{item.status === "proposed" && user.role === "admin" && <div className="mt-3"><input aria-label="归因处理理由" className="input" value={resolutionReason} onChange={(event) => setResolutionReason(event.target.value)} placeholder="填写确认或驳回理由（至少 8 个字符）" /><div className="mt-2 flex gap-2"><button type="button" onClick={() => void resolveAttribution(item, "confirm")} className="rounded-lg bg-emerald-800 px-3 py-2 text-xs font-semibold text-white">确认归因</button><button type="button" onClick={() => void resolveAttribution(item, "reject")} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700">驳回</button></div></div>}</article>)}</div>}</article>
        <article className="rounded-2xl border border-black/10 bg-white p-5"><h2 className="text-xl font-semibold">最近 AI 审计</h2>{audits.length === 0 ? <p className="mt-4 rounded-xl border border-dashed border-black/15 p-4 text-sm text-slate-500">尚无 AI 调用记录。</p> : <div className="mt-4 space-y-2">{audits.map((item) => <article key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-black/10 px-4 py-3"><div><p className="text-sm font-semibold">{aiCapabilityCopy[item.capability]?.title ?? item.capability}</p><p className="mt-1 text-xs text-slate-500">{item.subject_id} · {formatTime(item.created_at)}</p></div><span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">{item.validation.status ?? "已记录"}</span></article>)}</div>}</article>
      </section>
    </section>
  );
}

function ExperimentHealthCenter() {
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [sources, setSources] = useState<SourceCapability[]>([]);
  const [artifacts, setArtifacts] = useState<FeatureArtifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([
      api("/api/v1/experiments"),
      api("/api/v1/health/source-capabilities"),
      api("/api/v1/health/features"),
    ])
      .then(([experimentRows, sourceRows, artifactRows]) => {
        setExperiments(experimentRows);
        setSources(sourceRows);
        setArtifacts(artifactRows);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "实验治理信息加载失败"))
      .finally(() => setLoading(false));
  }, []);
  if (loading) return <DashboardState title="正在加载实验治理信息…" detail="实验与正式规则保持隔离。" />;
  return (
    <section className="space-y-6 p-6 md:px-10 md:py-7">
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}
      <section>
        <div className="mb-3 flex items-end justify-between gap-3"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">RULE EXPERIMENTS</p><h2 className="mt-2 text-2xl font-semibold">规则实验</h2></div><p className="text-xs text-slate-500">激活默认关闭 · 必须人工批准</p></div>
        {experiments.length === 0 ? <p className="rounded-2xl border border-dashed border-black/15 bg-white p-6 text-sm text-slate-500">尚无通过静态校验的规则实验。</p> : <div className="grid gap-3 md:grid-cols-2">{experiments.map((item) => <article key={item.id} className="rounded-2xl border border-black/10 bg-white p-5"><div className="flex items-start justify-between gap-3"><div><p className="font-mono text-xs text-slate-500">{item.proposal_id}</p><h3 className="mt-1 font-semibold">{item.candidate_rule_version}</h3></div><span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">{experimentStatusLabel(item.status)}</span></div><p className="mt-3 text-sm leading-6 text-slate-600">基线 {item.baseline_rule_version} · 回退 {item.rollback_version}</p><p className="mt-2 text-xs text-slate-500">{item.status_reason ?? "等待下一项受控验证"}</p></article>)}</div>}
      </section>
      <section>
        <div className="mb-3"><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">SOURCE CAPABILITIES</p><h2 className="mt-2 text-2xl font-semibold">数据源能力</h2></div>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">{sources.map((source) => <article key={`${source.source_id}-${source.adapter_version}-${source.dataset}`} className="rounded-2xl border border-black/10 bg-white p-4"><div className="flex items-start justify-between gap-2"><div><h3 className="font-semibold">{source.source_id}</h3><p className="mt-1 text-xs text-slate-500">{source.dataset} · 优先级 {source.fallback_priority}</p></div><span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${source.formal_eligibility === "formal" ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>{source.formal_eligibility === "formal" ? "正式可用" : source.formal_eligibility === "research_only" ? "仅研究" : "停用"}</span></div><p className="mt-3 text-xs text-slate-500">质量 {source.quality} · 条款复核 {source.terms_reviewed_at ?? "待补充"}</p>{source.last_failure_reason && <p className="mt-2 text-xs leading-5 text-amber-800">{source.last_failure_reason}</p>}</article>)}</div>
      </section>
      <section>
        <div className="mb-3"><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">FEATURE ARTIFACTS</p><h2 className="mt-2 text-2xl font-semibold">特征产物</h2></div>
        {artifacts.length === 0 ? <p className="rounded-2xl border border-dashed border-black/15 bg-white p-6 text-sm text-slate-500">尚无 Parquet 特征分区；当前正式规则继续读取 PostgreSQL 冻结特征。</p> : <div className="space-y-2">{artifacts.map((artifact) => <article key={artifact.id} className="flex flex-col justify-between gap-2 rounded-xl border border-black/10 bg-white px-4 py-3 sm:flex-row sm:items-center"><div><p className="font-semibold">{artifact.partition_key}</p><p className="mt-1 text-xs text-slate-500">{artifact.feature_version} · {artifact.row_count} 行 · {artifact.quality}</p></div><span className="text-sm font-semibold text-emerald-800">{artifact.status}</span></article>)}</div>}
      </section>
    </section>
  );
}

function experimentStatusLabel(status: string) {
  return ({ schema_validated: "已静态校验", replay_queued: "回放排队", replay_running: "回放中", replay_passed: "回放通过", replay_rejected: "回放拒绝", shadow_ready: "可进入影子", shadow_running: "影子运行", awaiting_approval: "等待批准", approved: "已批准", activated: "已激活", rolled_back: "已回滚" } as Record<string, string>)[status] ?? status;
}

function ApprovalCenter() {
  const weekId = activeDecisionWeekId();
  const outputWeekId = naturalWeekId();
  const reviewWeekId = weeklyReviewTargetWeekId();
  const today = shanghaiDateId();
  const [decisions, setDecisions] = useState<DecisionVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<WeeklyJob | null>(null);
  const [jobs, setJobs] = useState<WeeklyJob[]>([]);
  const [taskToConfirm, setTaskToConfirm] = useState<WeeklyJob["job_type"] | null>(null);
  const [replayEligibility, setReplayEligibility] = useState<ReplayEligibility[]>([]);

  const load = () => Promise.all([
    api(`/api/v1/weeks/${weekId}/decisions`),
    api(`/api/v1/weeks/${weekId}/jobs`),
    outputWeekId === weekId ? Promise.resolve([]) : api(`/api/v1/weeks/${outputWeekId}/jobs`),
    reviewWeekId === weekId || reviewWeekId === outputWeekId ? Promise.resolve([]) : api(`/api/v1/weeks/${reviewWeekId}/jobs`),
  ]).then(([decisionRows, currentJobs, outputJobs, reviewJobs]) => {
    const jobRows = [...currentJobs, ...outputJobs, ...reviewJobs].sort((left: WeeklyJob, right: WeeklyJob) => right.created_at.localeCompare(left.created_at));
    setDecisions(decisionRows);
    setJobs(jobRows);
    setJob(jobRows[0] ?? null);
  }).catch((reason) => setError(reason instanceof Error ? reason.message : "决策版本加载失败"));
  useEffect(() => {
    load().finally(() => setLoading(false));
  }, [weekId, outputWeekId, reviewWeekId]);
  const loadReplayEligibility = () => api("/api/v1/replays/eligible-weeks").then((rows: ReplayEligibility[]) => setReplayEligibility(rows));
  useEffect(() => {
    void loadReplayEligibility().catch(() => {
      setReplayEligibility([]);
      setError("目标周交易日历尚未可靠准备，暂不提供生成或历史回溯选项。");
    });
  }, []);
  async function prepareCalendar(candidateWeek: string) {
    setSubmitting(true);
    setError("");
    try {
      const result = await api("/api/v1/replays/prepare-calendar", {
        method: "POST",
        body: JSON.stringify({ week_id: candidateWeek }),
      }) as { status: string; warnings?: string[] };
      await loadReplayEligibility();
      if (result.status === "unavailable") setError(result.warnings?.join("；") ?? "交易日历准备失败，未猜测节假日。");
      else setNotice(result.status === "refreshed" ? "已使用官方/备份交易日历准备目标周，质量已记录。" : "目标周交易日历已就绪。");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "交易日历准备失败");
    } finally {
      setSubmitting(false);
    }
  }
  const hasRunningJob = jobs.some((item) => ["queued", "running"].includes(item.status));
  const publishedDecisions = decisions.filter((decision) => decision.decision_type === "published" && decision.status === "published");
  useEffect(() => {
    if (!hasRunningJob) return;
    const timer = window.setInterval(() => void load(), 1200);
    return () => window.clearInterval(timer);
  }, [hasRunningJob, weekId, outputWeekId, reviewWeekId]);

  async function runWeeklySelection() {
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const result = await api("/api/v1/jobs/weekly-selection", {
        method: "POST",
        body: JSON.stringify({ week_id: weekId, idempotency_key: crypto.randomUUID() }),
      });
      setJob(result);
      if (result.status === "failed") {
        setError(jobErrorLabel(result.error_code));
      } else if (result.status === "succeeded") {
        setNotice("本周周初任务已经执行过，本次未重复执行。");
      } else {
        setNotice(result.status === "queued" ? "任务已排队，页面会自动更新进度。" : "周初任务正在运行。");
      }
      await load();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "周初任务启动失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function runManualOutput(jobType: "daily_brief" | "weekly_review") {
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const result = await api("/api/v1/jobs/output", {
        method: "POST",
        body: JSON.stringify({
          job_type: jobType,
          week_id: jobType === "weekly_review" ? reviewWeekId : outputWeekId,
          ...(jobType === "daily_brief" ? { trade_date: today } : {}),
          idempotency_key: crypto.randomUUID(),
        }),
      });
      setJob(result);
      if (result.status === "failed") setError(jobErrorLabel(result.error_code));
      else if (result.status === "succeeded") setNotice(`本周${jobType === "daily_brief" ? "日报" : "周终复盘"}已经生成，本次未重复执行。`);
      await load();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "产出任务启动失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function runReplay(execution: TaskExecution) {
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const result = await api("/api/v1/jobs/replay", {
        method: "POST",
        body: JSON.stringify({
          stage: "weekly_review",
          week_id: execution.weekId,
          idempotency_key: crypto.randomUUID(),
        }),
      });
      setJob(result);
      if (result.status === "failed") setError(result.error_message ?? "历史回溯任务已安全停止。");
      else setNotice("整周历史回溯已排队，将依次生成周初名单、全部日报和周终复盘，完成后归档至历史数据。");
      await load();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "历史回溯任务启动失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <DashboardState title="正在加载决策版本…" detail={`当前自然周：${weekId}`} />;

  return (
    <section className="p-6 md:px-10 md:py-6">
      <div className="flex flex-col gap-4 border-b border-black/10 pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">WEEKLY APPROVAL</p><h2 className="mt-2 text-2xl font-semibold">{weekId} 决策版本</h2><p className="mt-1 text-xs text-slate-500">共 {publishedDecisions.length} 个正式发布版本</p></div>
        <div className="flex flex-wrap gap-2">
          <button disabled={submitting || hasActiveJob(jobs, "weekly_selection")} onClick={() => setTaskToConfirm("weekly_selection")} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700 disabled:opacity-50">周初名单</button>
          <button disabled={submitting || hasActiveJob(jobs, "daily_brief")} onClick={() => setTaskToConfirm("daily_brief")} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700 disabled:opacity-50">每日简报</button>
          <button disabled={submitting || hasActiveJob(jobs, "weekly_review")} onClick={() => setTaskToConfirm("weekly_review")} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700 disabled:opacity-50">周终复盘 · {reviewWeekId}</button>
        </div>
      </div>
      {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-2.5 text-sm text-red-800">{error}</p>}
      {(notice || job) && <p className={`mt-3 rounded-xl px-4 py-2.5 text-sm ${job?.status === "failed" ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>{notice || persistentJobMessage(job)}</p>}
      <div className="mt-4 grid items-start gap-4 lg:grid-cols-2">
        <div>
          {publishedDecisions.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-black/15 bg-white p-8 text-center"><h3 className="text-xl font-semibold">本周尚无正式发布版本</h3><p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-slate-600">这里只展示已经完成确认并正式发布的本周名单，等待发布和中间规则版本不再显示。</p></div>
          ) : publishedDecisions.map((decision) => (
            <article key={`${decision.decision_type}-${decision.version}`} className="rounded-2xl border border-black/10 bg-white p-5">
              <div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">{decisionTypeLabel(decision.decision_type)} · V{decision.version}</p><h3 className="mt-1 text-lg font-semibold">{decisionStatusLabel(decision.status)}</h3></div><span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">{decision.items.length} 只</span></div>
              <ol className="mt-4 space-y-1.5">{decision.items.map((item) => <li key={item.stock_code} className="flex justify-between rounded-xl bg-[#f7f5ef] px-4 py-2.5 text-sm"><span>{item.rank}. {item.stock_name}</span><span className="font-mono text-slate-500">{item.stock_code}</span></li>)}</ol>
            </article>
          ))}
        </div>
        {job ? <JobProgress job={job} history={jobs} /> : <div className="rounded-2xl border border-dashed border-black/15 bg-white p-8 text-center"><h3 className="text-lg font-semibold">暂无任务审计</h3><p className="mt-2 text-sm text-slate-500">执行周初、日报或周终任务后在这里查看进度。</p></div>}
      </div>
      {taskToConfirm && <ManualTaskConfirmation taskType={taskToConfirm} targetWeekId={taskToConfirm === "weekly_review" ? reviewWeekId : taskToConfirm === "weekly_selection" ? weekId : outputWeekId} replayEligibility={replayEligibility} onPrepareCalendar={(candidateWeek) => void prepareCalendar(candidateWeek)} onClose={() => setTaskToConfirm(null)} onConfirm={(execution) => { const task = taskToConfirm; setTaskToConfirm(null); if (execution.mode === "replay") void runReplay(execution); else if (task === "weekly_selection") void runWeeklySelection(); else if (task !== "replay") void runManualOutput(task); }} />}
    </section>
  );
}

function ManualTaskConfirmation({ taskType, targetWeekId, replayEligibility, onPrepareCalendar, onClose, onConfirm }: { taskType: WeeklyJob["job_type"]; targetWeekId: string; replayEligibility: ReplayEligibility[]; onPrepareCalendar: (weekId: string) => void; onClose: () => void; onConfirm: (execution: TaskExecution) => void }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  const taskLabel = taskType === "weekly_selection" ? "周初" : taskType === "daily_brief" ? "日报" : "周终";
  const publicationDeadlinePassed = taskType === "weekly_selection" && weeklySelectionDeadlinePassed(targetWeekId);
  const allowReplay = taskType === "weekly_selection";
  const replayOptions = allowReplay ? replayEligibility.filter((item) => item.stage === "weekly_review" && item.replay_available) : [];
  const currentEligibility = replayEligibility.find((item) => item.week_id === targetWeekId && item.stage === taskType);
  const currentReady = currentEligibility !== undefined && (currentEligibility.formal_available || currentEligibility.replay_available);
  const [mode, setMode] = useState<"formal" | "replay">("formal");
  const [replayWeekId, setReplayWeekId] = useState(targetWeekId);
  useEffect(() => {
    const preferred = replayOptions.find((item) => item.week_id === targetWeekId)?.week_id ?? "";
    if (mode === "replay" && !replayOptions.some((item) => item.week_id === replayWeekId)) setReplayWeekId(preferred);
  }, [mode, replayOptions, replayWeekId, targetWeekId]);
  const selectedReplay = replayOptions.find((item) => item.week_id === replayWeekId);
  const canConfirm = mode === "formal" ? currentReady : allowReplay && selectedReplay !== undefined;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/45 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={`执行本周${taskLabel}任务`}>
      <button type="button" aria-label="关闭任务确认" onClick={onClose} className="absolute inset-0 cursor-default" />
      <section className="relative z-10 w-full max-w-md rounded-[28px] bg-[#fcfbf7] p-7 shadow-2xl">
        <p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">MANUAL TASK</p>
        <h2 className="mt-2 text-2xl font-semibold">执行本周{taskLabel}任务</h2>
        <p className="mt-3 text-sm leading-6 text-slate-600">目标周：{mode === "formal" ? targetWeekId : replayWeekId}。{mode === "replay" ? "整周回溯会依次生成周初名单、全部交易日日报和周终复盘，只写入隔离历史数据。" : "在下一交易周开始前均按当周正式任务处理；补生成仍严格使用原定数据截止点。"}</p>
        {allowReplay && <div className="mt-4 grid grid-cols-2 gap-2 rounded-xl bg-slate-100 p-1">
          <button type="button" className={`rounded-lg px-3 py-2 text-sm font-semibold ${mode === "formal" ? "bg-white shadow-sm" : "text-slate-500"}`} onClick={() => setMode("formal")}>生成本周（正式）</button>
          <button type="button" className={`rounded-lg px-3 py-2 text-sm font-semibold ${mode === "replay" ? "bg-white shadow-sm" : "text-slate-500"}`} onClick={() => setMode("replay")}>历史回溯</button>
        </div>}
        {mode === "replay" && <div className="mt-4 space-y-3">
          <label className="block text-sm font-medium">回溯自然周<select className="input mt-1" value={replayWeekId} onChange={(event) => setReplayWeekId(event.target.value)}><option value="">请选择可用周</option>{replayOptions.map((item) => <option key={item.week_id} value={item.week_id}>{item.week_id}</option>)}</select></label>
          {replayOptions.length === 0 && <p className="rounded-xl bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">暂无质量合格的交易日历，已阻止提交，避免把目标周误当作可生成周。</p>}
          {selectedReplay && <p className="rounded-xl bg-emerald-50 px-4 py-3 text-sm leading-6 text-emerald-900">该周回溯完成后会作为一个完整周归档到历史数据，不会在决策管理中展开显示。</p>}
        </div>}
        {!currentReady && (mode === "formal" || replayWeekId === "") && <div className="mt-4 rounded-xl bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900"><p>目标周交易日历尚未准备，系统不会猜测节假日。</p><button type="button" onClick={() => onPrepareCalendar(targetWeekId)} className="mt-2 rounded-lg bg-amber-900 px-3 py-2 text-xs font-semibold text-white">加载官方/备份日历</button></div>}
        {publicationDeadlinePassed && mode === "formal" && <p className="mt-3 rounded-xl bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">这是当周正式补生成：实际运行时间会保留，但周初名单仍只读取本周开始前已经公开的数据。</p>}
        <div className="mt-6 flex justify-end gap-3"><button type="button" onClick={onClose} className="rounded-xl border border-black/15 px-4 py-2.5 text-sm font-semibold">取消</button><button type="button" disabled={!canConfirm} onClick={() => onConfirm({ mode, weekId: mode === "formal" ? targetWeekId : replayWeekId })} className="rounded-xl bg-emerald-800 px-4 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">确定执行</button></div>
      </section>
    </div>
  );
}

function decisionTypeLabel(type: DecisionType) { return type === "rule" ? "规则版" : type === "ai" ? "AI版" : "正式发布版"; }
function decisionStatusLabel(status: string) { return ({ awaiting_approval: "等待审批", approved: "已批准，等待发布", published: "已正式发布", superseded: "已被替代" } as Record<string, string>)[status] ?? status; }
const jobStageLabel: Record<string, string> = {
  queued: "等待后台领取",
  calendar_gate: "核对交易日历",
  publication_gate: "核对数据与发布窗口",
  snapshot_gate: "核对冻结快照",
  feature_gate: "核对 V9 特征",
  state_gate: "核对市场状态输入",
  classification_gate: "核对领域分类",
  rule_execution: "运行 V9 规则",
  result_persistence: "保存候选和版本",
  rule_gate: "规则结果检查",
  awaiting_approval: "等待人工审批",
  decision_ready: "决策结果就绪",
  internal_error: "安全停止",
  daily_gate: "核对日报生成条件",
  daily_data_fetch: "抓取收盘行情",
  daily_brief_ready: "日报已生成",
  review_gate: "核对周终复盘条件",
  review_data_fetch: "补齐周终行情",
  review_generation: "计算周终指标",
  weekly_review_ready: "周终复盘已生成",
  output_error: "产出任务安全停止",
};

const jobTypeLabel: Record<WeeklyJob["job_type"], string> = {
  weekly_selection: "周初名单",
  daily_brief: "每日日报",
  weekly_review: "周终复盘",
  replay: "历史回溯",
};

function hasActiveJob(jobs: WeeklyJob[], type: WeeklyJob["job_type"]) {
  return jobs.some((item) => item.job_type === type && ["queued", "running"].includes(item.status));
}

function persistentJobMessage(job: WeeklyJob | null) {
  if (!job) return "";
  const events = job.details.events ?? [];
  const latest = events.at(-1)?.message;
  if (latest) return `${jobTypeLabel[job.job_type]}：${latest}`;
  return `${jobTypeLabel[job.job_type]}：${job.status === "succeeded" ? "任务已完成。" : job.status === "failed" ? jobErrorLabel(job.error_code) : "任务正在执行。"}`;
}

function JobProgress({ job, history }: { job: WeeklyJob; history: WeeklyJob[] }) {
  const events = job.details.events ?? [];
  const statusLabel = ({ queued: "排队中", running: "运行中", succeeded: "已完成", failed: "已停止", cancelled: "已取消" } as const)[job.status];
  return (
    <details className="rounded-2xl border border-black/10 bg-white p-5" aria-live="polite">
      <summary className="cursor-pointer list-none">
        <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="text-xs font-semibold tracking-[0.16em] text-emerald-800">任务进度与审计 · {jobTypeLabel[job.job_type]}</p><h3 className="mt-2 text-lg font-semibold">{jobStageLabel[job.stage] ?? job.stage}</h3></div>
        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${job.status === "failed" ? "bg-red-100 text-red-800" : job.status === "succeeded" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>{statusLabel} · {job.progress_percent}%</span>
        </div>
      </summary>
      <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full transition-all duration-500 ${job.status === "failed" ? "bg-red-600" : "bg-emerald-700"}`} style={{ width: `${job.progress_percent}%` }} /></div>
      <ol className="mt-5 space-y-3 border-l border-black/10 pl-4">
        {events.map((event, index) => <li key={`${event.stage}-${event.at}-${index}`}><div className="flex flex-wrap items-baseline justify-between gap-2"><p className="text-sm font-medium">{jobStageLabel[event.stage] ?? event.stage}</p><time className="text-xs text-slate-400">{formatTime(event.at)}</time></div><p className="mt-1 text-xs leading-5 text-slate-500">{event.message}</p></li>)}
      </ol>
      {job.status === "succeeded" && <p className="mt-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{job.job_type === "weekly_selection" ? job.details.reused ? `已复用本周现有 ${job.details.baseline_count ?? decisionsCountFallback(job)} 只规则结果，没有创建重复决策版本。` : `已形成 ${job.details.baseline_count ?? decisionsCountFallback(job)} 只规则基线标的；结果位于下方“决策版本”，仍需人工审批和正式发布。` : persistentJobMessage(job)}</p>}
      {job.status === "failed" && <p className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">{jobErrorLabel(job.error_code)}</p>}
      <details className="mt-5 border-t border-black/10 pt-4">
        <summary className="cursor-pointer text-sm font-medium">历史运行记录（{history.length}）</summary>
        <div className="mt-3 space-y-2">{history.map((item) => <div key={item.id} className="flex flex-col gap-1 rounded-xl bg-[#f7f5ef] px-4 py-3 text-xs sm:flex-row sm:items-center sm:justify-between"><span>{formatTime(item.created_at)} · {jobTypeLabel[item.job_type]} · {jobStageLabel[item.stage] ?? item.stage}</span><span className="text-slate-500">{item.status === "succeeded" ? "完成" : item.status === "failed" ? `停止 · ${item.error_code ?? "未知原因"}` : item.status === "running" ? `运行中 · ${item.progress_percent}%` : "排队中"}</span></div>)}</div>
      </details>
    </details>
  );
}

function formatTime(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(value)); }
function decisionsCountFallback(job: WeeklyJob) { return job.details.candidate_count ? Math.min(job.details.candidate_count, 5) : 0; }
function jobErrorLabel(code: string | null) { return ({ CALENDAR_MISSING: "周初任务已安全停止：本周交易日历尚不完整。", CALENDAR_DEGRADED: "周初任务已安全停止：交易日历质量不足，需要人工核验。", CALENDAR_INELIGIBLE: "本自然周少于 3 个交易日，不生成周度名单。", PREVIOUS_OPEN_DATE_MISSING: "周初任务已安全停止：缺少首个交易日前一交易日，无法确定数据截止点。", PREPARATION_WINDOW_NOT_OPEN: "周初任务已安全停止：上一交易日尚未收盘，准备窗口尚未开放。", NOT_FIRST_TRADING_DAY: "周初任务已安全停止：当前不是本周首个交易日。", FORMAL_WEEK_ENDED: "下一交易周已经开始，请改用历史回溯。", SNAPSHOT_MISSING: "周初任务已安全停止：缺少决策截止时点的有效冻结快照。", FEATURE_SET_MISSING: "周初任务已安全停止：冻结快照存在，但版本化 V9 特征集尚未生成。", STATE_INPUT_MISSING: "周初任务已安全停止：缺少版本化 V9 市场状态输入。", RULE_EXECUTION_PENDING: "V9 输入已通过门禁；规则结果持久化将在下一阶段接入。", OUTPUT_NOT_READY: "产出任务尚未到允许生成的时间。", OUTPUT_NOT_AVAILABLE: "产出任务缺少交易日、正式名单或完整行情数据。", OUTPUT_GENERATION_FAILED: "产出任务已安全停止，请展开审计记录查看失败阶段。" } as Record<string, string>)[code ?? ""] ?? "任务未能生成产出物，请查看任务审计。"; }

function LoginScreen({ onLogin }: { onLogin: (user: User) => void }) {
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    const data = new FormData(event.currentTarget);
    try {
      const body = await api("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: data.get("username"), password: data.get("password") }),
      });
      onLogin(body.user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  function submitOnEnter(event: ReactKeyboardEvent<HTMLFormElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing || submitting) return;
    event.preventDefault();
    event.currentTarget.requestSubmit();
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="login-story">
          <div className="login-brand"><span>P</span><strong>PAWE</strong></div>
          <p className="text-xs font-semibold tracking-[0.24em] text-[#c2ef4e]">PICK A WEEKLY</p>
          <h1 className="mt-6 text-4xl font-semibold leading-tight tracking-tight md:text-5xl">研究有边界，<br />决策可验证。</h1>
          <p className="mt-6 max-w-sm text-sm leading-7 text-white/65">
            面向 A 股的 AI 周度研究系统。所有规则、调整、确认与复盘都保留可审计记录。
          </p>
        </div>
        <form className="login-form" onSubmit={submit} onKeyDown={submitOnEnter}>
          <p className="text-xs font-semibold tracking-[0.2em] text-[#6a5fc1]">SECURE ACCESS</p>
          <h2 className="mt-3 text-3xl font-semibold">登录 PAWE</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">请输入管理员为你创建的账户。</p>
          <label className="mt-8 block text-sm font-medium" htmlFor="username">用户名</label>
          <input className="input" id="username" name="username" autoComplete="username" required minLength={3} autoFocus />
          <label className="mt-5 block text-sm font-medium" htmlFor="password">密码</label>
          <input className="input" id="password" name="password" type="password" autoComplete="current-password" required />
          {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}
          <button type="submit" className="login-submit" disabled={submitting}>
            {submitting ? "正在验证…" : "登录"}
          </button>
          <p className="mt-5 text-xs leading-5 text-slate-400">研究内容不构成收益保证或交易指令。</p>
        </form>
      </section>
    </main>
  );
}

function UserManagement({ currentUser }: { currentUser: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = () => api("/api/v1/users").then(setUsers).catch((reason) => setError(reason.message));
  useEffect(() => {
    void load();
  }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api("/api/v1/users", {
        method: "POST",
        body: JSON.stringify({ username: data.get("username"), password: data.get("password"), role: "viewer" }),
      });
      form.reset();
      setNotice("普通用户已创建。请通过安全渠道告知其初始密码。");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    }
  }

  return (
    <div className="grid gap-8 p-6 md:grid-cols-[0.8fr_1.2fr] md:p-10">
      <form className="rounded-2xl border border-black/10 bg-white p-6" onSubmit={create}>
        <p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">NEW VIEWER</p>
        <h2 className="mt-2 text-2xl font-semibold">新增普通用户</h2>
        <label className="mt-6 block text-sm font-medium" htmlFor="new-username">用户名</label>
        <input className="input" id="new-username" name="username" required minLength={3} pattern="[a-zA-Z0-9_.-]+" />
        <label className="mt-5 block text-sm font-medium" htmlFor="new-password">初始密码</label>
        <input className="input" id="new-password" name="password" type="password" required minLength={12} autoComplete="new-password" />
        <p className="mt-2 text-xs text-slate-500">至少 12 个字符；系统只保存不可逆密码哈希。</p>
        {error && <p role="alert" className="mt-4 text-sm text-red-700">{error}</p>}
        {notice && <p className="mt-4 text-sm text-emerald-700">{notice}</p>}
        <button className="mt-6 rounded-xl bg-slate-900 px-5 py-3 font-semibold text-white">创建只读用户</button>
      </form>

      <section>
        <div className="mb-4 flex items-end justify-between">
          <div><p className="text-xs font-semibold tracking-[0.18em] text-slate-500">ACCOUNTS</p><h2 className="mt-2 text-2xl font-semibold">现有用户</h2></div>
          <span className="text-sm text-slate-500">{users.length} 个账户</span>
        </div>
        <div className="space-y-3">
          {users.map((item) => (
            <article key={item.id} className="flex flex-col gap-4 rounded-2xl border border-black/10 bg-white p-5 sm:flex-row sm:items-center sm:justify-between">
              <div><p className="font-semibold">{item.username}</p><p className="mt-1 text-sm text-slate-500">{item.role === "admin" ? "管理员 · 完整权限" : "普通用户 · 只读"}</p></div>
              <div className="flex items-center gap-3">
                <span className={`rounded-full px-3 py-1 text-xs font-semibold ${item.is_active ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-600"}`}>{item.is_active ? "启用" : "停用"}</span>
                {item.role === "viewer" && item.id !== currentUser.id && (
                  <button className="rounded-lg border border-black/15 px-3 py-2 text-sm" onClick={async () => { await api(`/api/v1/users/${item.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !item.is_active }) }); await load(); }}>
                    {item.is_active ? "停用" : "启用"}
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

function HistoryCenter() {
  const [reviews, setReviews] = useState<WeeklyReview[]>([]);
  const [archiveWeeks, setArchiveWeeks] = useState<string[]>([]);
  const [selectedWeek, setSelectedWeek] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ review: WeeklyReview; item: WeeklyReviewItem } | null>(null);
  const [briefsByWeek, setBriefsByWeek] = useState<Record<string, ArchivedBrief[]>>({});
  const [decisionsByWeek, setDecisionsByWeek] = useState<Record<string, DecisionVersion[]>>({});
  const [replaysByWeek, setReplaysByWeek] = useState<Record<string, ReplayRun[]>>({});
  const [attributionsByReview, setAttributionsByReview] = useState<Record<string, ErrorAttribution[]>>({});
  const [briefLoadingWeek, setBriefLoadingWeek] = useState<string | null>(null);
  const [briefErrorByWeek, setBriefErrorByWeek] = useState<Record<string, string>>({});
  const [expandedBriefDay, setExpandedBriefDay] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api("/api/v1/history/weeks"), api("/api/v1/reviews")])
      .then(([weeks, rows]: [string[], WeeklyReview[]]) => {
        const latestCompletedWeek = weeklyReviewTargetWeekId();
        setArchiveWeeks(weeks.filter((weekId) => weekId <= latestCompletedWeek));
        setReviews(rows);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "历史数据加载失败"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedWeek) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedWeek(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selectedWeek]);

  async function loadBriefs(weekId: string) {
    if (briefsByWeek[weekId] || briefLoadingWeek === weekId) return;
    setBriefLoadingWeek(weekId);
    const formalRequest = api(`/api/v1/weeks/${weekId}/briefs`)
      .then((rows: DailyBrief[]) => rows)
      .catch((reason) => {
        if (reason instanceof ApiError && reason.status === 404) return [];
        throw reason;
      });
    const replayRequest = api(`/api/v1/replays/${weekId}`)
      .then((payload: ReplayBriefPayload) => payload.daily_briefs)
      .catch((reason) => {
        if (reason instanceof ApiError && reason.status === 404) return [];
        throw reason;
      });
    const decisionRequest = api(`/api/v1/weeks/${weekId}/decisions`) as Promise<DecisionVersion[]>;
    const stagedReplayRequest = api(`/api/v1/weeks/${weekId}/replays`) as Promise<ReplayRun[]>;
    try {
      const [formal, replay, decisions, stagedReplays] = await Promise.all([formalRequest, replayRequest, decisionRequest, stagedReplayRequest]);
      const merged: ArchivedBrief[] = [
        ...formal.map((brief) => ({ ...brief, archive_source: "formal" as const })),
        ...replay.map((brief) => ({ ...brief, archive_source: "historical_replay" as const })),
      ].sort((left, right) => right.trade_date.localeCompare(left.trade_date));
      setBriefsByWeek((current) => ({ ...current, [weekId]: merged }));
      setDecisionsByWeek((current) => ({ ...current, [weekId]: decisions }));
      setReplaysByWeek((current) => ({ ...current, [weekId]: stagedReplays }));
    } catch (reason) {
      setBriefErrorByWeek((current) => ({ ...current, [weekId]: reason instanceof Error ? reason.message : "每日报告加载失败" }));
    } finally {
      setBriefLoadingWeek((current) => current === weekId ? null : current);
    }
  }

  async function runAttribution(review: WeeklyReview) {
    try {
      await api("/api/v1/ai/tasks", { method: "POST", body: JSON.stringify({ capability: "error_attribution", review_id: review.id }) });
      const rows = await api(`/api/v1/weeks/${review.week_id}/attributions`) as ErrorAttribution[];
      setAttributionsByReview((current) => ({ ...current, [review.id]: rows.filter((row) => row.review_id === review.id) }));
    } catch { /* The deterministic review remains visible when AI is unavailable. */ }
  }

  if (loading) return <DashboardState title="正在加载历史数据…" detail="正在按周整理复盘记录。" />;
  if (error) return <DashboardState title="历史数据加载失败" detail={error} tone="error" />;
  if (!archiveWeeks.length) return <DashboardState title="暂无历史数据" detail="交易周产生正式名单、日报或周终复盘后，记录会按周出现在这里。" />;

  const grouped = reviews.reduce<Record<string, WeeklyReview[]>>((result, review) => {
    (result[review.week_id] ??= []).push(review);
    return result;
  }, {});
  const weeks = archiveWeeks.map((weekId) => [weekId, grouped[weekId] ?? []] as const);

  return (
    <section className="px-6 py-6 md:px-10 md:py-8">
      <div className="mb-5 flex items-end justify-between gap-4">
        <div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">WEEKLY ARCHIVE</p><h2 className="mt-2 text-2xl font-semibold">按周归档</h2></div>
        <p className="text-sm text-slate-500">共 {weeks.length} 个交易周</p>
      </div>
      <div className="space-y-3">
        {weeks.map(([weekId, weekReviews]) => {
          const open = selectedWeek === weekId;
          const displayedReviews = selectPrimaryReviewVersion(weekReviews);
          const headline = displayedReviews[0];
          return (
            <article key={weekId} className="overflow-hidden rounded-2xl border border-black/10 bg-white">
              <button type="button" aria-haspopup="dialog" onClick={() => { setSelectedWeek(weekId); setExpandedBriefDay(null); void loadBriefs(weekId); }} className="grid w-full gap-3 px-5 py-4 text-left transition hover:bg-black/[0.025] sm:grid-cols-[0.8fr_1.5fr_auto] sm:items-center">
                <div><p className="text-xs text-slate-500">自然周</p><h3 className="mt-1 text-lg font-semibold">{weekId}</h3></div>
                <div><p className="text-sm leading-6 text-slate-700">{headline?.summary ?? "本周已有正式名单或日报归档，周终复盘尚未完成。"}</p><p className="mt-1 text-xs text-slate-500">{headline ? `${reviewSourceLabel[headline.source_type]} · ${headline.rule_version}` : "部分完成 · 可展开查看已有内容"}</p></div>
                <span className="text-sm font-semibold text-emerald-800">查看详情 →</span>
              </button>
              {open && (
                <div className="fixed inset-0 z-40 grid place-items-center bg-slate-950/45 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={`${weekId} 历史归档`}>
                  <button type="button" aria-label="关闭历史归档" onClick={() => setSelectedWeek(null)} className="absolute inset-0 cursor-default" />
                  <section className="relative z-10 max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-[28px] bg-[#f7f5ef] p-5 shadow-2xl md:p-7">
                  <header className="mb-5 flex items-start justify-between gap-4 border-b border-black/10 pb-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">WEEKLY ARCHIVE</p><h3 className="mt-2 text-2xl font-semibold">{weekId} 周归档</h3></div><button type="button" onClick={() => setSelectedWeek(null)} className="rounded-xl border border-black/15 bg-white px-4 py-2 text-sm font-semibold">关闭</button></header>
                  <div className="space-y-5">
                  <ArchivedWeeklyDecisions decisions={decisionsByWeek[weekId] ?? []} />
                  <ArchivedReplayRuns runs={replaysByWeek[weekId] ?? []} />
                  {weekReviews.length === 0 && <p className="rounded-xl bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">该周的周终复盘尚未生成；已有正式名单和日报仍保留在本周归档中，不会因复盘缺失而隐藏。</p>}
                  {displayedReviews.map((review) => (
                    <section key={review.id}>
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold">{reviewSourceLabel[review.source_type]} · V{review.source_version}</h4><span className="text-xs text-slate-500">入口 {review.entry_trade_date} · 周终 {review.final_trade_date}</span></div>
                      <div className="mb-3 rounded-xl border border-emerald-900/10 bg-emerald-950 px-4 py-3 text-white">
                        <p className="text-[11px] font-semibold tracking-[0.14em] text-emerald-100/70">周总结</p>
                        <p className="mt-2 text-sm leading-6 text-emerald-50">{review.summary}</p>
                      </div>
                      <div className="mb-3 flex flex-wrap items-center gap-2"><button type="button" onClick={() => void runAttribution(review)} className="rounded-lg border border-amber-700/30 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-900">生成错误归因提案</button>{(attributionsByReview[review.id] ?? []).map((attribution) => <span key={attribution.id} className="rounded-lg bg-amber-100 px-3 py-2 text-xs text-amber-900">{attribution.taxonomy} · {attribution.status} · {attribution.proposed_hypothesis}</span>)}</div>
                      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4"><StatCell label="触达 10%" value={`${Number(review.aggregate.target_touched_count ?? 0)}/${Number(review.aggregate.item_count ?? review.items.length)}`} /><StatCell label="平均周内最高" value={pct(Number(review.aggregate.average_week_high_return ?? 0))} /><StatCell label="平均周终收盘" value={pct(Number(review.aggregate.average_week_close_return ?? 0))} /><StatCell label="相对沪深300" value={review.aggregate.average_benchmark_excess == null ? "暂无" : pct(Number(review.aggregate.average_benchmark_excess))} /></div>
                      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                        {review.items.map((item) => (
                          <button type="button" key={item.stock_code} onClick={() => setSelected({ review, item })} className="rounded-xl border border-black/10 bg-white p-4 text-left transition hover:border-emerald-700/40 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-700">
                            <div className="flex items-start justify-between gap-3"><div><p className="font-mono text-[11px] text-slate-500">#{item.rank} · {item.stock_code}</p><h5 className="mt-1 font-semibold">{item.stock_name}</h5></div><span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${item.target_touched ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>{item.target_touched ? "触达10%" : "未触达"}</span></div>
                            <div className="mt-3 grid grid-cols-3 gap-2 text-xs"><BriefMetric label="周内最高" value={pct(item.week_high_return)} /><BriefMetric label="周终" value={pct(item.week_close_return)} /><BriefMetric label="回撤" value={pct(item.max_drawdown_from_entry)} /></div>
                            <p className="mt-3 text-xs font-medium text-emerald-800">查看标的详情 →</p>
                          </button>
                        ))}
                      </div>
                      {review.status === "degraded" && <p className="mt-3 text-xs leading-5 text-amber-800">研究性回放：信息按历史日期截断，实际抓取时间与历史前复权版本限制已保留审计。</p>}
                    </section>
                  ))}
                  <ArchivedDailyBriefs
                    weekId={weekId}
                    briefs={briefsByWeek[weekId] ?? []}
                    loading={briefLoadingWeek === weekId}
                    error={briefErrorByWeek[weekId] ?? ""}
                    expandedDay={expandedBriefDay}
                    onToggleDay={(day) => setExpandedBriefDay(expandedBriefDay === day ? null : day)}
                  />
                  </div>
                  </section>
                </div>
              )}
            </article>
          );
        })}
      </div>
      {selected && <HistoryItemDetail {...selected} onClose={() => setSelected(null)} />}
    </section>
  );
}

function ArchivedWeeklyDecisions({ decisions }: { decisions: DecisionVersion[] }) {
  const published = decisions.filter((decision) => decision.decision_type === "published" && decision.status === "published");
  if (!published.length) return null;
  return <section><div className="mb-3 flex items-end justify-between gap-3"><div><p className="text-[11px] font-semibold tracking-[0.16em] text-emerald-800">WEEKLY LIST</p><h4 className="mt-1 font-semibold">正式发布名单</h4></div><span className="text-xs text-slate-500">{published.length} 个版本</span></div><div className="grid gap-2 sm:grid-cols-2">{published.map((decision) => <article key={decision.version} className="rounded-xl border border-black/10 bg-white p-4"><p className="text-xs font-semibold text-slate-500">正式发布版 · V{decision.version}</p><ol className="mt-3 space-y-1.5">{decision.items.map((item) => <li key={item.stock_code} className="flex justify-between text-sm"><span>{item.rank}. {item.stock_name}</span><span className="font-mono text-xs text-slate-500">{item.stock_code}</span></li>)}</ol></article>)}</div></section>;
}

function ArchivedReplayRuns({ runs }: { runs: ReplayRun[] }) {
  const completedRuns = runs.filter((run) => run.status === "succeeded" && run.requested_stage === "weekly_review").sort((left, right) => right.actual_run_at.localeCompare(left.actual_run_at));
  if (!completedRuns.length) return null;
  return <section className="border-t border-amber-900/10 pt-5"><div className="mb-3 flex items-end justify-between gap-3"><div><p className="text-[11px] font-semibold tracking-[0.16em] text-amber-800">FULL-WEEK REPLAY</p><h4 className="mt-1 font-semibold">完整周历史回溯</h4></div><span className="text-xs text-slate-500">按生成时间倒序 · 隔离数据</span></div><div className="space-y-2">{completedRuns.map((run) => <details key={run.id} className="rounded-xl border border-amber-900/10 bg-amber-50/60 p-4"><summary className="cursor-pointer text-sm font-semibold">周初名单 / 日报 / 周终复盘 · 已完成</summary><p className="mt-2 text-xs leading-5 text-slate-600">模拟截止 {run.information_cutoff} · 实际运行 {run.actual_run_at} · 规则 {run.effective_rule_version}</p><div className="mt-3 grid gap-2 sm:grid-cols-3">{run.stages.map((stage) => <div key={stage.id} className="rounded-lg bg-white px-3 py-2 text-xs"><p className="font-semibold">{stage.stage} · {stage.status}</p><p className="mt-1 text-slate-500">{stage.trade_date ?? "阶段级"}</p>{stage.warnings.length > 0 && <p className="mt-1 text-amber-800">{stage.warnings.join("、")}</p>}{stage.items && stage.items.length > 0 && <ul className="mt-2 space-y-1 border-t border-black/5 pt-2">{stage.items.map((item, index) => <li key={`${stage.id}-${String(item.stock_code ?? index)}`} className="leading-5 text-slate-700">{replayStageItemSummary(stage.stage, item)}</li>)}</ul>}</div>)}</div></details>)}</div></section>;
}

function ArchivedDailyBriefs({ weekId, briefs, loading, error, expandedDay, onToggleDay }: { weekId: string; briefs: ArchivedBrief[]; loading: boolean; error: string; expandedDay: string | null; onToggleDay: (day: string) => void }) {
  const grouped = briefs.reduce<Record<string, ArchivedBrief[]>>((result, brief) => {
    (result[brief.trade_date] ??= []).push(brief);
    return result;
  }, {});
  const days = Object.keys(grouped)
    .sort((left, right) => right.localeCompare(left))
    .map((day) => [day, grouped[day] ?? []] as const);
  return (
    <section className="border-t border-black/10 pt-5">
      <div className="mb-3 flex items-end justify-between gap-3"><div><p className="text-[11px] font-semibold tracking-[0.16em] text-emerald-800">DAILY ARCHIVE</p><h4 className="mt-1 text-lg font-semibold">每日日报</h4></div><p className="text-xs text-slate-500">计划生成时间 15:30 · 信息截至收盘</p></div>
      {loading && <p className="rounded-xl bg-white p-4 text-sm text-slate-500">正在加载该周日报…</p>}
      {error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
      {!loading && !error && days.length === 0 && <p className="rounded-xl bg-white p-4 text-sm text-slate-500">该周尚无日报记录。</p>}
      <div className="space-y-2">
        {days.map(([tradeDate, dayBriefs]) => {
          const dayKey = `${weekId}-${tradeDate}`;
          const open = expandedDay === dayKey;
          const itemCount = Math.max(0, ...dayBriefs.map((brief) => brief.items.length));
          return (
            <article key={tradeDate} className="overflow-hidden rounded-xl border border-black/10 bg-white">
              <button type="button" aria-expanded={open} onClick={() => onToggleDay(dayKey)} className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left transition hover:bg-black/[0.025]"><div><p className="font-semibold">{tradeDate}</p><p className="mt-1 text-xs text-slate-500">{itemCount} 只标的 · {dayBriefs.length} 个来源版本</p></div><span className="text-sm font-semibold text-emerald-800">{open ? "收起 ↑" : "查看日报 ↓"}</span></button>
              {open && (
                <div className="space-y-4 border-t border-black/10 bg-[#fcfbf7] p-4">
                  {dayBriefs.map((brief) => (
                    <section key={`${brief.archive_source}-${brief.decision_version}`}>
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h5 className="text-sm font-semibold">{brief.archive_source === "formal" ? "正式发布日报" : "历史时点回放日报"} · V{brief.decision_version}</h5><p className="text-[11px] text-slate-500">信息截至 {formatShanghaiTime(brief.as_of)} · {brief.archive_source === "formal" ? `生成于 ${formatShanghaiTime(brief.fetched_at)}` : `实际回放抓取 ${formatShanghaiTime(brief.fetched_at)}`}</p></div>
                      <div className="space-y-2">
                        {brief.items.map((item) => (
                          <article key={item.stock_code} className="grid gap-3 rounded-xl border border-black/10 bg-white p-3 sm:grid-cols-[1fr_1.5fr] sm:items-center">
                            <div><p className="font-semibold">{item.stock_name} <span className="font-mono text-[10px] text-slate-500">{item.stock_code}</span></p><p className="mt-1 text-xs leading-5 text-slate-600">{item.summary}</p></div>
                            <div className="grid grid-cols-3 gap-2 sm:grid-cols-6"><BriefMetric label="当日" value={pct(item.daily_return)} /><BriefMetric label="周内" value={pct(item.week_to_date_return)} /><BriefMetric label="最高" value={pct(item.week_high_return)} /><BriefMetric label="高点回撤" value={pct(item.drawdown_from_week_high)} /><BriefMetric label="目标距离" value={pct(item.distance_to_target)} /><BriefMetric label="风险" value={riskStatusLabel[item.risk_status]} /></div>
                          </article>
                        ))}
                      </div>
                    </section>
                  ))}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function WatchlistWeeklyArchive({ review }: { review: WatchlistWeeklyReview }) {
  const [selected, setSelected] = useState<WeeklyReviewItem | null>(null);
  return <section className="border-t border-black/10 pt-5"><div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div><p className="text-[11px] font-semibold tracking-[0.14em] text-emerald-800">PERSONAL WATCHLIST</p><h4 className="mt-1 font-semibold">本周复盘结果</h4></div><p className="text-xs text-slate-500">按本人实际关注期间评价 · 不影响规则</p></div><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{review.items.map((item) => <button type="button" key={item.stock_code} onClick={() => setSelected(item)} className="rounded-xl border border-emerald-900/15 bg-emerald-50/50 p-4 text-left transition hover:border-emerald-700/40 hover:shadow-sm"><div className="flex items-start justify-between gap-3"><div><p className="font-mono text-[11px] text-slate-500">{item.stock_code}</p><h5 className="mt-1 font-semibold">{item.stock_name}</h5></div><span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${item.target_touched ? "bg-emerald-100 text-emerald-800" : "bg-white text-slate-600"}`}>{item.target_touched ? "触达10%" : "未触达"}</span></div><div className="mt-3 grid grid-cols-3 gap-2"><BriefMetric label="周内最高" value={pct(item.week_high_return)} /><BriefMetric label="周终" value={pct(item.week_close_return)} /><BriefMetric label="回撤" value={pct(item.max_drawdown_from_entry)} /></div><p className="mt-3 text-xs font-medium text-emerald-800">查看详情 →</p></button>)}</div>{selected && <WatchlistHistoryItemDetail weekId={review.week_id} item={selected} onClose={() => setSelected(null)} />}</section>;
}

function WatchlistHistoryItemDetail({ weekId, item, onClose }: { weekId: string; item: WeeklyReviewItem; onClose: () => void }) {
  useEffect(() => { const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; window.addEventListener("keydown", closeOnEscape); return () => window.removeEventListener("keydown", closeOnEscape); }, [onClose]);
  return <div className="fixed inset-0 z-50 grid items-end bg-slate-950/45 backdrop-blur-sm sm:place-items-center sm:p-6" role="dialog" aria-modal="true" aria-label="自选历史详情"><button type="button" aria-label="关闭自选历史详情" onClick={onClose} className="absolute inset-0 cursor-default" /><section className="relative z-10 w-full rounded-t-[28px] bg-[#fcfbf7] p-6 shadow-2xl sm:max-w-2xl sm:rounded-[28px] sm:p-8"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">PERSONAL REVIEW</p><h2 className="mt-2 text-2xl font-semibold">{item.stock_name} · 自选复盘</h2><p className="mt-2 text-sm text-slate-500">{weekId} · {item.stock_code} · 仅统计实际关注期间</p></div><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-3 py-2 text-sm">关闭</button></div><div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4"><StatCell label="入口价格" value={item.entry_price.toFixed(2)} /><StatCell label="周内最高" value={pct(item.week_high_return)} /><StatCell label="周终收盘" value={pct(item.week_close_return)} /><StatCell label="最大回撤" value={pct(item.max_drawdown_from_entry)} /><StatCell label="峰谷回撤" value={pct(item.max_peak_to_trough_drawdown)} /><StatCell label="入口可达" value={item.accessible_at_entry ? "是" : "否"} /></div><div className="mt-5 rounded-2xl bg-[#173f35] p-5 text-white"><p className="text-xs text-emerald-100/70">目标情景验证</p><p className="mt-2 text-xl font-semibold">{item.target_touched ? `已于 ${item.target_touch_date ?? "周内"} 触达约 10%` : "本周未触达约 10%"}</p></div></section></div>;
}

function formatShanghaiTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}

function HistoryItemDetail({ review, item, onClose }: { review: WeeklyReview; item: WeeklyReviewItem; onClose: () => void }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid items-end bg-slate-950/45 backdrop-blur-sm sm:place-items-center sm:p-6" role="dialog" aria-modal="true" aria-label="历史标的详情">
      <button type="button" aria-label="关闭历史标的详情" onClick={onClose} className="absolute inset-0 cursor-default" />
      <section className="relative z-10 max-h-[90vh] w-full overflow-y-auto rounded-t-[28px] bg-[#fcfbf7] p-6 shadow-2xl sm:max-w-2xl sm:rounded-[28px] sm:p-8">
        <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">HISTORICAL TARGET</p><h2 className="mt-2 text-2xl font-semibold">{item.stock_name} · 历史详情</h2><p className="mt-2 text-sm text-slate-500">{review.week_id} · {item.stock_code} · {reviewSourceLabel[review.source_type]} V{review.source_version}</p></div><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-3 py-2 text-sm">关闭</button></div>
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4"><StatCell label="入口价格" value={item.entry_price.toFixed(2)} /><StatCell label="周内最高" value={pct(item.week_high_return)} /><StatCell label="周终收盘" value={pct(item.week_close_return)} /><StatCell label="最大回撤" value={pct(item.max_drawdown_from_entry)} /><StatCell label="峰谷回撤" value={pct(item.max_peak_to_trough_drawdown)} /><StatCell label="沪深300超额" value={item.benchmark_excess == null ? "暂无" : pct(item.benchmark_excess)} /><StatCell label="行业超额" value={item.industry_excess == null ? "暂无" : pct(item.industry_excess)} /><StatCell label="入口可达" value={item.accessible_at_entry ? "是" : "否"} /></div>
        <div className="mt-5 rounded-2xl bg-[#173f35] p-5 text-white"><p className="text-xs text-emerald-100/70">目标情景验证</p><p className="mt-2 text-xl font-semibold">{item.target_touched ? `已于 ${item.target_touch_date ?? "周内"} 触达约 10%` : "本周未触达约 10%"}</p>{item.drawdown_before_touch != null && <p className="mt-2 text-sm text-emerald-50/80">触达前最大回撤 {pct(item.drawdown_before_touch)}</p>}</div>
        <p className="mt-5 text-sm leading-6 text-slate-600">{review.summary}</p>
      </section>
    </div>
  );
}

const reviewSourceLabel: Record<WeeklyReview["source_type"], string> = { rule: "规则版", ai: "AI版", published: "正式发布版", historical_replay: "历史时点回放" };

function Dashboard() {
  const [week, setWeek] = useState<WeekSummary | null>(null);
  const [briefs, setBriefs] = useState<DailyBrief[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [watchBriefs, setWatchBriefs] = useState<WatchlistDailyBrief[]>([]);
  const [watchReview, setWatchReview] = useState<WatchlistWeeklyReview | null>(null);
  const [selectedBriefDate, setSelectedBriefDate] = useState<string | null>(null);
  const [watchOutputOpen, setWatchOutputOpen] = useState<"daily" | "weekly" | null>(null);
  const [watchlistOpen, setWatchlistOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<{ type: "stock"; item: WeeklyDecisionItem } | { type: "market" } | { type: "confidence" } | { type: "version" } | null>(null);

  useEffect(() => {
    const personalWeek = naturalWeekId();
    const personalWatchlist = Promise.all([
      api("/api/v1/me/watchlist") as Promise<WatchlistItem[]>,
      api(`/api/v1/me/watchlist/weeks/${personalWeek}/briefs`) as Promise<WatchlistDailyBrief[]>,
      api(`/api/v1/me/watchlist/weeks/${personalWeek}/review`)
        .then((review: WatchlistWeeklyReview) => review)
        .catch((reason) => {
          if (reason instanceof ApiError && reason.status === 404) return null;
          throw reason;
        }),
    ]).then(([items, personalBriefs, personalReview]) => {
      setWatchlist(items);
      setWatchBriefs(personalBriefs);
      setWatchReview(personalReview);
    });
    const current = api("/api/v1/weeks/current")
      .then(async (publishedWeek: WeekSummary) => {
        setWeek(publishedWeek);
        const weeklyBriefs = await api(`/api/v1/weeks/${publishedWeek.week_id}/briefs`) as DailyBrief[];
        setBriefs(weeklyBriefs);
      })
      .catch((reason) => {
        if (reason instanceof ApiError && reason.status === 404) setWeek(null);
        else setError(reason instanceof Error ? reason.message : "本周名单加载失败");
      });
    Promise.all([current, personalWatchlist])
      .catch((reason) => setError(reason instanceof Error ? reason.message : "主页加载失败"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <DashboardState title="正在加载本周名单…" detail="正在核对已发布决策版本。" />;
  if (error) return <DashboardState title="本周名单加载失败" detail={error} tone="error" />;
  if (!week) {
    return (
      <section className="px-6 py-12 md:px-10 md:py-16"><div className="mx-auto max-w-2xl rounded-3xl border border-black/10 bg-white p-8 text-center"><p className="text-xs font-semibold tracking-[0.18em] text-slate-500">WEEKLY STATUS</p><h2 className="mt-3 text-2xl font-semibold">本周尚无正式发布名单</h2><p className="mt-3 text-sm leading-6 text-slate-600">数据库中还没有完成审批与发布的本周 V9 决策。个人自选仍可独立管理，并会从生效交易日起进入本人的每日简报和每周复盘。</p><button type="button" onClick={() => setWatchlistOpen(true)} className="mt-6 rounded-full bg-[#173f35] px-5 py-2.5 text-sm font-semibold text-white">我的自选 {watchlist.length}/5</button><WatchlistOutputButtons dailyCount={watchBriefs.length} weeklyReady={watchReview !== null} onDaily={() => setWatchOutputOpen("daily")} onWeekly={() => setWatchOutputOpen("weekly")} /></div>{watchlistOpen && <WatchlistManager items={watchlist} onItemsChange={setWatchlist} onClose={() => setWatchlistOpen(false)} />}{watchOutputOpen === "daily" && <WatchlistDailyBriefsModal briefs={watchBriefs} onClose={() => setWatchOutputOpen(null)} />}{watchOutputOpen === "weekly" && <WatchlistWeeklyReviewModal review={watchReview} onClose={() => setWatchOutputOpen(null)} />}</section>
    );
  }

  const briefDates = Array.from(new Set(briefs.map((brief) => brief.trade_date))).sort().reverse();
  const selectedBrief = briefs.find((brief) => brief.trade_date === selectedBriefDate) ?? null;

  return (
    <div className="grid gap-0 lg:grid-cols-[1.3fr_0.7fr]">
      <section className="px-6 py-4 md:px-10">
        <div className="mb-4 flex flex-col items-start gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Published selection</p><h2 className="mt-2 text-2xl font-semibold">本周观察名单</h2></div>
          <p className="whitespace-nowrap text-sm text-slate-500">{week.week_id} · V{week.decision_version}</p>
        </div>
        <div className="space-y-1.5">
          {week.items.map((item) => (
            <button type="button" onClick={() => setDetail({ type: "stock", item })} key={item.stock_code} className="grid w-full gap-3 rounded-xl border border-black/10 bg-white px-4 py-2 text-left transition hover:border-emerald-700/40 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-700 md:grid-cols-[0.7fr_0.4fr_1.9fr] md:items-center">
              <div><p className="font-mono text-xs text-slate-500">#{item.rank} · {item.stock_code}</p><h3 className="mt-1 text-base font-semibold">{item.stock_name}</h3></div>
              <div><p className="font-mono text-base font-semibold">{pct(item.target_return)}</p><p className="text-xs text-slate-500">{confidenceLabel[item.confidence]}</p></div>
              <div><p className="text-sm leading-5 text-slate-700">{item.summary}</p><p className="text-xs leading-5 text-amber-800">风险：{item.primary_risk}</p></div>
            </button>
          ))}
        </div>
        <section className="mt-3 border-t border-black/10 pt-3">
          <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-semibold tracking-[0.16em] text-emerald-800">DAILY BRIEF</p><h3 className="mt-1 text-lg font-semibold">每日简报</h3></div><div className="flex flex-wrap justify-end gap-2">{briefDates.length > 0 ? briefDates.map((tradeDate) => <button type="button" key={tradeDate} onClick={() => setSelectedBriefDate(tradeDate)} className="rounded-full border border-emerald-800/25 bg-emerald-50 px-3 py-2 font-mono text-sm font-semibold text-emerald-900 transition hover:border-emerald-700 hover:bg-emerald-100">{tradeDate}</button>) : <span className="text-sm text-slate-500">收盘后生成；不改变本周名单。</span>}</div></div>
        </section>
      </section>
      <aside className="dashboard-summary border-t border-black/10 p-6 text-white lg:border-l lg:border-t-0 md:px-8 md:py-4">
        <div className="flex items-end justify-between gap-3"><div><p className="text-xs font-semibold tracking-[0.18em] text-[#c2ef4e]">WEEKLY SNAPSHOT</p><h2 className="mt-2 text-2xl font-semibold">{week.shortage ? `实际发布 ${week.items.length} 只` : "本周正式 5 只"}</h2></div><p className="max-w-44 text-right text-xs leading-5 text-white/65">{week.shortage ? week.shortage_reason : "名单已审批冻结；日报不改变名单。"}</p></div>
        <dl className="mt-5 grid grid-cols-2 gap-x-5 gap-y-3"><Stat label="市场状态" value={marketStateLabel[week.market_state]} onClick={() => setDetail({ type: "market" })} /><Stat label="组合置信度" value={confidenceLabel[week.confidence]} onClick={() => setDetail({ type: "confidence" })} /><Stat label="核心目标" value="10%" /><Stat label="决策版本" value={`V${week.decision_version}`} onClick={() => setDetail({ type: "version" })} /></dl>
        <button type="button" onClick={() => setWatchlistOpen(true)} className="mt-5 flex w-full items-center justify-between rounded-xl border border-white/15 bg-white/10 px-4 py-3 text-left transition hover:bg-white/15 focus:outline-none focus:ring-2 focus:ring-[#c2ef4e]"><span><span className="block text-xs text-white/55">个人研究区 · 不影响规则</span><span className="mt-1 block font-semibold">我的自选</span></span><span className="rounded bg-[#c2ef4e] px-3 py-1 text-sm font-semibold text-[#1f1633]">{watchlist.length}/5</span></button>
        <WatchlistOutputButtons dark dailyCount={watchBriefs.length} weeklyReady={watchReview !== null} onDaily={() => setWatchOutputOpen("daily")} onWeekly={() => setWatchOutputOpen("weekly")} />
      </aside>
      {detail && <DashboardDetail detail={detail} week={week} onClose={() => setDetail(null)} />}
      {selectedBriefDate && <DailyBriefDetail tradeDate={selectedBriefDate} brief={selectedBrief} onClose={() => setSelectedBriefDate(null)} />}
      {watchlistOpen && <WatchlistManager items={watchlist} onItemsChange={setWatchlist} onClose={() => setWatchlistOpen(false)} />}
      {watchOutputOpen === "daily" && <WatchlistDailyBriefsModal briefs={watchBriefs} onClose={() => setWatchOutputOpen(null)} />}
      {watchOutputOpen === "weekly" && <WatchlistWeeklyReviewModal review={watchReview} onClose={() => setWatchOutputOpen(null)} />}
    </div>
  );
}

type DashboardDetailState = { type: "stock"; item: WeeklyDecisionItem } | { type: "market" } | { type: "confidence" } | { type: "version" };

function WatchlistOutputButtons({ dailyCount, weeklyReady, onDaily, onWeekly, dark = false }: { dailyCount: number; weeklyReady: boolean; onDaily: () => void; onWeekly: () => void; dark?: boolean }) {
  const buttonClass = dark
    ? "rounded-xl border border-white/15 bg-white/10 px-3 py-3 text-left transition hover:bg-white/15 focus:outline-none focus:ring-2 focus:ring-[#c2ef4e]"
    : "rounded-xl border border-black/10 bg-[#f7f5ef] px-3 py-3 text-left transition hover:border-emerald-700/40 focus:outline-none focus:ring-2 focus:ring-emerald-700";
  const metaClass = dark ? "text-white/55" : "text-slate-500";
  return <div className="mt-2 grid grid-cols-2 gap-2"><button type="button" onClick={onDaily} className={buttonClass}><span className="block text-sm font-semibold">每日简报</span><span className={`mt-1 block text-xs ${metaClass}`}>{dailyCount > 0 ? `本周 ${dailyCount} 份` : "本周暂未生成"}</span></button><button type="button" onClick={onWeekly} className={buttonClass}><span className="block text-sm font-semibold">每周复盘</span><span className={`mt-1 block text-xs ${metaClass}`}>{weeklyReady ? "本周已生成" : "周末生成"}</span></button></div>;
}

function WatchlistDailyBriefsModal({ briefs, onClose }: { briefs: WatchlistDailyBrief[]; onClose: () => void }) {
  const [selectedDate, setSelectedDate] = useState(briefs.map((brief) => brief.trade_date).sort().reverse()[0] ?? null);
  const selected = briefs.find((brief) => brief.trade_date === selectedDate) ?? null;
  useEffect(() => { const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; window.addEventListener("keydown", closeOnEscape); return () => window.removeEventListener("keydown", closeOnEscape); }, [onClose]);
  return <div className="fixed inset-0 z-50 grid items-end bg-slate-950/45 backdrop-blur-sm sm:place-items-center sm:p-6" role="dialog" aria-modal="true" aria-label="我的自选每日简报"><button type="button" aria-label="关闭自选每日简报" onClick={onClose} className="absolute inset-0 cursor-default" /><section className="relative z-10 max-h-[92vh] w-full overflow-y-auto rounded-t-[28px] bg-[#fcfbf7] p-5 shadow-2xl sm:max-w-4xl sm:rounded-[28px] sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">PERSONAL DAILY BRIEF</p><h2 className="mt-2 text-2xl font-semibold">我的自选 · 每日简报</h2><p className="mt-2 text-sm text-slate-500">与系统日报同批生成，仅本人可见，不影响公共名单与规则评分。</p></div><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-3 py-2 text-sm">关闭</button></div>{briefs.length > 0 ? <><div className="mt-5 flex flex-wrap gap-2">{[...briefs].sort((left, right) => right.trade_date.localeCompare(left.trade_date)).map((brief) => <button type="button" key={brief.trade_date} onClick={() => setSelectedDate(brief.trade_date)} className={`rounded-full border px-3 py-2 font-mono text-sm font-semibold ${selectedDate === brief.trade_date ? "border-emerald-800 bg-emerald-800 text-white" : "border-emerald-800/25 bg-emerald-50 text-emerald-900"}`}>{brief.trade_date}</button>)}</div>{selected && <DailyBriefCards title={`${selected.trade_date} 收盘简报`} items={selected.items} note={`${selected.items.length} 只本人自选`} />}</> : <p className="mt-5 rounded-xl bg-[#f7f5ef] p-4 text-sm text-slate-500">本周尚无个人自选简报；交易日收盘后的定时任务会自动生成。</p>}<p className="mt-4 text-xs leading-5 text-slate-400">只统计标的实际生效后的关注期间；不回写加入自选之前的历史记录。本简报不构成交易指令。</p></section></div>;
}

function WatchlistWeeklyReviewModal({ review, onClose }: { review: WatchlistWeeklyReview | null; onClose: () => void }) {
  useEffect(() => { const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; window.addEventListener("keydown", closeOnEscape); return () => window.removeEventListener("keydown", closeOnEscape); }, [onClose]);
  return <div className="fixed inset-0 z-50 grid items-end bg-slate-950/45 backdrop-blur-sm sm:place-items-center sm:p-6" role="dialog" aria-modal="true" aria-label="我的自选每周复盘"><button type="button" aria-label="关闭自选每周复盘" onClick={onClose} className="absolute inset-0 cursor-default" /><section className="relative z-10 max-h-[92vh] w-full overflow-y-auto rounded-t-[28px] bg-[#fcfbf7] p-5 shadow-2xl sm:max-w-4xl sm:rounded-[28px] sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">PERSONAL WEEKLY REVIEW</p><h2 className="mt-2 text-2xl font-semibold">我的自选 · 每周复盘</h2><p className="mt-2 text-sm text-slate-500">在本周最后一个交易日的日报完成后，同一批任务自动生成。</p></div><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-3 py-2 text-sm">关闭</button></div>{review ? <WatchlistWeeklyArchive review={review} /> : <p className="mt-5 rounded-xl bg-[#f7f5ef] p-4 text-sm text-slate-500">本周每周复盘尚未生成；周内最后一个交易日的每日简报完成后会自动执行。</p>}</section></div>;
}

function DashboardDetail({ detail, week, onClose }: { detail: DashboardDetailState; week: WeekSummary; onClose: () => void }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid items-end bg-slate-950/45 p-0 backdrop-blur-sm sm:place-items-center sm:p-6" role="dialog" aria-modal="true" aria-label="研究详情">
      <button type="button" aria-label="关闭详情弹窗" onClick={onClose} className="absolute inset-0 cursor-default" />
      <section className="relative z-10 max-h-[90vh] w-full overflow-y-auto rounded-t-[28px] bg-[#fcfbf7] p-6 shadow-2xl sm:max-w-2xl sm:rounded-[28px] sm:p-8">
        <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">DECISION DETAIL</p><h2 className="mt-2 text-2xl font-semibold">{detail.type === "stock" ? `${detail.item.stock_name} · 入选详情` : detail.type === "market" ? "市场状态说明" : detail.type === "confidence" ? "组合置信度说明" : "决策版本说明"}</h2></div><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-3 py-2 text-sm">关闭</button></div>
        {detail.type === "stock" && <StockDecisionDetail item={detail.item} />}
        {detail.type === "market" && <div className="mt-6 space-y-3">{(Object.keys(marketStateLabel) as MarketState[]).map((state) => <article key={state} className={`rounded-2xl border p-4 ${state === week.market_state ? "border-emerald-700 bg-emerald-50" : "border-black/10 bg-white"}`}><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">{marketStateLabel[state]}</h3>{state === week.market_state && <span className="rounded-full bg-emerald-800 px-2.5 py-1 text-xs text-white">本周状态</span>}</div><p className="mt-2 text-sm leading-6 text-slate-600">{marketStateMeaning[state]}</p></article>)}</div>}
        {detail.type === "confidence" && <div className="mt-6 space-y-3">{(["high", "medium", "low"] as Confidence[]).map((confidence) => <article key={confidence} className={`rounded-2xl border p-4 ${confidence === week.confidence ? "border-emerald-700 bg-emerald-50" : "border-black/10 bg-white"}`}><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">{confidenceLabel[confidence]}</h3>{confidence === week.confidence && <span className="rounded-full bg-emerald-800 px-2.5 py-1 text-xs text-white">当前组合</span>}</div><p className="mt-2 text-sm leading-6 text-slate-600">{confidenceMeaning[confidence]}</p></article>)}</div>}
        {detail.type === "version" && <div className="mt-6 space-y-4 text-sm leading-7 text-slate-600"><p>V{week.decision_version} 是本周正式发布名单的不可变版本号，用于把页面结果对应到当时的规则版本、冻结数据、审批记录和发布事件。</p><p>它的价值主要是审计与复现：如果以后名单经过 AI 调整或人工换入，版本号可以明确说明你看到的是哪一次决定，避免历史结果被后续修改覆盖。</p><div className="rounded-2xl bg-[#f7f5ef] p-4"><p className="font-semibold text-slate-900">是否有必要展示？</p><p className="mt-1">有必要保留，但不应抢占核心信息。因此驾驶舱只显示简短的 V{week.decision_version}，完整含义按需展开；普通查看不需要理解技术细节，复盘和审计时又能准确定位。</p></div></div>}
      </section>
    </div>
  );
}

function WatchlistManager({ items, onItemsChange, onClose }: { items: WatchlistItem[]; onItemsChange: (items: WatchlistItem[]) => void; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StockSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyCode, setBusyCode] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; window.addEventListener("keydown", closeOnEscape); return () => window.removeEventListener("keydown", closeOnEscape); }, [onClose]);
  async function search(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setLoading(true); setError("");
    try { setResults(await api(`/api/v1/stocks/search?q=${encodeURIComponent(query.trim())}`) as StockSearchResult[]); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "搜索失败"); }
    finally { setLoading(false); }
  }
  async function add(stockCode: string) {
    setBusyCode(stockCode); setError("");
    try {
      const added = await api("/api/v1/me/watchlist", { method: "POST", body: JSON.stringify({ stock_code: stockCode }) }) as WatchlistItem;
      onItemsChange([...items.filter((item) => item.stock_code !== added.stock_code), added]);
      setResults((current) => current.map((row) => row.stock_code === stockCode ? { ...row, already_followed: true } : row));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "添加失败"); }
    finally { setBusyCode(""); }
  }
  async function remove(stockCode: string) {
    setBusyCode(stockCode); setError("");
    try {
      await api(`/api/v1/me/watchlist/${stockCode}`, { method: "DELETE" });
      onItemsChange(items.filter((item) => item.stock_code !== stockCode));
      setResults((current) => current.map((row) => row.stock_code === stockCode ? { ...row, already_followed: false } : row));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "移除失败"); }
    finally { setBusyCode(""); }
  }
  return <div className="fixed inset-0 z-50 grid items-end bg-slate-950/45 backdrop-blur-sm sm:place-items-center sm:p-6" role="dialog" aria-modal="true" aria-label="我的自选"><button type="button" aria-label="关闭自选管理" onClick={onClose} className="absolute inset-0 cursor-default" /><section className="relative z-10 max-h-[92vh] w-full overflow-y-auto rounded-t-[28px] bg-[#fcfbf7] p-5 shadow-2xl sm:max-w-2xl sm:rounded-[28px] sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">PERSONAL WATCHLIST</p><h2 className="mt-2 text-2xl font-semibold">我的自选 · {items.length}/5</h2><p className="mt-2 text-sm leading-6 text-slate-500">每位用户独立管理；日报和周终复盘同步跟踪，但不参与规则评分或公共名单生成。</p></div><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-3 py-2 text-sm">关闭</button></div><div className="mt-5 space-y-2">{items.length === 0 ? <p className="rounded-xl bg-[#f7f5ef] p-4 text-sm text-slate-500">暂无自选，可按代码或名称搜索 A 股。</p> : items.map((item) => <article key={item.id} className="flex items-center justify-between gap-4 rounded-xl border border-black/10 bg-white p-3"><div><p className="font-semibold">{item.stock_name} <span className="font-mono text-xs text-slate-500">{item.stock_code}</span></p><p className="mt-1 text-xs text-slate-500">从 {item.effective_from} 交易日起跟踪</p></div><button type="button" disabled={busyCode === item.stock_code} onClick={() => void remove(item.stock_code)} className="rounded-full border border-red-200 px-3 py-1.5 text-xs font-semibold text-red-700 transition hover:bg-red-50 disabled:opacity-50">移除</button></article>)}</div><form onSubmit={search} className="mt-6 flex gap-2"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入 600519 或贵州茅台" className="min-w-0 flex-1 rounded-xl border border-black/15 bg-white px-4 py-3 text-sm outline-none focus:border-emerald-700 focus:ring-2 focus:ring-emerald-700/15" /><button type="submit" disabled={loading} className="rounded-xl bg-[#173f35] px-5 py-3 text-sm font-semibold text-white disabled:opacity-50">{loading ? "搜索中…" : "搜索"}</button></form>{error && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}<div className="mt-3 space-y-2">{results.map((row) => <article key={row.stock_code} className="flex items-center justify-between gap-4 rounded-xl bg-[#f7f5ef] p-3"><div><p className="font-semibold">{row.stock_name} <span className="font-mono text-xs text-slate-500">{row.stock_code}</span></p><p className="mt-1 text-xs text-slate-500">{row.exchange} · {row.board}</p></div><button type="button" disabled={row.already_followed || items.length >= 5 || busyCode === row.stock_code} onClick={() => void add(row.stock_code)} className="rounded-full bg-emerald-800 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-700 disabled:bg-slate-300">{row.already_followed ? "已关注" : items.length >= 5 ? "已满5只" : "加入自选"}</button></article>)}</div><p className="mt-5 text-xs leading-5 text-slate-400">开盘前加入的标的从当日起跟踪；其余时间加入的标的从下一交易日起跟踪。不回写已生成的历史产出。</p></section></div>;
}

function DailyBriefDetail({ tradeDate, brief, onClose }: { tradeDate: string; brief: DailyBrief | null; onClose: () => void }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid items-end bg-slate-950/45 backdrop-blur-sm sm:place-items-center sm:p-6" role="dialog" aria-modal="true" aria-label={`${tradeDate} 每日简报`}>
      <button type="button" aria-label="关闭每日简报" onClick={onClose} className="absolute inset-0 cursor-default" />
      <section className="relative z-10 max-h-[92vh] w-full overflow-y-auto rounded-t-[28px] bg-[#fcfbf7] p-5 shadow-2xl sm:max-w-4xl sm:rounded-[28px] sm:p-7">
        <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold tracking-[0.18em] text-emerald-800">DAILY BRIEF</p><h2 className="mt-2 text-2xl font-semibold">{tradeDate} 收盘简报</h2><p className="mt-2 text-xs text-slate-500">{brief ? `信息截至 ${formatShanghaiTime(brief.as_of)} · 数据质量 ${qualityLabel[brief.quality]} · 正式名单 V${brief.decision_version}` : "该日正式日报暂不可用"}</p></div><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-3 py-2 text-sm">关闭</button></div>
        {brief && <DailyBriefCards title="公共标的" items={brief.items} />}
        <p className="mt-4 text-xs leading-5 text-slate-400">消息面只展示生成时点前已接入且可追溯的证据；暂无证据不等于不存在市场消息。本简报不构成交易指令。</p>
      </section>
    </div>
  );
}

function DailyBriefCards({ title, items, note }: { title: string; items: DailyBriefItem[]; note?: string }) {
  return <section className="mt-5"><div className="mb-3 flex flex-wrap items-end justify-between gap-2"><h3 className="font-semibold">{title}</h3>{note && <p className="text-xs text-slate-500">{note}</p>}</div><div className="space-y-3">{items.map((item) => <article key={item.stock_code} className="rounded-2xl border border-black/10 bg-white p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-mono text-[10px] text-slate-500">{item.stock_code}</p><h4 className="mt-0.5 font-semibold">{item.stock_name}</h4></div><span className={`status status-${item.risk_status}`}>{riskStatusLabel[item.risk_status]}</span></div><div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-6"><StatCell label="当日" value={pct(item.daily_return)} /><StatCell label="周内" value={pct(item.week_to_date_return)} /><StatCell label="周内最高" value={pct(item.week_high_return)} /><StatCell label="高点回撤" value={pct(item.drawdown_from_week_high)} /><StatCell label="目标距离" value={pct(item.distance_to_target)} /><StatCell label="量能" value={item.volume_activity == null ? "暂无" : `${item.volume_activity.toFixed(2)}×`} /></div><div className="mt-3 grid gap-2 sm:grid-cols-2"><div className="rounded-xl bg-emerald-50 px-3 py-2.5"><p className="text-[10px] font-semibold text-emerald-900">技术面</p><p className="mt-1 text-xs leading-5 text-emerald-950/80">{technicalBriefSummary(item)}</p></div><div className="rounded-xl bg-slate-50 px-3 py-2.5"><p className="text-[10px] font-semibold text-slate-700">消息面</p><p className="mt-1 text-xs leading-5 text-slate-600">{newsBriefSummary(item)}</p></div></div></article>)}</div></section>;
}

function technicalBriefSummary(item: DailyBrief["items"][number]) {
  const volume = item.volume_activity == null ? "量能数据不足" : item.volume_activity >= 1 ? `量能为近5日均值的 ${item.volume_activity.toFixed(2)} 倍` : `量能为近5日均值的 ${item.volume_activity.toFixed(2)} 倍，低于均量`;
  return `当日${pct(item.daily_return)}，周内${pct(item.week_to_date_return)}，距周内高点${pct(item.drawdown_from_week_high)}；${volume}。`;
}

function newsBriefSummary(item: DailyBrief["items"][number]) {
  const evidenceCount = item.evidence_ids?.length ?? 0;
  return evidenceCount > 0 ? `已关联 ${evidenceCount} 条生成时点前的公告或消息证据；后续证据页将提供来源与原文。` : "当前尚无已接入并验证的公告或消息证据，不使用模型推测补全。";
}

function StockDecisionDetail({ item }: { item: WeeklyDecisionItem }) {
  const scores = Object.entries(item.score_breakdown ?? {}).sort((left, right) => right[1] - left[1]);
  return <div className="mt-6"><div className="grid gap-3 rounded-2xl bg-[#173f35] p-5 text-white sm:grid-cols-3"><StatCellDark label="代码" value={item.stock_code} /><StatCellDark label="主方向" value={item.primary_sector ?? "待补充"} /><StatCellDark label="V9 规则分" value={item.rule_score == null ? "待补充" : item.rule_score.toFixed(1)} /></div><h3 className="mt-6 font-semibold">为什么本周入选</h3><ol className="mt-3 space-y-2">{(item.selection_reasons ?? []).map((reason, index) => <li key={reason} className="flex gap-3 rounded-xl bg-white px-4 py-3 text-sm leading-6"><span className="font-mono text-emerald-700">{index + 1}</span><span>{reason}</span></li>)}</ol>{scores.length > 0 && <><h3 className="mt-6 font-semibold">规则评分构成</h3><div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">{scores.map(([key, value]) => <div key={key} className="rounded-xl border border-black/10 bg-white p-3"><p className="text-xs text-slate-500">{scoreLabel[key] ?? key}</p><p className="mt-1 font-mono text-lg font-semibold">{value.toFixed(1)}</p></div>)}</div></>}<div className="mt-6 rounded-2xl bg-amber-50 p-4"><p className="text-xs font-semibold text-amber-900">主要风险</p><p className="mt-2 text-sm leading-6 text-amber-900/80">{item.primary_risk}</p></div><p className="mt-4 text-xs leading-5 text-slate-400">以上是冻结快照和 V9 规则形成的可审计理由，不构成收益保证或交易指令。</p></div>;
}

function StatCellDark({ label, value }: { label: string; value: string }) { return <div><p className="text-xs text-emerald-100/65">{label}</p><p className="mt-1 font-semibold">{value}</p></div>; }

function DashboardState({ title, detail, tone = "neutral" }: { title: string; detail: string; tone?: "neutral" | "error" }) {
  return <section className="px-6 py-16 md:px-10 md:py-24"><div className={`mx-auto max-w-2xl rounded-3xl border p-8 text-center ${tone === "error" ? "border-red-200 bg-red-50" : "border-black/10 bg-white"}`}><p className="text-xs font-semibold tracking-[0.18em] text-slate-500">WEEKLY STATUS</p><h2 className="mt-3 text-2xl font-semibold">{title}</h2><p className="mt-3 text-sm leading-6 text-slate-600">{detail}</p></div></section>;
}

function LoadingScreen() { return <main className="grid min-h-screen place-items-center bg-[#f4f1ea] text-sm text-slate-500">正在检查登录状态…</main>; }
function Stat({ label, value, onClick }: { label: string; value: string; onClick?: () => void }) { return <div className="border-b border-white/15 pb-2"><dt className="text-xs text-emerald-100/65">{label}</dt><dd className="mt-1 text-base font-semibold">{onClick ? <button type="button" onClick={onClick} className="text-left underline decoration-emerald-200/30 underline-offset-4 transition hover:decoration-emerald-100 focus:outline-none focus:ring-2 focus:ring-emerald-200">{value}</button> : value}</dd></div>; }
function StatCell({ label, value }: { label: string; value: string }) { return <div className="rounded-xl bg-[#f7f5ef] px-3 py-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 font-mono font-semibold">{value}</p></div>; }
function BriefMetric({ label, value }: { label: string; value: string }) { return <div><p className="text-[10px] text-slate-500">{label}</p><p className="font-mono text-xs font-semibold">{value}</p></div>; }
const riskStatusLabel = { on_track: "进展正常", watch: "继续观察", risk_triggered: "风险触发", data_degraded: "数据降级" } as const;
const qualityLabel = { verified: "双源验证", single_source: "单源降级", degraded: "降级", conflicted: "冲突", missing: "缺失" } as const;
