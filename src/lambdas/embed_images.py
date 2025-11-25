"""
Lambda Function: Embed Images for Image Index

Purpose: Generate multimodal embeddings for frame images (from Feature 1.2)
         Matches Kubrick's approach: one embedding per frame (no chunking needed)

Triggered by: Manual invocation or EventBridge rule (after frames extracted)

Input Event:
{
    "video_id": "test-video",
    "frames_s3_prefix": "test-video/frames"
}

Output:
- Image embeddings stored in S3 for Image Index (Bedrock KB #3)
- DynamoDB updated with image_index_s3_prefix
- Status updated to "image_index_ready"

Feature: 1.6 - Image Index (NO chunking - already discrete frames)
"""

import json
import os
import boto3
import base64
from datetime import datetime
from decimal import Decimal
from typing import List, Dict

# AWS clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb')

# Environment variables
PROCESSED_BUCKET = os.environ['PROCESSED_BUCKET']
METADATA_TABLE = os.environ['METADATA_TABLE']
BEDROCK_MULTIMODAL_MODEL = os.environ.get('BEDROCK_MULTIMODAL_MODEL', 'amazon.titan-embed-image-v1')

# Cost tracking
COST_PER_EMBEDDING = 0.00006  # Approximate cost per Titan Image embedding


def load_frames_from_s3(video_id: str, frames_prefix: str) -> List[Dict]:
    """
    Load frame images from S3 (extracted by Feature 1.2)
    
    Args:
        video_id: Video identifier
        frames_prefix: S3 prefix for frame images
    
    Returns:
        List of frame dictionaries with metadata
    """
    frames = []
    
    # List all frame images in S3
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(
        Bucket=PROCESSED_BUCKET,
        Prefix=f"{frames_prefix}/"
    )
    
    for page in pages:
        if 'Contents' not in page:
            continue
        
        for obj in page['Contents']:
            key = obj['Key']
            
            # Skip directories
            if key.endswith('/'):
                continue
            
            # Skip non-image files
            if not key.lower().endswith(('.jpg', '.jpeg', '.png')):
                continue
            
            # Extract frame number from filename (e.g., frame_0001.jpg)
            filename = key.split('/')[-1]
            try:
                frame_number = int(filename.split('_')[1].split('.')[0])
            except (IndexError, ValueError):
                print(f"Warning: Could not parse frame number from {filename}")
                continue
            
            frames.append({
                'frame_number': frame_number,
                's3_key': key,
                'size_bytes': obj['Size']
            })
    
    # Sort by frame number
    frames.sort(key=lambda f: f['frame_number'])
    
    return frames


def generate_image_embedding(image_bytes: bytes) -> List[float]:
    """
    Generate multimodal embedding using Bedrock Titan Embed Image
    
    Args:
        image_bytes: Image data as bytes
    
    Returns:
        Embedding vector (list of floats)
    """
    try:
        # Encode image to base64
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        request_body = {
            "inputImage": image_base64
        }
        
        response = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MULTIMODAL_MODEL,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response['body'].read())
        embedding = response_body.get('embedding', [])
        
        return embedding
        
    except Exception as e:
        print(f"Error generating image embedding: {str(e)}")
        return []


def calculate_frame_timestamp(frame_number: int, total_frames: int, video_duration_sec: float) -> float:
    """
    Calculate timestamp for frame based on even distribution
    
    Args:
        frame_number: Frame number (1-based)
        total_frames: Total number of frames
        video_duration_sec: Video duration in seconds
    
    Returns:
        Timestamp in seconds
    """
    if total_frames <= 1:
        return 0.0
    
    # Frames are evenly distributed: frame_interval = duration / total_frames
    frame_interval = video_duration_sec / total_frames
    
    # Frame 1 is at 0s, frame N is at (N-1) * interval
    timestamp = (frame_number - 1) * frame_interval
    
    return timestamp


def handler(event, context):
    """
    Lambda handler: Generate multimodal embeddings for frame images (no chunking needed)
    """
    print(f"Event: {json.dumps(event)}")
    
    # Parse event
    video_id = event['video_id']
    frames_prefix = event.get('frames_s3_prefix')
    
    # If frames_prefix not provided, get from DynamoDB
    if not frames_prefix:
        table = dynamodb.Table(METADATA_TABLE)
        response = table.get_item(Key={'video_id': video_id})
        
        if 'Item' not in response:
            raise ValueError(f"Video {video_id} not found in metadata table")
        
        video_metadata = response['Item']
        frames_prefix = video_metadata.get('frames_s3_prefix')
        
        if not frames_prefix:
            raise ValueError(f"No frames found for video {video_id}")
        
        # Get video duration for timestamp calculation
        video_duration = float(video_metadata.get('duration_seconds', 0))
    else:
        # Get video duration from DynamoDB
        table = dynamodb.Table(METADATA_TABLE)
        response = table.get_item(Key={'video_id': video_id})
        video_duration = float(response['Item'].get('duration_seconds', 0)) if 'Item' in response else 0
    
    print(f"Loading frames from: s3://{PROCESSED_BUCKET}/{frames_prefix}/")
    print(f"Video duration: {video_duration:.2f}s")
    
    # Step 1: Load frame images from S3
    print("Loading frames from S3...")
    frames = load_frames_from_s3(video_id, frames_prefix)
    
    if not frames:
        raise ValueError(f"No frames found at s3://{PROCESSED_BUCKET}/{frames_prefix}/")
    
    print(f"Loaded {len(frames)} frames")
    
    # Step 2: Generate embeddings for each frame (no chunking!)
    print("Generating multimodal embeddings for frames...")
    image_index_docs = []
    
    for frame in frames:
        frame_number = frame['frame_number']
        s3_key = frame['s3_key']
        
        # Calculate frame timestamp (frames are evenly distributed)
        frame_timestamp_sec = calculate_frame_timestamp(
            frame_number=frame_number,
            total_frames=len(frames),
            video_duration_sec=video_duration
        )
        
        # Download frame image
        response = s3_client.get_object(Bucket=PROCESSED_BUCKET, Key=s3_key)
        image_bytes = response['Body'].read()
        
        # Generate embedding for frame image
        embedding = generate_image_embedding(image_bytes)
        
        if not embedding:
            print(f"Warning: Failed to generate embedding for frame {frame_number}")
            continue
        
        # Create image index document (for Bedrock KB #3)
        # Note: This is already at the correct granularity - no chunking needed!
        index_doc = {
            'video_id': video_id,
            'image_id': f"{video_id}_frame_{frame_number:04d}",
            'frame_number': frame_number,
            'frame_timestamp_sec': frame_timestamp_sec,
            'frame_s3_key': s3_key,
            'embedding': embedding,
            'embedding_dimension': len(embedding),
            'metadata': {
                'generated_at': datetime.utcnow().isoformat(),
                'embedding_model': BEDROCK_MULTIMODAL_MODEL,
                'source': 'frame_image',
                'image_size_bytes': frame['size_bytes']
            }
        }
        
        image_index_docs.append(index_doc)
        
        print(f"✓ Frame {frame_number}/{len(frames)}: {frame_timestamp_sec:.1f}s")
    
    print(f"Generated {len(image_index_docs)} image embeddings")
    
    # Step 3: Upload image index documents to S3
    image_index_prefix = f"{video_id}/image_embeddings"
    
    for doc in image_index_docs:
        doc_key = f"{image_index_prefix}/frame_{doc['frame_number']:04d}.json"
        
        # Store document with embedding
        s3_client.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=doc_key,
            Body=json.dumps(doc, indent=2),
            ContentType='application/json'
        )
    
    print(f"✓ Uploaded {len(image_index_docs)} documents to s3://{PROCESSED_BUCKET}/{image_index_prefix}/")
    
    # Step 4: Update DynamoDB with image index metadata
    estimated_cost = len(image_index_docs) * COST_PER_EMBEDDING
    
    table = dynamodb.Table(METADATA_TABLE)
    table.update_item(
        Key={'video_id': video_id},
        UpdateExpression="""
            SET #status = :status,
                image_embedding_count = :image_count,
                image_index_s3_prefix = :image_index_prefix,
                processing_cost_estimate = processing_cost_estimate + :embedding_cost,
                updated_at = :timestamp
        """,
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': 'image_index_ready',
            ':image_count': len(image_index_docs),
            ':image_index_prefix': image_index_prefix,
            ':embedding_cost': Decimal(str(round(estimated_cost, 4))),
            ':timestamp': datetime.utcnow().isoformat()
        }
    )
    
    print(f"✓ DynamoDB updated: status=image_index_ready, image_embedding_count={len(image_index_docs)}")
    print(f"✓ Estimated cost: ${estimated_cost:.4f}")
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'video_id': video_id,
            'image_embedding_count': len(image_index_docs),
            'image_index_s3_prefix': image_index_prefix,
            'estimated_cost': round(estimated_cost, 4)
        })
    }

