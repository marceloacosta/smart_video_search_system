"""
Lambda: Search Images

Text-to-image semantic search using multimodal embeddings (CLIP capability).
Provides API endpoint for searching video frames based on visual content.

Architecture:
1. Accept text query from user
2. Generate text embedding using Bedrock Titan Multimodal
3. Query S3 Vectors for similar image embeddings
4. Return matching frames with scores and metadata

Cost: ~$0.0001 per query + S3 Vectors query cost
"""

import json
import os
import boto3
from typing import List, Dict, Any

# Initialize AWS clients
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')
s3vectors = boto3.client('s3vectors', region_name='us-east-1')

# Environment variables
S3_VECTOR_BUCKET = os.environ.get('S3_VECTOR_BUCKET', 'mvip-image-vectors')
S3_VECTOR_INDEX = os.environ.get('S3_VECTOR_INDEX', 'image-embeddings')
BEDROCK_MODEL = os.environ.get('BEDROCK_MULTIMODAL_MODEL', 'amazon.titan-embed-image-v1')

print(f"Configuration:")
print(f"  Vector Bucket: {S3_VECTOR_BUCKET}")
print(f"  Vector Index: {S3_VECTOR_INDEX}")
print(f"  Bedrock Model: {BEDROCK_MODEL}")


def generate_text_embedding(text: str) -> List[float]:
    """
    Generate multimodal embedding for text query using Bedrock Titan Multimodal.
    This embedding can be compared with image embeddings (CLIP capability).
    
    Args:
        text: User query text (e.g., "Python code on screen")
    
    Returns:
        List of 1024 floats (embedding vector)
    """
    print(f"Generating text embedding for query: '{text}'")
    
    # Prepare request body for Titan Multimodal
    request_body = {
        "inputText": text
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


def query_similar_images(query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Query S3 Vectors for images similar to the query embedding.
    
    Args:
        query_embedding: Query vector (1024 dimensions)
        top_k: Number of results to return
    
    Returns:
        List of matching frames with scores and metadata
    """
    print(f"Querying S3 Vectors for top {top_k} matches...")
    
    # Query S3 Vectors
    # Query vector must also be in float32 format
    # IMPORTANT: returnMetadata must be True to get metadata back
    response = s3vectors.query_vectors(
        vectorBucketName=S3_VECTOR_BUCKET,
        indexName=S3_VECTOR_INDEX,
        queryVector={'float32': query_embedding},
        topK=top_k,
        returnMetadata=True,  # Required to get metadata in response
        returnDistance=True   # Also get similarity scores
    )
    
    # Parse results (S3 Vectors returns 'vectors' not 'matches')
    matches = []
    for result in response.get('vectors', []):
        # Metadata is returned as dict (not JSON string)
        metadata = result.get('metadata', {})
        
        matches.append({
            'key': result.get('key'),
            'distance': result.get('distance'),  # S3 Vectors returns 'distance' (lower is better)
            'video_id': metadata.get('video_id'),
            'frame_number': metadata.get('frame_number'),
            's3_key': metadata.get('s3_key'),
            's3_uri': metadata.get('s3_uri'),
            'size_bytes': metadata.get('size_bytes')
        })
        
        print(f"  Match {len(matches)}: Frame {metadata.get('frame_number')} (distance: {result.get('distance', 0):.4f})")
    
    print(f"✓ Found {len(matches)} matches")
    return matches


def handler(event, context):
    """
    Lambda handler: Text-to-image semantic search.
    
    Event format (API Gateway):
    {
        "body": "{\"query\": \"Python code on screen\", \"top_k\": 5}"
    }
    
    Or direct invocation:
    {
        "query": "Python code on screen",
        "top_k": 5
    }
    """
    print("=" * 80)
    print("Search Images Lambda (CLIP Text-to-Image)")
    print("=" * 80)
    
    # Parse event (handle both API Gateway and direct invocation)
    if 'body' in event:
        # API Gateway format
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    else:
        # Direct invocation format
        body = event
    
    print(f"Request: {json.dumps(body, indent=2)}")
    
    # Extract parameters
    query_text = body.get('query')
    top_k = body.get('top_k', 5)
    
    if not query_text:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Missing required parameter: query'
            })
        }
    
    print(f"\nQuery: '{query_text}'")
    print(f"Top K: {top_k}")
    
    try:
        # Step 1: Generate text embedding
        query_embedding = generate_text_embedding(query_text)
        
        # Step 2: Search for similar images
        matches = query_similar_images(query_embedding, top_k)
        
        # Prepare response
        response_body = {
            'query': query_text,
            'top_k': top_k,
            'matches': matches,
            'count': len(matches)
        }
        
        print(f"\n{'=' * 80}")
        print("✓ Search complete")
        print(f"{'=' * 80}")
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'  # CORS for web UI
            },
            'body': json.dumps(response_body, indent=2)
        }
    
    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'query': query_text
            })
        }

