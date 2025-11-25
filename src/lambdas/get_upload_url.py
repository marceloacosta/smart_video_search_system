"""
Generate Pre-signed Upload URL Lambda
Returns a pre-signed S3 URL for direct browser-to-S3 uploads.

This bypasses API Gateway's 10MB payload limit and allows
uploading videos of any size directly to S3.
"""

import json
import os
import boto3
import re
from typing import Dict, Any

s3 = boto3.client('s3')

VIDEOS_BUCKET = os.environ.get('VIDEOS_BUCKET')


def sanitize_filename(name: str) -> str:
    """
    Convert a title to a safe filename.
    """
    # Remove/replace unsafe characters
    safe = name.replace(' ', '-').replace('/', '-').replace('\\', '-')
    safe = ''.join(c for c in safe if c.isalnum() or c in '-_.')
    # Limit length
    return safe[:200]


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Generate a pre-signed S3 URL for direct upload.
    
    Input (API Gateway):
        POST /videos/upload-url
        {
            "file_name": "My Video.mp4",  // Required
            "title": "Custom Title"       // Optional
        }
    
    Returns:
        {
            "upload_url": "https://s3.amazonaws.com/...",
            "video_id": "my-video",
            "fields": {
                "key": "my-video.mp4",
                "bucket": "...",
                ...
            },
            "expires_in": 3600
        }
    """
    print(f"=== get_upload_url Lambda invoked ===")
    print(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Parse request body
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event.get('body', {})
        
        file_name = body.get('file_name')
        custom_title = body.get('title')
        
        # Validation
        if not file_name:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'file_name is required'})
            }
        
        # Auto-generate video_id from file_name
        video_id = sanitize_filename(os.path.splitext(file_name)[0])
        s3_key = f'{video_id}.mp4'
        
        print(f"Generating pre-signed URL for: {video_id}")
        print(f"S3 Key: {s3_key}")
        
        # Generate pre-signed POST URL (allows direct browser upload)
        presigned_post = s3.generate_presigned_post(
            Bucket=VIDEOS_BUCKET,
            Key=s3_key,
            Fields={
                'Content-Type': 'video/mp4',
                'x-amz-meta-title': custom_title or file_name,
                'x-amz-meta-original-filename': file_name
            },
            Conditions=[
                ['content-length-range', 1, 1073741824],  # 1 byte to 1GB
                {'Content-Type': 'video/mp4'},
                {'x-amz-meta-title': custom_title or file_name},
                {'x-amz-meta-original-filename': file_name}
            ],
            ExpiresIn=3600  # 1 hour
        )
        
        response_body = {
            'upload_url': presigned_post['url'],
            'fields': presigned_post['fields'],
            'video_id': video_id,
            's3_key': s3_key,
            'bucket': VIDEOS_BUCKET,
            'expires_in': 3600,
            'metadata': {
                'title': custom_title or file_name,
                'file_name': file_name
            }
        }
        
        print(f"✅ Pre-signed URL generated for {video_id}")
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(response_body)
        }
    
    except Exception as e:
        error_msg = f"Error generating upload URL: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': error_msg})
        }


# For local testing
if __name__ == '__main__':
    test_event = {
        'body': json.dumps({
            'file_name': 'Test Video.mp4',
            'title': 'My Test Video'
        })
    }
    result = handler(test_event, None)
    print("\n=== Test Result ===")
    print(json.dumps(result, indent=2))

