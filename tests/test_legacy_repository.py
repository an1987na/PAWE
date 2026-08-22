import uuid

from pawe_api.experiments.legacy import LegacyBucket, LegacyItem
from pawe_api.experiments.legacy_repository import _decimal, _item_row


def test_legacy_item_row_stays_unverified_and_preserves_claimed_values() -> None:
    document_id = uuid.uuid4()
    row = _item_row(
        document_id,
        LegacyItem(
            bucket=LegacyBucket.MAIN,
            stock_code="300383",
            stock_name="光环新网",
            baseline_price=17.62,
            week_high_return=0.1742,
        ),
    )
    assert row.document_id == document_id
    assert row.verification_status == "legacy_unverified"
    assert str(row.baseline_price) == "17.62"
    assert str(row.week_high_return) == "0.1742"


def test_legacy_float_conversion_uses_decimal_text_not_binary_artifact() -> None:
    assert str(_decimal(0.1)) == "0.1"
    assert _decimal(None) is None
