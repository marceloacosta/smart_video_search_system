"""
Lambda: Embed and Index Images

Generates multimodal embeddings for video frames and stores them in S3 Vectors.
Triggered after frame extraction completes (Feature 1.2).

Architecture:
1. Read frames from S3 (JPG images)
2. Generate embeddings using Bedrock Titan Multimodal
3. Store vectors in S3 Vectors using put-vectors API
4. Update DynamoDB metadata

Cost: ~$0.004 per video (45 images × $0.0001 per embedding)
"""

import json
import base64
import os
import boto3
from decimal import Decimal
from typing import List, Dict, Any

# Initialize AWS clients
s3 = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb')
s3vectors = boto3.client('s3vectors', region_name='us-east-1')

# Environment variables
PROCESSED_BUCKET = os.environ['PROCESSED_BUCKET']
METADATA_TABLE = os.environ['METADATA_TABLE']
S3_VECTOR_BUCKET = os.environ.get('S3_VECTOR_BUCKET', 'mvip-image-vectors')
S3_VECTOR_INDEX = os.environ.get('S3_VECTOR_INDEX', 'image-embeddings')
BEDROCK_MODEL = os.environ.get('BEDROCK_MULTIMODAL_MODEL', 'amazon.titan-embed-image-v1')

print(f"Configuration:")
print(f"  Processed Bucket: {PROCESSED_BUCKET}")
print(f"  Vector Bucket: {S3_VECTOR_BUCKET}")
print(f"  Vector Index: {S3_VECTOR_INDEX}")
print(f"  Bedrock Model: {BEDROCK_MODEL}")

def generate_image_embedding(image_bytes: bytes) -> List[float]:
    """
    Generate multimodal embedding for an image using Bedrock Titan Multimodal.
    
    Args:
        image_bytes: Raw image bytes (JPG)
    
    Returns:
        List of 1024 floats (embedding vector)
    """
    # Encode image as base64
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    # Prepare request body for Titan Multimodal
    request_body = {
        "inputImage": image_base64
    }
    
    # Invoke Bedrock
    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL,
        body=json.dumps(request_body)
    )
    
    # Parse response
    response_body = json.loads(response['body'].read())
    embedding = response_body['embedding']
    
    print(f"  Generated embedding: {len(embedding)} dimensions")
    return embedding


def extract_frame_number(s3_key: str) -> int:
    """
    Extract frame number from S3 key.
    Example: 'test-metadata-update/frames/frame_0023.jpg' -> 23
    """
    filename = s3_key.split('/')[-1]  # Get 'frame_0023.jpg'
    frame_num = filename.replace('frame_', '').replace('.jpg', '')
    return int(frame_num)


def store_vectors_in_s3(vectors: List[Dict[str, Any]]) -> None:
    """
    Store embedding vectors in S3 Vectors.
    
    Args:
        vectors: List of vector records with id, embedding, and metadata
    """
    print(f"Storing {len(vectors)} vectors in S3 Vectors...")
    
    # Prepare records for put-vectors API
    # S3 Vectors API structure: data must be dict with 'float32' key
    vector_records = []
    for vec in vectors:
        vector_records.append({
            'key': vec['id'],
            'data': {
                'float32': vec['embedding']  # Wrap embedding in float32 dict
            },
            'metadata': vec['metadata']  # Metadata as dict (not JSON string)
        })
    
    # Put vectors in batches (API limit: 100 vectors per request)
    batch_size = 100
    for i in range(0, len(vector_records), batch_size):
        batch = vector_records[i:i + batch_size]
        
        s3vectors.put_vectors(
            vectorBucketName=S3_VECTOR_BUCKET,
            indexName=S3_VECTOR_INDEX,
            vectors=batch
        )
        
        print(f"  Stored batch {i // batch_size + 1} ({len(batch)} vectors)")
    
    print(f"✓ All {len(vectors)} vectors stored successfully")


