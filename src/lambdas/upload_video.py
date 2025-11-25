"""
Video Upload Lambda
Handles video file uploads to S3 and triggers processing pipeline.

Accepts base64 encoded video files via API Gateway.
After upload to S3, automatically triggers the processing pipeline.

Note: For YouTube videos, use a separate script to download first,
then upload via this API. yt-dlp is too large to bundle in Lambda.
"""

import json
import os
import boto3
import tempfile
import base64
from typing import Dict, Any
from datetime import datetime

# AWS clients
s3 = boto3.client('s3')
lambda_client = boto3.client('lambda')

# Environment variables
VIDEOS_BUCKET = os.environ.get('VIDEOS_BUCKET')
PROCESS_VIDEO_LAMBDA = os.environ.get('PROCESS_VIDEO_LAMBDA')


def sanitize_filename(name: str) -> str:
    """
    Convert a title to a safe filename.
    """
    # Remove/replace unsafe characters
    safe = name.replace(' ', '-').replace('/', '-').replace('\\', '-')
    safe = ''.join(c for c in safe if c.isalnum() or c in '-_.')
    # Limit length
    return safe[:200]


def upload_to_s3(local_path: str, video_id: str) -> str:
    """
    Upload video file to S3 raw videos bucket.
    Returns the S3 key.
    """
    key = f"{video_id}.mp4"
    
    print(f"  📤 Uploading to s3://{VIDEOS_BUCKET}/{key}")
    
    file_size = os.path.getsize(local_path)
    
    # Upload with progress tracking for large files
    s3.upload_file(
        local_path,
        VIDEOS_BUCKET,
        key,
        ExtraArgs={
            'ContentType': 'video/mp4',
            'Metadata': {
                'uploaded_at': datetime.utcnow().isoformat(),
                'uploaded_by': 'upload_video_lambda'
            }
        }
    )
    
    print(f"  ✅ Uploaded {file_size / 1024 / 1024:.2f} MB to S3")
    
    return key


def trigger_processing_pipeline(video_id: str) -> bool:
    """
    Trigger the process_video Lambda to start the processing pipeline.
    """
    print(f"  ⚙️  Triggering processing pipeline for {video_id}")
    
    try:
        # Create a synthetic S3 event
        event = {
            'Records': [{
                's3': {
                    'bucket': {'name': VIDEOS_BUCKET},
                    'object': {'key': f'{video_id}.mp4'}
                }
            }]
        }
        
        response = lambda_client.invoke(
            FunctionName=PROCESS_VIDEO_LAMBDA,
            InvocationType='Event',  # Async invocation
            Payload=json.dumps(event)
        )
        
        print(f"  ✅ Processing pipeline triggered")
        return True
    
    except Exception as e:
        print(f"  ⚠️  Error triggering pipeline: {e}")
        return False


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handle video upload from multiple sources.
    
    Input (API Gateway event):
        POST /videos
        {
            "file_name": "video.mp4",     # required: original file name (auto-generates video_id)
            "file_data": "base64...",     # required: base64 encoded video file
            "title": "Custom Title"       # optional: custom display title (defaults to file_name)
        }
    
    Returns:
        {
            "statusCode": 200,
            "body": {
                "message": "Video uploaded successfully",
                "video_id": "succession-trailer",
                "status": "processing",
                "s3_key": "succession-trailer.mp4"
            }
        }
    """
    print(f"=== upload_video Lambda invoked ===")
    print(f"Event: {json.dumps(event, default=str)[:500]}")  # Truncate for large payloads
    
    temp_dir = None
    
    try:
        # Parse request body
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event.get('body', {})
        
        file_name = body.get('file_name')
        file_data = body.get('file_data')
        custom_title = body.get('title')
        
        # Validation
        if not file_name:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'file_name is required'})
            }
        
        if not file_data:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'file_data (base64 encoded) is required'})
            }
        
        # Auto-generate video_id from file_name
        video_id = sanitize_filename(os.path.splitext(file_name)[0])
        
        # Use custom title or fallback to file_name without extension
        display_title = custom_title or os.path.splitext(file_name)[0]
        
        print(f"\n📥 Uploading video: {video_id}")
        print(f"   File name: {file_name}")
        print(f"   Display title: {display_title}")
        
        # Create temp directory
        temp_dir = tempfile.mkdtemp()
        
        # Decode base64 file data
        try:
            file_bytes = base64.b64decode(file_data)
        except Exception as e:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': f'Invalid base64 data: {str(e)}'})
            }
        
        # Save to temp file
        local_video_path = os.path.join(temp_dir, f'{video_id}.mp4')
        with open(local_video_path, 'wb') as f:
            f.write(file_bytes)
        
        video_metadata = {
            'title': file_name,
            'size_bytes': len(file_bytes)
        }
        
        print(f"  ✅ Decoded file: {len(file_bytes) / 1024 / 1024:.2f} MB")
        
        # Upload to S3
        s3_key = upload_to_s3(local_video_path, video_id)
        
        # Trigger processing pipeline
        pipeline_triggered = trigger_processing_pipeline(video_id)
        
        response_body = {
            'message': 'Video uploaded successfully',
            'video_id': video_id,
            'status': 'processing' if pipeline_triggered else 'uploaded',
            's3_key': s3_key,
            'metadata': {
                'title': display_title,
                'file_name': file_name,
                'size_mb': round(video_metadata.get('size_bytes', 0) / 1024 / 1024, 2)
            }
        }
        
        print(f"\n✅ Video uploaded: {video_id}")
        print(f"Response: {json.dumps(response_body, indent=2)}")
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(response_body)
        }
    
    except Exception as e:
        error_msg = f"Error uploading video: {str(e)}"
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
    
    finally:
        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


# For local testing
if __name__ == '__main__':
    # Example with a small test file (you'd need to provide actual base64 data)
    test_event = {
        'body': json.dumps({
            'file_name': 'Test Video.mp4',
            'file_data': 'VGVzdCBkYXRh',  # Base64 for "Test data"
            'title': 'My Custom Title'
        })
    }
    result = handler(test_event, None)
    print("\n=== Test Result ===")
    print(json.dumps(result, indent=2))

