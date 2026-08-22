export function activeDecisionWeekId(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const current = new Date(
    Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day)),
  );
  const mondayOffset = (current.getUTCDay() + 6) % 7;
  current.setUTCDate(current.getUTCDate() - mondayOffset);
  return current.toISOString().slice(0, 10);
}

export function naturalWeekId(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const current = new Date(
    Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day)),
  );
  const mondayOffset = (current.getUTCDay() + 6) % 7;
  current.setUTCDate(current.getUTCDate() - mondayOffset);
  return current.toISOString().slice(0, 10);
}

export function naturalWeekIdFromDateId(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const [year, month, day] = value.split("-").map(Number);
  const current = new Date(Date.UTC(year, month - 1, day));
  const mondayOffset = (current.getUTCDay() + 6) % 7;
  current.setUTCDate(current.getUTCDate() - mondayOffset);
  return current.toISOString().slice(0, 10);
}

export function weeklyReviewTargetWeekId(now = new Date()) {
  return naturalWeekId(now);
}

export function weeklySelectionDeadlinePassed(weekId: string, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const localNow = new Date(
    Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day), Number(values.hour), Number(values.minute)),
  );
  const [year, month, day] = weekId.split("-").map(Number);
  const deadline = new Date(Date.UTC(year, month - 1, day, 9, 30));
  return localNow >= deadline;
}

export function replayRequiredForStage(
  weekId: string,
  stage: string,
  eligibility: Array<{
    week_id: string;
    stage: string;
    formal_available: boolean;
    replay_available: boolean;
  }>,
) {
  const current = eligibility.find((item) => item.week_id === weekId && item.stage === stage);
  return current?.replay_available === true && current.formal_available === false;
}

export function replayStageItemSummary(
  stage: "weekly_selection" | "daily_brief" | "weekly_review",
  item: Record<string, unknown>,
): string {
  const name = typeof item.stock_name === "string" ? item.stock_name : "未知标的";
  const code = typeof item.stock_code === "string" ? item.stock_code : "未知代码";
  const percent = (key: string) => {
    const value = item[key];
    return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "暂无";
  };
  if (stage === "weekly_selection") {
    const rank = typeof item.rank === "number" ? `#${item.rank} ` : "";
    const role = typeof item.role === "string" ? item.role : "规则候选";
    return `${rank}${name}（${code}）· ${role} · 目标 ${percent("target_return")} · ${item.confidence ?? "低置信度"}`;
  }
  if (stage === "daily_brief") {
    return `${name}（${code}）· 日涨 ${percent("daily_return")} · 周内 ${percent("week_to_date_return")} · ${item.risk_status ?? "数据降级"}`;
  }
  return `${name}（${code}）· 周内最高 ${percent("week_high_return")} · 周终 ${percent("week_close_return")} · ${item.target_touched ? "触达10%" : "未触达"}`;
}

export function selectLatestReplaySelection<T extends {
  status: string;
  stages: Array<{ stage: string; status: string; items?: Array<Record<string, unknown>> }>;
}>(runs: T[]) {
  return runs
    .filter((run) => run.status === "succeeded")
    .flatMap((run) => run.stages)
    .find((stage) => stage.stage === "weekly_selection" && stage.status === "succeeded" && (stage.items?.length ?? 0) > 0);
}

export function canConfirmTask(
  mode: "formal" | "replay",
  currentReady: boolean,
  selectedReplayReady: boolean,
  replayRequired: boolean,
  dailyTargetReady: boolean,
) {
  return mode === "formal"
    ? currentReady && !replayRequired
    : selectedReplayReady && dailyTargetReady;
}

export function selectPrimaryReviewVersion<T extends { source_type: string }>(reviews: T[]) {
  const priority: Record<string, number> = { published: 0, rule: 1, ai: 2 };
  return [...reviews]
    .sort((left, right) => (priority[left.source_type] ?? 3) - (priority[right.source_type] ?? 3))
    .slice(0, 1);
}

export function shanghaiDateId(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}
