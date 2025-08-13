#!/bin/sh
# Enhanced Tailscale setup with Serve and Funnel for Memory Palace

set -e

echo "🚀 Starting Tailscale daemon..."
tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock &
sleep 5

echo "🔐 Authenticating with Tailscale..."
tailscale up --authkey=${TS_AUTHKEY} --hostname=memory-palace
sleep 5

# Get the actual Tailscale hostname for accurate URL display
TAILSCALE_HOSTNAME=$(tailscale status --json | jq -r '.Self.DNSName' 2>/dev/null || echo "memory-palace.[tailnet].ts.net")

echo "🔄 Setting up port forwarding from API container..."
socat TCP-LISTEN:8000,fork,reuseaddr TCP:memory-palace-api:8000 &

# Wait for API to be ready
echo "⏳ Waiting for Memory Palace API to be ready..."
until curl -sf http://127.0.0.1:8000/health > /dev/null 2>&1; do
  sleep 2
  echo -n "."
done
echo " Ready!"

# Configure Tailscale Serve for HTTPS (internal access)
echo "🔒 Configuring Tailscale Serve (HTTPS for tailnet)..."
tailscale serve --bg --https=443 http://127.0.0.1:8000

# Configure Tailscale Funnel for public access (needed for Claude.ai)
echo "🌐 Enabling Tailscale Funnel (public HTTPS access)..."
tailscale funnel --bg --https=443 http://127.0.0.1:8000

# Display access information
echo ""
echo "✅ Memory Palace is now accessible!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔗 Tailnet URL (internal): https://${TAILSCALE_HOSTNAME}"
echo "🌍 Public URL (Claude.ai): https://${TAILSCALE_HOSTNAME}/mcp"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Show serve status
tailscale serve status

# Keep container running
echo ""
echo "📡 Tailscale proxy running. Container will stay alive..."
tail -f /dev/null