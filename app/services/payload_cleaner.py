from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError

SUPPORTED_TOOLS = {
    "check_availability",
    "create_reservation",
    "list_next_slots",
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
    print("raw_payload:", payload)
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    tool: Optional[str] = None
    tool_call_id: str = "0"
    arguments: Dict[str, Any] = {}

    def extract_from_call(call: Dict[str, Any]) -> None:
        nonlocal tool, tool_call_id, arguments
        tool_call_id = call.get("id", tool_call_id)
        tool_candidate = call.get("name") or call.get("tool")
        func = call.get("function") if isinstance(call, dict) else {}
        if not tool_candidate and isinstance(func, dict):
            tool_candidate = func.get("name")
        tool = tool or tool_candidate

        args = call.get("arguments") or (func.get("arguments") if isinstance(func, dict) else {})
        # arguments sometimes come como string JSON
        if isinstance(args, str):
            import json
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        arguments = args

    # VAPI format: toolCalls or toolCallList
    message = payload.get("message", {})
    if isinstance(message, dict):
        tool_calls = message.get("toolCalls") or message.get("toolCallList")
        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            first_call = tool_calls[0] or {}
            extract_from_call(first_call)

    # Fallback simple format
    if tool is None:
        tool = payload.get("tool")
        tool_call_id = payload.get("toolCallId", tool_call_id)
        arguments = payload.get("arguments", {}) or {}

    # Attach customer number if present
    customer = payload.get("customer") or (message.get("customer") if isinstance(message, dict) else {})
    if isinstance(customer, dict) and customer.get("number") and "customer_number" not in arguments:
        arguments["customer_number"] = customer.get("number")

    # Use chat/session id as fallback customer identifier to scope suggestions per user.
    chat_id = None
    try:
        chat_id = payload.get("message", {}).get("server", {}).get("headers", {}).get("X-Chat-Id")
    except Exception:
        chat_id = None
    if not chat_id:
        chat_id = payload.get("chat", {}).get("id")
    if chat_id and "chat_id" not in arguments:
        arguments["chat_id"] = chat_id
    if chat_id and "customer_number" not in arguments:
        arguments["customer_number"] = chat_id

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
