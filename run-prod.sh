#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down services...${NC}"
    
    # Stop docker compose
    echo -e "${BLUE}Stopping all services...${NC}"
    docker compose -f docker-compose.prod.yml down
    
    exit 0
}

# Trap Ctrl+C and cleanup
trap cleanup INT TERM

echo -e "${GREEN}🚀 Starting Memory Palace (Production Mode with Tailscale)${NC}"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}❌ Error: .env file not found!${NC}"
    echo -e "${YELLOW}Copy .env.example to .env and add your API keys:${NC}"
    echo "  cp .env.example .env"
    exit 1
fi

# Check for Tailscale auth key
if ! grep -q "TAILSCALE_AUTHKEY=" .env || grep -q "TAILSCALE_AUTHKEY=$" .env || grep -q "TAILSCALE_AUTHKEY=\"\"" .env; then
    echo -e "${RED}❌ Error: TAILSCALE_AUTHKEY not set in .env!${NC}"
    echo -e "${YELLOW}To get an auth key:${NC}"
    echo "  1. Go to https://login.tailscale.com/admin/settings/keys"
    echo "  2. Click 'Generate auth key'"
    echo "  3. Select: Reusable=Yes, Pre-authorized=Yes, Expiration=90 days"
    echo "  4. Add to .env: TAILSCALE_AUTHKEY=tskey-auth-xxxxx"
    echo ""
    read -p "Do you want to continue without Tailscale? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Build the image if needed
echo -e "${BLUE}Building Docker image...${NC}"
docker compose -f docker-compose.prod.yml build

# Start all services
echo -e "${BLUE}Starting all services...${NC}"
docker compose -f docker-compose.prod.yml up -d

# Wait for Neo4j to be ready
echo -e "${YELLOW}Waiting for Neo4j to be ready...${NC}"
until docker compose -f docker-compose.prod.yml exec -T neo4j cypher-shell -u neo4j -p password "RETURN 1" &>/dev/null; do
    sleep 1
    echo -n "."
done
echo -e "\n${GREEN}✓ Neo4j is ready!${NC}"

# Wait for API to be ready
echo -e "${YELLOW}Waiting for API to be ready...${NC}"
until curl -f http://localhost:8000/health &>/dev/null; do
    sleep 1
    echo -n "."
done
echo -e "\n${GREEN}✓ API is ready!${NC}"

# Check Tailscale status if auth key was provided
if grep -q "TAILSCALE_AUTHKEY=tskey" .env 2>/dev/null; then
    echo -e "${YELLOW}Waiting for Tailscale to connect...${NC}"
    
    # Wait for Tailscale container to be ready
    for i in {1..30}; do
        if docker compose -f docker-compose.prod.yml exec -T tailscale tailscale status &>/dev/null; then
            break
        fi
        sleep 1
        echo -n "."
    done
    echo ""
    
    # Try to get Tailscale hostname (using jq if available in container)
    TAILSCALE_HOSTNAME=$(docker compose -f docker-compose.prod.yml exec -T tailscale sh -c "tailscale status --json | jq -r '.Self.DNSName' 2>/dev/null || tailscale status --json | grep -o '\"DNSName\":\"[^\"]*\"' | cut -d'\"' -f4" 2>/dev/null || echo "")
    
    if [ ! -z "$TAILSCALE_HOSTNAME" ]; then
        echo -e "${GREEN}✓ Tailscale connected!${NC}"
        echo -e "${GREEN}✓ Tailscale Funnel enabled for public access!${NC}"
        TAILSCALE_URL="https://${TAILSCALE_HOSTNAME}"
        MCP_URL="https://${TAILSCALE_HOSTNAME}/mcp"
    else
        echo -e "${YELLOW}⚠ Tailscale is optional - continuing without it${NC}"
        echo -e "${YELLOW}  To enable: Add TAILSCALE_AUTHKEY to .env${NC}"
    fi
else
    echo -e "${BLUE}ℹ Tailscale not configured (optional)${NC}"
fi

echo -e "\n${GREEN}✅ All services started!${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${BLUE}Local Access:${NC}"
echo -e "    Neo4j Browser: ${BLUE}http://localhost:7474${NC}"
echo -e "    FastAPI Docs:  ${BLUE}http://localhost:8000/docs${NC}"
echo -e "    API Base:      ${BLUE}http://localhost:8000/api/v1${NC}"

if [ ! -z "$TAILSCALE_URL" ]; then
    echo -e "\n  ${CYAN}Tailscale Access:${NC}"
    echo -e "    Tailnet URL:   ${CYAN}${TAILSCALE_URL}${NC}"
    if [ ! -z "$MCP_URL" ]; then
        echo -e "    Claude.ai MCP: ${CYAN}${MCP_URL}${NC}"
    fi
fi

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "\n${YELLOW}Press Ctrl+C to stop all services${NC}\n"

# Function to prefix logs with service name
prefix_logs() {
    local service=$1
    local color=$2
    while IFS= read -r line; do
        echo -e "${color}[$service]${NC} $line"
    done
}

# Follow logs from all services
echo -e "${GREEN}📋 Following logs...${NC}\n"

# Follow logs
docker compose -f docker-compose.prod.yml logs -f