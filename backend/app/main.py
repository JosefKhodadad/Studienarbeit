from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.hilo_import import router as hilo_router
from app.api.routes.imports import router as imports_router
from app.api.routes.measurements import router as measurements_router
from app.api.routes.ml import router as ml_router
from app.api.routes.patients import router as patients_router
from app.api.routes.reports import router as reports_router

app = FastAPI(title="BlutdruckMonitor Backend", version="1.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(imports_router)        # POST /api/imports/hilo
app.include_router(dashboard_router)      # GET /api/dashboard/*
app.include_router(patients_router)       # GET /api/patients/*
app.include_router(hilo_router)           # POST /api/hilo/import (legacy)
app.include_router(measurements_router)   # POST/PATCH/DELETE /api/measurements
app.include_router(reports_router)        # GET/DELETE/POST /api/reports/*
app.include_router(ml_router)             # POST/GET /api/ml/*


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
