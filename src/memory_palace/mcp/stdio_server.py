#!/usr/bin/env python3
"""
Memory Palace MCP Server with stdio transport for Claude Code CLI.

This script provides a stdio transport wrapper around our existing MCP server
implementation, allowing Claude Code to connect via stdin/stdout while our
main application continues to serve streamable HTTP for Claude.ai web.
"""

import json
from typing import Any

import anyio
import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from memory_palace.core.logging import get_logger

logger = get_logger(__name__)


def create_mcp_server(base_url: str = "http://localhost:8000") -> Server:
    """Create MCP server instance with stdio transport."""
    app = Server("memory-palace")
    
    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        """Route tool calls to FastAPI endpoints."""
        
        async with httpx.AsyncClient() as client:
            try:
                # Memory tools
                if name == "remember_turn":
                    response = await client.post(
                        f"{base_url}/api/v1/memory/remember",
                        json=arguments
                    )
                    response.raise_for_status()
                    result = response.json()
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=f"Stored turn {result['turn_id']}: {result['message']}"
                        )
                    ]
                
                elif name == "recall_memories":
                    response = await client.post(
                        f"{base_url}/api/v1/memory/recall",
                        json=arguments
                    )
                    response.raise_for_status()
                    result = response.json()
                    
                    messages = result.get("messages", [])
                    count = result.get("count", 0)
                    
                    if count == 0:
                        return [
                            types.TextContent(
                                type="text",
                                text="No memories found matching your query."
                            )
                        ]
                    
                    # Format the recalled memories
                    memory_text = f"Found {count} relevant memories:\n\n"
                    for msg in messages[:5]:  # Show first 5
                        memory_text += f"- {msg.get('content', 'No content')}\n"
                        if msg.get('metadata'):
                            memory_text += f"  Metadata: {json.dumps(msg['metadata'], indent=2)}\n"
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=memory_text
                        )
                    ]
                
                elif name == "execute_unified_query":
                    response = await client.post(
                        f"{base_url}/api/v1/unified/query",
                        json=arguments
                    )
                    response.raise_for_status()
                    result = response.json()
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                
                # Health and status tools
                elif name == "health_check":
                    response = await client.get(f"{base_url}/health")
                    response.raise_for_status()
                    result = response.json()
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=f"Service is {result['status']} at {result['timestamp']}"
                        )
                    ]
                
                elif name == "get_job_status":
                    response = await client.get(f"{base_url}/admin/jobs/status")
                    response.raise_for_status()
                    result = response.json()
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                
                elif name == "trigger_job":
                    job_id = arguments.get("job_id")
                    response = await client.post(f"{base_url}/admin/jobs/trigger/{job_id}")
                    response.raise_for_status()
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=f"Job {job_id} triggered successfully"
                        )
                    ]
                
                elif name == "get_cache_stats":
                    response = await client.get(f"{base_url}/admin/cache/stats")
                    response.raise_for_status()
                    result = response.json()
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                
                # Discovery tools
                elif name == "oauth_metadata":
                    response = await client.get(f"{base_url}/.well-known/oauth-authorization-server")
                    response.raise_for_status()
                    result = response.json()
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                
                elif name == "mcp_discovery":
                    response = await client.get(f"{base_url}/.well-known/mcp")
                    response.raise_for_status()
                    result = response.json()
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                
                else:
                    return [
                        types.TextContent(
                            type="text",
                            text=f"Unknown tool: {name}"
                        )
                    ]
                    
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error calling {name}: {e!s}"
                    )
                ]
    
    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        """List all available tools."""
        return [
            # Core memory operations
            types.Tool(
                name="remember_turn",
                description="Store a conversation turn in memory",
                inputSchema={
                    "type": "object",
                    "required": ["user_content", "assistant_content"],
                    "properties": {
                        "user_content": {"type": "string", "description": "The user's message"},
                        "assistant_content": {"type": "string", "description": "The assistant's response"},
                        "conversation_id": {"type": "string", "description": "Optional conversation ID"},
                        "salience": {"type": "number", "description": "Memory importance (0-1)"},
                        "metadata": {"type": "object", "description": "Additional metadata"},
                        "ontology_path": {"type": "array", "items": {"type": "string"}, "description": "Ontology path"}
                    }
                }
            ),
            types.Tool(
                name="recall_memories",
                description="Search and recall relevant memories",
                inputSchema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "k": {"type": "integer", "description": "Number of results", "default": 10},
                        "threshold": {"type": "number", "description": "Similarity threshold", "default": 0.7},
                        "min_salience": {"type": "number", "description": "Minimum salience"},
                        "topic_ids": {"type": "array", "items": {"type": "integer"}, "description": "Filter by topics"},
                        "ontology_path": {"type": "array", "items": {"type": "string"}, "description": "Filter by ontology"}
                    }
                }
            ),
            types.Tool(
                name="execute_unified_query",
                description="Execute a unified query using the declarative DSL",
                inputSchema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "object",
                            "description": "Declarative query DSL",
                            "properties": {
                                "filters": {"type": "object", "description": "Specification-based filters"},
                                "similarity": {"type": "object", "description": "Similarity search config"},
                                "limit": {"type": "integer", "description": "Max results", "default": 20},
                                "skip": {"type": "integer", "description": "Skip results", "default": 0},
                                "order_by": {"type": "string", "description": "Order by clause"},
                                "return_fields": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Fields to return"
                                }
                            }
                        },
                        "debug": {"type": "boolean", "description": "Enable debug mode", "default": False}
                    }
                }
            ),
            
            # Health and status tools
            types.Tool(
                name="health_check",
                description="Check the health status of the Memory Palace service",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            types.Tool(
                name="get_job_status",
                description="Get dream job orchestrator status",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            types.Tool(
                name="trigger_job",
                description="Manually trigger a specific dream job",
                inputSchema={
                    "type": "object",
                    "required": ["job_id"],
                    "properties": {
                        "job_id": {"type": "string", "description": "The job ID to trigger"}
                    }
                }
            ),
            types.Tool(
                name="get_cache_stats",
                description="Get basic statistics about the embedding cache",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            
            # Discovery tools
            types.Tool(
                name="oauth_metadata",
                description="Get OAuth 2.0 Authorization Server Metadata",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            types.Tool(
                name="mcp_discovery",
                description="Get MCP discovery information",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
        ]
    
    return app


async def main():
    """Main entry point for stdio MCP server."""
    logger.info("Starting Memory Palace MCP Server with stdio transport")
    
    # Test FastAPI connection first
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/health", timeout=5.0)
            logger.info(f"FastAPI server status: {response.status_code}")
    except Exception as e:
        logger.error(f"Cannot connect to FastAPI server: {e}")
        # Continue anyway - the server might start after initialization
    
    # Create the MCP server instance
    app = create_mcp_server()
    
    # Run with stdio transport
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)