from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.hilo_import import router as hilo_router
from app.api.routes.imports import router as imports_router
from app.api.routes.patients import router as patients_router

app = FastAPI(title="BlutdruckMonitor Backend", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(imports_router)
app.include_router(dashboard_router)
app.include_router(patients_router)
app.include_router(hilo_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
