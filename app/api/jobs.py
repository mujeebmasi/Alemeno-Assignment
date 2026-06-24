import base64
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Job, JobStatus, JobSummary, Transaction
from app.schemas import (
    AnomalyOut,
    JobCreateResponse,
    JobListItem,
    JobListResponse,
    JobResultsResponse,
    JobStatusResponse,
    JobSummaryBrief,
    TransactionOut,
)
from app.services.cleaning import validate_csv
from app.worker.tasks import process_transaction_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


def _summary_brief(summary: JobSummary | None) -> JobSummaryBrief | None:
    if not summary:
        return None
    return JobSummaryBrief(
        total_spend_inr=summary.total_spend_inr,
        total_spend_usd=summary.total_spend_usd,
        anomaly_count=summary.anomaly_count,
        risk_level=summary.risk_level,
        narrative=summary.narrative,
    )


@router.post("/upload", response_model=JobCreateResponse, status_code=202)
async def upload_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Accept CSV, create a pending job, enqueue background processing."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        validate_csv(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = Job(filename=file.filename, status=JobStatus.PENDING)
    db.add(job)
    db.commit()
    db.refresh(job)

    encoded = base64.b64encode(content).decode("ascii")
    process_transaction_job.delay(job.id, encoded)

    return JobCreateResponse(
        job_id=job.id,
        status=job.status.value,
        message="Job created and queued for processing",
    )


@router.get("/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    summary = None
    if job.status == JobStatus.COMPLETED:
        summary = _summary_brief(job.summary)

    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        filename=job.filename,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        summary=summary,
    )


@router.get("/{job_id}/results", response_model=JobResultsResponse)
def get_job_results(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail=f"Job is not ready. Current status: {job.status.value}",
        )

    if job.status == JobStatus.FAILED:
        raise HTTPException(
            status_code=500,
            detail=job.error_message or "Job processing failed",
        )

    transactions = (
        db.query(Transaction).filter(Transaction.job_id == job_id).all()
    )
    anomalies = [
        AnomalyOut(
            txn_id=t.txn_id,
            merchant=t.merchant,
            amount=t.amount,
            currency=t.currency,
            account_id=t.account_id,
            anomaly_reason=t.anomaly_reason,
        )
        for t in transactions
        if t.is_anomaly
    ]

    breakdown = job.summary.category_breakdown if job.summary else {}

    return JobResultsResponse(
        job_id=job.id,
        status=job.status.value,
        transactions=[TransactionOut.model_validate(t) for t in transactions],
        anomalies=anomalies,
        category_breakdown=breakdown or {},
        summary=_summary_brief(job.summary),
    )


@router.get("", response_model=JobListResponse)
def list_jobs(
    status: str | None = Query(None, description="Filter by job status"),
    db: Session = Depends(get_db),
):
    query = db.query(Job).order_by(Job.created_at.desc())

    if status:
        try:
            status_enum = JobStatus(status.lower())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid status. Use: pending, processing, completed, failed",
            ) from exc
        query = query.filter(Job.status == status_enum)

    jobs = query.all()
    items = [
        JobListItem(
            job_id=j.id,
            filename=j.filename,
            status=j.status.value,
            row_count_raw=j.row_count_raw,
            row_count_clean=j.row_count_clean,
            created_at=j.created_at,
        )
        for j in jobs
    ]
    return JobListResponse(jobs=items, total=len(items))
