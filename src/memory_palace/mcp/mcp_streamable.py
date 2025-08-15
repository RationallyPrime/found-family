# mcp_streamable.py - Add this to your memory_palace/mcp/ directory
"""
Streamable HTTP transport for MCP - the new standard that Claude.ai requires.
This replaces the SSE-based fastapi-mcp which is now deprecated.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import logfire
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.logging import get_logger
from memory_palace.services.memory_service import MemoryService

logger = get_logger(__name__)

router = APIRouter()


class MCPRequest(BaseModel):
    """MCP JSON-RPC request."""
    jsonrpc: str = "2.0"
    id: str | int
    method: str
    params: dict[str, Any] | None = None


class MCPResponse(BaseModel):
    """MCP JSON-RPC response."""
    jsonrpc: str = "2.0"
    id: str | int
    result: Any = None
    error: dict[str, Any] | None = None


class MCPServerInfo(BaseModel):
    """MCP server information."""
    name: str = "memory-palace"
    version: str = "1.0.0"
    protocolVersion: str = "2024-11-05"  # Latest MCP protocol version


class MCPTool(BaseModel):
    """MCP tool definition."""
    name: str
    description: str
    inputSchema: dict[str, Any]


# MCP server instance state (in production, use Redis or similar)
mcp_sessions = {}


@router.get("/mcp")
@router.head("/mcp")
async def mcp_discovery(request: Request):
    """MCP discovery endpoint for Claude.ai."""
    
    client_protocol = request.headers.get("mcp-protocol-version", "2025-06-18")
    
    # Log all incoming headers for debugging
    headers = dict(request.headers)
    logger.info(
        "MCP discovery endpoint called",
        method=request.method,
        headers=headers,
        client=request.client.host if request.client else "unknown",
        url=str(request.url)
    )

    response = {
        "mcpVersion": "1.0",
        "protocolVersion": client_protocol,  # Use client's protocol version
        "serverInfo": {
            "name": "memory-palace",
            "version": "1.0.0",
            "protocolVersion": client_protocol
        },
        "capabilities": {
            "tools": {
                "listable": True
            },
            "prompts": False,
            "resources": False,
            "logging": False,
            "sampling": False
        },
        # ⚠️ CRITICAL CHANGE: Use "transport" NOT "transports"!
        "transport": {  # ← SINGULAR, NOT PLURAL!
            "type": "streamable-http",
            "endpoint": "https://memory-palace.sokrates.is/mcp/stream"
        },
        "tools": [
            {
                "name": "remember",
                "description": "Store a conversation turn in memory",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "user_content": {
                            "type": "string",
                            "description": "What the user said"
                        },
                        "assistant_content": {
                            "type": "string",
                            "description": "What Claude responded"
                        },
                        "salience": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.3,
                            "description": "Importance rating (0-1)"
                        }
                    },
                    "required": ["user_content", "assistant_content"]
                }
            },
            {
                "name": "recall",
                "description": "Search and recall relevant memories",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query"
                        },
                        "k": {
                            "type": "integer",
                            "default": 10,
                            "description": "Number of results"
                        },
                        "threshold": {
                            "type": "number",
                            "default": 0.7,
                            "minimum": 0,
                            "maximum": 1,
                            "description": "Similarity threshold"
                        },
                        "min_salience": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "description": "Minimum importance filter"
                        }
                    },
                    "required": ["query"]
                }
            }
        ]
    }

    logger.info("MCP discovery response sent", response_summary={
        "mcpVersion": response["mcpVersion"],
        "protocolVersion": response["protocolVersion"],
        "transport": response["transport"]["type"],
        "tools_count": len(response["tools"]),
        "tools_listable": response["capabilities"]["tools"]["listable"]
    })

    return response


async def stream_handler(
    request_iterator: AsyncIterator[MCPRequest],
    memory_service: MemoryService
) -> AsyncIterator[str]:
    """
    Handle streaming MCP requests and generate responses.
    This implements the Streamable HTTP transport.
    """
    last_activity = asyncio.get_event_loop().time()
    heartbeat_interval = 30  # Send heartbeat every 30 seconds

    async def heartbeat_generator():
        """Generate periodic heartbeat messages to keep connection alive."""
        while True:
            await asyncio.sleep(heartbeat_interval)
            current_time = asyncio.get_event_loop().time()
            if current_time - last_activity > heartbeat_interval:
                # Send a heartbeat/ping message
                heartbeat = MCPResponse(
                    id="heartbeat",
                    result={"type": "ping", "timestamp": datetime.now(UTC).isoformat()}
                )
                yield json.dumps(heartbeat.dict()) + "\n"
                logger.debug("Sent heartbeat to keep connection alive")

    # Create heartbeat task
    heartbeat_task = asyncio.create_task(heartbeat_generator().__anext__())

    try:
        async for mcp_request in request_iterator:
            last_activity = asyncio.get_event_loop().time()
            try:
                logger.info(f"MCP request: {mcp_request.method}", extra={
                    "id": mcp_request.id,
                    "method": mcp_request.method
                })

                # Handle different MCP methods
                if mcp_request.method == "initialize":
                    response = MCPResponse(
                        id=mcp_request.id,
                        result={
                            "protocolVersion": "2024-11-05",
                            "serverInfo": MCPServerInfo().dict(),
                            "capabilities": {
                                "tools": {}
                            }
                        }
                    )

                elif mcp_request.method == "tools/list":
                    response = MCPResponse(
                        id=mcp_request.id,
                        result={
                            "tools": [
                            {
                                "name": "remember",
                                "description": "Store a conversation turn in memory",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "user_content": {"type": "string"},
                                        "assistant_content": {"type": "string"},
                                        "salience": {"type": "number", "minimum": 0, "maximum": 1}
                                    },
                                    "required": ["user_content", "assistant_content"]
                                }
                            },
                            {
                                "name": "recall",
                                "description": "Search memories",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                        "k": {"type": "integer"},
                                        "threshold": {"type": "number"}
                                    },
                                    "required": ["query"]
                                }
                            }
                        ]
                    }
                )

                elif mcp_request.method == "tools/call":
                    # Extract tool name and arguments
                    params = mcp_request.params or {}
                    tool_name = params.get("name")
                    arguments = params.get("arguments", {})

                    if tool_name == "remember":
                        # Call the memory service
                        user_memory, assistant_memory = await memory_service.remember_turn(
                            user_content=arguments.get("user_content", ""),
                            assistant_content=arguments.get("assistant_content", ""),
                            salience=arguments.get("salience", 0.3)
                        )

                        response = MCPResponse(
                            id=mcp_request.id,
                            result={
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Stored memory with ID: {assistant_memory.id}"
                                }
                            ]
                        }
                    )

                    elif tool_name == "recall":
                        # Search memories
                        messages = await memory_service.search_memories(
                            query=arguments.get("query", ""),
                            limit=arguments.get("k", 10),
                            similarity_threshold=arguments.get("threshold", 0.7),
                            min_salience=arguments.get("min_salience")
                        )

                        # Format results
                        results = []
                        for msg in messages:
                            role = "user" if msg.memory_type.value == "friend_utterance" else "assistant"
                            results.append(f"[{role}]: {msg.content}")

                        response = MCPResponse(
                            id=mcp_request.id,
                            result={
                            "content": [
                                {
                                    "type": "text",
                                    "text": "\n\n".join(results) if results else "No relevant memories found"
                                }
                            ]
                        }
                    )
                    else:
                        response = MCPResponse(
                            id=mcp_request.id,
                            error={
                                "code": -32601,
                                "message": f"Unknown tool: {tool_name}"
                            }
                        )
                    
                else:
                    # Method not found
                    response = MCPResponse(
                        id=mcp_request.id,
                        error={
                            "code": -32601,
                            "message": f"Method not found: {mcp_request.method}"
                        }
                    )

                # Stream the response as JSONL
                yield json.dumps(response.dict()) + "\n"
                last_activity = asyncio.get_event_loop().time()

            except Exception as e:
                logger.error(f"MCP handler error: {e}", exc_info=True)
                error_response = MCPResponse(
                    id=mcp_request.id if mcp_request else 0,
                    error={
                        "code": -32603,
                        "message": str(e)
                    }
                )
                yield json.dumps(error_response.dict()) + "\n"
    finally:
        # Clean up heartbeat task
        if 'heartbeat_task' in locals():
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task


async def parse_request_stream(request: Request) -> AsyncIterator[MCPRequest]:
    """
    Parse incoming JSONL stream into MCP requests.
    """
    buffer = ""
    try:
        async for chunk in request.stream():
            buffer += chunk.decode('utf-8')

            # Process complete lines
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()

                if line:
                    try:
                        data = json.loads(line)
                        yield MCPRequest(**data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON in stream: {e}")
                        continue
    except ClientDisconnect:
        logger.info("Client disconnected gracefully")
        return
    except Exception as e:
        logger.error(f"Error reading request stream: {e}", exc_info=True)
        raise


@router.post("/mcp/stream")
@logfire.instrument("mcp_stream")
async def mcp_stream(
    request: Request,
    memory_service: MemoryService = Depends(get_memory_service)
):
    """
    Streamable HTTP endpoint for MCP.
    This is the new transport that Claude.ai requires.

    Accepts JSONL requests and returns JSONL responses.
    """
    # Log detailed request information for debugging
    headers = dict(request.headers)

    # Check for Cloudflare headers
    cf_headers = {
        "cf-ray": headers.get("cf-ray"),
        "cf-connecting-ip": headers.get("cf-connecting-ip"),
        "cf-ipcountry": headers.get("cf-ipcountry"),
        "cf-visitor": headers.get("cf-visitor"),
        "x-forwarded-for": headers.get("x-forwarded-for"),
        "x-forwarded-proto": headers.get("x-forwarded-proto"),
        "x-real-ip": headers.get("x-real-ip")
    }

    # Determine if request came through Cloudflare
    via_cloudflare = any(cf_headers.get(k) for k in ["cf-ray", "cf-connecting-ip"])

    logger.info(
        "MCP stream connection initiated",
        headers=headers,
        client=request.client.host if request.client else "unknown",
        content_type=headers.get("content-type"),
        user_agent=headers.get("user-agent"),
        url=str(request.url),
        via_cloudflare=via_cloudflare,
        cf_headers={k: v for k, v in cf_headers.items() if v}
    )

    # Track connection start time
    connection_start = datetime.now(UTC)

    try:
        # Parse the incoming request stream
        request_stream = parse_request_stream(request)

        # Handle requests and generate responses
        response_stream = stream_handler(request_stream, memory_service)

        # Return streaming response with correct content type
        return StreamingResponse(
            response_stream,
            media_type="application/x-jsonlines",
            headers={
                "Cache-Control": "no-cache",
                "X-MCP-Transport": "streamable-http",
                "X-Accel-Buffering": "no"  # Disable Nginx/proxy buffering
            }
        )
    except ClientDisconnect:
        duration = (datetime.now(UTC) - connection_start).total_seconds()
        logger.info(f"MCP stream client disconnected after {duration:.2f} seconds")
        return Response(status_code=204)
    except Exception as e:
        duration = (datetime.now(UTC) - connection_start).total_seconds()
        logger.error(f"MCP stream error after {duration:.2f} seconds: {e}", exc_info=True)
        raise


# Add a test endpoint
@router.post("/mcp/test")
async def test_mcp_tools(
    tool: str,
    args: dict[str, Any],
    memory_service: MemoryService = Depends(get_memory_service)
):
    """Test MCP tools directly without the streaming protocol."""
    if tool == "remember":
        user_memory, assistant_memory = await memory_service.remember_turn(
            user_content=args.get("user_content", ""),
            assistant_content=args.get("assistant_content", ""),
            salience=args.get("salience", 0.3)
        )
        return {"turn_id": str(assistant_memory.id)}

    elif tool == "recall":
        messages = await memory_service.search_memories(
            query=args.get("query", ""),
            limit=args.get("k", 10),
            similarity_threshold=args.get("threshold", 0.7)
        )
        return {
            "count": len(messages),
            "messages": [
                {
                    "content": msg.content,
                    "type": msg.memory_type.value,
                    "timestamp": msg.timestamp.isoformat()
                }
                for msg in messages
            ]
        }

    else:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool}")
