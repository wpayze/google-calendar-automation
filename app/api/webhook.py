from fastapi import APIRouter, Body, Depends, HTTPException

from app.services.payload_cleaner import CleanPayload, extract_tool_payload
from app.services.reservation_service import ReservationService

router = APIRouter(tags=["Webhook"])


@router.post(
    "/webhook",
    summary="Webhook de reservas",
    description="Recibe el payload grande de VAPI, limpia tool/arguments y ejecuta la acción.",
)
async def handle_webhook(
    payload: dict = Body(
        ...,
        example={
            "tool": "check_availability",
            "arguments": {
                "date": "2024-12-15",
                "time": "19:30",
                "duration_minutes": 60,
                "timezone": "UTC",
            },
            "extra": "cualquier otro dato se ignora",
        },
    ),
    service: ReservationService = Depends(ReservationService),
) -> dict:
    try:
        clean_payload: CleanPayload = extract_tool_payload(payload)
    except ValueError as exc:  # keep VAPI noise from propagating
        raise HTTPException(status_code=400, detail=str(exc))

    result = await service.dispatch(clean_payload)
    return {"status": "ok", "tool": clean_payload.tool, "result": result}
