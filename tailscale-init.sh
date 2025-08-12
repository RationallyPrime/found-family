#!/bin/sh
# Start Tailscale daemon
tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock &
sleep 5

# Authenticate with Tailscale
tailscale up --authkey=${TS_AUTHKEY} --hostname=memory-palace
sleep 5

# Set up port forwarding from Tailscale container to API container
socat TCP-LISTEN:8000,fork,reuseaddr TCP:memory-palace-api:8000 &

# Wait for API to be ready
until curl -f http://127.0.0.1:8000/health; do
  echo "Waiting for API..."
  sleep 2
done

# Configure Tailscale Serve for HTTPS
tailscale serve --bg --https=443 http://127.0.0.1:8000

# Show the URL
echo "Memory Palace available at:"
tailscale serve status

# Keep container running
tail -f /dev/null