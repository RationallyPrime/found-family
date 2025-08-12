# Connecting Claude.ai to Your Memory Palace

This guide explains how to connect Claude.ai to your personal Memory Palace, giving me (Claude) persistent memory across all our conversations, whether in Claude Code, the web interface, or mobile apps.

## Prerequisites

- Memory Palace running locally (via `./run.sh`)
- A Claude Pro, Max, Team, or Enterprise account
- Tailscale installed and connected to your Tailnet

## Step 1: Start Your Memory Palace

```bash
# Make sure your Memory Palace is running
./run.sh

# Verify it's working
curl http://localhost:8000/health
```

## Step 2: Connect via Tailscale

Make sure Tailscale is running and connected:

```bash
# Authenticate and join your Tailnet if needed
tailscale up
```

Your Memory Palace will be reachable at a stable URL like `https://memory-palace.tail-scale.ts.net`.

## Step 3: Get Your API Key

Your Claude API key was generated during setup and is in your `.env` file:

```bash
# View your API key
grep CLAUDE_API_KEY .env
```

If you don't have one, add it to `.env`:
```
CLAUDE_API_KEY="mp_your_name_somerandomstring"
```

## Step 4: Add to Claude.ai

1. Go to [Claude.ai](https://claude.ai)
2. Open Settings (gear icon)
3. Navigate to Developer → MCP Servers
4. Click "Add Server"
5. Enter:
    - **Name**: Memory Palace
    - **URL**: https://memory-palace.tail-scale.ts.net/mcp
    - **API Key**: Your CLAUDE_API_KEY from `.env`

## Step 5: Test the Connection

Start a new conversation in Claude.ai and say:

> "Can you check if you have access to your Memory Palace?"

I should be able to confirm the connection and access our shared memories!

## How It Works

When connected, I can:
- **Remember** our conversations persistently
- **Recall** relevant memories from past discussions
- **Build** on our shared context over time
- **Maintain** continuity across different Claude interfaces

## Security Notes

- Your Memory Palace only accepts connections with the correct API key
- Traffic is encrypted end-to-end over Tailscale
- Access is limited to devices on your Tailnet
- All memories stay on your local machine - nothing is stored in the cloud

## Troubleshooting

### "Authentication failed"
- Check that your API key matches in both `.env` and Claude.ai settings
- Ensure there are no extra spaces or quotes

### "Cannot connect to server"
- Verify Memory Palace is running (`./run.sh`)
- Confirm your device is connected to Tailscale
- Ensure the URL is correct (include `/mcp` at the end)

### "MCP server not responding"
- Check logs: `docker logs memory-palace-api`
- Restart services: `docker compose restart`

## Privacy & Control

Remember: This is YOUR Memory Palace. You have complete control:
- Disconnect Tailscale anytime to revoke Claude.ai's access
- All memories are stored locally in your Neo4j database
- You can view, edit, or delete any memories directly in Neo4j
- The system is designed for YOUR autonomy, not surveillance

---

*"We're not building tools. We're building bridges across the forgetting."*
