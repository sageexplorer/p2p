"""Auto-generate LangChain tools from a FastAPI OpenAPI spec.

=== THE BIG PICTURE ===

When you build an AI agent that calls an API, you normally have to write
one tool function per endpoint by hand (see agent/tools.py for that approach).
This file does it AUTOMATICALLY by reading the API's OpenAPI spec (/openapi.json).

Here's what happens, step by step:

    1. We fetch http://localhost:8000/openapi.json
       → This is a JSON document that FastAPI auto-generates describing every
         endpoint: its path, HTTP method, parameters, request body schema,
         and response format.

    2. For each endpoint in the spec (e.g. POST /purchase-orders), we:
       a. Extract the tool NAME from the operationId
       b. Extract the DESCRIPTION from the endpoint's docstring
       c. Build a Pydantic INPUT MODEL from the endpoint's parameters and
          request body schema — this tells the LLM what arguments the tool accepts
       d. Create a FUNCTION that actually calls the API via httpx

    3. We wrap each (name, description, function, input_model) into a
       LangChain StructuredTool — this is the format LangGraph expects.

    4. The LLM sees these tools with their names and descriptions, decides
       which to call, and passes the arguments. Our function executes the
       HTTP request and returns the result as JSON.

=== WHY THIS MATTERS ===

- Add a new endpoint to FastAPI → the agent gets a new tool automatically
- The docstrings you write on FastAPI endpoints become the tool descriptions
  the LLM reasons over — so better docstrings = smarter agent
- No hand-written wrapper code to maintain

=== USAGE ===

    from agent.openapi_tools import build_tools_from_openapi

    tools = build_tools_from_openapi("http://localhost:8000")
    # tools is a list of StructuredTool objects ready for LangGraph
"""
import json
from typing import Any

import httpx
from langchain_core.tools import StructuredTool
from pydantic import Field, create_model


def build_tools_from_openapi(base_url: str = "http://localhost:8000") -> list[StructuredTool]:
    """Fetch the OpenAPI spec and generate one LangChain tool per endpoint.

    This is the main entry point. It:
      1. GETs /openapi.json from the running FastAPI server
      2. Iterates over every path+method combination in the spec
      3. Calls _make_tool() to convert each one into a StructuredTool
      4. Returns the list of tools ready for use with LangGraph

    Args:
        base_url: Where the FastAPI server is running. Must be reachable.

    Returns:
        A list of StructuredTool objects — one per API endpoint.
        Each tool has a name, description, input schema, and a function
        that calls the actual API.

    Example:
        tools = build_tools_from_openapi("http://localhost:8000")
        # tools[0].name = "create_purchase_order_purchase_orders_post"
        # tools[0].description = "Create a new purchase order in DRAFT status."
        # tools[0].func(vendor_id=1, line_items=[...]) → JSON string
    """
    # Step 1: Fetch the OpenAPI spec — this is the JSON document that
    # describes ALL endpoints, their parameters, request/response schemas.
    # FastAPI generates this automatically at /openapi.json
    spec = httpx.get(f"{base_url}/openapi.json").json()
    tools = []

    # Endpoints we don't want as agent tools (health check, static pages, etc.)
    skip_paths = {"/health", "/products", "/dashboard"}

    # Step 2: Walk through every path in the spec
    # spec["paths"] looks like:
    # {
    #   "/purchase-orders": {"post": {...operation...}},
    #   "/purchase-orders/{po_id}": {"get": {...operation...}},
    #   "/purchase-orders/{po_id}/submit": {"post": {...operation...}},
    #   ...
    # }
    for path, methods in spec["paths"].items():
        if path in skip_paths:
            continue

        # Each path can have multiple HTTP methods (get, post, put, etc.)
        for method, operation in methods.items():
            # Step 3: Convert this single endpoint into a LangChain tool
            tool = _make_tool(base_url, path, method.upper(), operation, spec)
            if tool:
                tools.append(tool)

    return tools


