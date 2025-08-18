#!/bin/bash

# Setup script for Cloudflare Tunnel
# Replaces Tailscale for Memory Palace public access

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🌐 Setting up Cloudflare Tunnel for Memory Palace${NC}"
echo ""

# Check if cloudflared is installed
if ! command -v cloudflared &> /dev/null; then
    echo -e "${RED}❌ cloudflared is not installed!${NC}"
    echo "Install it from: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/tunnel-cli/"
    exit 1
fi

echo -e "${GREEN}✓ cloudflared found: $(cloudflared --version)${NC}"

# Check if authenticated
if [ ! -f ~/.cloudflared/cert.pem ]; then
    echo -e "${YELLOW}⚠ Not authenticated with Cloudflare${NC}"
    echo -e "${BLUE}Please run: cloudflared tunnel login${NC}"
    echo "This will open a browser to authenticate."
    echo ""
    read -p "Press Enter after you've authenticated..." 
fi

# Get domain from user
echo ""
echo -e "${BLUE}Enter your domain for the tunnel:${NC}"
echo "Examples: memory-palace.example.com, mp.yourdomain.com"
read -p "Domain: " DOMAIN

if [ -z "$DOMAIN" ]; then
    echo -e "${RED}Domain is required!${NC}"
    exit 1
fi

# Create tunnel
echo -e "${BLUE}Creating tunnel 'memory-palace'...${NC}"
if cloudflared tunnel create memory-palace 2>&1 | grep -q "already exists"; then
    echo -e "${YELLOW}Tunnel already exists, using existing tunnel${NC}"
else
    echo -e "${GREEN}✓ Tunnel created successfully${NC}"
fi

# Update config file with domain
echo -e "${BLUE}Updating configuration...${NC}"
SCRIPT_DIR=$(dirname "$0")
sed -i "s/memory-palace.YOUR_DOMAIN.com/${DOMAIN}/g" "${SCRIPT_DIR}/cloudflare-tunnel-config.yml"
cp "${SCRIPT_DIR}/cloudflare-tunnel-config.yml" ~/.cloudflared/config.yml
echo -e "${GREEN}✓ Configuration updated${NC}"

# Set up DNS routing
echo -e "${BLUE}Setting up DNS routing...${NC}"
cloudflared tunnel route dns memory-palace "${DOMAIN}" || {
    echo -e "${YELLOW}DNS routing may already be configured${NC}"
}

# Test the tunnel
echo -e "${BLUE}Testing tunnel connection...${NC}"
echo "Starting tunnel (press Ctrl+C to stop after testing)..."
echo ""
echo -e "${GREEN}Your Memory Palace will be available at:${NC}"
echo -e "${GREEN}  Public URL: https://${DOMAIN}${NC}"
echo -e "${GREEN}  MCP Endpoint: https://${DOMAIN}/mcp${NC}"
echo ""
echo "Starting in 3 seconds..."
sleep 3

# Run tunnel (user can Ctrl+C when ready)
cloudflared tunnel --config ~/.cloudflared/config.yml run memory-palace