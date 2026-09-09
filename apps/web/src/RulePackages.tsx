import { useEffect, useState } from "react";

type Validation = { status: string; executable: boolean; blockers: string[]; warnings: string[] };
type Package = { id: string; version: string; content_hash: string; validation: Validation };
type Api = (path: string, init?: RequestInit) => Promise<any>;
const labels: Record<string, string> = {
  "Invalid rule package schema": "规则包格式或字段不受支持；只能上传符合规范的数据，不能上传可执行代码。",
  "Version already exists with different content; upload a new version": "此版本号已存在且内容不同，请使用新版本号，不可覆盖。",
  "Rule package exceeds size limit": "规则包超过服务端大小限制。",
  CANDIDATE_FEATURE_ADAPTER_REQUIRED: "待接通候选规则特征适配器",
  POLICY_CONFLICT_FIXED_FIVE: "需确认固定五只与允许不足五只的口径",
  POLICY_CONFLICT_OVERHEAT_FALLBACK: "需确认过热候选能否补足名单",
  POLICY_CONFLICT_PROSPECTIVE_CUTOFF: "需区分正式补生成与前瞻实验资格",
  POLICY_CONFLICT_FULL_FIVE_DAY_WEEK: "需确认非完整五交易日周的处理",
  WALK_FORWARD_AND_LIVE_SHADOW_REQUIRED: "尚未完成时序隔离回放和实时影子验证",
};

export function RulePackages({ api }: { api: Api }) {
  const [rows, setRows] = useState<Package[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const load = () => api("/api/v1/rule-packages").then(setRows);
  useEffect(() => {
    load().catch(() => setError("规则版本服务暂不可用；请检查 API 版本及 0020 数据库迁移。"))
      .finally(() => setLoading(false));
  }, []);
  async function upload() {
    if (!file || busy) return;
    setError(""); setNotice(""); setBusy(true);
    try {
      if (!file.name.endsWith(".json") || file.size > 768000) throw new Error("请选择不超过 768 KB 的 JSON 规则包。");
      const content = await file.text();
      JSON.parse(content);
      const result: Package = await api("/api/v1/rule-packages", {method: "POST", body: content});
      await load();
      setNotice(`已保存 ${result.version}。上传不会覆盖或激活正式规则。`);
    } catch (e) {
      setError(e instanceof SyntaxError ? "JSON 格式无效，请检查规则包。" : e instanceof Error ? (labels[e.message] ?? e.message) : "上传失败");
    } finally { setBusy(false); }
  }
  async function validate(id: string) {
    setBusy(true); setError("");
    try {
      const validation: Validation = await api(`/api/v1/rule-packages/${id}/validate`, {method: "POST"});
      setRows(current => current.map(row => row.id === id ? {...row, validation} : row));
      setNotice("已重新校验；校验结果不代表正式激活。");
    } catch { setError("重新校验失败，请稍后重试。"); }
    finally { setBusy(false); }
  }
  return <section className="rounded-2xl border border-black/10 bg-white p-5" aria-label="规则版本">
    <h2 className="text-xl font-semibold">规则版本</h2>
    <p className="mt-2 text-sm text-slate-600">上传 JSON 规则包，可包含 Markdown 来源材料。同版本内容不可覆盖；新规则需另建版本。当前正式引擎仍为 PAWE v9.0.0。</p>
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <label className="min-w-0 flex-1 text-sm">选择规则包
        <input className="mt-1 block w-full max-w-full text-sm" type="file" accept=".json,application/json" disabled={busy} onChange={event => setFile(event.target.files?.[0] ?? null)} />
      </label>
      <button className="rounded-xl bg-emerald-900 px-4 py-2 text-sm text-white disabled:opacity-50" disabled={!file || busy} onClick={upload}>{busy ? "处理中…" : "上传新版本"}</button>
    </div>
    {error && <p role="alert" className="mt-3 break-words text-sm text-red-800">{error}</p>}
    {notice && <p role="status" className="mt-3 text-sm text-emerald-800">{notice}</p>}
    {loading ? <p className="mt-4 text-sm">正在加载规则版本…</p> : rows.length === 0 && !error ? <p className="mt-4 text-sm text-slate-500">尚未上传规则包。</p> : null}
    <div className="mt-4 space-y-3">{rows.map(row => <article key={row.id} className="rounded-xl border border-black/10 p-4">
      <div className="flex flex-wrap justify-between gap-2"><h3 className="break-all font-semibold">{row.version}</h3><span className="text-sm text-amber-800">{row.validation.status === "compatible" ? "引擎兼容 · 未激活" : row.validation.status === "reference_only" ? "仅来源文档" : "存在待解决项"}</span></div>
      <p className="mt-2 break-all font-mono text-xs text-slate-500">SHA256 {row.content_hash}</p>
      {row.validation.blockers.length > 0 && <ul className="mt-2 list-inside list-disc text-sm text-slate-600">{row.validation.blockers.map(item => <li className="break-words" key={item}>{labels[item] ?? item}</li>)}</ul>}
      <button disabled={busy} onClick={() => validate(row.id)} className="mt-3 text-sm font-semibold text-emerald-900 disabled:opacity-50">重新校验</button>
    </article>)}</div>
  </section>;
}
