"""Memory Palace FastAPI application with Remote MCP support."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from memory_palace.api import router

# Import OAuth and MCP components
from memory_palace.auth.oauth_endpoints import oauth_router
from memory_palace.mcp.remote_server import MCPRequest, MCPResponse, handle_mcp_http, handle_mcp_sse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    print("Starting Memory Palace...")
    yield
    # Shutdown
    print("Shutting down Memory Palace...")


app = FastAPI(
    title="Memory Palace",
    description="Personal memory system with persistent ontology and remote MCP support",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify allowed origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, prefix="/api/v1")

# Include OAuth routes
app.include_router(oauth_router)

# MCP endpoints
@app.get("/mcp/sse")
async def mcp_sse_endpoint():
    """MCP Server-Sent Events endpoint."""
    return await handle_mcp_sse()

@app.post("/mcp", response_model=MCPResponse)  
async def mcp_http_endpoint(request: MCPRequest):
    """MCP HTTP endpoint."""
    return await handle_mcp_http(request)

# Protected Resource Metadata (required for MCP OAuth discovery)
@app.get("/.well-known/mcp-server")
async def mcp_server_metadata():
    """MCP Server metadata for client discovery."""
    return {
        "mcp_version": "1.0",
        "server_info": {
            "name": "memory-palace", 
            "version": "0.1.0",
            "description": "Personal memory system with persistent ontology"
        },
        "capabilities": {
            "tools": True,
            "resources": False,
            "prompts": False
        },
        "transport": {
            "sse": "/mcp/sse",
            "http": "/mcp"
        },
        "auth": {
            "oauth2": {
                "authorization_server": "/.well-known/oauth-authorization-server"
            }
        }
    }

# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "memory-palace"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        # For HTTPS in production, add:
        # ssl_keyfile="path/to/key.pem",
        # ssl_certfile="path/to/cert.pem",
    )