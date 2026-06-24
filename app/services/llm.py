import json
import logging
import time
from typing import Any

import httpx
import pandas as pd

from app.config import settings

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """You are a financial transaction classifier.
For each transaction below, assign exactly one category from this list:
Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other

Return ONLY valid JSON array. Each item must have:
- index (integer, matching the input index)
- category (one of the allowed values)

Transactions:
{transactions}
"""

SUMMARY_PROMPT = """Analyze these financial transaction statistics and return ONLY valid JSON with keys:
- total_spend_inr (number)
- total_spend_usd (number)
- top_merchants (array of up to 3 objects with merchant and total_amount)
- anomaly_count (integer)
- narrative (2-3 sentence spending summary)
- risk_level (one of: low, medium, high)

Statistics:
{stats}
"""


def _retry_call(func, *args, **kwargs) -> Any:
    """Call an LLM function up to 3 times with 1s, 2s, 4s delays."""
    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(settings.llm_max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            logger.warning("LLM call failed (attempt %s): %s", attempt + 1, exc)
            if attempt < settings.llm_max_retries - 1:
                time.sleep(delay)
                delay *= 2
    raise last_error or RuntimeError("LLM call failed")


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


def _call_gemini(prompt: str) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={settings.gemini_api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_ollama(prompt: str) -> str:
    url = f"{settings.ollama_base_url.rstrip('/')}/api/generate"
    payload = {"model": "llama3.2", "prompt": prompt, "stream": False}
    with httpx.Client(timeout=120.0) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()["response"]


def _call_llm(prompt: str) -> str:
    provider = settings.llm_provider.lower()
    if provider == "ollama":
        return _call_ollama(prompt)
    if provider == "gemini" and settings.gemini_api_key:
        return _call_gemini(prompt)
    raise RuntimeError("No LLM provider configured")


def _fallback_classify(batch: list[dict]) -> list[dict]:
    merchant_map = {
        "swiggy": "Food",
        "zomato": "Food",
        "ola": "Transport",
        "irctc": "Travel",
        "makemytrip": "Travel",
        "flipkart": "Shopping",
        "amazon": "Shopping",
        "jio": "Utilities",
        "bookmyshow": "Entertainment",
        "hdfc": "Cash Withdrawal",
    }
    results = []
    for item in batch:
        merchant = (item.get("merchant") or "").lower()
        category = "Other"
        for key, cat in merchant_map.items():
            if key in merchant:
                category = cat
                break
        results.append({"index": item["index"], "category": category})
    return results


def classify_uncategorised(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Ask the LLM to assign categories to rows marked Uncategorised (in batches)."""
    df = df.copy()
    df["llm_category"] = None
    df["llm_raw_response"] = None
    df["llm_failed"] = False

    mask = df["category"].isin(["Uncategorised", "", None]) | df["category"].isna()
    uncategorised = df[mask].copy()
    raw_responses: list[str] = []

    if uncategorised.empty:
        return df, raw_responses

    indices = uncategorised.index.tolist()
    batch_size = settings.llm_batch_size

    for start in range(0, len(indices), batch_size):
        batch_indices = indices[start : start + batch_size]
        batch_payload = []
        for i, idx in enumerate(batch_indices):
            row = df.loc[idx]
            batch_payload.append(
                {
                    "index": i,
                    "merchant": row.get("merchant"),
                    "amount": row.get("amount"),
                    "currency": row.get("currency"),
                    "notes": row.get("notes"),
                }
            )

        try:
            prompt = CLASSIFICATION_PROMPT.format(
                transactions=json.dumps(batch_payload, indent=2)
            )
            response_text = _retry_call(_call_llm, prompt)
            raw_responses.append(response_text)
            parsed = _extract_json(response_text)
            for item in parsed:
                local_idx = item.get("index")
                category = item.get("category", "Other")
                if category not in settings.valid_categories:
                    category = "Other"
                if local_idx is not None and local_idx < len(batch_indices):
                    df_idx = batch_indices[local_idx]
                    df.at[df_idx, "llm_category"] = category
                    df.at[df_idx, "category"] = category
                    df.at[df_idx, "llm_raw_response"] = response_text
        except Exception as exc:
            logger.error("Classification batch failed: %s", exc)
            fallback = _fallback_classify(batch_payload)
            for item in fallback:
                local_idx = item["index"]
                if local_idx < len(batch_indices):
                    df_idx = batch_indices[local_idx]
                    df.at[df_idx, "llm_category"] = item["category"]
                    df.at[df_idx, "category"] = item["category"]
                    df.at[df_idx, "llm_failed"] = True
                    df.at[df_idx, "llm_raw_response"] = f"llm_failed: {exc}"

    return df, raw_responses


def _compute_stats(df: pd.DataFrame) -> dict:
    success_df = df[df["status"] == "SUCCESS"]
    inr_total = float(success_df.loc[success_df["currency"] == "INR", "amount"].sum())
    usd_total = float(success_df.loc[success_df["currency"] == "USD", "amount"].sum())

    merchant_totals = (
        success_df.groupby("merchant")["amount"]
        .sum()
        .sort_values(ascending=False)
        .head(3)
    )
    top_merchants = [
        {"merchant": m, "total_amount": float(a)} for m, a in merchant_totals.items()
    ]

    return {
        "total_spend_inr": inr_total,
        "total_spend_usd": usd_total,
        "top_merchants": top_merchants,
        "anomaly_count": int(df["is_anomaly"].sum()),
        "transaction_count": len(df),
        "success_count": int((df["status"] == "SUCCESS").sum()),
        "failed_count": int((df["status"] == "FAILED").sum()),
    }


def _fallback_summary(stats: dict) -> dict:
    anomaly_count = stats["anomaly_count"]
    if anomaly_count >= 5:
        risk = "high"
    elif anomaly_count >= 2:
        risk = "medium"
    else:
        risk = "low"

    narrative = (
        f"Processed {stats['transaction_count']} transactions with "
        f"{stats['success_count']} successful and {stats['failed_count']} failed. "
        f"Total spend was INR {stats['total_spend_inr']:.2f} and "
        f"USD {stats['total_spend_usd']:.2f} with {anomaly_count} anomalies flagged."
    )
    return {
        "total_spend_inr": stats["total_spend_inr"],
        "total_spend_usd": stats["total_spend_usd"],
        "top_merchants": stats["top_merchants"],
        "anomaly_count": anomaly_count,
        "narrative": narrative,
        "risk_level": risk,
    }


def generate_narrative_summary(df: pd.DataFrame) -> dict:
    """One LLM call to produce spend totals, top merchants, narrative, and risk level."""
    stats = _compute_stats(df)
    try:
        prompt = SUMMARY_PROMPT.format(stats=json.dumps(stats, indent=2))
        response_text = _retry_call(_call_llm, prompt)
        summary = _extract_json(response_text)
        summary.setdefault("total_spend_inr", stats["total_spend_inr"])
        summary.setdefault("total_spend_usd", stats["total_spend_usd"])
        summary.setdefault("top_merchants", stats["top_merchants"])
        summary.setdefault("anomaly_count", stats["anomaly_count"])
        if summary.get("risk_level") not in ("low", "medium", "high"):
            summary["risk_level"] = _fallback_summary(stats)["risk_level"]
        return summary
    except Exception as exc:
        logger.error("Narrative summary LLM failed: %s", exc)
        return _fallback_summary(stats)


def category_breakdown(df: pd.DataFrame) -> dict:
    success_df = df[df["status"] == "SUCCESS"]
    breakdown: dict[str, dict[str, float]] = {}
    for category, group in success_df.groupby("category"):
        breakdown[str(category)] = {
            "INR": float(group.loc[group["currency"] == "INR", "amount"].sum()),
            "USD": float(group.loc[group["currency"] == "USD", "amount"].sum()),
            "count": int(len(group)),
        }
    return breakdown
