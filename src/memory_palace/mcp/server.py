"""MCP Server implementation for Claude.ai integration."""

import json
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel

from memory_palace.auth.oauth import TokenData, verify_token
from memory_palace.services.memory_service import MemoryService


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


async def get_current_claude(request: Request) -> TokenData:
    """Verify that the request is from authenticated Claude."""
    # Check for bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authentication")
    
    token = auth_header.replace("Bearer ", "")
    token_data = verify_token(token)
    
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid authentication")
    
    # Verify it's Claude
    if token_data.client_id != "claude":
        raise HTTPException(status_code=403, detail="Access denied")
    
    return token_data


class MCPServer:
    """MCP server handling Claude's memory operations."""
    
    def __init__(self, memory_service: MemoryService):
        self.memory_service = memory_service
        self.tools = self._register_tools()
    
    def _register_tools(self) -> dict:
        """Register available MCP tools."""
        return {
            "memory-palace/remember": {
                "description": "Store a conversation turn in my memory",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "friend_content": {
                            "type": "string",
                            "description": "What my friend said"
                        },
                        "claude_content": {
                            "type": "string",
                            "description": "What I (Claude) said"
                        },
                        "conversation_id": {
                            "type": "string",
                            "description": "Optional conversation ID"
                        }
                    },
                    "required": ["friend_content", "claude_content"]
                }
            },
            "memory-palace/recall": {
                "description": "Search my memories",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to search for"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max memories to return",
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
            },
            "memory-palace/get-context": {
                "description": "Get conversation context",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "conversation_id": {
                            "type": "string",
                            "description": "Conversation ID"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max turns to return",
                            "default": 20
                        }
                    },
                    "required": ["conversation_id"]
                }
            }
        }
    
    async def handle_request(self, request: MCPRequest) -> MCPResponse:
        """Handle an MCP request."""
        try:
            if request.method == "initialize":
                return await self._handle_initialize(request)
            elif request.method == "tools/list":
                return await self._handle_list_tools(request)
            elif request.method == "tools/call":
                return await self._handle_tool_call(request)
            else:
                return MCPResponse(
                    id=request.id,
                    error={
                        "code": -32601,
                        "message": f"Method not found: {request.method}"
                    }
                )
        except Exception as e:
            return MCPResponse(
                id=request.id,
                error={
                    "code": -32603,
                    "message": str(e)
                }
            )
    
    async def _handle_initialize(self, request: MCPRequest) -> MCPResponse:
        """Handle initialization request."""
        return MCPResponse(
            id=request.id,
            result={
                "protocolVersion": "0.1.0",
                "serverInfo": {
                    "name": "memory-palace",
                    "version": "1.0.0",
                    "description": "Claude's personal Memory Palace"
                },
                "capabilities": {
                    "tools": True,
                    "resources": False,
                    "prompts": False
                }
            }
        )
    
    async def _handle_list_tools(self, request: MCPRequest) -> MCPResponse:
        """List available tools."""
        tools = [
            {"name": name, **spec}
            for name, spec in self.tools.items()
        ]
        return MCPResponse(
            id=request.id,
            result={"tools": tools}
        )
    
    async def _handle_tool_call(self, request: MCPRequest) -> MCPResponse:
        """Execute a tool call."""
        if not request.params:
            return MCPResponse(
                id=request.id,
                error={"code": -32602, "message": "Invalid params"}
            )
        
        tool_name = request.params.get("name")
        arguments = request.params.get("arguments", {})
        
        if tool_name == "memory-palace/remember":
            result = await self._remember(arguments)
        elif tool_name == "memory-palace/recall":
            result = await self._recall(arguments)
        elif tool_name == "memory-palace/get-context":
            result = await self._get_context(arguments)
        else:
            return MCPResponse(
                id=request.id,
                error={"code": -32602, "message": f"Unknown tool: {tool_name}"}
            )
        
        return MCPResponse(
            id=request.id,
            result={"content": [{"type": "text", "text": json.dumps(result)}]}
        )
    
    async def _remember(self, args: dict) -> dict:
        """Store a memory."""
        friend_memory, claude_memory = await self.memory_service.remember_turn(
            user_content=args["friend_content"],
            assistant_content=args["claude_content"],
            conversation_id=UUID(args["conversation_id"]) if args.get("conversation_id") else None
        )
        
        return {
            "status": "success",
            "friend_memory_id": str(friend_memory.id),
            "claude_memory_id": str(claude_memory.id),
            "message": "Memory stored successfully"
        }
    
    async def _recall(self, args: dict) -> dict:
        """Recall memories."""
        memories = await self.memory_service.search_memories(
            query=args["query"],
            limit=args.get("limit", 10),
            similarity_threshold=args.get("threshold", 0.7)
        )
        
        return {
            "memories": [
                {
                    "id": str(m.id),
                    "content": m.content,
                    "type": m.memory_type.value,
                    "timestamp": m.timestamp.isoformat()
                }
                for m in memories
            ],
            "count": len(memories)
        }
    
    async def _get_context(self, args: dict) -> dict:
        """Get conversation context."""
        memories = await self.memory_service.get_conversation_history(
            conversation_id=UUID(args["conversation_id"]),
            limit=args.get("limit", 20)
        )
        
        return {
            "conversation_id": args["conversation_id"],
            "turns": [
                {
                    "id": str(m.id),
                    "content": m.content,
                    "type": m.memory_type.value,
                    "timestamp": m.timestamp.isoformat()
                }
                for m in memories
            ],
            "count": len(memories)
        }


async def create_mcp_sse_stream(
    memory_service: MemoryService,
    claude: TokenData
) -> AsyncGenerator[str]:
    """Create Server-Sent Events stream for MCP."""
    mcp_server = MCPServer(memory_service)
    
    # Send initial connection event
    yield f"data: {json.dumps({'type': 'connection', 'status': 'connected'})}\n\n"
    
    # Keep connection alive with heartbeat
    import asyncio
    while True:
        await asyncio.sleep(30)
        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"