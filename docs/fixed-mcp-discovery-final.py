# Replace your mcp_discovery function in mcp_streamable.py with this:

@router.get("/mcp")
@router.head("/mcp")
async def mcp_discovery(request: Request):
    """
    MCP discovery endpoint for Claude.ai.
    Returns server capabilities and transport information.
    """
    # Get the protocol version from request headers
    client_protocol = request.headers.get("mcp-protocol-version", "2025-06-18")
    
    logger.info(
        "MCP discovery endpoint called",
        extra={
            "method": request.method,
            "client": request.client.host if request.client else None,
            "headers": dict(request.headers),
            "protocol_version": client_protocol,
        }
    )
    
    response = {
        "mcpVersion": "1.0",
        "protocolVersion": client_protocol,  # CRITICAL: Add at root level
        "serverInfo": {
            "name": "memory-palace",
            "version": "1.0.0",
            "protocolVersion": client_protocol
        },
        "capabilities": {
            "tools": {
                "listable": True  # Indicate tools can be listed
            },
            "prompts": False,
            "resources": False,
            "logging": False,
            "sampling": False
        },
        # CRITICAL: Use "transport" (singular) not "transports" (plural)
        "transport": {  # NOT "transports"!
            "type": "streamable-http",
            "endpoint": "https://memory-palace.sokrates.is/mcp/stream"
        },
        # Include tools in discovery for convenience
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
    
    logger.info(
        "MCP discovery response sent",
        extra={
            "mcpVersion": response["mcpVersion"],
            "protocolVersion": response["protocolVersion"],
            "transport_type": response["transport"]["type"],
            "endpoint": response["transport"]["endpoint"],
            "tools_count": len(response["tools"])
        }
    )
    
    # For HEAD requests, return empty body with headers
    if request.method == "HEAD":
        return Response(
            content="",
            headers={
                "Content-Type": "application/json",
                "MCP-Protocol-Version": client_protocol
            }
        )
    
    return response