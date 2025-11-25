"""
MVIP Agent API Lambda Handler

This Lambda provides the API endpoints for the MVIP video search agent.
It connects to the AgentCore Gateway and provides a conversational interface.
"""

import os
import json
import boto3
import requests
from typing import Dict, Any
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


# Configuration from environment
GATEWAY_URL = os.environ.get('GATEWAY_URL')
REGION = os.environ.get('REGION', 'us-east-1')
SERVICE = "bedrock-agentcore"


class MVIPGatewayClient:
    """Simple client for AgentCore Gateway with IAM auth"""
    
    def __init__(self):
        self.session = boto3.Session()
        self.credentials = self.session.get_credentials()
        self.request_id = 0
    
    def _sign_and_send(self, mcp_request: Dict[Any, Any]) -> Dict[Any, Any]:
        """Sign and send MCP request"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        body = json.dumps(mcp_request)
        
        request = AWSRequest(
            method="POST",
            url=GATEWAY_URL,
            headers=headers,
            data=body
        )
        
        SigV4Auth(self.credentials, SERVICE, REGION).add_auth(request)
        
        response = requests.post(
            GATEWAY_URL,
            headers=dict(request.headers),
            data=body,
            timeout=30
        )
        
        return response.json()
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool through the Gateway"""
        self.request_id += 1
        
        request = {
            "jsonrpc": "2.0",
            "id": f"req-{self.request_id}",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        
        response = self._sign_and_send(request)
        
        if "result" in response:
            content = response['result'].get('content', [{}])[0]
            text_response = content.get('text', '{}')
            
            try:
                lambda_response = json.loads(text_response)
                if lambda_response.get('statusCode') == 200:
                    body = json.loads(lambda_response.get('body', '{}'))
                    return body
                else:
                    return {"error": lambda_response.get('body', 'Unknown error')}
            except json.JSONDecodeError:
                return {"error": "Failed to parse response"}
        else:
            return {"error": response.get('error', {}).get('message', 'Unknown error')}


# Global client instance (reused across invocations)
gateway_client = None


def get_gateway_client():
    """Get or create gateway client (singleton pattern for Lambda reuse)"""
    global gateway_client
    if gateway_client is None:
        gateway_client = MVIPGatewayClient()
    return gateway_client


def handle_chat(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /chat endpoint - intelligent agent with Claude"""
    message = body.get('message', '')
    video_id = body.get('video_id')
    
    if not message:
        return {"error": "message is required"}
    
    # Use Claude to intelligently select the right tool
    bedrock = boto3.client('bedrock-runtime', region_name=REGION)
    
    # Tool definitions for Claude
    tools = [
        {
            "name": "search_by_speech",
            "description": "Search for spoken words or dialogue in video transcripts. Use this when the user asks about what was SAID in the video.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The speech/dialogue to search for"},
                    "video_id": {"type": "string", "description": "Optional video ID to filter results"},
                    "top_k": {"type": "integer", "description": "Number of results", "default": 3}
                },
                "required": ["query"]
            }
        },
        {
            "name": "search_by_caption",
            "description": "Search for visual descriptions of what's happening in scenes. Use this when the user asks about ACTIONS, SCENES, or WHAT IS HAPPENING visually.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Description of the scene or action"},
                    "video_id": {"type": "string", "description": "Optional video ID to filter results"},
                    "top_k": {"type": "integer", "description": "Number of results", "default": 3}
                },
                "required": ["query"]
            }
        },
        {
            "name": "search_by_image",
            "description": "Search for visual appearance and objects in video frames. Use this when the user asks about OBJECTS, VISUAL APPEARANCE, or what things LOOK LIKE.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Description of visual content or objects"},
                    "video_id": {"type": "string", "description": "Optional video ID to filter results"},
                    "top_k": {"type": "integer", "description": "Number of results", "default": 3}
                },
                "required": ["query"]
            }
        }
    ]
    
    # Call Claude with tools
    try:
        response = bedrock.invoke_model(
            modelId="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "tools": tools,
                "messages": [
                    {
                        "role": "user",
                        "content": message
                    }
                ]
            })
        )
        
        response_body = json.loads(response['body'].read())
        
        # Check if Claude wants to use a tool
        if response_body.get('stop_reason') == 'tool_use':
            for content in response_body.get('content', []):
                if content.get('type') == 'tool_use':
                    tool_name = content['name']
                    tool_input = content['input']
                    
                    # Add video_id if provided in request
                    if video_id and 'video_id' not in tool_input:
                        tool_input['video_id'] = video_id
                    
                    # Map tool names to Gateway tool names
                    gateway_tool_map = {
                        "search_by_speech": "mvip-search-speech-target___search_by_speech",
                        "search_by_caption": "mvip-search-caption-target___search_by_caption",
                        "search_by_image": "mvip-search-image-target___search_by_image"
                    }
                    
                    gateway_tool_name = gateway_tool_map.get(tool_name)
                    if not gateway_tool_name:
                        return {"error": f"Unknown tool: {tool_name}"}
                    
                    # Call the tool through Gateway
                    client = get_gateway_client()
                    result = client.call_tool(gateway_tool_name, tool_input)
                    
                    # Format response based on search type
                    search_type = tool_name.replace("search_by_", "")
                    formatted_response = format_search_results(result, search_type)
                    
                    return {"response": formatted_response}
        
        # If no tool was used, return Claude's text response
        text_response = ""
        for content in response_body.get('content', []):
            if content.get('type') == 'text':
                text_response += content.get('text', '')
        
        return {"response": text_response or "I'm not sure how to help with that query."}
        
    except Exception as e:
        print(f"Error calling Claude: {e}")
        import traceback
        traceback.print_exc()
        return {"error": f"Failed to process query: {str(e)}"}


def handle_search_speech(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /search/speech endpoint"""
    query = body.get('query', '')
    top_k = body.get('top_k', 5)
    video_id = body.get('video_id')
    
    if not query:
        return {"error": "query is required"}
    
    client = get_gateway_client()
    args = {"query": query, "top_k": top_k}
    if video_id:
        args["video_id"] = video_id
    
    return client.call_tool("mvip-search-speech-target___search_by_speech", args)


def handle_search_caption(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /search/caption endpoint"""
    query = body.get('query', '')
    top_k = body.get('top_k', 5)
    video_id = body.get('video_id')
    
    if not query:
        return {"error": "query is required"}
    
    client = get_gateway_client()
    args = {"query": query, "top_k": top_k}
    if video_id:
        args["video_id"] = video_id
    
    return client.call_tool("mvip-search-caption-target___search_by_caption", args)


def handle_search_image(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /search/image endpoint"""
    query = body.get('query', '')
    top_k = body.get('top_k', 5)
    video_id = body.get('video_id')
    
    if not query:
        return {"error": "query is required"}
    
    client = get_gateway_client()
    args = {"query": query, "top_k": top_k}
    if video_id:
        args["video_id"] = video_id
    
    return client.call_tool("mvip-search-image-target___search_by_image", args)


def handle_list_videos() -> Dict[str, Any]:
    """Handle /videos endpoint"""
    client = get_gateway_client()
    return client.call_tool("mvip-list-videos-target___list_videos", {"limit": 10})


def format_video_list(result: Dict[str, Any]) -> str:
    """Format video list for chat response"""
    if "error" in result:
        return f"❌ Error: {result['error']}"
    
    videos = result.get('videos', [])
    count = result.get('count', 0)
    
    response = f"📹 Found {count} videos:\n\n"
    for i, video in enumerate(videos, 1):
        response += f"{i}. **{video.get('video_id')}**\n"
        response += f"   Status: {video.get('status')}\n"
        response += f"   Duration: {video.get('duration_seconds', 0):.1f}s\n"
        response += f"   Frames: {video.get('frame_count', 0)}\n\n"
    
    return response


def handle_get_video_url(video_id: str) -> Dict[str, Any]:
    """
    Generate pre-signed URL for video playback
    """
    try:
        s3 = boto3.client('s3')
        bucket = os.environ.get('VIDEOS_BUCKET', 'mvip-videos-raw')
        key = f'{video_id}.mp4'
        
        # Generate pre-signed URL (valid for 1 hour)
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=3600
        )
        
        return {
            "video_id": video_id,
            "url": url,
            "expires_in": 3600
        }
    except Exception as e:
        print(f"Error generating video URL: {e}")
        return {"error": f"Failed to generate video URL: {str(e)}"}


def format_search_results(result: Dict[str, Any], search_type: str) -> str:
    """Format search results for chat response with video timestamps"""
    if "error" in result:
        return f"❌ Error: {result['error']}"
    
    results = result.get('results', [])
    count = result.get('count', 0)
    query = result.get('query', '')
    
    response = f"🔍 Search results for \"{query}\" ({search_type}):\n\n"
    response += f"Found {count} matches:\n\n"
    
    for i, match in enumerate(results, 1):
        video_id = match.get('video_id', 'unknown')
        response += f"{i}. Video: {video_id}\n"
        
        if search_type == "speech":
            start_time = match.get('start_time', 0)
            end_time = match.get('end_time', 0)
            response += f"   Score: {match.get('score', 0):.3f}\n"
            response += f"   Time: {start_time:.1f}s - {end_time:.1f}s\n"
            
            # Add video URL with timestamp
            if video_id and start_time:
                # Generate S3 URL with timestamp fragment
                # Format: #t=start,end (HTML5 media fragment)
                # Note: This URL format requires public bucket or presigned URLs in production
                response += f"   ⏱️ Time: {int(start_time)}s - {int(end_time)}s\n"
            
            text = match.get('text', '')
            if len(text) > 100:
                text = text[:100] + "..."
            response += f"   Text: {text}\n\n"
            
        elif search_type == "caption":
            frame_number = match.get('frame_number', 0)
            response += f"   Score: {match.get('score', 0):.3f}\n"
            response += f"   Frame: {frame_number}\n"
            
            # Calculate approximate timestamp (assuming 45 frames evenly distributed)
            # This is a rough estimate - actual timestamp would need video duration
            response += f"   Approx time: Frame {frame_number}/45\n"
            
            caption = match.get('caption', '')
            if len(caption) > 100:
                caption = caption[:100] + "..."
            response += f"   Caption: {caption}\n\n"
            
        elif search_type == "image":
            frame_number = match.get('frame_number', 0)
            response += f"   Distance: {match.get('distance', 0):.3f}\n"
            response += f"   Frame: {frame_number}\n"
            response += f"   Approx time: Frame {frame_number}/45\n"
            response += f"   🖼️ Image: {match.get('s3_uri', '')}\n\n"
    
    return response


def handler(event, context):
    """
    Lambda handler for API Gateway proxy integration
    
    Routes:
    - POST /chat - conversational agent
    - POST /search/speech - search transcripts
    - POST /search/caption - search captions
    - POST /search/image - search images
    - GET /videos - list videos
    - GET /videos/{video_id}/url - get pre-signed video URL
    - GET /health - health check
    """
    print(f"Event: {json.dumps(event)}")
    
    try:
        # Parse request
        path = event.get('path', '').rstrip('/')
        method = event.get('httpMethod', '')
        path_params = event.get('pathParameters', {}) or {}
        
        # Parse body for POST requests
        body = {}
        if method == 'POST':
            body_str = event.get('body', '{}')
            body = json.loads(body_str) if body_str else {}
        
        # Health check
        if path.endswith('/health'):
            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*"
                },
                "body": json.dumps({
                    "status": "healthy",
                    "agent_ready": True
                })
            }
        
        # Route to handlers
        if path.endswith('/chat'):
            result = handle_chat(body)
        elif path.endswith('/search/speech'):
            result = handle_search_speech(body)
        elif path.endswith('/search/caption'):
            result = handle_search_caption(body)
        elif path.endswith('/search/image'):
            result = handle_search_image(body)
        elif '/videos/' in path and path.endswith('/url'):
            # GET /videos/{video_id}/url - get pre-signed URL
            video_id = path_params.get('video_id') or path.split('/videos/')[-1].replace('/url', '')
            result = handle_get_video_url(video_id)
        elif path.endswith('/videos'):
            result = handle_list_videos()
        else:
            result = {"error": f"Unknown path: {path}"}
        
        return {
            "statusCode": 200 if "error" not in result else 400,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps(result)
        }
    
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "error": str(e)
            })
        }

