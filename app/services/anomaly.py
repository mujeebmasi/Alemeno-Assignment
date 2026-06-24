import pandas as pd

from app.config import settings


def _is_domestic_merchant(merchant: str | None) -> bool:
    if not merchant:
        return False
    merchant_lower = merchant.lower()
    return any(d.lower() in merchant_lower for d in settings.domestic_merchants)


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Flag rows that look suspicious based on two simple rules."""
    df = df.copy()
    df["is_anomaly"] = False
    df["anomaly_reason"] = None

    # Rule 1 prep: calculate median spend per account
    account_medians: dict[str, float] = {}
    for account_id, group in df.groupby("account_id"):
        amounts = group["amount"].dropna()
        if len(amounts) == 0:
            continue
        account_medians[str(account_id)] = float(amounts.median())

    reasons: list[str | None] = []
    flags: list[bool] = []

    for _, row in df.iterrows():
        row_reasons: list[str] = []
        account_id = str(row.get("account_id") or "")
        amount = row.get("amount")
        currency = row.get("currency")
        merchant = row.get("merchant")

        # Rule 1: amount more than 3x the account's normal spend
        if account_id in account_medians and amount is not None:
            median = account_medians[account_id]
            if median > 0 and amount > 3 * median:
                row_reasons.append(
                    f"Amount {amount} exceeds 3x account median ({median:.2f})"
                )

        # Rule 2: USD payment to a domestic-only Indian merchant
        if currency == "USD" and _is_domestic_merchant(merchant):
            row_reasons.append(
                f"USD transaction with domestic-only merchant: {merchant}"
            )

        flags.append(bool(row_reasons))
        reasons.append("; ".join(row_reasons) if row_reasons else None)

    df["is_anomaly"] = flags
    df["anomaly_reason"] = reasons
    return df
