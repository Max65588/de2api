"""Adapters between OpenAI Responses and Chat Completions shapes."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import HTTPException


SUPPORTED_RESPONSE_FIELDS = {
    "model",
    "input",
    "instructions",
    "previous_response_id",
    "stream",
    "store",
    "metadata",
    "temperature",
    "top_p",
    "max_output_tokens",
    "tools",
    "tool_choice",
    "text",
    "response_format",
    "user",
}


def new_response_id() -> str:
    return f"resp_{uuid.uuid4().hex}"


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def response_error(message: str, code: str = "unsupported_feature") -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": code,
            }
        },
    )


def input_to_messages(input_value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(input_value, str):
        item = {
            "id": f"item_{uuid.uuid4().hex}",
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": input_value}],
        }
        return [{"role": "user", "content": input_value}], [item]
    if not isinstance(input_value, list):
        raise response_error("Responses input must be a string or an array.", "invalid_input")

    messages: list[dict[str, Any]] = []
    input_items: list[dict[str, Any]] = []
    for entry in input_value:
        if not isinstance(entry, dict):
            raise response_error("Each input item must be an object.", "invalid_input")
        item_type = entry.get("type")
        if item_type in {None, "message"}:
            role = str(entry.get("role") or "user")
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant", "tool"}:
                raise response_error(f"Unsupported input role: {role}", "unsupported_role")
            content = _content_to_chat(entry.get("content", ""))
            messages.append({"role": role, "content": content})
            input_items.append(_normalize_input_message(entry, role))
            continue
        if item_type == "function_call_output":
            call_id = str(entry.get("call_id") or "")
            output = entry.get("output", "")
            messages.append({"role": "tool", "tool_call_id": call_id, "content": str(output)})
            input_items.append(
                {
                    "id": str(entry.get("id") or f"item_{uuid.uuid4().hex}"),
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": str(output),
                }
            )
            continue
        raise response_error(f"Unsupported input item type: {item_type}", "unsupported_input_type")
    return messages, input_items


def build_chat_request(
    body: dict[str, Any],
    previous_response: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    unsupported = sorted(set(body) - SUPPORTED_RESPONSE_FIELDS)
    if unsupported:
        raise response_error(f"Unsupported Responses request fields: {', '.join(unsupported)}")
    if body.get("background"):
        raise response_error("background responses are not supported.")
    if body.get("include"):
        raise response_error("include is not supported.")

    model = body.get("model")
    if not model:
        raise response_error("model is required.", "missing_required_parameter")
    messages: list[dict[str, Any]] = []
    input_items: list[dict[str, Any]] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": str(body["instructions"])})
    if previous_response:
        messages.extend(response_to_history_messages(previous_response))
    input_messages, input_items = input_to_messages(body.get("input", ""))
    messages.extend(input_messages)

    chat_body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": bool(body.get("stream")),
    }
    for key in ("temperature", "top_p", "user"):
        if key in body:
            chat_body[key] = body[key]
    if "max_output_tokens" in body:
        chat_body["max_tokens"] = body["max_output_tokens"]
    if "tools" in body:
        chat_body["tools"] = convert_tools(body["tools"])
    if "tool_choice" in body:
        chat_body["tool_choice"] = body["tool_choice"]
    response_format = convert_response_format(body)
    if response_format:
        chat_body["response_format"] = response_format
    return chat_body, input_items


def convert_tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        raise response_error("tools must be an array.", "invalid_tools")
    chat_tools: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise response_error("Each tool must be an object.", "invalid_tools")
        tool_type = tool.get("type")
        if tool_type == "function":
            if "function" in tool:
                chat_tools.append(tool)
                continue
            name = tool.get("name")
            if not name:
                raise response_error("Function tools require a name.", "invalid_tools")
            chat_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
            )
            continue
        raise response_error(f"Unsupported tool type: {tool_type}")
    return chat_tools


def convert_response_format(body: dict[str, Any]) -> dict[str, Any] | None:
    if "response_format" in body:
        return body["response_format"]
    text = body.get("text")
    if not isinstance(text, dict):
        return None
    fmt = text.get("format")
    if not isinstance(fmt, dict):
        return None
    fmt_type = fmt.get("type")
    if fmt_type in {None, "text"}:
        return None
    if fmt_type == "json_object":
        return {"type": "json_object"}
    if fmt_type == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": fmt.get("name", "response_schema"),
                "schema": fmt.get("schema", {}),
                "strict": fmt.get("strict", False),
            },
        }
    raise response_error(f"Unsupported text.format type: {fmt_type}")


def chat_completion_to_response(
    *,
    response_id: str,
    request_body: dict[str, Any],
    chat_data: dict[str, Any],
    created_at: int | None = None,
    status: str = "completed",
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = created_at or int(time.time())
    output = chat_to_output_items(chat_data)
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "background": False,
        "error": error,
        "incomplete_details": None,
        "instructions": request_body.get("instructions"),
        "max_output_tokens": request_body.get("max_output_tokens"),
        "metadata": request_body.get("metadata") or {},
        "model": request_body.get("model") or chat_data.get("model"),
        "output": output,
        "output_text": output_text(output),
        "parallel_tool_calls": True,
        "previous_response_id": request_body.get("previous_response_id"),
        "reasoning": {"effort": None, "summary": None},
        "store": request_body.get("store", True) is not False,
        "temperature": request_body.get("temperature"),
        "text": request_body.get("text", {"format": {"type": "text"}}),
        "tool_choice": request_body.get("tool_choice", "auto"),
        "tools": request_body.get("tools", []),
        "top_p": request_body.get("top_p"),
        "truncation": "disabled",
        "usage": convert_usage(chat_data.get("usage")),
        "user": request_body.get("user"),
    }


def chat_to_output_items(chat_data: dict[str, Any]) -> list[dict[str, Any]]:
    choices = chat_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    if not isinstance(message, dict):
        message = {}
    output: list[dict[str, Any]] = []
    content = message.get("content")
    if content:
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": str(content), "annotations": []}],
            }
        )
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict):
            output.append(chat_tool_call_to_response(call))
    return output


def chat_tool_call_to_response(call: dict[str, Any]) -> dict[str, Any]:
    raw_function = call.get("function")
    function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
    return {
        "id": str(call.get("id") or f"fc_{uuid.uuid4().hex}"),
        "type": "function_call",
        "status": "completed",
        "call_id": str(call.get("id") or f"call_{uuid.uuid4().hex}"),
        "name": str(function.get("name") or ""),
        "arguments": str(function.get("arguments") or ""),
    }


def output_text(output: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                parts.append(str(content.get("text") or ""))
    return "".join(parts)


def response_to_history_messages(response: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    text = output_text(response.get("output", []))
    tool_calls: list[dict[str, Any]] = []
    for item in response.get("output", []):
        if isinstance(item, dict) and item.get("type") == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id"),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    },
                }
            )
    if text or tool_calls:
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        messages.append(message)
    return messages


def convert_usage(usage: Any) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        },
        "total_tokens": total_tokens,
    }


def _content_to_chat(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    chat_content: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            raise response_error("Content parts must be objects.", "invalid_input")
        part_type = part.get("type")
        if part_type in {"input_text", "output_text", "text"}:
            chat_content.append({"type": "text", "text": str(part.get("text") or "")})
            continue
        if part_type in {"input_image", "image_url"}:
            raise response_error("Image input is not supported by the current DevEco upstream.")
        raise response_error(f"Unsupported content part type: {part_type}", "unsupported_content_type")
    return chat_content


def _normalize_input_message(entry: dict[str, Any], role: str) -> dict[str, Any]:
    content = entry.get("content", "")
    if isinstance(content, str):
        normalized = [{"type": "input_text", "text": content}]
    elif isinstance(content, list):
        normalized = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"input_text", "text"}:
                normalized.append({"type": "input_text", "text": str(part.get("text") or "")})
            elif isinstance(part, dict) and part.get("type") == "output_text":
                normalized.append({"type": "input_text", "text": str(part.get("text") or "")})
            else:
                _content_to_chat([part])
    else:
        normalized = [{"type": "input_text", "text": str(content)}]
    return {
        "id": str(entry.get("id") or f"item_{uuid.uuid4().hex}"),
        "type": "message",
        "role": role,
        "content": normalized,
    }


def append_tool_delta(target: dict[int, dict[str, Any]], call: dict[str, Any]) -> None:
    index = int(call.get("index", 0))
    current = target.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    if call.get("id"):
        current["id"] = call["id"]
    raw_function = call.get("function")
    function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
    current_fn = current.setdefault("function", {})
    if not isinstance(current_fn, dict):
        current_fn = {}
        current["function"] = current_fn
    if function.get("name"):
        current_fn["name"] = function["name"]
    if function.get("arguments"):
        current_fn["arguments"] = str(current_fn.get("arguments", "")) + str(function["arguments"])