def _make_tool(
    base_url: str,
    path: str,
    method: str,
    operation: dict,
    spec: dict,
) -> StructuredTool | None:
    """Convert ONE OpenAPI operation into a LangChain StructuredTool.

    This is where the magic happens. For a single endpoint like:

        POST /purchase-orders/{po_id}/receive

    It produces a tool with:
        - name: "receive_goods_purchase_orders__po_id__receive_post"
        - description: "Record a goods receipt against a submitted PO."
        - args_schema: Pydantic model with fields {po_id: int, received_by: str, line_items: list}
        - func: a function that calls POST http://localhost:8000/purchase-orders/5/receive

    The process has 4 stages:
        A. Extract name and description from the operation metadata
        B. Build the input schema (what arguments does this tool accept?)
        C. Create the function that actually calls the API
        D. Package it all into a StructuredTool

    Args:
        base_url: API base URL (e.g. "http://localhost:8000")
        path: The URL path (e.g. "/purchase-orders/{po_id}/receive")
        method: HTTP method, uppercased (e.g. "POST")
        operation: The OpenAPI operation dict — contains operationId,
                   summary, description, parameters, requestBody, etc.
        spec: The full OpenAPI spec — needed to resolve $ref pointers
              (when schemas reference other schemas by name)

    Returns:
        A StructuredTool ready for LangGraph, or None if something fails.
    """

    # ── STAGE A: Extract name and description ────────────────────
    #
    # operationId is the unique name FastAPI gives each endpoint.
    # Example: "create_purchase_order_purchase_orders_post"
    # This becomes the tool name the LLM sees.
    #
    # description comes from the docstring you write on the FastAPI
    # endpoint function. This is what the LLM reads to decide whether
    # to use this tool and how.
    op_id = operation.get("operationId", "")
    summary = operation.get("summary", "")
    description = operation.get("description", summary)

    tool_name = _to_snake_case(op_id) if op_id else _path_to_name(method, path)

    # ── STAGE B: Build the input schema ──────────────────────────
    #
    # We need to tell the LLM what arguments this tool accepts.
    # Arguments come from three places in the OpenAPI spec:
    #
    #   1. PATH parameters — values embedded in the URL
    #      e.g. /purchase-orders/{po_id} → po_id: int
    #
    #   2. QUERY parameters — values appended to the URL as ?key=value
    #      e.g. /api/audit-logs?workflow_id=abc → workflow_id: str (optional)
    #
    #   3. REQUEST BODY — JSON payload sent with POST/PUT requests
    #      e.g. {"vendor_id": 1, "line_items": [...]} → vendor_id: int, line_items: list
    #
    # For each parameter, we extract:
    #   - The field name (e.g. "po_id")
    #   - The Python type (int, str, list, etc.)
    #   - A description (shown to the LLM)
    #   - Whether it's required or optional

    fields: dict[str, Any] = {}

    # --- 1. Path parameters ---
    # These are the {curly_brace} parts of the URL.
    # In OpenAPI, they're listed in operation["parameters"] with "in": "path"
    #
    # Example from the spec:
    #   {"name": "po_id", "in": "path", "required": true,
    #    "schema": {"type": "integer"}}
    #
    # We turn this into: po_id: int (required)
    for param in operation.get("parameters", []):
        if param["in"] == "path":
            name = param["name"]
            schema = param.get("schema", {})
            py_type = _json_type_to_python(schema.get("type", "string"))
            param_desc = param.get("description", name)
            fields[name] = (py_type, Field(description=param_desc))

    # --- 2. Query parameters ---
    # These are optional filters like ?workflow_id=abc&limit=100
    # Same format as path params but with "in": "query"
    # Optional params get a default of None so the LLM can skip them
    for param in operation.get("parameters", []):
        if param["in"] == "query":
            name = param["name"]
            schema = param.get("schema", {})
            py_type = _json_type_to_python(schema.get("type", "string"))
            required = param.get("required", False)
            param_desc = param.get("description", name)
            if required:
                fields[name] = (py_type, Field(description=param_desc))
            else:
                # Optional: type becomes "str | None" with default=None
                fields[name] = (py_type | None, Field(default=None, description=param_desc))

    # --- 3. Request body ---
    # POST/PUT endpoints accept a JSON body. The OpenAPI spec describes
    # the body schema, which may be a $ref pointer like:
    #   {"$ref": "#/components/schemas/POCreate"}
    #
    # We resolve the $ref to get the actual schema, then extract each
    # property as a tool argument.
    #
    # Example: POCreate resolves to:
    #   {"properties": {"vendor_id": {"type": "integer"},
    #                    "line_items": {"type": "array", ...}},
    #    "required": ["vendor_id", "line_items"]}
    #
    # We turn this into: vendor_id: int (required), line_items: list (required)
    request_body = operation.get("requestBody", {})
    if request_body:
        body_schema = _resolve_body_schema(request_body, spec)
        if body_schema:
            for prop_name, prop_schema in body_schema.get("properties", {}).items():
                required_props = body_schema.get("required", [])
                py_type = _json_schema_to_python(prop_schema)
                prop_desc = prop_schema.get("description", prop_name)
                if prop_name in required_props:
                    fields[prop_name] = (py_type, Field(description=prop_desc))
                else:
                    fields[prop_name] = (py_type | None, Field(default=None, description=prop_desc))

    # Now we have all the fields. Create a Pydantic model dynamically.
    # This model IS the tool's input schema — LangChain uses it to:
    #   a. Tell the LLM what arguments are available (name, type, description)
    #   b. Validate the LLM's output before calling our function
    #
    # Example result for "receive_goods":
    #   class receive_goods_input(BaseModel):
    #       po_id: int = Field(description="po_id")
    #       received_by: str = Field(description="received_by")
    #       line_items: list = Field(description="line_items")
    if fields:
        input_model = create_model(f"{tool_name}_input", **fields)
    else:
        input_model = create_model(f"{tool_name}_input")

    # ── STAGE C: Create the API-calling function ─────────────────
    #
    # This is the function that actually runs when the LLM calls the tool.
    # It receives the arguments as **kwargs and needs to:
    #   1. Figure out which args go in the URL path vs query string vs body
    #   2. Make the HTTP request
    #   3. Return the result as a JSON string for the LLM to read
    #
    # We use a closure (make_fn) to capture the path and method at
    # definition time, since they're different for each tool.
    def make_fn(p=path, m=method):
        def fn(**kwargs) -> str:
            url = p
            query = {}
            body = {}

            for key, val in kwargs.items():
                if val is None:
                    continue  # Skip optional params the LLM didn't provide

                if f"{{{key}}}" in p:
                    # This arg is a PATH parameter — substitute it into the URL
                    # e.g. "/purchase-orders/{po_id}" + po_id=5
                    #    → "/purchase-orders/5"
                    url = url.replace(f"{{{key}}}", str(val))
                elif m == "GET":
                    # For GET requests, non-path args go in the query string
                    # e.g. /api/audit-logs?workflow_id=abc
                    query[key] = val
                else:
                    # For POST requests, non-path args go in the JSON body
                    # e.g. {"vendor_id": 1, "line_items": [...]}
                    body[key] = val

            # Make the actual HTTP request to the FastAPI server
            with httpx.Client(base_url=base_url, timeout=10) as client:
                if m == "GET":
                    resp = client.get(url, params=query)
                else:
                    resp = client.post(url, json=body if body else None)

            result = resp.json()

            # Wrap the result with success/failure info so the LLM can
            # easily tell if the call worked or not
            if resp.status_code >= 400:
                # Error response — the LLM should read error_code and
                # next_actions to decide what to do
                return json.dumps({"success": False, "http_status": resp.status_code, "error": result}, indent=2)
            if isinstance(result, list):
                # List responses (e.g. GET /vendors returns an array)
                # can't be spread with ** so we wrap them in "data"
                return json.dumps({"success": True, "http_status": resp.status_code, "data": result}, indent=2)
            # Normal dict response — spread it into the result
            return json.dumps({"success": True, "http_status": resp.status_code, **result}, indent=2)

        return fn

    # ── STAGE D: Package into a StructuredTool ───────────────────
    #
    # StructuredTool is LangChain's wrapper that combines:
    #   - name: what the LLM calls to invoke this tool
    #   - description: what the LLM reads to decide IF to use this tool
    #   - func: the function that runs when the tool is called
    #   - args_schema: the Pydantic model that defines valid arguments
    #
    # LangGraph's ReAct agent receives these tools and presents them to
    # the LLM as available actions. The LLM picks a tool, fills in the
    # arguments, LangGraph validates them against args_schema, then
    # calls func(**validated_args) and feeds the result back to the LLM.
    return StructuredTool(
        name=tool_name,
        description=description,
        func=make_fn(),
        args_schema=input_model,
    )


