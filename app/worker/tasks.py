import base64
import logging

from celery.signals import worker_ready

from app.database import Base, SessionLocal, engine
from app.services.pipeline import process_job
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@worker_ready.connect
def init_db(**_kwargs):
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ensured on worker startup")


@celery_app.task(bind=True, name="app.worker.tasks.process_transaction_job")
def process_transaction_job(self, job_id: int, file_content_b64: str) -> dict:
    """Background task: decode CSV and run the processing pipeline."""
    file_content = base64.b64decode(file_content_b64)
    db = SessionLocal()
    try:
        process_job(db, job_id, file_content)
        return {"job_id": job_id, "status": "completed"}
    except Exception as exc:
        logger.exception("Task failed for job %s", job_id)
        return {"job_id": job_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()
