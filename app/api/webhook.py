from fastapi import APIRouter, Body, Depends, HTTPException

from app.services.payload_cleaner import CleanPayload, extract_tool_payload
from app.services.reservation_service import ReservationService

router = APIRouter(tags=["Webhook"])


@router.post(
    "/webhook",
    summary="Webhook de reservas",
    description="Recibe el payload grande de VAPI, limpia tool/arguments y ejecuta la accion.",
)
async def handle_webhook(
    payload: dict = Body(...),
    service: ReservationService = Depends(ReservationService),
) -> dict:
    try:
        clean_payload: CleanPayload = extract_tool_payload(payload)
    except ValueError as exc:  # keep VAPI noise from propagating
        raise HTTPException(status_code=400, detail=str(exc))

    result = await service.dispatch(clean_payload)
    return {
        "results": [
            {
                "toolCallId": clean_payload.tool_call_id,
                "result": result,
            }
        ]
    }
