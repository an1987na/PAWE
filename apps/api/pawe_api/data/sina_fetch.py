"""Killable transport boundary for the synchronous third-party Sina adapter."""

import contextlib
import json
import sys
from datetime import date


def main() -> None:
    symbol, raw_start, raw_end = sys.argv[1:]
    start, end = date.fromisoformat(raw_start), date.fromisoformat(raw_end)
    with contextlib.redirect_stdout(sys.stderr):
        import akshare

        frame = akshare.stock_zh_a_daily(
            symbol=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
    print(json.dumps(frame.to_dict(orient="records"), default=str))


if __name__ == "__main__":
    main()