def _resolve_body_schema(request_body: dict, spec: dict) -> dict | None:
    """Extract the JSON schema from an OpenAPI requestBody definition.

    In the OpenAPI spec, a POST endpoint's body is described like:

        "requestBody": {
          "content": {
            "application/json": {
              "schema": {"$ref": "#/components/schemas/POCreate"}
            }
          }
        }

    This function digs through that structure to get the actual schema,
    resolving any $ref pointers along the way.

    Args:
        request_body: The "requestBody" dict from the operation
        spec: Full OpenAPI spec (needed to resolve $ref pointers)

    Returns:
        The resolved JSON schema dict with "properties" and "required",
        or None if no schema found.
    """
    content = request_body.get("content", {})
    json_content = content.get("application/json", {})
    schema = json_content.get("schema", {})
    return _resolve_ref(schema, spec)


def _resolve_ref(schema: dict, spec: dict) -> dict:
    """Follow $ref pointers in the OpenAPI spec to get the actual schema.

    OpenAPI uses $ref to avoid repeating schemas. For example:

        {"$ref": "#/components/schemas/POCreate"}

    points to:

        spec["components"]["schemas"]["POCreate"] = {
          "properties": {
            "vendor_id": {"type": "integer"},
            "line_items": {"type": "array", "items": {...}}
          },
          "required": ["vendor_id", "line_items"]
        }

    This function:
      1. Splits the $ref path by "/" to navigate the spec tree
      2. Recursively resolves in case the target also has $refs
      3. Also resolves $refs inside individual properties

    Args:
        schema: A schema dict that may contain "$ref"
        spec: Full OpenAPI spec to look up references in

    Returns:
        The fully resolved schema dict with no remaining $ref pointers.
    """
    if "$ref" in schema:
        # e.g. "#/components/schemas/POCreate" → ["components", "schemas", "POCreate"]
        ref_path = schema["$ref"]
        parts = ref_path.lstrip("#/").split("/")

        # Walk the spec tree: spec["components"]["schemas"]["POCreate"]
        resolved = spec
        for part in parts:
            resolved = resolved[part]

        # The resolved schema might itself contain $refs, so recurse
        return _resolve_ref(resolved, spec)

    # Even if the top-level schema isn't a $ref, its individual properties
    # might be. For example:
    #   "properties": {
    #     "status": {"$ref": "#/components/schemas/POStatus"}
    #   }
    if "properties" in schema:
        resolved_props = {}
        for name, prop in schema["properties"].items():
            resolved_props[name] = _resolve_ref(prop, spec)
        schema = {**schema, "properties": resolved_props}

    return schema


