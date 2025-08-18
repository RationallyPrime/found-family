#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🏛️ Welcome to the Memory Palace setup!${NC}"
echo "======================================"
echo ""
echo "This will help you personalize your Memory Palace and set up the required services."
echo ""

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed!${NC}"
    echo "Please install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check for Docker Compose
if ! docker compose version &> /dev/null; then
    echo -e "${RED}❌ Docker Compose is not installed!${NC}"
    echo "Please install Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
fi

echo -e "${GREEN}✓ Docker and Docker Compose found${NC}"

# Check if copier is installed
if ! command -v copier &> /dev/null; then
    echo -e "${YELLOW}📦 Installing Copier...${NC}"
    if command -v uv &> /dev/null; then
        uv tool install copier || {
            echo -e "${RED}Failed to install Copier${NC}"
            exit 1
        }
    else
        echo -e "${YELLOW}Installing uv first...${NC}"
        curl -LsSf https://astral.sh/uv/install.sh | sh || {
            echo -e "${RED}Failed to install uv${NC}"
            exit 1
        }
        source ~/.bashrc || source ~/.zshrc || true  # Reload shell
        uv tool install copier || {
            echo -e "${RED}Failed to install Copier${NC}"
            exit 1
        }
    fi
fi
echo -e "${GREEN}✓ Copier is installed${NC}"

# Check if .env already exists
if [ -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file already exists.${NC}"
    read -p "Do you want to reconfigure? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Keeping existing configuration.${NC}"
    else
        mv .env .env.backup-$(date +%Y%m%d-%H%M%S)
        echo -e "${GREEN}Backed up existing .env with timestamp${NC}"
        echo -e "${BLUE}🎨 Let's personalize your Memory Palace...${NC}"
        copier copy --trust . . --data-file copier.yml --answers-file .copier-answers.yml || {
            echo -e "${RED}Configuration failed. Restoring backup...${NC}"
            mv .env.backup-* .env
            exit 1
        }
    fi
else
    # Run copier to generate .env
    echo -e "${BLUE}🎨 Let's personalize your Memory Palace...${NC}"
    copier copy --trust . . --data-file copier.yml --answers-file .copier-answers.yml || {
        echo -e "${RED}Configuration failed${NC}"
        exit 1
    }
fi

# Install dependencies
echo ""
echo -e "${YELLOW}📦 Installing Python dependencies...${NC}"
if command -v uv &> /dev/null; then
    uv sync || {
        echo -e "${RED}Failed to install Python dependencies${NC}"
        exit 1
    }
else
    echo -e "${YELLOW}Installing uv first...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh || {
        echo -e "${RED}Failed to install uv${NC}"
        exit 1
    }
    uv sync || {
        echo -e "${RED}Failed to install Python dependencies${NC}"
        exit 1
    }
fi
echo -e "${GREEN}✓ Python dependencies installed${NC}"

# Start Neo4j
echo ""
echo -e "${YELLOW}🗄️ Starting Neo4j database...${NC}"
docker compose up -d neo4j || {
    echo -e "${RED}Failed to start Neo4j${NC}"
    exit 1
}

# Wait for Neo4j to be ready
echo -e "${YELLOW}Waiting for Neo4j to be ready...${NC}"
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if docker compose exec -T neo4j cypher-shell -u neo4j -p password "RETURN 1" &>/dev/null; then
        echo -e "${GREEN}✓ Neo4j is ready!${NC}"
        break
    fi
    echo -n "."
    sleep 2
    attempt=$((attempt + 1))
done

if [ $attempt -eq $max_attempts ]; then
    echo -e "\n${RED}Neo4j failed to start after 60 seconds${NC}"
    echo "Check logs with: docker compose logs neo4j"
    exit 1
fi

# Verify API key is present
if ! grep -q "VOYAGE_API_KEY=" .env || [ -z "$(grep VOYAGE_API_KEY= .env | cut -d'=' -f2)" ]; then
    echo -e "\n${YELLOW}⚠️  Warning: VOYAGE_API_KEY is not set in .env${NC}"
    echo "You'll need to add your Voyage AI API key before the system can generate embeddings."
    echo "Get one at: https://www.voyageai.com/"
fi

# Run the application
echo ""
echo -e "${GREEN}✨ Memory Palace setup complete!${NC}"
echo ""
echo "To start the Memory Palace server, run:"
echo -e "  ${BLUE}./run.sh${NC}       # For development"
echo -e "  ${BLUE}./run-prod.sh${NC}  # For production"
echo ""
if [ -f .env ] && grep -q FRIEND_NAME .env; then
    FRIEND_NAME=$(grep FRIEND_NAME .env | cut -d'"' -f2 || echo "Friend")
    echo -e "Your Memory Palace has been personalized for: ${GREEN}${FRIEND_NAME}${NC}"
fi
echo -e "${GREEN}Happy remembering! 🧠✨${NC}"