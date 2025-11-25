# Image Embeddings and Vector Search

**Component:** Visual similarity search using Titan Multimodal Embeddings  
**Purpose:** Find visually similar frames using vector search

## Overview

The image embeddings pipeline uses Amazon Titan Multimodal Embeddings to create vector representations of video frames, which are then indexed in S3 Vectors for similarity search. This enables queries like "find similar scenes" or uploading an image to find matching frames.

**Important:** The same extracted frames are used for both caption generation (Claude Vision) and image embeddings (Titan Multimodal). This ensures consistency between text-based search (captions) and visual similarity search (embeddings).

## Architecture

```
Frames in S3 (from extract_frames Lambda)
        ↓
        ├─→ generate_captions (Claude Vision)
        └─→ embed_and_index_images (Titan Multimodal)
                ↓
        Vector Embeddings (1024-dim)
                ↓
        S3 Vectors Index
                ↓
        search_by_image Tool (Vector Similarity)
```

**Note:** Both caption generation and image embedding pipelines consume the same frames from `{video_id}/frames/` in S3. Frames are extracted once using an evenly distributed approach (45-120 frames per video).

## Titan Multimodal Embeddings

**Model:** `amazon.titan-embed-image-v1`  
**Output:** 1,024-dimensional vector per image  
**Modality:** Images and text (multimodal)

### Why Titan Multimodal?

1. **Native AWS Integration**: Bedrock service, no external APIs
2. **Multimodal**: Supports text-to-image and image-to-image search
3. **Performance**: Fast inference, batch processing
4. **Cost**: $0.00006 per image (very affordable)
5. **Quality**: Good for general visual similarity

### API Call

```python
import boto3
import json
import base64

bedrock = boto3.client('bedrock-runtime')

# Read image
with open('frame.jpg', 'rb') as f:
    image_bytes = f.read()

# Generate embedding
response = bedrock.invoke_model(
    modelId='amazon.titan-embed-image-v1',
    body=json.dumps({
        "inputImage": base64.b64encode(image_bytes).decode('utf-8')
    })
)

result = json.loads(response['body'].read())
embedding = result['embedding']  # 1024-dimensional vector

print(f"Embedding dimensions: {len(embedding)}")
# Output: Embedding dimensions: 1024
```

## embed_images Lambda

**File:** `src/lambdas/embed_images.py`

### Purpose
Generate embeddings for all video frames

### Implementation

```python
def lambda_handler(event, context):
    video_id = event['video_id']
    frames_prefix = f"{video_id}/frames/"
    
    s3 = boto3.client('s3')
    bedrock = boto3.client('bedrock-runtime')
    
    # List all frames
    frames = list_s3_objects(s3, processed_bucket, frames_prefix)
    
    embeddings = []
    for frame_key in frames:
        # Download frame
        response = s3.get_object(Bucket=processed_bucket, Key=frame_key)
        image_bytes = response['Body'].read()
        
        # Generate embedding
        embedding = generate_embedding(bedrock, image_bytes)
        
        # Extract frame metadata
        frame_num = extract_frame_number(frame_key)
        # Calculate timestamp from evenly distributed frames
        timestamp_sec = (frame_num - 1) * duration_seconds / (frame_count - 1)
        
        embeddings.append({
            'frame_key': frame_key,
            'frame_number': frame_num,
            'timestamp_sec': timestamp_sec,
            'embedding': embedding,
            'video_id': video_id
        })
    
    # Save embeddings
    embeddings_key = f"{video_id}/embeddings/image_embeddings.json"
    s3.put_object(
        Bucket=processed_bucket,
        Key=embeddings_key,
        Body=json.dumps(embeddings).encode('utf-8')
    )
    
    return {
        'embeddings_generated': len(embeddings),
        'embeddings_key': embeddings_key
    }

def generate_embedding(bedrock_client, image_bytes):
    response = bedrock_client.invoke_model(
        modelId='amazon.titan-embed-image-v1',
        body=json.dumps({
            "inputImage": base64.b64encode(image_bytes).decode('utf-8')
        })
    )
    result = json.loads(response['body'].read())
    return result['embedding']
```

