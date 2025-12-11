from typing import Any, Dict, Optional

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
    tool_call_id: str = "0"
    arguments: Dict[str, Any] = Field(default_factory=dict)


def extract_tool_payload(payload: Dict[str, Any]) -> CleanPayload:
    """
    Adapta payloads en formato simple (tool/arguments) y formato VAPI con message.toolCallList.
    """
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    tool: Optional[str] = None
    tool_call_id: str = "0"
    arguments: Dict[str, Any] = {}

    # VAPI format
    message = payload.get("message", {})
    tool_call_list = message.get("toolCallList") if isinstance(message, dict) else None
    if tool_call_list and isinstance(tool_call_list, list) and len(tool_call_list) > 0:
        first_call = tool_call_list[0] or {}
        tool = first_call.get("name")
        tool_call_id = first_call.get("id", "0")
        arguments = first_call.get("arguments", {}) or {}

    # Fallback simple format
    if tool is None:
        tool = payload.get("tool")
        tool_call_id = payload.get("toolCallId", tool_call_id)
        arguments = payload.get("arguments", {}) or {}

    if tool is None:
        raise ValueError("Payload missing required 'tool' key.")

    if tool not in SUPPORTED_TOOLS:
        raise ValueError(
            f"Unsupported tool '{tool}'. Supported: {sorted(SUPPORTED_TOOLS)}"
        )

    try:
        return CleanPayload(tool=tool, tool_call_id=tool_call_id, arguments=arguments)
    except ValidationError as exc:  # catch noisy payloads from VAPI
        raise ValueError(f"Invalid payload: {exc}") from exc
