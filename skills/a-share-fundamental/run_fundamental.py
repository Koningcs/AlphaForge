from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fundamental import get_fundamental


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch A-share fundamental data with AKShare.")
    parser.add_argument("--stock-code", required=True, help="6-digit A-share stock code, for example 300308")
    parser.add_argument("--years", type=int, default=5, help="Number of recent annual periods to fetch")
    parser.add_argument("--output-dir", default=None, help="Output root directory; defaults to project-root ./outputs")
    args = parser.parse_args()

    result = get_fundamental(
        stock_code=args.stock_code,
        years=args.years,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    printable: dict[str, Any] = {
        "json_path": result["json_path"],
        "markdown_path": result["markdown_path"],
        "analysis_path": result["analysis_path"],
        "data_quality_path": result["data_quality_path"],
        "warnings": result["data"]["metadata"]["warnings"],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