### Output Format

**image_embeddings.json:**
```json
[
  {
    "frame_key": "video-123/frames/frame_0001.jpg",
    "frame_number": 1,
    "timestamp_sec": 0.167,
    "embedding": [0.123, -0.456, 0.789, ...],  # 1024 values
    "video_id": "video-123"
  },
  {
    "frame_key": "video-123/frames/frame_0002.jpg",
    "frame_number": 2,
    "timestamp_sec": 0.333,
    "embedding": [-0.234, 0.567, -0.890, ...],
    "video_id": "video-123"
  }
]
```

## S3 Vectors Integration

### Vector Store Setup

**Bucket Name:** `mvip-image-vectors`  
**Index Name:** `image-embeddings`  
**Type:** Vector search  
**Dimensions:** 1024  
**Engine:** S3 Vectors (native AWS vector storage)

### Vector Record Format

Each vector record contains:
- **key**: Unique identifier (`video_id_frame_NNNN`)
- **data**: Embedding vector wrapped in `float32` format
- **metadata**: Frame information (video_id, frame_number, timestamp, etc.)

```json
{
  "key": "video-123_frame_0042",
  "data": {
    "float32": [0.123, -0.456, 0.789, ...]
  },
  "metadata": {
    "video_id": "video-123",
    "frame_number": 42,
    "timestamp": 7.0,
    "duration_seconds": 60.0,
    "s3_key": "video-123/frames/frame_0042.jpg",
    "s3_uri": "s3://processed-bucket/video-123/frames/frame_0042.jpg",
    "size_bytes": 45678
  }
}
```

**Benefits of S3 Vectors:**
- **Serverless**: No infrastructure management
- **Cost-effective**: Pay only for storage and queries
- **Native AWS**: Integrated with IAM, CloudWatch
- **Simple API**: put_vectors() and query_vectors()

## embed_and_index_images Lambda

**File:** `src/lambdas/embed_and_index_images.py`

### Purpose
Index embeddings into S3 Vectors

### Implementation

```python
def store_vectors_in_s3(vectors: List[Dict[str, Any]]) -> None:
    """
    Store embedding vectors in S3 Vectors.
    
    Args:
        vectors: List of vector records with id, embedding, and metadata
    """
    print(f"Storing {len(vectors)} vectors in S3 Vectors...")
    
    # Prepare records for put-vectors API
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

def lambda_handler(event, context):
    video_id = event['video_id']
    frames_prefix = event['frames_prefix']
    
    # Process frames and generate embeddings
    vectors = []
    for frame in frame_objects:
        embedding = generate_image_embedding(image_bytes)
        
        vector_id = f"{video_id}_frame_{frame_number:04d}"
        vectors.append({
            'id': vector_id,
            'embedding': embedding,
            'metadata': {
                'video_id': video_id,
                'frame_number': frame_number,
                'timestamp': round(timestamp, 2),
                'duration_seconds': round(duration_seconds, 2),
                's3_key': frame_key,
                's3_uri': f"s3://{PROCESSED_BUCKET}/{frame_key}",
                'size_bytes': image_size
            }
        })
    
    # Store all vectors in S3 Vectors
    store_vectors_in_s3(vectors)
    
    return {'indexed_count': len(vectors)}
```

## Vector Search

### search_by_image Tool

**File:** `src/lambdas/tools/search_by_image.py`

### Query Types

**1. Image-to-Image Search**
User uploads image → find similar frames

**2. Text-to-Image Search**
User provides text description → find matching frames

### Image-to-Image Implementation

