"""Remote MCP Server implementation with OAuth authentication."""
import json
from typing import Any

from fastapi import Depends, Request
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from memory_palace.api.dependencies import get_memory_service
from memory_palace.services.memory_service import MemoryService

from .oauth_auth import User, get_current_user


class MCPRequest(BaseModel):
    """MCP protocol request."""
    jsonrpc: str = "2.0"
    id: str | None = None
    method: str
    params: dict[str, Any] | None = None


class MCPResponse(BaseModel):
    """MCP protocol response."""
    jsonrpc: str = "2.0"
    id: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class RemoteMCPServer:
    """Remote MCP server with OAuth authentication."""
    
    def __init__(self):
        self.tools = {
            "remember": {
                "name": "remember",
                "description": "Store a conversation turn in memory",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "user_content": {
                            "type": "string",
                            "description": "User message content"
                        },
                        "assistant_content": {
                            "type": "string", 
                            "description": "Assistant response content"
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Optional metadata"
                        }
                    },
                    "required": ["user_content", "assistant_content"]
                }
            },
            "recall": {
                "name": "recall",
                "description": "Search and recall relevant memories",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for memories"
                        },
                        "k": {
                            "type": "integer",
                            "description": "Number of memories to return",
                            "default": 10
                        },
                        "threshold": {
                            "type": "number",
                            "description": "Similarity threshold (0-1)",
                            "default": 0.7
                        }
                    },
                    "required": ["query"]
                }
            }
        }
    
    async def handle_request(
        self,
        request: MCPRequest,
        memory_service: MemoryService,
        user: User,
    ) -> MCPResponse:
        """Handle MCP protocol request."""
        try:
            if request.method == "tools/list":
                return MCPResponse(
                    id=request.id,
                    result={
                        "tools": list(self.tools.values())
                    }
                )
            
            elif request.method == "tools/call":
                tool_name = request.params.get("name")
                arguments = request.params.get("arguments", {})
                
                if tool_name == "remember":
                    result = await self._handle_remember(arguments, memory_service)
                elif tool_name == "recall":
                    result = await self._handle_recall(arguments, memory_service)
                else:
                    raise ValueError(f"Unknown tool: {tool_name}")
                
                return MCPResponse(
                    id=request.id,
                    result=result
                )
            
            elif request.method == "initialize":
                return MCPResponse(
                    id=request.id,
                    result={
                        "protocolVersion": "1.0",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": "memory-palace",
                            "version": "0.1.0"
                        }
                    }
                )
            
            else:
                raise ValueError(f"Unknown method: {request.method}")
                
        except Exception as e:
            return MCPResponse(
                id=request.id,
                error={
                    "code": -32603,  # Internal error
                    "message": str(e)
                }
            )
    
    async def _handle_remember(self, arguments: dict, memory_service: MemoryService) -> dict:
        """Handle remember tool call."""
        user_content = arguments.get("user_content")
        assistant_content = arguments.get("assistant_content")
        metadata = arguments.get("metadata")
        
        if not user_content or not assistant_content:
            raise ValueError("user_content and assistant_content are required")
        
        turn = await memory_service.store_turn(
            user_content=user_content,
            assistant_content=assistant_content,
            metadata=metadata,
        )
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Stored conversation turn with ID: {turn.id}"
                }
            ]
        }
    
    async def _handle_recall(self, arguments: dict, memory_service: MemoryService) -> dict:
        """Handle recall tool call."""
        query = arguments.get("query")
        k = arguments.get("k", 10)
        threshold = arguments.get("threshold", 0.7)
        
        if not query:
            raise ValueError("query is required")
        
        memories = await memory_service.search_memories(
            query=query,
            k=k,
            threshold=threshold,
        )
        
        if not memories:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "No relevant memories found."
                    }
                ]
            }
        
        # Format memories for response
        memory_text = "Found relevant memories:\n\n"
        for i, memory in enumerate(memories, 1):
            memory_text += f"{i}. [{memory.role.value}] {memory.content[:200]}...\n"
            memory_text += f"   Timestamp: {memory.timestamp.isoformat()}\n\n"
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": memory_text
                }
            ]
        }


# Global MCP server instance
mcp_server = RemoteMCPServer()


async def handle_mcp_sse(
    request: Request,
    memory_service: MemoryService = Depends(get_memory_service),
    user: User = Depends(get_current_user),
):
    """Handle MCP over Server-Sent Events."""
    
    async def event_generator():
        """Generate SSE events for MCP protocol."""
        # Send initialization
        yield {
            "data": json.dumps({
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "1.0",
                    "capabilities": {
                        "tools": {}
                    }
                }
            })
        }
        
        # In a real implementation, you'd handle bidirectional communication
        # For now, just keep the connection alive
        import asyncio
        while True:
            await asyncio.sleep(30)  # Keep-alive
            yield {"data": json.dumps({"type": "ping"})}
    
    return EventSourceResponse(event_generator())


async def handle_mcp_http(
    request: MCPRequest,
    memory_service: MemoryService = Depends(get_memory_service),
    user: User = Depends(get_current_user),
) -> MCPResponse:
    """Handle MCP over HTTP."""
    return await mcp_server.handle_request(request, memory_service, user)