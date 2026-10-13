"""Automatic transaction categorisation.

Order of precedence for each raw bank line:
  1. keyword rules  (the user's own rules first, then system rules)   -> source 'rule'
  2. local Ollama, for whatever the rules did not recognise                  -> source 'ai' (+ confidence)
  3. the 'Uncategorized' / 'Other Income' system category              -> source 'default'

Rules are free, instant and deterministic, so local AI only sees the leftovers.
The database is read in a short transaction and the slow network call happens *after* it closes,
so an API timeout can never hold a DB connection open.
"""
import logging
import re
from decimal import Decimal

from ..db import db
from . import ai

log = logging.getLogger("fintrack.categorizer")
# A small batch keeps local CPU inference within the API timeout on typical PCs.
BATCH = 8
DEFAULT_EXPENSE, DEFAULT_INCOME = "Uncategorized", "Other Income"


def _load_context(user_id: int):
    with db() as cur:
        cur.execute(
            "SELECT id, name, kind, user_id IS NULL AS is_system FROM categories "
            "WHERE user_id IS NULL OR user_id = %s ORDER BY kind, name",
            (user_id,),
        )
        categories = cur.fetchall()
        # user rules before system rules, longer (more specific) keywords first
        cur.execute(
            "SELECT keyword, category_id FROM category_rules WHERE user_id IS NULL OR user_id = %s "
            "ORDER BY (user_id IS NULL), length(keyword) DESC",
            (user_id,),
        )
        rules = cur.fetchall()
    return categories, rules


def categorize(user_id: int, rows: list[dict]) -> tuple[list[dict], str]:
    """rows: [{'description': str, 'amount': Decimal}]
    returns (results, ai_status); results[i] = {category_id, category_source, ai_confidence}
    ai_status: 'not_needed' | 'ok' | 'unavailable' | 'error'"""
    categories, rules = _load_context(user_id)
    system = {c["name"]: c["id"] for c in categories if c["is_system"]}
    default_expense, default_income = system.get(DEFAULT_EXPENSE), system.get(DEFAULT_INCOME)
    default_ids = {default_expense, default_income}

    compiled = [(re.compile(r"\b" + re.escape(r["keyword"]), re.IGNORECASE), r["category_id"]) for r in rules]
    results: list[dict | None] = [None] * len(rows)
    pending: list[int] = []

    for i, row in enumerate(rows):                                   # 1) rules
        for pattern, category_id in compiled:
            if pattern.search(row["description"]):
                results[i] = {"category_id": category_id, "category_source": "rule", "ai_confidence": None}
                break
        else:
            pending.append(i)

    ai_status = "not_needed"
    if pending:                                                       # 2) AI for the leftovers
        ai_status = "ok"
        try:
            for start in range(0, len(pending), BATCH):
                chunk = pending[start:start + BATCH]
                items = [
                    {"i": i, "description": rows[i]["description"][:200], "amount": float(rows[i]["amount"])}
                    for i in chunk
                ]
                catalogue = [{"id": c["id"], "name": c["name"], "kind": c["kind"]} for c in categories]
                for i, hit in ai.classify_batch(items, catalogue).items():
                    if hit["category_id"] not in default_ids:
                        results[i] = {
                            "category_id": hit["category_id"],
                            "category_source": "ai",
                            "ai_confidence": hit["confidence"],
                        }
        except ai.AIUnavailable:
            ai_status = "unavailable"
        except Exception:                                             # network, bad JSON, rate limit...
            log.exception("AI categorisation failed; falling back to defaults")
            ai_status = "error"

    for i, result in enumerate(results):                              # 3) safe default
        if result is None:
            amount = Decimal(str(rows[i]["amount"]))
            results[i] = {
                "category_id": default_income if amount > 0 else default_expense,
                "category_source": "default",
                "ai_confidence": None,
            }
    return results, ai_status
