import { describe, expect, it } from "vitest";

import { activeDecisionWeekId, canConfirmTask, naturalWeekId, naturalWeekIdFromDateId, replayRequiredForStage, replayStageItemSummary, selectLatestReplaySelection, selectPrimaryReviewVersion, shanghaiDateId, weeklyReviewTargetWeekId, weeklySelectionDeadlinePassed } from "./week";

describe("activeDecisionWeekId", () => {
  it("keeps the current natural week before Friday close", () => {
    expect(activeDecisionWeekId(new Date("2026-08-07T06:59:00Z"))).toBe("2026-08-03");
  });

  it("keeps the current natural week after Friday close", () => {
    expect(activeDecisionWeekId(new Date("2026-08-07T07:00:00Z"))).toBe("2026-08-03");
  });

  it("keeps the just-finished natural week throughout the weekend", () => {
    expect(activeDecisionWeekId(new Date("2026-08-09T10:00:00Z"))).toBe("2026-08-03");
  });

  it("returns to the current natural week on Monday", () => {
    expect(activeDecisionWeekId(new Date("2026-08-10T00:00:00Z"))).toBe("2026-08-10");
  });
});

describe("manual output dates", () => {
  it("normalizes any selected date to its Monday week id", () => {
    expect(naturalWeekIdFromDateId("2026-08-21")).toBe("2026-08-17");
  });

  it("summarizes replay items for each displayed stage", () => {
    expect(replayStageItemSummary("weekly_selection", { rank: 1, stock_name: "甲公司", stock_code: "600001", role: "core", target_return: 0.1, confidence: "low" })).toContain("甲公司（600001）");
    expect(replayStageItemSummary("daily_brief", { stock_name: "甲公司", stock_code: "600001", daily_return: 0.02, week_to_date_return: 0.05, risk_status: "watch" })).toContain("日涨 2.0%");
    expect(replayStageItemSummary("weekly_review", { stock_name: "甲公司", stock_code: "600001", week_high_return: 0.12, week_close_return: 0.03, target_touched: true })).toContain("触达10%");
  });

  it("uses the newest replay selection when the API returns newest first", () => {
    const stage = (code: string) => ({ stage: "weekly_selection", status: "succeeded", items: [{ stock_code: code }] });
    expect(selectLatestReplaySelection([
      { status: "succeeded", stages: [stage("new")] },
      { status: "succeeded", stages: [stage("old")] },
    ])?.items?.[0].stock_code).toBe("new");
  });
  it("keeps the current natural week after Friday close", () => {
    expect(naturalWeekId(new Date("2026-08-07T08:00:00Z"))).toBe("2026-08-03");
  });

  it("uses the Shanghai calendar date", () => {
    expect(shanghaiDateId(new Date("2026-08-10T16:30:00Z"))).toBe("2026-08-11");
  });

  it("targets the new current week once Monday starts", () => {
    expect(weeklyReviewTargetWeekId(new Date("2026-08-17T04:00:00Z"))).toBe("2026-08-17");
  });

  it("targets the current week after the Friday review cutoff", () => {
    expect(weeklyReviewTargetWeekId(new Date("2026-08-14T09:30:00Z"))).toBe("2026-08-10");
  });

  it("keeps the just-finished week throughout the weekend", () => {
    expect(weeklyReviewTargetWeekId(new Date("2026-08-16T04:00:00Z"))).toBe("2026-08-10");
  });

  it("recognizes the weekly selection deadline in Shanghai time", () => {
    expect(weeklySelectionDeadlinePassed("2026-08-17", new Date("2026-08-17T01:29:00Z"))).toBe(false);
    expect(weeklySelectionDeadlinePassed("2026-08-17", new Date("2026-08-17T01:30:00Z"))).toBe(true);
  });

  it("routes a closed current-week window to replay", () => {
    expect(replayRequiredForStage("2026-08-10", "weekly_review", [{
      week_id: "2026-08-10",
      stage: "weekly_review",
      formal_available: false,
      replay_available: true,
    }])).toBe(true);
    expect(replayRequiredForStage("2026-08-10", "daily_brief", [{
      week_id: "2026-08-10",
      stage: "daily_brief",
      formal_available: true,
      replay_available: true,
    }])).toBe(false);
  });

  it("allows an existing historical week when the current week calendar is missing", () => {
    expect(canConfirmTask("replay", false, true, true, true)).toBe(true);
    expect(canConfirmTask("formal", false, false, false, true)).toBe(false);
  });

  it("shows one published review when multiple formal versions exist", () => {
    const reviews = [{ source_type: "rule", id: "rule" }, { source_type: "published", id: "published" }];
    expect(selectPrimaryReviewVersion(reviews).map((review) => review.id)).toEqual(["published"]);
    expect(selectPrimaryReviewVersion([{ source_type: "ai", id: "ai" }, { source_type: "rule", id: "rule" }]).map((review) => review.id)).toEqual(["rule"]);
  });
});
