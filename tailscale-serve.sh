#!/bin/sh
# Dynamic Tailscale setup script for Memory Palace

echo "Starting Tailscale daemon..."
tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock &
sleep 5

echo "Authenticating with Tailscale..."
tailscale up --authkey="${TS_AUTHKEY}" --hostname=memory-palace

echo "Waiting for Tailscale to connect..."
sleep 5

# Get the actual Tailscale hostname
TAILSCALE_HOSTNAME=$(tailscale status --json | grep -o '"Self":{[^}]*"DNSName":"[^"]*"' | sed 's/.*"DNSName":"\([^"]*\)".*/\1/')
echo "Memory Palace accessible at: https://${TAILSCALE_HOSTNAME}"

# Configure Tailscale Serve to proxy to the API
echo "Configuring Tailscale Serve..."
tailscale serve https / proxy http://127.0.0.1:8000

echo "Tailscale setup complete!"
echo "Your Memory Palace URL for Claude.ai: https://${TAILSCALE_HOSTNAME}/mcp"

# Keep the container running
wait