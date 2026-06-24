import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.jobs import router as jobs_router
from app.database import Base, engine

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="AI Transaction Processing Pipeline",
    description="Async CSV transaction processing with LLM classification and anomaly detection",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(jobs_router)