```python
def search_by_image(query_image_bytes, video_id=None, top_k=5):
    # Generate embedding for query image
    bedrock = boto3.client('bedrock-runtime')
    response = bedrock.invoke_model(
        modelId='amazon.titan-embed-image-v1',
        body=json.dumps({
            "inputImage": base64.b64encode(query_image_bytes).decode('utf-8')
        })
    )
    query_embedding = json.loads(response['body'].read())['embedding']
    
    # Query S3 Vectors
    s3vectors = boto3.client('s3vectors')
    response = s3vectors.query_vectors(
        vectorBucketName=S3_VECTOR_BUCKET,
        indexName=S3_VECTOR_INDEX,
        queryVector={'float32': query_embedding},
        topK=top_k * 10 if video_id else top_k,  # Get more if filtering
        returnMetadata=True,
        returnDistance=True
    )
    
    # Filter by video_id if specified
    matches = []
    for result in response.get('vectors', []):
        metadata = result.get('metadata', {})
        
        if video_id and metadata.get('video_id') != video_id:
            continue
        
        matches.append({
            'video_id': metadata.get('video_id'),
            'timestamp': metadata.get('timestamp'),
            'frame_key': metadata.get('s3_key'),
            'distance': result.get('distance'),  # Lower is better
            'frame_number': metadata.get('frame_number')
        })
    
    # Return top_k after filtering
    return matches[:top_k]
```

### Text-to-Image Search

```python
def search_by_text_description(text_query, video_id=None, top_k=5):
    # Generate embedding from text using Titan Multimodal
    bedrock = boto3.client('bedrock-runtime')
    response = bedrock.invoke_model(
        modelId='amazon.titan-embed-image-v1',
        body=json.dumps({
            "inputText": text_query,
            "embeddingConfig": {
                "outputEmbeddingLength": 1024
            }
        })
    )
    query_embedding = json.loads(response['body'].read())['embedding']
    
    # Query S3 Vectors (same as image-to-image)
    s3vectors = boto3.client('s3vectors')
    response = s3vectors.query_vectors(
        vectorBucketName=S3_VECTOR_BUCKET,
        indexName=S3_VECTOR_INDEX,
        queryVector={'float32': query_embedding},
        topK=top_k,
        returnMetadata=True,
        returnDistance=True
    )
    
    return format_results(response)
```

## Performance & Cost

### Embedding Generation

- **Time**: ~100-200ms per frame
- **100 frames**: ~10-20 seconds
- **Cost**: $0.00006 per image = $0.006 for 100 frames

### Vector Search

- **Query Latency**: ~50-200ms
- **Cost**: S3 Vectors pricing
  - Storage: ~$0.025 per GB-month
  - Queries: ~$0.0001 per query
  - Pay-per-use with no minimum costs (serverless)

### Optimization

**1. Batch Processing**
```python
def batch_generate_embeddings(image_list, batch_size=10):
    embeddings = []
    for i in range(0, len(image_list), batch_size):
        batch = image_list[i:i+batch_size]
        batch_embeddings = [generate_embedding(img) for img in batch]
        embeddings.extend(batch_embeddings)
    return embeddings
```

**2. Parallel Indexing**
```python
from concurrent.futures import ThreadPoolExecutor

def parallel_index(embeddings, max_workers=5):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(index_embedding, emb) for emb in embeddings]
        results = [f.result() for f in futures]
    return results
```

## Troubleshooting

### Issue: Low Similarity Scores
- Embeddings may not capture desired features
- Try different embedding models
- Check image quality and preprocessing

### Issue: Slow Indexing
- Increase Lambda memory
- Use batch indexing (100 vectors per batch)
- Parallelize embedding generation

### Issue: Search Returns Irrelevant Results
- Review the distance threshold (lower is better)
- Increase top_k to get more candidates
- Review embedding quality
- Consider filtering by video_id first

## Related Documentation

- [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md) - Text-based search
- [08-intelligent-agent.md](08-intelligent-agent.md) - Tool routing
- [02-video-ingestion.md](02-video-ingestion.md) - Frame extraction

## Next Steps

After indexing:
1. Frames searchable via `search_by_image` tool
2. Frontend supports image upload for similarity search
3. Agent can route visual similarity queries

