"""
MCP Tool: get_video_metadata

Retrieves detailed metadata for a specific video.

Returns comprehensive information including duration, frame count,
processing status, timestamps, and cost estimates.
"""

import os
import json
import boto3
from decimal import Decimal
from typing import Dict, Any

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ.get('METADATA_TABLE', 'mvip-video-metadata'))


class DecimalEncoder(json.JSONEncoder):
    """Helper to convert Decimal to float for JSON serialization"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Get detailed metadata for a specific video.
    
    Input Schema:
        {
            "video_id": "my-video-id"  # Required
        }
    
    Output Schema:
        {
            "video_id": "...",
            "title": "...",
            "duration_seconds": 123.45,
            "frame_count": 45,
            "upload_timestamp": "...",
            "last_modified": "...",
            "status": "completed",
            "s3_bucket": "...",
            "s3_key": "...",
            "processed_bucket": "...",
            "transcript_s3_key": "...",
            "frames_s3_prefix": "...",
            "transcribe_job_name": "...",
            "processing_cost_estimate": 1.00,
            "captions": {
                "count": 45,
                "sample": {"1": "A person coding...", "10": "..."}
            }
        }
    
    Returns:
        HTTP-style response for AgentCore Gateway
    """
    print(f"=== get_video_metadata tool invoked ===")
    print(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Validate required input
        video_id = event.get('video_id')
        if not video_id:
            raise ValueError("video_id is required")
        
        print(f"Fetching metadata for video: {video_id}")
        
        # Get item from DynamoDB
        response = table.get_item(Key={'video_id': video_id})
        
        if 'Item' not in response:
            return {
                'statusCode': 404,
                'body': json.dumps({
                    'error': f"Video not found: {video_id}"
                })
            }
        
        item = response['Item']
        print(f"✓ Found video: {item.get('title', 'Untitled')}")
        
        # Format metadata response
        metadata = {
            'video_id': item.get('video_id', ''),
            'title': item.get('title', 'Untitled'),
            'duration_seconds': float(item.get('duration_seconds', 0)),
            'frame_count': int(item.get('frame_count', 0)),
            'upload_timestamp': item.get('upload_timestamp', ''),
            'last_modified': item.get('last_modified', ''),
            'created_at': item.get('created_at', ''),
            'updated_at': item.get('updated_at', ''),
            'status': item.get('status', 'unknown'),
            's3_bucket': item.get('s3_bucket', ''),
            's3_key': item.get('s3_key', ''),
            'size_bytes': int(item.get('size_bytes', 0)),
            'processed_bucket': item.get('processed_bucket', ''),
            'transcript_s3_key': item.get('transcript_s3_key', ''),
            'frames_s3_prefix': item.get('frames_s3_prefix', ''),
            'transcribe_job_name': item.get('transcribe_job_name', ''),
            'processing_cost_estimate': float(item.get('processing_cost_estimate', 0))
        }
        
        # Add captions if available
        if 'captions' in item:
            captions_map = item['captions']
            # Get sample of captions (first 3)
            sample_keys = sorted(captions_map.keys(), key=int)[:3]
            caption_sample = {k: captions_map[k] for k in sample_keys}
            
            metadata['captions'] = {
                'count': len(captions_map),
                'sample': caption_sample
            }
        
        print(f"✓ Returning metadata for {video_id}")
        
        return {
            'statusCode': 200,
            'body': json.dumps(metadata, cls=DecimalEncoder)
        }
    
    except ValueError as e:
        # Input validation error
        error_msg = f"Invalid input: {str(e)}"
        print(f"❌ {error_msg}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': error_msg})
        }
    
    except Exception as e:
        # Unexpected error
        error_msg = f"Internal error: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }


# For local testing
if __name__ == '__main__':
    # Test with a video ID
    test_event = {'video_id': 'test-metadata-update'}
    result = handler(test_event, None)
    print("\n=== Test Result ===")
    print(json.dumps(result, indent=2))

