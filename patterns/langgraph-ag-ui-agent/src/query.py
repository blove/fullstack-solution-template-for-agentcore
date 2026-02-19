# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import csv
import logging
from pathlib import Path
from typing import Any

from langchain.tools import tool

logger = logging.getLogger("langgraph_ag_ui_agent.query")
logger.setLevel(logging.INFO)


def _load_rows() -> list[dict[str, str]]:
    csv_path = Path(__file__).resolve().parents[1] / "db.csv"
    with csv_path.open(encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows: list[dict[str, str]] = []
        for row in reader:
            normalized = {key: value for key, value in row.items() if key is not None}
            extra = row.get(None)
            if extra:
                normalized["notes_extra"] = ", ".join(extra)
            rows.append(normalized)
    return rows


def _aggregate(
    rows: list[dict[str, str]],
    kind: str,
) -> list[dict[str, float | str]]:
    totals: dict[str, float] = {}
    for row in rows:
        row_type = (row.get("type") or "").strip().lower()
        include = (kind == "revenue" and row_type == "income") or (
            kind == "expenses" and row_type == "expense"
        )
        if not include:
            continue

        label = (row.get("subcategory") or row.get("category") or "Unknown").strip()
        amount_raw = (row.get("amount") or "0").replace(",", "").strip()
        try:
            amount = float(amount_raw)
        except ValueError:
            amount = 0.0
        totals[label] = totals.get(label, 0.0) + amount

    return [
        {"label": label, "value": round(value, 2)}
        for label, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


@tool
def query_data(query: str) -> dict[str, Any]:
    """
    Query the dataset once and return chart-ready aggregates.
    This tool does not execute SQL; it interprets the request and returns prepared data.
    """
    query_preview = (query or "").strip().replace("\n", " ")
    if len(query_preview) > 200:
        query_preview = f"{query_preview[:200]}..."
    logger.info("[TOOL query_data] start query=%s", query_preview or "<empty>")

    rows = _load_rows()
    query_lower = query.lower()

    if "expense" in query_lower:
        selected_view = "expenses_by_subcategory"
        data = _aggregate(rows, "expenses")
    else:
        selected_view = "revenue_by_subcategory"
        data = _aggregate(rows, "revenue")

    result = {
        "selected_view": selected_view,
        "data": data,
        "available_views": {
            "revenue_by_subcategory": _aggregate(rows, "revenue"),
            "expenses_by_subcategory": _aggregate(rows, "expenses"),
        },
        "notes": [
            "SQL statements are not executed.",
            "Use selected_view + data to render the requested chart.",
        ],
        "raw_row_count": len(rows),
    }

    logger.info(
        "[TOOL query_data] end selected_view=%s points=%s raw_row_count=%s",
        selected_view,
        len(data),
        len(rows),
    )
    return result
