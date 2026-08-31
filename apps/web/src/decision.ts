type DecisionSummary = {
  decision_type: "rule" | "ai" | "published";
  version: number;
  status: string;
};

export function isDecisionActionable(decision: DecisionSummary) {
  return decision.status === "awaiting_approval" || (decision.decision_type === "published" && decision.status === "approved");
}

export function orderDecisionVersions<T extends DecisionSummary>(decisions: readonly T[]): T[] {
  const priority = (decision: DecisionSummary) => {
    if (decision.status === "awaiting_approval") return 0;
    if (decision.decision_type === "published" && decision.status === "approved") return 1;
    if (decision.decision_type === "published" && decision.status === "published") return 2;
    return 3;
  };
  return [...decisions].sort((left, right) => priority(left) - priority(right) || right.version - left.version);
}

export function selectDecisionVersionsForDisplay<T extends DecisionSummary>(decisions: readonly T[]): T[] {
  const published = decisions.filter((decision) => decision.decision_type === "published" && decision.status === "published");
  return orderDecisionVersions(published.length > 0 ? published : decisions);
}
