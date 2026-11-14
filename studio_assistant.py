"""Server-side design assistant with a truthful local demo and bounded actions."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PRODUCTS = {"tshirt", "mug", "pillow", "banner", "frame", "phonecase", "poster", "gift"}
COLOR_PRODUCTS = {"tshirt", "mug", "pillow", "phonecase", "gift"}
COLORS = {"white", "black", "navy", "red", "blue", "pink", "yellow"}
ACTIONS = {"select_product", "set_location", "select_artwork", "add_uploaded_image", "place_artwork", "move_artwork", "resize_artwork", "rotate_artwork", "set_color", "add_text", "set_text_style", "set_view", "undo", "redo", "reset_design", "quality_check"}
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")


class AssistantError(Exception):
    pass


def _clean_actions(actions, context, print_areas):
    """Whitelist and normalize model actions before they reach the browser editor."""
    clean = []
    current_product = context.get("product") if context.get("product") in PRODUCTS else "tshirt"
    image_count = context.get("image_count", 0)
    image_count = image_count if isinstance(image_count, int) and not isinstance(image_count, bool) else 0
    for item in actions[:12] if isinstance(actions, list) else []:
        if not isinstance(item, dict) or item.get("action") not in ACTIONS:
            continue
        action = item["action"]
        entry = {"action": action}
        if action == "select_product":
            product = item.get("product")
            if product not in PRODUCTS:
                continue
            current_product = product
            entry["product"] = product
        elif action == "set_location":
            location = item.get("location")
            if location not in print_areas.get(current_product, set()):
                continue
            entry["location"] = location
        elif action in {"select_artwork", "add_uploaded_image"}:
            index = item.get("index")
            if image_count < 1 or not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < image_count:
                continue
            entry["index"] = index
        elif action in {"place_artwork", "move_artwork"}:
            if image_count < 1:
                continue
            placement = item.get("placement")
            if placement not in {"center", "left_chest", "right_chest", "upper_center", "lower_center"}:
                continue
            if placement in {"left_chest", "right_chest"} and current_product != "tshirt":
                continue
            entry["placement"] = placement
            if action == "move_artwork":
                x, y = item.get("x", 0), item.get("y", 0)
                if not _finite_number(x) or not _finite_number(y) or not -.35 <= x <= .35 or not -.35 <= y <= .35:
                    continue
                entry.update(x=float(x), y=float(y))
        elif action == "resize_artwork":
            scale = item.get("scale")
            if image_count < 1 or not _finite_number(scale) or not .3 <= scale <= 2.2:
                continue
            entry["scale"] = float(scale)
        elif action == "rotate_artwork":
            angle = item.get("angle")
            if image_count < 1 or not _finite_number(angle) or not -180 <= angle <= 180:
                continue
            entry["angle"] = float(angle)
        elif action == "set_color":
            color = item.get("color")
            available_colors = context.get("available_colors", COLORS)
            if current_product not in COLOR_PRODUCTS or color not in COLORS or color not in available_colors:
                continue
            entry["color"] = color
        elif action == "add_text":
            text = item.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 80:
                continue
            entry["text"] = text.strip()
            entry["color"] = item.get("color") if item.get("color") in {"black", "white", "red", "blue", "navy", "purple", "green"} else "navy"
            entry["font"] = item.get("font") if item.get("font") in {"Arial", "Georgia", "Impact", "Verdana", "Times New Roman"} else "Arial"
        elif action == "set_text_style":
            if not context.get("has_text_selection"):
                continue
            color = item.get("color")
            font = item.get("font")
            if color not in {"black", "white", "red", "blue", "navy", "purple", "green"} and font not in {"Arial", "Georgia", "Impact", "Verdana", "Times New Roman"}:
                continue
            entry["color"] = color if color in {"black", "white", "red", "blue", "navy", "purple", "green"} else None
            entry["font"] = font if font in {"Arial", "Georgia", "Impact", "Verdana", "Times New Roman"} else None
        elif action == "set_view":
            view = item.get("view")
            if current_product != "tshirt" or view not in {"front", "back"}:
                continue
            entry["view"] = view
        elif action in {"quality_check", "undo", "redo", "reset_design"}:
            pass
        clean.append(entry)
    return clean


def _finite_number(value):
    import math
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _tool_schema(print_areas):
    return {
        "type": "function", "name": "apply_studio_actions",
        "description": "Return bounded design-editor actions and a customer-facing explanation. Never claim unsupported locations are available.",
        "strict": True,
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "reply": {"type": "string"},
                "actions": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "action": {"type": "string", "enum": sorted(ACTIONS)},
                        "product": {"type": ["string", "null"], "enum": sorted(PRODUCTS) + [None]},
                        "location": {"type": ["string", "null"], "enum": sorted({loc for choices in print_areas.values() for loc in choices}) + [None]},
                        "placement": {"type": ["string", "null"], "enum": ["center", "left_chest", "right_chest", "upper_center", "lower_center", None]},
                        "color": {"type": ["string", "null"], "enum": sorted(COLORS) + [None]},
                        "text": {"type": ["string", "null"]},
                        "font": {"type": ["string", "null"], "enum": ["Arial", "Georgia", "Impact", "Verdana", "Times New Roman", None]},
                        "view": {"type": ["string", "null"], "enum": ["front", "back", None]},
                        "index": {"type": ["integer", "null"]},
                        "x": {"type": ["number", "null"]}, "y": {"type": ["number", "null"]},
                        "scale": {"type": ["number", "null"]}, "angle": {"type": ["number", "null"]},
                    },
                    "required": ["action", "product", "location", "placement", "color", "text", "view", "index", "x", "y", "scale", "angle", "font"],
                }},
            },
            "required": ["reply", "actions"],
        },
    }


def assistant_response(message, context, print_areas):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise AssistantError("AI setup is incomplete: configure OPENAI_API_KEY on the Flask server, then restart it. No design changes were made.")
    prompt = (
        "You are the ANSUMAN GRAPHIC design assistant. Help customers prepare print designs. "
        "Use only the supplied supported locations and products. Call the function with actions only when appropriate. "
        "If the request is ambiguous or required details are missing, ask one concise clarifying question and return an empty actions array. "
        "Place or transform uploaded artwork only when image_count is positive. Text must be at most 80 characters. "
        "Only use set_text_style when has_text_selection is true. Never invent an uploaded image. "
        "Explain image-resolution limits and suggest an original high-resolution export; never claim to repair or enhance pixels. "
        "The application, not you, applies validated commands. Give a concise reply.\nStudio context: "
        + json.dumps({key: value for key, value in context.items() if key != "conversation"}, ensure_ascii=True)
    )
    conversation = context.get("conversation", [])
    prior = [{"role": turn["role"], "content": turn["content"]} for turn in conversation[-8:]
             if isinstance(turn, dict) and turn.get("role") in {"user", "assistant"} and isinstance(turn.get("content"), str)]
    payload = {
        "model": os.environ.get("OPENAI_MODEL", MODEL), "instructions": prompt,
        "input": prior + [{"role": "user", "content": message}],
        "tools": [_tool_schema(print_areas)], "tool_choice": {"type": "function", "name": "apply_studio_actions"},
        "parallel_tool_calls": False,
    }
    req = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode("utf-8"),
                  headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise AssistantError("The assistant response was too large. Please try a shorter request.")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise AssistantError("The AI provider returned an invalid response. Please try again.")
    except AssistantError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise AssistantError("The AI assistant is temporarily unavailable. Your design and uploads are unchanged.") from error
    call = next((item for item in body.get("output", []) if item.get("type") == "function_call" and item.get("name") == "apply_studio_actions"), None)
    if not call:
        raise AssistantError("The AI assistant did not return valid studio actions. Please rephrase your request.")
    try:
        result = json.loads(call.get("arguments", "{}"))
        if not isinstance(result, dict):
            raise ValueError("Expected an action object")
    except (json.JSONDecodeError, ValueError) as error:
        raise AssistantError("The AI assistant returned an invalid action. Please try again.") from error
    clean = _clean_actions(result.get("actions", []), context, print_areas)
    reply = result.get("reply", "")
    if not isinstance(reply, str):
        reply = "I could not prepare a response. Please try again."
    return {"mode": "ai", "reply": reply[:1200], "actions": clean}

