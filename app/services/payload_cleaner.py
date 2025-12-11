from typing import Any, Dict

from pydantic import BaseModel, Field, ValidationError

SUPPORTED_TOOLS = {
    "check_availability",
    "create_reservation",
    "update_reservation",
    "delete_reservation",
    "ping",
}


class CleanPayload(BaseModel):
    tool: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


def extract_tool_payload(payload: Dict[str, Any]) -> CleanPayload:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    tool = payload.get("tool")
    arguments = payload.get("arguments", {})

    if tool is None:
        raise ValueError("Payload missing required 'tool' key.")

    if tool not in SUPPORTED_TOOLS:
        raise ValueError(
            f"Unsupported tool '{tool}'. Supported: {sorted(SUPPORTED_TOOLS)}"
        )

    if arguments is None:
        arguments = {}

    try:
        return CleanPayload(tool=tool, arguments=arguments)
    except ValidationError as exc:  # catch noisy payloads from VAPI
        raise ValueError(f"Invalid payload: {exc}") from exc
