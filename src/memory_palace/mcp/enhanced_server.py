"""Enhanced MCP Server with proper error handling integration.

This module extends fastapi-mcp to properly handle errors using our existing
error handling architecture, ensuring detailed error information is preserved
in JSON-RPC responses.
"""

import json
from typing import Any, Dict, List, Optional, Union

import httpx
import mcp.types as types
from fastapi import FastAPI, HTTPException
from fastapi_mcp import FastApiMCP

from memory_palace.core.base import ApplicationError, ErrorCode, ErrorLevel
from memory_palace.core.error_context import ErrorContextManager
from memory_palace.core.handlers import ErrorHandler
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)


class EnhancedMCPServer(FastApiMCP):
    """Extended MCP server with proper error handling that preserves error details."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Create an error handler for MCP errors
        self.error_handler = ErrorHandler(ErrorContextManager(None))
    
    async def _execute_api_tool(
        self,
        client: httpx.AsyncClient,
        tool_name: str,
        arguments: Dict[str, Any],
        operation_map: Dict[str, Dict[str, Any]],
        http_request_info: Optional[Any] = None,
    ) -> List[Union[types.TextContent, types.ImageContent, types.EmbeddedResource]]:
        """
        Execute an MCP tool with proper error handling that preserves error details.
        
        This overrides the parent method to catch HTTPExceptions and format them
        properly for MCP clients, preserving all error details in the response.
        """
        try:
            # Call the parent implementation
            return await super()._execute_api_tool(
                client=client,
                tool_name=tool_name,
                arguments=arguments,
                operation_map=operation_map,
                http_request_info=http_request_info,
            )
        except Exception as e:
            # Extract the actual error details from the wrapped exception
            error_details = {}
            error_message = str(e)
            error_code = ErrorCode.PROCESSING_FAILED
            
            # Parse the error message from fastapi-mcp which contains the response
            if "Status code:" in error_message and "Response:" in error_message:
                try:
                    # Extract the JSON response from the error message
                    response_start = error_message.find("Response:") + len("Response:")
                    response_json = error_message[response_start:].strip()
                    
                    # Try to parse it as JSON
                    try:
                        response_data = json.loads(response_json)
                        if isinstance(response_data, dict):
                            # Extract meaningful error information
                            if "detail" in response_data:
                                error_details = response_data
                                error_message = response_data.get("detail", error_message)
                            elif "error" in response_data:
                                error_details = response_data
                                error_message = response_data.get("error", error_message)
                            else:
                                error_details = response_data
                    except json.JSONDecodeError:
                        # If it's not JSON, use the raw response
                        error_details = {"raw_response": response_json}
                except Exception as parse_error:
                    logger.warning(f"Failed to parse error response: {parse_error}")
                    error_details = {"original_error": error_message}
            
            # Log the error with full context
            logger.error(
                f"MCP tool execution failed for {tool_name}",
                extra={
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "error_message": error_message,
                    "error_details": error_details,
                },
                exc_info=True,
            )
            
            # Format the error response in a way that MCP clients can understand
            # Include both a human-readable message and structured data
            error_response = {
                "error": error_message,
                "tool": tool_name,
                "details": error_details,
                "type": "tool_execution_error",
            }
            
            # Return the error as structured text content
            # MCP clients will see this as the tool response
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(error_response, indent=2, ensure_ascii=False)
                )
            ]


def create_enhanced_mcp(app: FastAPI, **kwargs) -> EnhancedMCPServer:
    """
    Factory function to create an enhanced MCP server with proper error handling.
    
    Args:
        app: The FastAPI application
        **kwargs: Additional arguments to pass to FastApiMCP
    
    Returns:
        An EnhancedMCPServer instance with improved error handling
    """
    return EnhancedMCPServer(app, **kwargs)