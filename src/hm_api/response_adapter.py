"""Adapters between OpenAI Responses and Chat Completions shapes."""

from __future__ import annotations

import json
import re
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
    "background",
    "client_metadata",
    "conversation",
    "include",
    "max_tool_calls",
    "parallel_tool_calls",
    "prompt",
    "prompt_cache_key",
    "prompt_cache_retention",
    "reasoning",
    "safety_identifier",
    "service_tier",
    "stream_options",
    "top_logprobs",
    "truncation",
}


def new_response_id() -> str:
    return f"resp_{uuid.uuid4().hex}"


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    sequence_number = data.get("sequence_number", 0)
    response_data = {key: value for key, value in data.items() if key != "sequence_number"}
    if event in {"response.created", "response.in_progress", "response.completed", "response.failed"} and data.get(
        "object"
    ) == "response":
        event_data = {"type": event, "response": response_data}
    else:
        event_data = {**response_data}
        event_data.setdefault("type", event)
    event_data.setdefault("sequence_number", sequence_number)
    payload = json.dumps(event_data, ensure_ascii=False, separators=(",", ":"))
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
        if item_type in {"reasoning", "web_search_call", "file_search_call", "computer_call", "image_generation_call"}:
            input_items.append(dict(entry))
            continue
        if item_type in {"function_call", "custom_tool_call"}:
            call_id = str(entry.get("call_id") or entry.get("id") or f"call_{uuid.uuid4().hex}")
            name = str(entry.get("name") or "")
            namespace = str(entry.get("namespace") or "")
            if namespace:
                name = qualify_namespace_tool_name(namespace, name)
            arguments = str(entry.get("arguments") or entry.get("input") or "")
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": sanitize_tool_name(name), "arguments": arguments},
                        }
                    ],
                }
            )
            input_items.append(dict(entry))
            continue
        if item_type in {"function_call_output", "custom_tool_call_output"}:
            call_id = str(entry.get("call_id") or "")
            output = entry.get("output", "")
            messages.append({"role": "tool", "tool_call_id": call_id, "content": str(output)})
            input_items.append(
                {
                    "id": str(entry.get("id") or f"item_{uuid.uuid4().hex}"),
                    "type": item_type,
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
    previous_input_items: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    unsupported = sorted(set(body) - SUPPORTED_RESPONSE_FIELDS)
    if unsupported:
        raise response_error(f"Unsupported Responses request fields: {', '.join(unsupported)}")
    if body.get("background"):
        raise response_error("background responses are not supported.")
    include = body.get("include")
    if include is not None and not isinstance(include, list):
        raise response_error("include must be an array.", "invalid_include")

    model = body.get("model")
    if not model:
        raise response_error("model is required.", "missing_required_parameter")
    messages: list[dict[str, Any]] = []
    input_items: list[dict[str, Any]] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": str(body["instructions"])})
    if previous_response:
        if previous_input_items:
            messages.extend(input_items_to_history_messages(previous_input_items))
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
    if body.get("parallel_tool_calls") is not None:
        chat_body["parallel_tool_calls"] = body["parallel_tool_calls"]
    if "max_output_tokens" in body:
        chat_body["max_tokens"] = body["max_output_tokens"]
    if "tools" in body:
        chat_tools = convert_tools(body["tools"])
        if chat_tools:
            chat_body["tools"] = chat_tools
    if "tool_choice" in body:
        tool_choice = convert_tool_choice(body["tool_choice"], body)
        if tool_choice is not None:
            chat_body["tool_choice"] = tool_choice
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
        if tool_type in {None, "", "function", "custom"}:
            converted = convert_function_tool(tool)
            if converted:
                chat_tools.append(converted)
            continue
        if tool_type == "namespace":
            namespace = str(tool.get("name") or "").strip()
            children = tool.get("tools")
            if not isinstance(children, list):
                continue
            for child in children:
                if not isinstance(child, dict):
                    continue
                child_type = child.get("type")
                if child_type not in {None, "", "function", "custom"}:
                    continue
                child_name = responses_tool_name(child)
                qualified_name = qualify_namespace_tool_name(namespace, child_name)
                converted = convert_function_tool(child, override_name=qualified_name)
                if converted:
                    chat_tools.append(converted)
            continue
        if tool_type in {"web_search", "file_search", "computer_use_preview", "image_generation", "tool_search"}:
            continue
        raise response_error(f"Unsupported tool type: {tool_type}")
    return chat_tools


def convert_tool_choice(tool_choice: Any, body: dict[str, Any]) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str) and tool_choice in {"auto", "none", "required"}:
        return tool_choice
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    choice_type = tool_choice.get("type")
    if choice_type in {"auto", "none", "required"}:
        return choice_type
    if choice_type in {"function", "tool", "custom"}:
        name = str(tool_choice.get("name") or "")
        raw_function = tool_choice.get("function")
        function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
        if not name:
            name = str(function.get("name") or "")
        namespace = str(tool_choice.get("namespace") or "")
        if namespace:
            name = qualify_namespace_tool_name(namespace, name)
        name = qualify_tool_name_from_request(body, name)
        if name:
            return {"type": "function", "function": {"name": sanitize_tool_name(name)}}
    return "auto"


def qualify_tool_name_from_request(body: dict[str, Any], name: str) -> str:
    if not name:
        return ""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return name
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "namespace":
            continue
        namespace = str(tool.get("name") or "").strip()
        children = tool.get("tools")
        if not namespace or not isinstance(children, list):
            continue
        for child in children:
            if isinstance(child, dict) and responses_tool_name(child) == name:
                return qualify_namespace_tool_name(namespace, name)
    return name


def convert_function_tool(tool: dict[str, Any], override_name: str | None = None) -> dict[str, Any] | None:
    name = (override_name or responses_tool_name(tool)).strip()
    if not name:
        return None
    safe_name = sanitize_tool_name(name)
    raw_function = tool.get("function")
    function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
    return {
        "type": "function",
        "function": {
            "name": safe_name,
            "description": responses_tool_description(tool, function),
            "parameters": normalize_tool_parameters(responses_tool_parameters(tool, function)),
        },
    }


def responses_tool_name(tool: dict[str, Any]) -> str:
    raw_function = tool.get("function")
    function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
    return str(tool.get("name") or function.get("name") or "").strip()


def responses_tool_description(tool: dict[str, Any], function: dict[str, Any]) -> str:
    return str(tool.get("description") or function.get("description") or "")


def responses_tool_parameters(tool: dict[str, Any], function: dict[str, Any]) -> Any:
    for key in ("parameters", "parametersJsonSchema", "input_schema"):
        if key in tool:
            return tool[key]
    for key in ("parameters", "parametersJsonSchema", "input_schema"):
        if key in function:
            return function[key]
    return {"type": "object", "properties": {}}


def normalize_tool_parameters(parameters: Any) -> dict[str, Any]:
    if not isinstance(parameters, dict):
        return {"type": "object", "properties": {}}
    normalized = dict(parameters)
    if not normalized.get("type"):
        normalized["type"] = "object"
    if normalized.get("type") == "object" and not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    return normalized


def qualify_namespace_tool_name(namespace: str, child_name: str) -> str:
    namespace = namespace.strip()
    child_name = child_name.strip()
    if not child_name or not namespace or child_name.startswith("mcp__"):
        return child_name
    if child_name.startswith(namespace):
        return child_name
    if namespace.endswith("__"):
        return f"{namespace}{child_name}"
    return f"{namespace}__{child_name}"


def sanitize_tool_name(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return sanitized or f"tool_{uuid.uuid4().hex[:8]}"


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
    output = chat_to_output_items(chat_data, request_body=request_body)
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
        "metadata": request_body.get("metadata") or request_body.get("client_metadata") or {},
        "model": request_body.get("model") or chat_data.get("model"),
        "output": output,
        "output_text": output_text(output),
        "parallel_tool_calls": request_body.get("parallel_tool_calls", True),
        "previous_response_id": request_body.get("previous_response_id"),
        "reasoning": request_body.get("reasoning") or {"effort": None, "summary": None},
        "store": request_body.get("store", True) is not False,
        "temperature": request_body.get("temperature"),
        "text": request_body.get("text", {"format": {"type": "text"}}),
        "tool_choice": request_body.get("tool_choice", "auto"),
        "tools": request_body.get("tools", []),
        "top_p": request_body.get("top_p"),
        "truncation": request_body.get("truncation", "disabled"),
        "usage": convert_usage(chat_data.get("usage")),
        "user": request_body.get("user"),
    }


def chat_to_output_items(chat_data: dict[str, Any], request_body: dict[str, Any] | None = None) -> list[dict[str, Any]]:
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
            output.append(chat_tool_call_to_response(call, request_body=request_body))
    return output


def chat_tool_call_to_response(call: dict[str, Any], request_body: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_function = call.get("function")
    function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
    name = str(function.get("name") or "")
    child_name, namespace = split_namespace_tool_name(request_body, name)
    item = {
        "id": str(call.get("id") or f"fc_{uuid.uuid4().hex}"),
        "type": "function_call",
        "status": "completed",
        "call_id": str(call.get("id") or f"call_{uuid.uuid4().hex}"),
        "name": child_name or name,
        "arguments": str(function.get("arguments") or ""),
    }
    if namespace:
        item["namespace"] = namespace
    return item


def split_namespace_tool_name(request_body: dict[str, Any] | None, qualified_name: str) -> tuple[str, str]:
    qualified_name = qualified_name.strip()
    if not request_body:
        return qualified_name, ""
    tools = request_body.get("tools")
    if not isinstance(tools, list):
        return qualified_name, ""
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "namespace":
            continue
        namespace = str(tool.get("name") or "").strip()
        children = tool.get("tools")
        if not namespace or not isinstance(children, list):
            continue
        for child in children:
            if not isinstance(child, dict):
                continue
            child_name = responses_tool_name(child)
            if qualify_namespace_tool_name(namespace, child_name) == qualified_name:
                return child_name, namespace
    return qualified_name, ""


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
            name = str(item.get("name") or "")
            namespace = str(item.get("namespace") or "")
            if namespace:
                name = qualify_namespace_tool_name(namespace, name)
            tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id"),
                    "type": "function",
                    "function": {
                        "name": sanitize_tool_name(name),
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


def input_items_to_history_messages(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in input_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            role = str(item.get("role") or "user")
            content_parts = item.get("content", [])
            if isinstance(content_parts, list):
                text = "".join(
                    str(part.get("text") or "")
                    for part in content_parts
                    if isinstance(part, dict) and part.get("type") in {"input_text", "output_text", "text"}
                )
            else:
                text = str(content_parts)
            messages.append({"role": role, "content": text})
            continue
        if item_type == "function_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id") or ""),
                    "content": str(item.get("output") or ""),
                }
            )
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
