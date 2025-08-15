#!/usr/bin/env python3
"""
Test script to verify MCP server is properly configured for Claude.ai
"""

import json
import asyncio
import aiohttp
from datetime import datetime

BASE_URL = "https://memory-palace.sokrates.is"
# BASE_URL = "http://localhost:8000"  # For local testing

async def test_discovery():
    """Test the discovery endpoint."""
    print("\n🔍 Testing MCP Discovery Endpoint...")
    
    async with aiohttp.ClientSession() as session:
        headers = {
            "MCP-Protocol-Version": "2025-06-18",  # What Claude sends
            "Accept": "application/json"
        }
        
        async with session.get(f"{BASE_URL}/mcp", headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                
                # Check critical fields
                errors = []
                
                # Check for "transport" (singular) not "transports" (plural)
                if "transport" not in data:
                    errors.append("❌ Missing 'transport' field (found 'transports'?)")
                elif not isinstance(data["transport"], dict):
                    errors.append("❌ 'transport' should be an object, not an array")
                else:
                    print("✅ 'transport' field is correct (object, not array)")
                
                # Check for top-level protocolVersion
                if "protocolVersion" not in data:
                    errors.append("❌ Missing top-level 'protocolVersion'")
                else:
                    print(f"✅ Protocol version: {data['protocolVersion']}")
                
                # Check transport details
                if "transport" in data:
                    transport = data["transport"]
                    if transport.get("type") != "streamable-http":
                        errors.append(f"❌ Wrong transport type: {transport.get('type')}")
                    else:
                        print("✅ Transport type: streamable-http")
                    
                    if "endpoint" not in transport:
                        errors.append("❌ Missing transport endpoint")
                    else:
                        print(f"✅ Transport endpoint: {transport['endpoint']}")
                
                # Check capabilities
                if "capabilities" in data:
                    caps = data["capabilities"]
                    if "tools" in caps and isinstance(caps["tools"], dict):
                        if caps["tools"].get("listable"):
                            print("✅ Tools are listable")
                        else:
                            errors.append("❌ Tools not marked as listable")
                
                # Print errors or success
                if errors:
                    print("\n⚠️  Issues found:")
                    for error in errors:
                        print(f"  {error}")
                else:
                    print("\n✅ Discovery endpoint looks good!")
                
                # Show the actual response
                print("\n📋 Full response:")
                print(json.dumps(data, indent=2))
                
                return not bool(errors)
            else:
                print(f"❌ Failed with status {response.status}")
                return False


async def test_stream():
    """Test the streaming endpoint."""
    print("\n🔄 Testing MCP Stream Endpoint...")
    
    async with aiohttp.ClientSession() as session:
        # Test initialize request
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "clientInfo": {
                    "name": "test-client",
                    "version": "1.0.0"
                }
            }
        }
        
        headers = {
            "Content-Type": "application/x-jsonlines",
            "Accept": "application/x-jsonlines",
            "MCP-Protocol-Version": "2025-06-18"
        }
        
        # Send as JSONL
        data = json.dumps(request) + "\n"
        
        async with session.post(
            f"{BASE_URL}/mcp/stream",
            data=data,
            headers=headers
        ) as response:
            if response.status == 200:
                result = await response.text()
                print("✅ Stream endpoint responded")
                
                # Parse JSONL response
                for line in result.strip().split('\n'):
                    if line:
                        try:
                            response_data = json.loads(line)
                            if "result" in response_data:
                                print(f"✅ Got initialize response with protocol {response_data['result'].get('protocolVersion')}")
                            elif "error" in response_data:
                                print(f"❌ Error: {response_data['error']}")
                        except json.JSONDecodeError:
                            print(f"⚠️  Could not parse: {line}")
                
                return True
            else:
                print(f"❌ Failed with status {response.status}")
                body = await response.text()
                print(f"Response: {body[:500]}")
                return False


async def test_tools_list():
    """Test listing tools via stream."""
    print("\n🔧 Testing Tools List...")
    
    async with aiohttp.ClientSession() as session:
        # First initialize
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}
        }
        
        # Then list tools
        tools_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        
        headers = {
            "Content-Type": "application/x-jsonlines",
            "Accept": "application/x-jsonlines",
            "MCP-Protocol-Version": "2025-06-18"
        }
        
        # Send both requests
        data = json.dumps(init_request) + "\n" + json.dumps(tools_request) + "\n"
        
        async with session.post(
            f"{BASE_URL}/mcp/stream",
            data=data,
            headers=headers
        ) as response:
            if response.status == 200:
                result = await response.text()
                
                tools_found = False
                for line in result.strip().split('\n'):
                    if line:
                        try:
                            response_data = json.loads(line)
                            if response_data.get("id") == 2 and "result" in response_data:
                                tools = response_data["result"].get("tools", [])
                                if tools:
                                    print(f"✅ Found {len(tools)} tools:")
                                    for tool in tools:
                                        print(f"   - {tool['name']}: {tool['description']}")
                                    tools_found = True
                        except json.JSONDecodeError:
                            pass
                
                return tools_found
            else:
                print(f"❌ Failed with status {response.status}")
                return False


async def main():
    """Run all tests."""
    print("=" * 60)
    print("🧪 Memory Palace MCP Test Suite")
    print(f"🌐 Testing: {BASE_URL}")
    print(f"🕐 Time: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Run tests
    discovery_ok = await test_discovery()
    stream_ok = await test_stream()
    tools_ok = await test_tools_list()
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 TEST RESULTS")
    print("=" * 60)
    
    all_ok = discovery_ok and stream_ok and tools_ok
    
    if all_ok:
        print("\n✅ ALL TESTS PASSED!")
        print("\nYour MCP server should now work with Claude.ai!")
        print(f"\n📌 Use this URL in Claude.ai: {BASE_URL}/mcp")
    else:
        print("\n❌ SOME TESTS FAILED")
        print("\nIssues to fix:")
        if not discovery_ok:
            print("  1. Fix discovery endpoint (check 'transport' vs 'transports')")
        if not stream_ok:
            print("  2. Fix stream endpoint initialization")
        if not tools_ok:
            print("  3. Fix tools listing in stream")
    
    print("\n💡 After fixing, restart your server and run this test again.")


if __name__ == "__main__":
    asyncio.run(main())