def handler(event, context):
    """
    Lambda handler: Process frames and generate multimodal embeddings.
    
    Event format:
    {
        "video_id": "test-metadata-update",
        "frames_prefix": "test-metadata-update/frames/"
    }
    """
    print("=" * 80)
    print("Embed and Index Images Lambda")
    print("=" * 80)
    print(f"Event: {json.dumps(event, indent=2)}")
    
    # Parse event
    video_id = event.get('video_id')
    frames_prefix = event.get('frames_prefix')
    
    if not video_id or not frames_prefix:
        raise ValueError("Missing required fields: video_id, frames_prefix")
    
    print(f"\nProcessing video: {video_id}")
    print(f"Frames prefix: {frames_prefix}")
    
    # Get video metadata for duration calculation
    table = dynamodb.Table(METADATA_TABLE)
    metadata_response = table.get_item(Key={'video_id': video_id})
    
    if 'Item' not in metadata_response:
        raise ValueError(f"Video metadata not found for {video_id}")
    
    video_metadata = metadata_response['Item']
    duration_seconds = float(video_metadata.get('duration_seconds', 0))
    frame_count = int(video_metadata.get('frame_count', 45))
    
    print(f"Video duration: {duration_seconds}s, Total frames: {frame_count}")
    
    # List all frames in S3
    print(f"\nListing frames from s3://{PROCESSED_BUCKET}/{frames_prefix}")
    response = s3.list_objects_v2(
        Bucket=PROCESSED_BUCKET,
        Prefix=frames_prefix
    )
    
    if 'Contents' not in response:
        raise ValueError(f"No frames found at {frames_prefix}")
    
    frame_objects = [obj for obj in response['Contents'] if obj['Key'].endswith('.jpg')]
    print(f"Found {len(frame_objects)} frames")
    
    # Process each frame
    vectors = []
    total_embedding_cost = 0.0
    
    for idx, frame_obj in enumerate(frame_objects, 1):
        frame_key = frame_obj['Key']
        frame_number = extract_frame_number(frame_key)
        
        print(f"\nProcessing frame {idx}/{len(frame_objects)}: {frame_key}")
        
        # Read image from S3
        image_response = s3.get_object(Bucket=PROCESSED_BUCKET, Key=frame_key)
        image_bytes = image_response['Body'].read()
        image_size = len(image_bytes)
        
        print(f"  Size: {image_size:,} bytes")
        
        # Generate embedding
        embedding = generate_image_embedding(image_bytes)
        
        # Calculate exact timestamp for this frame
        # Frames are evenly spaced: frame 1 at 0s, last frame at duration
        timestamp = (frame_number - 1) * duration_seconds / (frame_count - 1) if frame_count > 1 else 0.0
        
        # Prepare vector record
        # S3 Vectors supports string, number, boolean, and list types for metadata
        vector_id = f"{video_id}_frame_{frame_number:04d}"
        vectors.append({
            'id': vector_id,
            'embedding': embedding,
            'metadata': {
                'video_id': video_id,
                'frame_number': frame_number,
                'timestamp': round(timestamp, 2),  # Exact capture time in seconds
                'duration_seconds': round(duration_seconds, 2),
                's3_key': frame_key,
                's3_uri': f"s3://{PROCESSED_BUCKET}/{frame_key}",
                'size_bytes': image_size
            }
        })
        
        # Track cost (Titan Multimodal: ~$0.0001 per image)
        total_embedding_cost += 0.0001
    
    print(f"\n{'=' * 80}")
    print(f"Generated {len(vectors)} embeddings")
    print(f"Estimated cost: ${total_embedding_cost:.4f}")
    print(f"{'=' * 80}")
    
    # Store all vectors in S3 Vectors
    store_vectors_in_s3(vectors)
    
    # Update DynamoDB metadata
    print(f"\nUpdating DynamoDB metadata for video: {video_id}")
    table = dynamodb.Table(METADATA_TABLE)
    
    table.update_item(
        Key={'video_id': video_id},
        UpdateExpression='SET image_index_status = :status, image_index_count = :count, image_embedding_cost = :cost',
        ExpressionAttributeValues={
            ':status': 'indexed',
            ':count': len(vectors),
            ':cost': Decimal(str(round(total_embedding_cost, 4)))
        }
    )
    
    print("✓ DynamoDB metadata updated")
    
    # Return summary
    result = {
        'video_id': video_id,
        'frames_processed': len(vectors),
        'vector_bucket': S3_VECTOR_BUCKET,
        'vector_index': S3_VECTOR_INDEX,
        'embedding_cost': round(total_embedding_cost, 4),
        'status': 'success'
    }
    
    print(f"\n{'=' * 80}")
    print("✓ Image indexing complete")
    print(json.dumps(result, indent=2))
    print(f"{'=' * 80}")
    
    return result

