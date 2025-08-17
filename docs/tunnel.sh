#!/bin/bash
set -e

echo "🌐 Setting up secure tunnel for Claude.ai access"
echo "================================================"
echo ""

# Check if cloudflared is installed
if ! command -v cloudflared &> /dev/null; then
    echo "📦 Installing Cloudflare Tunnel..."
    
    # Detect OS and install accordingly
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
        sudo dpkg -i cloudflared-linux-amd64.deb
        rm cloudflared-linux-amd64.deb
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install cloudflared
        else
            echo "Please install Homebrew first: https://brew.sh"
            exit 1
        fi
    else
        echo "Unsupported OS. Please install cloudflared manually:"
        echo "https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation"
        exit 1
    fi
fi

# Check if Memory Palace is running
if ! curl -s http://localhost:8000/health > /dev/null; then
    echo "⚠️  Memory Palace is not running. Please start it first:"
    echo "   ./run.sh"
    exit 1
fi

echo "✅ Memory Palace is running"
echo ""

# Generate a tunnel name based on friend name
FRIEND_NAME=$(grep FRIEND_NAME .env | cut -d'"' -f2 | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
TUNNEL_NAME="memory-palace-${FRIEND_NAME:-default}"

echo "🔐 Creating secure tunnel: $TUNNEL_NAME"
echo ""
echo "Starting Cloudflare Tunnel..."
echo "You'll receive a URL like: https://something.trycloudflare.com"
echo ""
echo "📋 To connect from Claude.ai:"
echo "   1. Copy the HTTPS URL shown below"
echo "   2. In Claude.ai, go to Settings > Developer > MCP Servers"
echo "   3. Add a new server with:"
echo "      - URL: <your-tunnel-url>/mcp"
echo "      - API Key: (from your .env file CLAUDE_API_KEY)"
echo ""
echo "Press Ctrl+C to stop the tunnel when done."
echo "----------------------------------------"
echo ""

# Run the tunnel
cloudflared tunnel --url http://localhost:8000