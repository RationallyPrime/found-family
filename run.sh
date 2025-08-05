#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down services...${NC}"
    
    # Kill the uvicorn process
    if [ ! -z "$APP_PID" ]; then
        echo -e "${BLUE}Stopping FastAPI app...${NC}"
        kill $APP_PID 2>/dev/null
    fi
    
    # Stop docker compose
    echo -e "${BLUE}Stopping Neo4j...${NC}"
    docker compose down
    
    exit 0
}

# Trap Ctrl+C and cleanup
trap cleanup INT TERM

echo -e "${GREEN}🚀 Starting Memory Palace${NC}"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}❌ Error: .env file not found!${NC}"
    echo -e "${YELLOW}Copy .env.example to .env and add your API keys:${NC}"
    echo "  cp .env.example .env"
    exit 1
fi

# Start Neo4j in background
echo -e "${BLUE}Starting Neo4j...${NC}"
docker compose up -d neo4j

# Wait for Neo4j to be ready
echo -e "${YELLOW}Waiting for Neo4j to be ready...${NC}"
until docker compose exec -T neo4j cypher-shell -u neo4j -p password "RETURN 1" &>/dev/null; do
    sleep 1
    echo -n "."
done
echo -e "\n${GREEN}✓ Neo4j is ready!${NC}"

# Start the FastAPI app in background
echo -e "${BLUE}Starting FastAPI app...${NC}"
uv run uvicorn memory_palace.main:app --reload --host 0.0.0.0 --port 8000 &
APP_PID=$!

# Give the app a moment to start
sleep 2

echo -e "\n${GREEN}✅ All services started!${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Neo4j Browser: ${BLUE}http://localhost:7474${NC}"
echo -e "  FastAPI Docs:  ${BLUE}http://localhost:8000/docs${NC}"
echo -e "  API Base:      ${BLUE}http://localhost:8000/api/v1${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "\n${YELLOW}Press Ctrl+C to stop all services${NC}\n"

# Function to prefix logs with service name
prefix_logs() {
    local service=$1
    local color=$2
    while IFS= read -r line; do
        echo -e "${color}[$service]${NC} $line"
    done
}

# Follow logs from both services
echo -e "${GREEN}📋 Following logs...${NC}\n"

# Start following docker logs in background
docker compose logs -f neo4j 2>&1 | prefix_logs "Neo4j" "$BLUE" &

# Wait for the FastAPI process
wait $APP_PID