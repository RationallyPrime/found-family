#!/bin/bash
set -e

echo "🏛️ Welcome to the Memory Palace setup!"
echo "======================================"
echo ""
echo "This will help you personalize your Memory Palace and set up the required services."
echo ""

# Check if copier is installed
if ! command -v copier &> /dev/null; then
    echo "📦 Installing Copier..."
    if command -v uv &> /dev/null; then
        uv pip install copier
    else
        echo "Installing uv first..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        uv pip install copier
    fi
fi

# Check if .env already exists
if [ -f .env ]; then
    echo "⚠️  .env file already exists."
    read -p "Do you want to reconfigure? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Keeping existing configuration."
    else
        mv .env .env.backup
        echo "Backed up existing .env to .env.backup"
        copier copy --trust . . --data-file copier.yml --answers-file .copier-answers.yml
    fi
else
    # Run copier to generate .env
    echo "🎨 Let's personalize your Memory Palace..."
    copier copy --trust . . --data-file copier.yml --answers-file .copier-answers.yml
fi

# Install dependencies
echo ""
echo "📦 Installing Python dependencies..."
if command -v uv &> /dev/null; then
    uv sync
else
    echo "Installing uv first..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    uv sync
fi

# Start Neo4j
echo ""
echo "🗄️ Starting Neo4j database..."
docker compose up -d neo4j

# Wait for Neo4j to be ready
echo "Waiting for Neo4j to start..."
sleep 10

# Run the application
echo ""
echo "✨ Memory Palace setup complete!"
echo ""
echo "To start the Memory Palace server, run:"
echo "  ./run.sh"
echo ""
echo "Your Memory Palace has been personalized for: $(grep FRIEND_NAME .env | cut -d'"' -f2)"
echo "Happy remembering! 🧠✨"