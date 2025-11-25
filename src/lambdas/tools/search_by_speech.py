"""
MCP Tool: search_by_speech

Semantic search in video transcripts using the Speech Knowledge Base.

Uses Amazon Bedrock Knowledge Base with S3 Vectors to find
relevant segments based on spoken content.
"""

import os
import json
import boto3
from typing import Dict, Any, List
from urllib.parse import urlparse

bedrock_agent = boto3.client('bedrock-agent-runtime')
s3_client = boto3.client('s3')

SPEECH_KB_ID = os.environ.get('SPEECH_KB_ID')  # Speech index KB ID


def get_s3_content(s3_uri: str) -> Dict[str, Any]:
    """Fetch and parse JSON content from S3"""
    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip('/')
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except Exception as e:
        print(f"Error fetching S3 content from {s3_uri}: {e}")
        return {}


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Search for video segments by semantic search in transcripts.
    """
    print(f"=== search_by_speech tool invoked ===")
    print(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Validate required input
        query = event.get('query')
        if not query:
            raise ValueError("query is required")
        
        top_k = min(int(event.get('top_k', 5)), 20)  # Cap at 20
        video_id = event.get('video_id')  # Optional filter
        
        print(f"Searching speech: query='{query}', top_k={top_k}")
        if video_id:
            print(f"Filtering by video_id: {video_id}")
        
        # Query Bedrock Knowledge Base
        retrieve_params = {
            'knowledgeBaseId': SPEECH_KB_ID,
            'retrievalQuery': {'text': query},
            'retrievalConfiguration': {
                'vectorSearchConfiguration': {
                    'numberOfResults': top_k
                }
            }
        }
        
        # Add filter if video_id specified
        if video_id:
            retrieve_params['retrievalConfiguration']['vectorSearchConfiguration']['filter'] = {
                'equals': {
                    'key': 'video_id',
                    'value': video_id
                }
            }
        
        response = bedrock_agent.retrieve(**retrieve_params)
        
        # Parse results
        results = []
        for item in response.get('retrievalResults', []):
            content = item.get('content', {})
            metadata = item.get('metadata', {})
            s3_uri = item.get('location', {}).get('s3Location', {}).get('uri', '')
            score = float(item.get('score', 0))
            
            # Try to parse content text as JSON first
            text_content = content.get('text', '')
            data = {}
            
            try:
                if text_content.strip().startswith('{'):
                    data = json.loads(text_content)
                else:
                    # If text doesn't look like JSON (e.g. embedding vector string),
                    # fetch original file from S3
                    print(f"Content text not JSON, fetching from S3: {s3_uri}")
                    data = get_s3_content(s3_uri)
            except Exception as e:
                print(f"Error parsing content, falling back to S3: {e}")
                data = get_s3_content(s3_uri)
            
            # Fallback: check if S3 fetch also failed or returned empty
            if not data and s3_uri:
                data = get_s3_content(s3_uri)

            # Extract fields with fallbacks
            result = {
                'video_id': data.get('video_id') or metadata.get('video_id', ''),
                'chunk_id': data.get('chunk_id', ''),
                'text': data.get('chunk_text') or data.get('text', ''),
                'start_time': float(data.get('start_time_sec') or 0),
                'end_time': float(data.get('end_time_sec') or 0),
                'score': score,
                's3_uri': s3_uri
            }
            
            # Skip if no text found
            if not result['text']:
                print(f"Skipping result with no text: {s3_uri}")
                continue
                
            results.append(result)
            print(f"  Match: score={score:.3f}, video={result['video_id']}, time={result['start_time']:.1f}s")
        
        output = {
            'query': query,
            'results': results,
            'count': len(results)
        }
        
        print(f"✓ Found {len(results)} matches")
        
        return {
            'statusCode': 200,
            'body': json.dumps(output)
        }
    
    except ValueError as e:
        error_msg = f"Invalid input: {str(e)}"
        print(f"❌ {error_msg}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': error_msg})
        }
    
    except Exception as e:
        error_msg = f"Internal error: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }
