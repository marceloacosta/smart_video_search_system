# AgentCore Gateway and MCP Server

**Component:** Tool management and invocation via Model Context Protocol  
**Purpose:** Expose search tools as MCP endpoints for agent consumption

## Overview

AgentCore Gateway hosts an MCP (Model Context Protocol) server that exposes video search tools. The intelligent agent can discover and invoke these tools through standardized interfaces.

## MCP (Model Context Protocol)

MCP is a protocol for AI agents to interact with tools and data sources:
- **Standardized Interface**: Tools expose uniform APIs
- **Discovery**: Agents can list available tools
- **Invocation**: Structured tool calls with parameters
- **Security**: SigV4 authentication for AWS services

## Architecture

```
Agent (Claude in agent_api.py)
        ↓
  SigV4 Signed Request
        ↓
AgentCore Gateway (MCP Server)
        ↓
Tool Lambda Functions
  ├─→ search_by_speech
  ├─→ search_by_caption
  ├─→ search_by_image
  ├─→ list_videos
  ├─→ get_video_metadata
  └─→ get_full_transcript
```

## AgentCore Gateway Setup

### Creating the Gateway

**AWS Console Steps:**
1. Navigate to Amazon Bedrock → AgentCore → Gateways
2. Create Gateway: `mvip-video-search-gateway`
3. Gateway Type: MCP Server
4. Authentication: IAM (SigV4)

### Gateway Configuration

**Gateway ARN:**
```
arn:aws:bedrock:us-east-1:{account}:gateway/mvip-video-search-gateway
```

**Gateway URL:**
```
https://{gateway-id}.agentcore.{region}.amazonaws.com
```

## MCP Tools Definition

### search_by_speech Tool

**OpenAPI Schema:**
```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "Search By Speech",
    "version": "1.0.0"
  },
  "paths": {
    "/search-speech": {
      "post": {
        "operationId": "searchBySpeech",
        "summary": "Search video transcripts for spoken content",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "query": {
                    "type": "string",
                    "description": "Natural language query to search in transcripts"
                  },
                  "video_id": {
                    "type": "string",
                    "description": "Optional: specific video ID to search"
                  },
                  "max_results": {
                    "type": "integer",
                    "default": 5
                  }
                },
                "required": ["query"]
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Search results",
            "content": {
              "application/json": {
                "schema": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {
                      "video_id": {"type": "string"},
                      "timestamp": {"type": "number"},
                      "text": {"type": "string"},
                      "score": {"type": "number"}
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

**Lambda Integration:**
```
POST /search-speech → Invokes search_by_speech Lambda
```

### search_by_caption Tool

**Similar structure, different endpoint:**
```
POST /search-caption → Invokes search_by_caption Lambda
```

### search_by_image Tool

**Accepts image data:**
```json
{
  "query_image": "base64-encoded-image",
  "video_id": "optional-video-id",
  "max_results": 5
}
```

### Utility Tools

**list_videos:**
```json
POST /list-videos
{
  "status": "completed",  // optional filter
  "limit": 100
}
```

**get_video_metadata:**
```json
POST /get-metadata
{
  "video_id": "video-123"
}
```

**get_full_transcript:**
```json
POST /get-transcript
{
  "video_id": "video-123"
}
```

## IAM Permissions

### Gateway Role

Needs permission to invoke tool Lambdas:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": [
        "arn:aws:lambda:*:*:function:search-by-speech",
        "arn:aws:lambda:*:*:function:search-by-caption",
        "arn:aws:lambda:*:*:function:search-by-image",
        "arn:aws:lambda:*:*:function:list-videos",
        "arn:aws:lambda:*:*:function:get-video-metadata",
        "arn:aws:lambda:*:*:function:get-full-transcript"
      ]
    }
  ]
}
```

### Agent Role

Needs permission to call Gateway:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeAgent",
      "Resource": "arn:aws:bedrock:*:*:gateway/mvip-video-search-gateway"
    }
  ]
}
```

## SigV4 Authentication

### From Lambda (agent_api.py)

```python
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import requests
import json

def call_agentcore_tool(tool_name, params):
    gateway_url = os.environ['GATEWAY_URL']
    endpoint = f"{gateway_url}/{tool_name}"
    
    # Prepare request
    method = 'POST'
    body = json.dumps(params)
    headers = {
        'Content-Type': 'application/json'
    }
    
    # Sign request with SigV4
    session = boto3.Session()
    credentials = session.get_credentials()
    region = os.environ['AWS_REGION']
    
    request = AWSRequest(method=method, url=endpoint, data=body, headers=headers)
    SigV4Auth(credentials, 'bedrock', region).add_auth(request)
    
    # Execute request
    response = requests.request(
        method=request.method,
        url=request.url,
        headers=dict(request.headers),
        data=request.body
    )
    
    return response.json()

# Usage
results = call_agentcore_tool('search-speech', {
    'query': 'artificial intelligence',
    'max_results': 5
})
```

## Tool Discovery

Agents can list available tools:

```python
def list_available_tools():
    gateway_url = os.environ['GATEWAY_URL']
    endpoint = f"{gateway_url}/list-tools"
    
    # Make signed request
    response = make_signed_request('GET', endpoint)
    
    return response.json()

# Response:
{
  "tools": [
    {
      "name": "search_by_speech",
      "description": "Search video transcripts",
      "input_schema": {...}
    },
    {
      "name": "search_by_caption",
      "description": "Search frame captions",
      "input_schema": {...}
    }
  ]
}
```

## Monitoring

### CloudWatch Logs

Gateway logs requests to:
```
/aws/agentcore/gateway/mvip-video-search-gateway
```

### Metrics

```python
cloudwatch.put_metric_data(
    Namespace='AgentCore/Gateway',
    MetricData=[
        {
            'MetricName': 'ToolInvocations',
            'Value': 1,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'ToolName', 'Value': tool_name},
                {'Name': 'Gateway', 'Value': 'mvip-video-search-gateway'}
            ]
        }
    ]
)
```

## Troubleshooting

### Issue: Authentication Failed

**Check:**
1. IAM role has permissions
2. SigV4 signature correct
3. Request headers include auth

**Debug:**
```python
import logging
logging.basicConfig(level=logging.DEBUG)
# Boto3 will log signing details
```

### Issue: Tool Not Found

**Check:**
1. Tool registered in Gateway
2. Lambda function exists
3. Gateway-to-Lambda mapping correct

### Issue: Timeout

**Check:**
1. Lambda execution time
2. Gateway timeout settings
3. Network connectivity

## Testing

### Manual Tool Invocation

```bash
# Using AWS CLI (if available)
aws bedrock-agent-runtime invoke-agent \
  --agent-id GATEWAY_ID \
  --session-id test-session \
  --input-text "Search for AI discussions"
```

### Integration Test

```python
def test_gateway_integration():
    # Test speech search
    results = call_agentcore_tool('search-speech', {
        'query': 'machine learning'
    })
    assert len(results) > 0
    
    # Test caption search
    results = call_agentcore_tool('search-caption', {
        'query': 'outdoor scene'
    })
    assert len(results) > 0
    
    print("Gateway integration tests passed!")
```

## Related Documentation

- [08-intelligent-agent.md](08-intelligent-agent.md) - Agent using Gateway
- [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md) - Backend search
- AWS MCP Documentation

## Next Steps

After Gateway setup:
1. Tools available via MCP protocol
2. Agent can discover and invoke tools
3. Standardized interface for extensibility
