# Update your mcp_stream function in mcp_streamable.py:

@router.post("/mcp/stream")
async def mcp_stream(
    request: Request,
    memory_service: MemoryService = Depends(get_memory_service),
    authorization: Optional[str] = Header(None)
):
    """
    Streamable HTTP endpoint for MCP.
    This is the new transport that Claude.ai requires.
    
    Accepts JSONL requests and returns JSONL responses.
    """
    # Get protocol version from headers
    protocol_version = request.headers.get("mcp-protocol-version", "2025-06-18")
    
    logger.info(
        "MCP stream connection initiated",
        extra={
            "protocol_version": protocol_version,
            "has_auth": bool(authorization),
            "content_type": request.headers.get("content-type"),
            "headers": dict(request.headers)
        }
    )
    
    # Validate OAuth token if present
    if authorization:
        from memory_palace.api.oauth import verify_token
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        token_data = verify_token(token)
        if token_data:
            logger.info(f"Valid OAuth token for client: {token_data.client_id}")
        else:
            logger.warning("Invalid OAuth token, continuing anyway")
    
    # Parse the incoming request stream
    request_stream = parse_request_stream(request)
    
    # Handle requests with the correct protocol version
    response_stream = stream_handler(request_stream, memory_service, protocol_version)
    
    # Return streaming response with correct content type and headers
    return StreamingResponse(
        response_stream,
        media_type="application/x-jsonlines",  # Claude expects this
        headers={
            "Cache-Control": "no-cache",
            "X-MCP-Transport": "streamable-http",
            "MCP-Protocol-Version": protocol_version,  # Echo back the protocol version
            # Allow Claude.ai origin
            "Access-Control-Allow-Origin": "https://claude.ai",
            "Access-Control-Allow-Credentials": "true"
        }
    )


# Also update your stream_handler to properly handle initialize:
async def stream_handler(
    request_iterator: AsyncIterator[MCPRequest],
    memory_service: MemoryService,
    protocol_version: str = "2025-06-18"
) -> AsyncIterator[str]:
    """
    Handle streaming MCP requests and generate responses.
    This implements the Streamable HTTP transport.
    """
    async for mcp_request in request_iterator:
        try:
            logger.info(
                f"MCP request received",
                extra={
                    "id": mcp_request.id,
                    "method": mcp_request.method,
                    "params": mcp_request.params
                }
            )
            
            # Handle different MCP methods
            if mcp_request.method == "initialize":
                # Claude sends this first to establish the session
                response = MCPResponse(
                    id=mcp_request.id,
                    result={
                        "protocolVersion": protocol_version,
                        "serverInfo": {
                            "name": "memory-palace",
                            "version": "1.0.0",
                            "protocolVersion": protocol_version
                        },
                        "capabilities": {
                            "tools": {
                                "listable": True
                            },
                            "prompts": False,
                            "resources": False,
                            "logging": False,
                            "sampling": False
                        }
                    }
                )
                logger.info("Sent initialize response")
                
            elif mcp_request.method == "tools/list":
                # Claude asks for the list of available tools
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
                logger.info("Sent tools list")
                
            elif mcp_request.method == "tools/call":
                # Extract tool name and arguments
                params = mcp_request.params or {}
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                
                logger.info(f"Tool call: {tool_name}", extra={"arguments": arguments})
                
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
                logger.warning(f"Unknown MCP method: {mcp_request.method}")
                response = MCPResponse(
                    id=mcp_request.id,
                    error={
                        "code": -32601,
                        "message": f"Method not found: {mcp_request.method}"
                    }
                )
            
            # Stream the response as JSONL
            response_json = json.dumps(response.dict(exclude_none=True))
            logger.info(f"Sending response: {response_json[:200]}...")
            yield response_json + "\n"
            
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