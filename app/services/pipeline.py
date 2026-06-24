import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Job, JobStatus, JobSummary, Transaction
from app.services.anomaly import detect_anomalies
from app.services.cleaning import clean_transactions
from app.services.llm import (
    category_breakdown,
    classify_uncategorised,
    generate_narrative_summary,
)

logger = logging.getLogger(__name__)


def process_job(db: Session, job_id: int, file_content: bytes) -> None:
    """Run the full pipeline for one uploaded CSV job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    job.status = JobStatus.PROCESSING
    db.commit()

    try:
        # Step 1: Clean raw CSV (dates, amounts, casing, dedup)
        df, raw_count = clean_transactions(file_content)
        job.row_count_raw = raw_count
        job.row_count_clean = len(df)

        # Step 2: Flag statistical outliers and currency mismatches
        df = detect_anomalies(df)

        # Step 3: Use LLM to classify rows missing a category
        df, _ = classify_uncategorised(df)

        # Step 4: Generate spend summary narrative via LLM
        summary_data = generate_narrative_summary(df)
        breakdown = category_breakdown(df)

        # Step 5: Save cleaned rows and summary to PostgreSQL
        db.query(Transaction).filter(Transaction.job_id == job_id).delete()

        for _, row in df.iterrows():
            txn = Transaction(
                job_id=job_id,
                txn_id=row.get("txn_id"),
                date=row.get("date"),
                merchant=row.get("merchant"),
                amount=row.get("amount"),
                currency=row.get("currency"),
                status=row.get("status"),
                category=row.get("category"),
                account_id=row.get("account_id"),
                notes=row.get("notes"),
                is_anomaly=bool(row.get("is_anomaly")),
                anomaly_reason=row.get("anomaly_reason"),
                llm_category=row.get("llm_category"),
                llm_raw_response=row.get("llm_raw_response"),
                llm_failed=bool(row.get("llm_failed")),
            )
            db.add(txn)

        existing_summary = (
            db.query(JobSummary).filter(JobSummary.job_id == job_id).first()
        )
        if existing_summary:
            db.delete(existing_summary)

        job_summary = JobSummary(
            job_id=job_id,
            total_spend_inr=float(summary_data.get("total_spend_inr", 0)),
            total_spend_usd=float(summary_data.get("total_spend_usd", 0)),
            top_merchants=summary_data.get("top_merchants"),
            anomaly_count=int(summary_data.get("anomaly_count", 0)),
            narrative=summary_data.get("narrative"),
            risk_level=summary_data.get("risk_level"),
            category_breakdown=breakdown,
        )
        db.add(job_summary)

        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = None
        db.commit()
        logger.info("Job %s completed successfully", job_id)

    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise
