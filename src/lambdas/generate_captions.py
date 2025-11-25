"""
Lambda Function: Generate Frame Captions

Purpose: Generate descriptive captions for extracted video frames using Bedrock Claude Vision
         Matches Kubrick's approach: resize frames to 1024x768, simple prompt

Triggered by: Manual invocation or EventBridge rule (after frame extraction completes)

Input Event:
{
    "video_id": "test-video",
    "s3_bucket": "mvip-processed-{account}-{region}",
    "frames_s3_prefix": "test-video/frames"
}

Output:
- Captions stored in DynamoDB (frame-level metadata)
- Caption documents uploaded to S3 for Caption Index (Bedrock KB #2)
- Status updated to "captions_ready"

Feature: 1.3 - Frame Captions (Bedrock Claude Vision)
"""

import json
import os
import boto3
import base64
from datetime import datetime
from decimal import Decimal
from io import BytesIO
try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    print("Warning: Pillow not available. Frame resizing disabled.")

# AWS clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

# Environment variables
PROCESSED_BUCKET = os.environ['PROCESSED_BUCKET']
METADATA_TABLE = os.environ['METADATA_TABLE']
# Use inference profile instead of direct model ID
# Inference profiles provide cross-region routing and better availability
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_VISION_MODEL', 'us.anthropic.claude-3-5-sonnet-20241022-v2:0')

# Cost tracking
COST_PER_IMAGE = 0.006  # Approximate cost per Claude Vision API call

# Frame resizing (match Kubrick's approach)
TARGET_WIDTH = 1024
TARGET_HEIGHT = 768

def resize_frame(image_bytes: bytes) -> bytes:
    """
    Resize frame to 1024x768 (Kubrick's approach)
    
    Reduces token consumption and inference cost for VLM
    
    Note: Requires Pillow. For PoC without Pillow, returns original bytes.
    For production: Use Lambda Layer with pre-built Pillow for Amazon Linux.
    """
    if not PILLOW_AVAILABLE:
        print("Pillow not available, using original frame size")
        return image_bytes
    
    try:
        image = Image.open(BytesIO(image_bytes))
        
        # Resize maintaining aspect ratio
        image.thumbnail((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)
        
        # Convert back to JPEG bytes
        output = BytesIO()
        image.save(output, format='JPEG', quality=85)
        return output.getvalue()
    except Exception as e:
        print(f"Error resizing frame: {e}, using original")
        return image_bytes


def get_frame_caption(image_bytes: bytes, frame_number: int, video_context: str = "") -> dict:
    """
    Generate a descriptive caption for a frame using Bedrock Claude Vision
    Matches Kubrick's approach: simple, direct prompt
    
    Args:
        image_bytes: Raw image bytes (JPEG)
        frame_number: Frame number for context
        video_context: Optional context about the video (title, etc.)
    
    Returns:
        dict with 'caption' and 'confidence' keys
    """
    # Resize frame (match Kubrick: 1024x768)
    resized_bytes = resize_frame(image_bytes)
    
    # Encode image as base64
    image_base64 = base64.b64encode(resized_bytes).decode('utf-8')
    
    # Simple prompt (match Kubrick's approach)
    user_prompt = "Describe what is happening in the image"

    # Bedrock Claude Vision API request (simple, like Kubrick's GPT-4o mini call)
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 200,  # Shorter responses
        "temperature": 0.3,  # Lower temperature for consistent descriptions
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": user_prompt
                    }
                ]
            }
        ]
    }
    
    try:
        response = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response['body'].read())
        caption = response_body['content'][0]['text'].strip()
        
        return {
            'caption': caption,
            'model': BEDROCK_MODEL_ID,
            'confidence': 1.0  # Claude doesn't provide confidence scores
        }
    except Exception as e:
        print(f"Error generating caption for frame {frame_number}: {str(e)}")
        return {
            'caption': f"Error generating caption: {str(e)}",
            'model': BEDROCK_MODEL_ID,
            'confidence': 0.0
        }


def process_frame(video_id: str, frame_key: str, frame_number: int, 
                 timestamp_sec: float, video_title: str) -> dict:
    """
    Process a single frame: download, generate caption, prepare for index
    
    Returns:
        Caption document ready for S3 upload (Caption Index)
    """
    print(f"Processing frame {frame_number}: {frame_key}")
    
    # Download frame from S3
    response = s3_client.get_object(Bucket=PROCESSED_BUCKET, Key=frame_key)
    image_bytes = response['Body'].read()
    frame_size = len(image_bytes)
    
    print(f"Frame size: {frame_size} bytes")
    
    # Generate caption using Bedrock Claude Vision
    caption_result = get_frame_caption(
        image_bytes=image_bytes,
        frame_number=frame_number,
        video_context=video_title
    )
    
    caption = caption_result['caption']
    print(f"Caption generated: {caption[:100]}...")
    
    # Create caption document for Caption Index (Bedrock KB #2)
    caption_doc = {
        'video_id': video_id,
        'frame_id': f"{video_id}_frame_{frame_number:04d}",
        'frame_number': frame_number,
        'frame_timestamp_sec': timestamp_sec,
        'caption': caption,
        'frame_s3_key': frame_key,
        'frame_size_bytes': frame_size,
        'model': caption_result['model'],
        'metadata': {
            'video_title': video_title,
            'generated_at': datetime.utcnow().isoformat()
        }
    }
    
    return caption_doc


def handler(event, context):
    """
    Lambda handler: Generate captions for all frames of a video
    """
    print(f"Event: {json.dumps(event)}")
    
    # Parse event
    video_id = event['video_id']
    frames_s3_prefix = event['frames_s3_prefix']
    
    print(f"Generating captions for video: {video_id}")
    print(f"Frames location: s3://{PROCESSED_BUCKET}/{frames_s3_prefix}/")
    
    # Get video metadata
    table = dynamodb.Table(METADATA_TABLE)
    response = table.get_item(Key={'video_id': video_id})
    
    if 'Item' not in response:
        raise ValueError(f"Video {video_id} not found in metadata table")
    
    video_metadata = response['Item']
    video_title = video_metadata.get('title', video_id)
    duration_seconds = float(video_metadata.get('duration_seconds', 0))
    frame_count = int(video_metadata.get('frame_count', 0))
    
    print(f"Video: {video_title}")
    print(f"Duration: {duration_seconds} seconds")
    print(f"Frames to process: {frame_count}")
    
    # List all frames in S3
    frames = []
    paginator = s3_client.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=PROCESSED_BUCKET, Prefix=frames_s3_prefix + '/'):
        if 'Contents' in page:
            for obj in page['Contents']:
                if obj['Key'].endswith('.jpg'):
                    frames.append(obj['Key'])
    
    frames.sort()  # Ensure correct order
    print(f"Found {len(frames)} frames in S3")
    
    if len(frames) == 0:
        raise ValueError(f"No frames found in s3://{PROCESSED_BUCKET}/{frames_s3_prefix}/")
    
    # Calculate timestamp for each frame
    # Frames are evenly distributed across video duration
    time_per_frame = duration_seconds / len(frames) if len(frames) > 0 else 0
    
    # Process each frame
    caption_documents = []
    captions_by_frame = {}  # For DynamoDB storage
    
    for idx, frame_key in enumerate(frames, start=1):
        # Extract frame number from filename (e.g., "frame_0012.jpg" -> 12)
        frame_filename = frame_key.split('/')[-1]
        frame_number = int(frame_filename.replace('frame_', '').replace('.jpg', ''))
        
        # Calculate timestamp
        timestamp_sec = (frame_number - 1) * time_per_frame
        
        try:
            caption_doc = process_frame(
                video_id=video_id,
                frame_key=frame_key,
                frame_number=frame_number,
                timestamp_sec=timestamp_sec,
                video_title=video_title
            )
            
            caption_documents.append(caption_doc)
            captions_by_frame[str(frame_number)] = caption_doc['caption']  # DynamoDB Map keys must be strings
            
            print(f"✓ Frame {idx}/{len(frames)} processed")
            
        except Exception as e:
            print(f"✗ Error processing frame {frame_number}: {str(e)}")
            # Continue with other frames
    
    print(f"Generated {len(caption_documents)} captions")
    
    # Upload caption documents to S3 for Caption Index (Bedrock KB #2)
    caption_index_prefix = f"{video_id}/caption_index"
    
    for doc in caption_documents:
        doc_key = f"{caption_index_prefix}/frame_{doc['frame_number']:04d}.json"
        s3_client.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=doc_key,
            Body=json.dumps(doc, indent=2),
            ContentType='application/json'
        )
    
    print(f"Uploaded {len(caption_documents)} caption documents to s3://{PROCESSED_BUCKET}/{caption_index_prefix}/")
    
    # Update DynamoDB with captions and status
    estimated_cost = len(caption_documents) * COST_PER_IMAGE
    
    table.update_item(
        Key={'video_id': video_id},
        UpdateExpression="""
            SET #status = :status,
                captions = :captions,
                caption_count = :caption_count,
                caption_index_s3_prefix = :caption_prefix,
                processing_cost_estimate = processing_cost_estimate + :caption_cost,
                updated_at = :timestamp
        """,
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': 'captions_ready',
            ':captions': captions_by_frame,
            ':caption_count': len(caption_documents),
            ':caption_prefix': caption_index_prefix,
            ':caption_cost': Decimal(str(round(estimated_cost, 4))),
            ':timestamp': datetime.utcnow().isoformat()
        }
    )
    
    print(f"✓ DynamoDB updated: status=captions_ready, caption_count={len(caption_documents)}")
    print(f"✓ Estimated cost: ${estimated_cost:.4f}")
    
    # Trigger embed_captions Lambda to embed captions into Caption KB
    try:
        lambda_client.invoke(
            FunctionName='mvip-embed-captions',
            InvocationType='Event',  # Async invocation
            Payload=json.dumps({
                'video_id': video_id,
                'frames_s3_prefix': frames_s3_prefix
            })
        )
        print(f"✅ Triggered embed_captions for {video_id}")
    except Exception as e:
        print(f"⚠️  Failed to trigger embed_captions: {e}")
        # Don't fail the whole process if embedding trigger fails
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'video_id': video_id,
            'caption_count': len(caption_documents),
            'caption_index_s3_prefix': caption_index_prefix,
            'estimated_cost': round(estimated_cost, 4)
        })
    }

