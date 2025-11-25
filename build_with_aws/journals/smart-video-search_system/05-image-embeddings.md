# Image Embeddings and Vector Search

**Component:** Visual similarity search using Titan Multimodal Embeddings  
**Purpose:** Find visually similar frames using vector search

## Overview

The image embeddings pipeline uses Amazon Titan Multimodal Embeddings to create vector representations of video frames, which are then indexed in OpenSearch Serverless for similarity search. This enables queries like "find similar scenes" or uploading an image to find matching frames.

## Architecture

```
Frames in S3
        ↓
embed_images Lambda (Titan)
        ↓
Vector Embeddings
        ↓
embed_and_index_images Lambda
        ↓
OpenSearch Serverless Collection
        ↓
search_by_image Tool (Vector Similarity)
```

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
        timestamp_sec = frame_num / 6  # 6 fps
        
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

## OpenSearch Serverless Integration

### Collection Setup

**Collection Name:** `video-frame-vectors`  
**Type:** Vector search  
**Dimensions:** 1024  
**Engine:** OpenSearch Serverless (AOSS)

### Index Mapping

```json
{
  "settings": {
    "index": {
      "knn": true,
      "knn.algo_param.ef_search": 512
    }
  },
  "mappings": {
    "properties": {
      "video_id": {"type": "keyword"},
      "frame_number": {"type": "integer"},
      "timestamp_sec": {"type": "float"},
      "frame_key": {"type": "keyword"},
      "embedding": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "nmslib",
          "parameters": {
            "ef_construction": 512,
            "m": 16
          }
        }
      }
    }
  }
}
```

**HNSW Parameters:**
- **ef_construction**: 512 (index quality)
- **m**: 16 (connections per node)
- **space_type**: cosinesimil (cosine similarity)

## embed_and_index_images Lambda

**File:** `src/lambdas/embed_and_index_images.py`

### Purpose
Index embeddings into OpenSearch Serverless

### Implementation

```python
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

def lambda_handler(event, context):
    video_id = event['video_id']
    embeddings_key = f"{video_id}/embeddings/image_embeddings.json"
    
    s3 = boto3.client('s3')
    
    # Download embeddings
    response = s3.get_object(Bucket=processed_bucket, Key=embeddings_key)
    embeddings = json.loads(response['Body'].read())
    
    # Connect to OpenSearch
    service = 'aoss'
    credentials = boto3.Session().get_credentials()
    awsauth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        os.environ['AWS_REGION'],
        service,
        session_token=credentials.token
    )
    
    opensearch = OpenSearch(
        hosts=[{'host': os.environ['OPENSEARCH_ENDPOINT'], 'port': 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection
    )
    
    # Index embeddings
    index_name = 'video-frames'
    
    for emb in embeddings:
        doc = {
            'video_id': emb['video_id'],
            'frame_number': emb['frame_number'],
            'timestamp_sec': emb['timestamp_sec'],
            'frame_key': emb['frame_key'],
            'embedding': emb['embedding']
        }
        
        doc_id = f"{video_id}_frame_{emb['frame_number']:04d}"
        
        opensearch.index(
            index=index_name,
            id=doc_id,
            body=doc
        )
    
    print(f"Indexed {len(embeddings)} embeddings for {video_id}")
    
    return {'indexed_count': len(embeddings)}
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
    
    # Build OpenSearch query
    query_body = {
        "size": top_k,
        "query": {
            "knn": {
                "embedding": {
                    "vector": query_embedding,
                    "k": top_k
                }
            }
        }
    }
    
    # Filter by video_id if specified
    if video_id:
        query_body["query"] = {
            "bool": {
                "must": [
                    {"knn": {"embedding": {"vector": query_embedding, "k": top_k}}},
                    {"term": {"video_id": video_id}}
                ]
            }
        }
    
    # Execute search
    opensearch = get_opensearch_client()
    response = opensearch.search(
        index='video-frames',
        body=query_body
    )
    
    # Format results
    results = []
    for hit in response['hits']['hits']:
        results.append({
            'video_id': hit['_source']['video_id'],
            'timestamp': hit['_source']['timestamp_sec'],
            'frame_key': hit['_source']['frame_key'],
            'similarity_score': hit['_score']
        })
    
    return results
```

### Text-to-Image Search

```python
def search_by_text_description(text_query, video_id=None, top_k=5):
    # Generate embedding from text
    bedrock = boto3.client('bedrock-runtime')
    response = bedrock.invoke_model(
        modelId='amazon.titan-embed-image-v1',
        body=json.dumps({
            "inputText": text_query
        })
    )
    query_embedding = json.loads(response['body'].read())['embedding']
    
    # Same vector search as image-to-image
    return search_with_embedding(query_embedding, video_id, top_k)
```

## Performance & Cost

### Embedding Generation

- **Time**: ~100-200ms per frame
- **100 frames**: ~10-20 seconds
- **Cost**: $0.00006 per image = $0.006 for 100 frames

### Vector Search

- **Query Latency**: ~50-200ms
- **Cost**: OpenSearch Serverless OCU (compute units)
  - Indexing: ~$0.24/OCU-hour
  - Search: ~$0.24/OCU-hour

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
- Use batch indexing
- Check OpenSearch cluster size

### Issue: Search Returns Irrelevant Results
- Adjust k-NN parameters (ef_search)
- Fine-tune similarity threshold
- Review embedding quality

## Related Documentation

- [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md) - Text-based search
- [08-intelligent-agent.md](08-intelligent-agent.md) - Tool routing
- [02-video-ingestion.md](02-video-ingestion.md) - Frame extraction

## Next Steps

After indexing:
1. Frames searchable via `search_by_image` tool
2. Frontend supports image upload for similarity search
3. Agent can route visual similarity queries

