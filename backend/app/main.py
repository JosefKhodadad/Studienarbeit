from fastapi import FastAPI

from app.api.routes.hilo_import import router as hilo_router

app = FastAPI(title="BlutdruckMonitor Backend", version="1.0.0")
app.include_router(hilo_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
