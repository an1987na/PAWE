import { describe, expect, it } from "vitest";
import { isDecisionActionable, orderDecisionVersions, selectDecisionVersionsForDisplay } from "./decision";

describe("decision management visibility", () => {
  it("keeps the waiting approval version visible before formal publication", () => {
    const decisions = [
      { decision_type: "published" as const, version: 1, status: "approved" },
      { decision_type: "rule" as const, version: 2, status: "awaiting_approval" },
    ];

    expect(orderDecisionVersions(decisions)).toEqual([decisions[1], decisions[0]]);
    expect(selectDecisionVersionsForDisplay(decisions)).toEqual([decisions[1], decisions[0]]);
    expect(isDecisionActionable(decisions[1])).toBe(true);
  });

  it("shows only the formal version after publication", () => {
    const decisions = [
      { decision_type: "rule" as const, version: 1, status: "approved" },
      { decision_type: "published" as const, version: 2, status: "approved" },
      { decision_type: "published" as const, version: 2, status: "published" },
    ];

    expect(selectDecisionVersionsForDisplay(decisions)).toEqual([decisions[2]]);
  });

  it("marks an approved human version as ready to publish", () => {
    expect(isDecisionActionable({ decision_type: "published", version: 3, status: "approved" })).toBe(true);
    expect(isDecisionActionable({ decision_type: "published", version: 2, status: "published" })).toBe(false);
  });
});
