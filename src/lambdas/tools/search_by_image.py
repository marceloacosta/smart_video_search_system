"""
MCP Tool: search_by_image

Text-to-image search using custom S3 Vectors implementation.

Uses Amazon Titan Multimodal Embeddings G1 to find visually
similar frames based on text queries (CLIP-equivalent).
"""

import os
import json
import boto3
from typing import Dict, Any, List

bedrock_runtime = boto3.client('bedrock-runtime')
s3vectors = boto3.client('s3vectors')

S3_VECTOR_BUCKET = os.environ.get('S3_VECTOR_BUCKET', 'mvip-image-vectors')
S3_VECTOR_INDEX = os.environ.get('S3_VECTOR_INDEX', 'image-embeddings')
BEDROCK_MULTIMODAL_MODEL = os.environ.get('BEDROCK_MULTIMODAL_MODEL', 'amazon.titan-embed-image-v1')


def get_text_embedding(text: str) -> list:
    """
    Generate multimodal embedding for text using Titan Multimodal Embeddings G1.
    
    This embedding is in the same semantic space as image embeddings,
    enabling text-to-image search (CLIP capability).
    """
    body = json.dumps({
        'inputText': text,
        'embeddingConfig': {
            'outputEmbeddingLength': 1024
        }
    })
    
    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MULTIMODAL_MODEL,
        accept='application/json',
        contentType='application/json',
        body=body
    )
    
    response_body = json.loads(response.get('body').read())
    return response_body.get('embedding')


def query_s3_vectors(query_embedding: list, top_k: int = 5) -> list:
    """
    Query the S3 Vector Store for similar image frames.
    """
    response = s3vectors.query_vectors(
        vectorBucketName=S3_VECTOR_BUCKET,
        indexName=S3_VECTOR_INDEX,
        queryVector={'float32': query_embedding},
        topK=top_k,
        returnMetadata=True,  # Required to get frame metadata
        returnDistance=True   # Get similarity distances
    )
    
    matches = []
    for result in response.get('vectors', []):
        metadata = result.get('metadata', {})
        
        matches.append({
            'key': result.get('key'),
            'distance': result.get('distance'),  # Lower is better
            'video_id': metadata.get('video_id'),
            'frame_number': metadata.get('frame_number'),
            'timestamp': metadata.get('timestamp'),  # Exact frame capture time
            'duration_seconds': metadata.get('duration_seconds'),  # Video duration
            's3_key': metadata.get('s3_key'),
            's3_uri': metadata.get('s3_uri'),
            'size_bytes': metadata.get('size_bytes')
        })
    
    return matches


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Search for video frames by visual similarity using text queries.
    
    Uses CLIP-equivalent multimodal embeddings to find frames that
    visually match the text description.
    
    Input Schema:
        {
            "query": "Python code on screen",  # Required
            "top_k": 5                         # Optional, default 5
        }
    
    Output Schema:
        {
            "query": "...",
            "results": [
                {
                    "video_id": "...",
                    "frame_number": 32,
                    "distance": 0.56,
                    "s3_key": "video_id/frames/frame_0032.jpg",
                    "s3_uri": "s3://..."
                }
            ],
            "count": 5
        }
    
    Returns:
        HTTP-style response for AgentCore Gateway
    """
    print(f"=== search_by_image tool invoked ===")
    print(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Validate required input
        query = event.get('query')
        if not query:
            raise ValueError("query is required")
        
        top_k = min(int(event.get('top_k', 5)), 20)  # Cap at 20
        video_id = event.get('video_id')  # Optional filter
        
        print(f"Searching images: query='{query}', top_k={top_k}")
        if video_id:
            print(f"Filtering by video_id: {video_id}")
        
        # Step 1: Generate text embedding
        print(f"Generating text embedding for query...")
        query_embedding = get_text_embedding(query)
        print(f"✓ Generated embedding: {len(query_embedding)} dimensions")
        
        # Step 2: Query S3 Vectors (get more results if filtering by video)
        query_limit = top_k * 10 if video_id else top_k
        print(f"Querying S3 Vectors for top {query_limit} matches...")
        matches = query_s3_vectors(query_embedding, query_limit)
        
        # Step 3: Filter by video_id if specified
        if video_id:
            matches = [m for m in matches if m.get('video_id') == video_id]
            print(f"✓ Filtered to {len(matches)} matches for video_id={video_id}")
        
        # Step 4: Take top_k after filtering
        matches = matches[:top_k]
        
        # Format results
        results = []
        for match in matches:
            # S3 Vectors metadata is already in correct types (numbers, not strings)
            result = {
                'video_id': match['video_id'],
                'frame_number': match.get('frame_number'),
                'timestamp': match.get('timestamp'),  # Exact capture time (float)
                'duration': match.get('duration_seconds'),  # Video duration (float)
                'distance': round(match['distance'], 4),
                's3_key': match['s3_key'],
                's3_uri': match['s3_uri']
            }
            results.append(result)
            
            print(f"  Match: frame={result['frame_number']}, timestamp={result.get('timestamp', 'N/A')}s, distance={result['distance']:.4f}")
        
        output = {
            'query': query,
            'results': results,
            'count': len(results)
        }
        
        print(f"✓ Found {len(results)} visually similar frames")
        
        return {
            'statusCode': 200,
            'body': json.dumps(output)
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
    # Test with a visual search query
    test_event = {
        'query': 'code editor',
        'top_k': 3
    }
    result = handler(test_event, None)
    print("\n=== Test Result ===")
    print(json.dumps(result, indent=2))

