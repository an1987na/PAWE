from datetime import UTC, datetime
from pathlib import Path

import pytest
from pawe_api.experiments.legacy import (
    LegacyBucket,
    LegacyDocumentType,
    LegacyParseQuality,
    LegacySource,
    inventory_legacy_markdown,
    parse_legacy_markdown,
    read_and_parse_legacy,
    stage_legacy_markdown,
)


def _source(name: str) -> LegacySource:
    return LegacySource(
        relative_path=f"outputs/{name}",
        size_bytes=1,
        modified_at=datetime(2026, 8, 3, tzinfo=UTC),
        sha256="fixture",
    )


def test_parse_selection_main_and_reserve_tables() -> None:
    markdown = """# 2025-02-17 周初预选
- 规则版本：V1.9
## 本周主观察池 5 只
| 序号 | 代码 | 名称 | 方向 | 预期目标 |
|---:|---|---|---|---:|
| 1 | 300383 | 光环新网 | 算力 / IDC | 约 11% |
| 2 | 000977 | 浪潮信息 | AI 服务器 | 约 10% |
## 备选池
| 代码 | 名称 | 方向 | 备选原因 |
|---|---|---|---|
| 688256 | 寒武纪 | AI 芯片 | 科创板限制 |
"""
    result = parse_legacy_markdown(markdown, source=_source("2025-02-17_周初预选.md"))
    assert result.document_type is LegacyDocumentType.WEEKLY_SELECTION
    assert result.rule_version == "V1.9"
    assert result.parse_quality is LegacyParseQuality.PARTIAL
    assert result.verification_status == "legacy_unverified"
    assert [item.bucket for item in result.items] == [
        LegacyBucket.MAIN,
        LegacyBucket.MAIN,
        LegacyBucket.RESERVE,
    ]
    assert result.items[0].target_return == pytest.approx(0.11)
    assert result.warnings == ("unexpected_main_count:2",)


def test_parse_review_metrics_and_link() -> None:
    markdown = """# 2025-02-21 周终复盘
- 对应预选文件：`2025-02-17_周初预选.md`
- 规则版本：V1.9
## 本周结果
| 代码 | 名称 | 预期目标 | 周内最高涨幅 | 周五收盘涨幅 | 周内最大回撤 |
|---|---|---:|---:|---:|---:|
| 300383 | 光环新网 | 约 11% | +17.42% | +17.42% | -10.39% |
"""
    result = parse_legacy_markdown(markdown, source=_source("2025-02-21_周终复盘.md"))
    assert result.parse_quality is LegacyParseQuality.COMPLETE
    assert result.linked_source_ref == "2025-02-17_周初预选.md"
    assert result.items[0].week_high_return == pytest.approx(0.1742)
    assert result.items[0].baseline_price is None
    assert result.items[0].close_return == pytest.approx(0.1742)
    assert result.items[0].max_drawdown == pytest.approx(-0.1039)


def test_skip_week_is_complete_without_items() -> None:
    result = parse_legacy_markdown(
        "# 2026-04-27 劳动节前短周跳过说明",
        source=_source("2026-04-27_休市周跳过说明.md"),
    )
    assert result.document_type is LegacyDocumentType.SKIPPED_WEEK
    assert result.parse_quality is LegacyParseQuality.COMPLETE
    assert result.items == ()


def test_inventory_hash_guard_detects_source_change(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    source_path = outputs / "2025-02-17_周初预选.md"
    source_path.write_text("# source", encoding="utf-8")
    inventory = inventory_legacy_markdown(tmp_path)
    assert len(inventory) == 1
    assert inventory[0].relative_path == "outputs/2025-02-17_周初预选.md"
    assert len(inventory[0].sha256) == 64

    source_path.write_text("# changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash changed"):
        read_and_parse_legacy(tmp_path, inventory[0])


def test_stage_batch_infers_nearest_prior_selection_link(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    selection = """## 本周主观察池 5 只
| 序号 | 代码 | 名称 | 预期目标 |
|---:|---|---|---:|
| 1 | 000001 | 样本1 | 约 10% |
| 2 | 000002 | 样本2 | 约 10% |
| 3 | 000003 | 样本3 | 约 10% |
| 4 | 000004 | 样本4 | 约 10% |
| 5 | 000005 | 样本5 | 约 10% |
"""
    review = """## 本周结果
| 代码 | 名称 | 周内最高涨幅 | 周终收盘涨幅 | 周内最大回撤 |
|---|---|---:|---:|---:|
| 000001 | 样本1 | +10% | +5% | -2% |
"""
    (outputs / "2026-07-20_周初预选.md").write_text(selection, encoding="utf-8")
    (outputs / "2026-07-24_周终复盘.md").write_text(review, encoding="utf-8")

    batch = stage_legacy_markdown(tmp_path)
    staged_review = next(
        document
        for document in batch.documents
        if document.document_type is LegacyDocumentType.WEEKLY_REVIEW
    )
    assert len(batch.manifest_hash) == 64
    assert staged_review.linked_source_ref == "2026-07-20_周初预选.md"
    assert staged_review.parse_quality is LegacyParseQuality.PARTIAL
    assert staged_review.warnings == ("linked_selection_inferred",)
