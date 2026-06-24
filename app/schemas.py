from datetime import datetime
from typing import Any

from pydantic import BaseModel

class JobCreateResponse(BaseModel):
    job_id: int
    status: str
    message: str


class JobSummaryBrief(BaseModel):
    total_spend_inr: float
    total_spend_usd: float
    anomaly_count: int
    risk_level: str | None
    narrative: str | None


class JobStatusResponse(BaseModel):
    job_id: int
    status: str
    filename: str
    row_count_raw: int
    row_count_clean: int
    created_at: datetime
    completed_at: datetime | None
    error_message: str | None = None
    summary: JobSummaryBrief | None = None


class TransactionOut(BaseModel):
    txn_id: str | None
    date: str | None
    merchant: str | None
    amount: float | None
    currency: str | None
    status: str | None
    category: str | None
    account_id: str | None
    notes: str | None
    is_anomaly: bool
    anomaly_reason: str | None
    llm_category: str | None
    llm_failed: bool

    model_config = {"from_attributes": True}


class AnomalyOut(BaseModel):
    txn_id: str | None
    merchant: str | None
    amount: float | None
    currency: str | None
    account_id: str | None
    anomaly_reason: str | None


class JobResultsResponse(BaseModel):
    job_id: int
    status: str
    transactions: list[TransactionOut]
    anomalies: list[AnomalyOut]
    category_breakdown: dict[str, Any]
    summary: JobSummaryBrief | None


class JobListItem(BaseModel):
    job_id: int
    filename: str
    status: str
    row_count_raw: int
    row_count_clean: int
    created_at: datetime

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    jobs: list[JobListItem]
    total: int
