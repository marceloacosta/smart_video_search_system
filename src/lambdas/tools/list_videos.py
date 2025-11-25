"""
MCP Tool: list_videos

Lists all processed videos in the system with their metadata.

This is a foundational tool that enables the agent to discover
what videos are available for search and analysis.
"""

import os
import json
import boto3
from decimal import Decimal
from typing import Dict, List, Any

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
    List all processed videos in the system.
    
    Input Schema:
        {
            "limit": 10,        # Optional, defaults to 10, max 100
            "status": "all"     # Optional: "all", "completed", "processing", "failed"
        }
    
    Output Schema:
        {
            "videos": [
                {
                    "video_id": "...",
                    "title": "...",
                    "duration_seconds": 123.45,
                    "frame_count": 45,
                    "upload_timestamp": "...",
                    "status": "completed",
                    "s3_bucket": "...",
                    "s3_key": "..."
                }
            ],
            "count": 5,
            "total_duration_seconds": 617.25
        }
    
    Returns:
        HTTP-style response for AgentCore Gateway
    """
    print(f"=== list_videos tool invoked ===")
    print(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Parse input parameters
        limit = min(int(event.get('limit', 10)), 100)  # Cap at 100
        status_filter = event.get('status', 'all').lower()
        
        print(f"Listing videos: limit={limit}, status={status_filter}")
        
        # Query DynamoDB
        scan_params = {
            'Limit': limit,
            'ProjectionExpression': (
                'video_id, title, duration_seconds, frame_count, '
                'upload_timestamp, #status, s3_bucket, s3_key, '
                'processing_cost_estimate'
            ),
            'ExpressionAttributeNames': {
                '#status': 'status'  # 'status' is a reserved word
            }
        }
        
        # Add status filter if specified
        if status_filter != 'all':
            scan_params['FilterExpression'] = '#status = :status_val'
            scan_params['ExpressionAttributeValues'] = {
                ':status_val': status_filter
            }
        
        response = table.scan(**scan_params)
        items = response.get('Items', [])
        
        print(f"Found {len(items)} videos")
        
        # Format response
        videos = []
        total_duration = 0
        
        for item in items:
            video = {
                'video_id': item.get('video_id', ''),
                'title': item.get('title', 'Untitled'),
                'duration_seconds': float(item.get('duration_seconds', 0)),
                'frame_count': int(item.get('frame_count', 0)),
                'upload_timestamp': item.get('upload_timestamp', ''),
                'status': item.get('status', 'unknown'),
                's3_bucket': item.get('s3_bucket', ''),
                's3_key': item.get('s3_key', ''),
                'processing_cost_estimate': float(item.get('processing_cost_estimate', 0))
            }
            videos.append(video)
            total_duration += video['duration_seconds']
        
        result = {
            'videos': videos,
            'count': len(videos),
            'total_duration_seconds': round(total_duration, 2)
        }
        
        print(f"✓ Returning {len(videos)} videos")
        
        return {
            'statusCode': 200,
            'body': json.dumps(result, cls=DecimalEncoder)
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
    # Test with default parameters
    test_event = {'limit': 5}
    result = handler(test_event, None)
    print("\n=== Test Result ===")
    print(json.dumps(result, indent=2))