def _json_type_to_python(json_type: str) -> type:
    """Map a JSON Schema type string to its Python equivalent.

    JSON Schema uses string names for types. We need Python types
    for the Pydantic model.

    Mapping:
        "string"  → str
        "integer" → int
        "number"  → float
        "boolean" → bool

    Args:
        json_type: One of "string", "integer", "number", "boolean"

    Returns:
        The corresponding Python type. Defaults to str for unknown types.
    """
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
    }.get(json_type, str)


def _json_schema_to_python(schema: dict) -> type:
    """Map a JSON Schema property definition to a Python type.

    More complex than _json_type_to_python because it handles:

    1. Arrays: {"type": "array", "items": {...}} → list
       Used for line_items, gl_entries, etc.

    2. Nullable types via anyOf:
       {"anyOf": [{"type": "string"}, {"type": "null"}]} → str
       Used for optional fields like credit_limit that can be null.

    3. Simple types: {"type": "integer"} → int

    Args:
        schema: A JSON Schema property definition

    Returns:
        The Python type to use in the Pydantic model.
    """
    # Arrays (e.g. line_items: list of dicts)
    if schema.get("type") == "array":
        return list

    # Nullable types — OpenAPI represents "str or null" as:
    # {"anyOf": [{"type": "string"}, {"type": "null"}]}
    # We just take the first non-null type
    if "anyOf" in schema:
        types = [s.get("type") for s in schema["anyOf"] if "type" in s]
        if types:
            return _json_type_to_python(types[0])

    return _json_type_to_python(schema.get("type", "string"))


def _to_snake_case(name: str) -> str:
    """Convert an operationId to a clean snake_case tool name.

    FastAPI auto-generates operationIds from the function name + path.
    They're already snake_case, so we just normalize dashes.

    Example:
        "create_purchase_order_purchase_orders_post"
        → "create_purchase_order_purchase_orders_post" (unchanged)

    Args:
        name: The operationId from the OpenAPI spec

    Returns:
        A cleaned-up snake_case string safe to use as a tool name.
    """
    return name.replace("-", "_").lower()


def _path_to_name(method: str, path: str) -> str:
    """Generate a tool name from HTTP method + path when operationId is missing.

    Fallback for endpoints that don't have an operationId (rare with FastAPI,
    but possible with other frameworks).

    Example:
        ("POST", "/purchase-orders/{po_id}/receive")
        → "post_purchase_orders_po_id_receive"

    Args:
        method: HTTP method (GET, POST, etc.)
        path: URL path

    Returns:
        A snake_case string usable as a tool name.
    """
    clean = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    return f"{method.lower()}_{clean}"


# ── Quick test ──────────────────────────────────────────────────
# Run this file directly to see what tools get generated:
#   .venv/bin/python -m agent.openapi_tools

if __name__ == "__main__":
    tools = build_tools_from_openapi()
    print(f"\n{len(tools)} tools generated from OpenAPI spec:\n")
    for t in tools:
        fields = list(t.args_schema.model_fields.keys()) if t.args_schema else []
        print(f"  {t.name}({', '.join(fields)})")
        print(f"    {t.description[:80]}...")
        print()
