import io
import re

import pandas as pd
from dateutil import parser as date_parser

REQUIRED_COLUMNS = {
    "txn_id",
    "date",
    "merchant",
    "amount",
    "currency",
    "status",
    "category",
    "account_id",
    "notes",
}


def validate_csv(content: bytes) -> None:
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"Invalid CSV file: {exc}") from exc

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

    if df.empty:
        raise ValueError("CSV file contains no data rows")


def _parse_date(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = date_parser.parse(text, dayfirst=True)
        return parsed.date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _parse_amount(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_str(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def clean_transactions(content: bytes) -> tuple[pd.DataFrame, int]:
    df = pd.read_csv(io.BytesIO(content))
    raw_count = len(df)

    df = df.copy()
    df["date"] = df["date"].apply(_parse_date)
    df["amount"] = df["amount"].apply(_parse_amount)
    df["currency"] = df["currency"].apply(
        lambda v: _clean_str(v).upper() if _clean_str(v) else None
    )
    df["status"] = df["status"].apply(
        lambda v: _clean_str(v).upper() if _clean_str(v) else None
    )
    df["category"] = df["category"].apply(
        lambda v: _clean_str(v) if _clean_str(v) else "Uncategorised"
    )
    for col in ("txn_id", "merchant", "account_id", "notes"):
        df[col] = df[col].apply(_clean_str)

    compare_cols = list(df.columns)
    df = df.drop_duplicates(subset=compare_cols, keep="first")

    return df, raw_count
