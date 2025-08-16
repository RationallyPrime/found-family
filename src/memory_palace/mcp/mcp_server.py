"""
MCP Server implementation using the official MCP SDK.

This is the single, authoritative MCP implementation for Memory Palace.
It exposes all our FastAPI endpoints as MCP tools and handles the 
streamable HTTP transport that Claude.ai requires.
"""

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send

from memory_palace.core.logging import get_logger

logger = get_logger(__name__)


class MemoryPalaceMCPServer:
    """MCP Server for Memory Palace using the official SDK."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        """Initialize the MCP server.
        
        Args:
            base_url: The base URL for the FastAPI application
        """
        self.base_url = base_url
        self.app = Server("memory-palace")
        self.session_manager: StreamableHTTPSessionManager | None = None
        self._setup_tools()
        
    def _setup_tools(self):
        """Set up all MCP tools and handlers."""
        
        # The MCP SDK handles initialization automatically
        # We only need to define tools and list_tools
        
        # Memory operations
        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.ContentBlock]:
            """Route tool calls to appropriate handlers."""
            
            # Memory tools
            if name == "remember_turn":
                return await self._remember_turn(arguments)
            elif name == "recall_memories":
                return await self._recall_memories(arguments)
            elif name == "execute_unified_query":
                return await self._execute_unified_query(arguments)
            
            # Health and status tools
            elif name == "health_check":
                return await self._health_check()
            elif name == "get_job_status":
                return await self._get_job_status()
            elif name == "trigger_job":
                return await self._trigger_job(arguments)
            elif name == "get_cache_stats":
                return await self._get_cache_stats()
            
            # OAuth tools (for completeness, though Claude.ai handles OAuth separately)
            elif name == "oauth_metadata":
                return await self._oauth_metadata()
            elif name == "mcp_discovery":
                return await self._mcp_discovery()
            
            else:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Unknown tool: {name}"
                    )
                ]
        
        @self.app.list_tools()
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
    
    async def _remember_turn(self, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        """Store a conversation turn."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/v1/memory/remember",
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
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error storing memory: {e!s}"
                    )
                ]
    
    async def _recall_memories(self, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        """Recall memories based on query."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/v1/memory/recall",
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
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error recalling memories: {e!s}"
                    )
                ]
    
    async def _execute_unified_query(self, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        """Execute a unified query."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/v1/unified/query",
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
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error executing query: {e!s}"
                    )
                ]
    
    async def _health_check(self) -> list[types.ContentBlock]:
        """Check health status."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.base_url}/health")
                response.raise_for_status()
                result = response.json()
                
                return [
                    types.TextContent(
                        type="text",
                        text=f"Service is {result['status']} at {result['timestamp']}"
                    )
                ]
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Health check failed: {e!s}"
                    )
                ]
    
    async def _get_job_status(self) -> list[types.ContentBlock]:
        """Get job status."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.base_url}/admin/jobs/status")
                response.raise_for_status()
                result = response.json()
                
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(result, indent=2)
                    )
                ]
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error getting job status: {e!s}"
                    )
                ]
    
    async def _trigger_job(self, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        """Trigger a job."""
        job_id = arguments.get("job_id")
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(f"{self.base_url}/admin/jobs/trigger/{job_id}")
                response.raise_for_status()
                
                return [
                    types.TextContent(
                        type="text",
                        text=f"Job {job_id} triggered successfully"
                    )
                ]
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error triggering job: {e!s}"
                    )
                ]
    
    async def _get_cache_stats(self) -> list[types.ContentBlock]:
        """Get cache stats."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.base_url}/admin/cache/stats")
                response.raise_for_status()
                result = response.json()
                
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(result, indent=2)
                    )
                ]
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error getting cache stats: {e!s}"
                    )
                ]
    
    async def _oauth_metadata(self) -> list[types.ContentBlock]:
        """Get OAuth metadata."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.base_url}/.well-known/oauth-authorization-server")
                response.raise_for_status()
                result = response.json()
                
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(result, indent=2)
                    )
                ]
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error getting OAuth metadata: {e!s}"
                    )
                ]
    
    async def _mcp_discovery(self) -> list[types.ContentBlock]:
        """Get MCP discovery info."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.base_url}/.well-known/mcp")
                response.raise_for_status()
                result = response.json()
                
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(result, indent=2)
                    )
                ]
            except httpx.HTTPError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error getting MCP discovery: {e!s}"
                    )
                ]
    
    def create_session_manager(self) -> StreamableHTTPSessionManager:
        """Create the StreamableHTTP session manager."""
        # Use event store for session persistence and resumability
        from memory_palace.mcp.event_store import InMemoryEventStore
        from mcp.server.streamable_http_manager import TransportSecuritySettings
        
        # Create security settings - allow Claude.ai origins
        security_settings = TransportSecuritySettings(
            allowed_origins=["https://claude.ai", "https://*.claude.ai"],
            allowed_hosts=["memory-palace.sokrates.is"]
        )
        
        self.session_manager = StreamableHTTPSessionManager(
            app=self.app,
            event_store=InMemoryEventStore(max_events_per_stream=200),  # Enable session persistence
            json_response=False,  # Use SSE for streaming
            security_settings=security_settings,  # Security settings for CORS
        )
        return self.session_manager
    
    async def handle_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI handler for streamable HTTP connections."""
        if not self.session_manager:
            raise RuntimeError("Session manager not initialized. Call create_session_manager() first.")
        
        # Log incoming MCP requests for debugging
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "UNKNOWN")
        headers = dict(scope.get("headers", []))
        
        logger.info(f"🌐 MCP Request: {method} {path}")
        logger.info(f"🔑 Authorization header: {'Authorization' in headers}")
        
        await self.session_manager.handle_request(scope, receive, send)
    
    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """Context manager for running the session manager."""
        if not self.session_manager:
            self.create_session_manager()
        
        async with self.session_manager.run():
            logger.info("MCP Server started with StreamableHTTP transport")
            try:
                yield
            finally:
                logger.info("MCP Server shutting down...")


# Global instance that will be created in main.py
mcp_server: MemoryPalaceMCPServer | None = None


def get_mcp_server() -> MemoryPalaceMCPServer:
    """Get the global MCP server instance."""
    global mcp_server
    if not mcp_server:
        mcp_server = MemoryPalaceMCPServer()
    return mcp_server