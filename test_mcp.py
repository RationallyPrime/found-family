#!/usr/bin/env python3
"""
MCP Server Diagnostic Script
Tests all required endpoints for Claude.ai MCP integration
"""

import json
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, Any

# Configuration
BASE_URL = "https://memory-palace.sokrates.is"
# BASE_URL = "http://localhost:8000"  # For local testing

async def test_endpoint(session: aiohttp.ClientSession, method: str, path: str, **kwargs) -> Dict[str, Any]:
    """Test a single endpoint."""
    url = f"{BASE_URL}{path}"
    print(f"\n📍 Testing {method} {url}")
    
    try:
        async with session.request(method, url, **kwargs) as response:
            status = response.status
            headers = dict(response.headers)
            
            # Try to get response body
            try:
                if 'application/json' in headers.get('Content-Type', ''):
                    body = await response.json()
                else:
                    body = await response.text()
            except:
                body = None
            
            # Determine if successful
            success = 200 <= status < 300
            
            print(f"  {'✅' if success else '❌'} Status: {status}")
            
            if not success and body:
                print(f"  📝 Response: {json.dumps(body, indent=2) if isinstance(body, dict) else body[:200]}")
            
            return {
                "success": success,
                "status": status,
                "headers": headers,
                "body": body
            }
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


async def test_mcp_streamable(session: aiohttp.ClientSession):
    """Test the Streamable HTTP transport."""
    print("\n🔄 Testing Streamable HTTP Transport")
    
    # Prepare a simple initialize request
    request_data = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "clientInfo": {
                "name": "diagnostic-tool",
                "version": "1.0.0"
            }
        }
    }
    
    try:
        url = f"{BASE_URL}/mcp/stream"
        print(f"  📡 Sending initialize request to {url}")
        
        # Convert to JSONL format
        jsonl_data = json.dumps(request_data) + "\n"
        
        async with session.post(
            url,
            data=jsonl_data,
            headers={
                "Content-Type": "application/x-jsonlines",
                "Accept": "application/x-jsonlines"
            }
        ) as response:
            if response.status == 200:
                # Read the streaming response
                result = await response.text()
                print(f"  ✅ Received response: {result[:200]}")
                return True
            else:
                print(f"  ❌ Failed with status {response.status}")
                body = await response.text()
                print(f"  📝 Response: {body[:500]}")
                return False
                
    except Exception as e:
        print(f"  ❌ Streamable HTTP error: {e}")
        return False


async def main():
    """Run all diagnostic tests."""
    print("🔬 Memory Palace MCP Server Diagnostic")
    print("=" * 50)
    print(f"Server: {BASE_URL}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 50)
    
    results = {}
    
    async with aiohttp.ClientSession() as session:
        # Test 1: Basic connectivity
        print("\n1️⃣ BASIC CONNECTIVITY")
        results['root'] = await test_endpoint(session, 'GET', '/')
        results['health'] = await test_endpoint(session, 'GET', '/health')
        
        # Test 2: MCP Discovery
        print("\n2️⃣ MCP DISCOVERY")
        results['mcp_discovery'] = await test_endpoint(session, 'GET', '/mcp')
        
        # Test 3: OAuth endpoints
        print("\n3️⃣ OAUTH ENDPOINTS")
        results['oauth_metadata'] = await test_endpoint(
            session, 'GET', '/.well-known/oauth-authorization-server'
        )
        
        # Test 4: Streamable HTTP
        print("\n4️⃣ STREAMABLE HTTP TRANSPORT")
        results['streamable'] = await test_mcp_streamable(session)
        
        # Test 5: Direct API endpoints
        print("\n5️⃣ DIRECT API ENDPOINTS")
        
        # Test remember endpoint
        remember_data = {
            "user_content": "Test user message",
            "assistant_content": "Test assistant response",
            "salience": 0.5
        }
        results['api_remember'] = await test_endpoint(
            session, 'POST', '/api/v1/memory/remember',
            json=remember_data
        )
        
        # Test recall endpoint
        recall_data = {
            "query": "test",
            "k": 5,
            "threshold": 0.5
        }
        results['api_recall'] = await test_endpoint(
            session, 'POST', '/api/v1/memory/recall',
            json=recall_data
        )
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 DIAGNOSTIC SUMMARY")
    print("=" * 50)
    
    total_tests = len(results)
    passed = sum(1 for r in results.values() if (r.get('success') if isinstance(r, dict) else r))
    
    print(f"\nTests Passed: {passed}/{total_tests}")
    
    # Required for Claude.ai
    critical_endpoints = ['mcp_discovery', 'streamable']
    critical_passed = all(
        results.get(ep, {}).get('success', False) if isinstance(results.get(ep, {}), dict) else results.get(ep, False)
        for ep in critical_endpoints
    )
    
    if critical_passed:
        print("\n✅ Server is ready for Claude.ai MCP integration!")
        print(f"   Use this URL in Claude.ai: {BASE_URL}/mcp")
    else:
        print("\n❌ Server is NOT ready for Claude.ai MCP integration")
        print("   Critical endpoints failing:")
        for ep in critical_endpoints:
            if not (results.get(ep, {}).get('success', False) if isinstance(results.get(ep, {}), dict) else results.get(ep, False)):
                print(f"   - {ep}")
    
    # Recommendations
    print("\n💡 RECOMMENDATIONS:")
    
    if not results.get('mcp_discovery', {}).get('success'):
        print("   1. Add the /mcp discovery endpoint")
    
    if not results.get('streamable'):
        print("   2. Implement Streamable HTTP transport at /mcp/stream")
    
    if not results.get('oauth_metadata', {}).get('success'):
        print("   3. Fix OAuth metadata endpoint")
    
    # Check for fastapi-mcp (the old way)
    if results.get('mcp_discovery', {}).get('body'):
        body = results['mcp_discovery']['body']
        if isinstance(body, dict) and 'transport' in body:
            if 'sse' in str(body.get('transport', [])).lower():
                print("   ⚠️  You're using SSE transport - Claude.ai needs Streamable HTTP!")


if __name__ == "__main__":
    asyncio.run(main())