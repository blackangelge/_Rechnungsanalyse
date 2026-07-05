"""
Status- und Steuerungs-Endpunkte der Worker-FastAPI-App.

Wird ausschließlich intern vom Backend-Container abgefragt (Docker-Netz,
kein Host-Port). Das Backend proxyt diese Endpunkte unter
/api/logs/worker-stats und /api/tasks/workers bzw. /api/tasks/pause|resume.

    GET  /health   — Liveness-Check
    GET  /status   — worker_count, queue_size, servers[], paused
    POST /pause    — Dispatcher lädt keine neuen Tasks mehr aus der DB
    POST /resume   — Dispatcher nimmt wieder neue Tasks an
"""

from fastapi import APIRouter, Request

from app.worker.worker import Dispatcher

router = APIRouter(tags=["Worker-Status"])


def _get_dispatcher(request: Request) -> Dispatcher:
    return request.app.state.dispatcher


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/status")
def get_status(request: Request):
    d = _get_dispatcher(request)
    return {
        "worker_count": len(d.workers),
        "queue_size": d.queue.qsize(),
        "paused": d.paused,
        "servers": [
            {
                "id": s.id,
                "name": s.name,
                "active": s.active,
                "is_down": s.is_down,
                "parallel_request": s.parallel_request,
                "url": s.url,
            }
            for s in d.pool._servers.values()
        ],
    }


@router.post("/pause")
def pause(request: Request):
    d = _get_dispatcher(request)
    d.paused = True
    return {"paused": True}


@router.post("/resume")
def resume(request: Request):
    d = _get_dispatcher(request)
    d.paused = False
    return {"paused": False}
