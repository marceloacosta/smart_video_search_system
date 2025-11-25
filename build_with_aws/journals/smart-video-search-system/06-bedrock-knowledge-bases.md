# Bedrock Knowledge Bases Setup

**Component:** RAG-powered semantic search  
**Purpose:** Enable natural language queries over speech transcripts and frame captions

## Overview

Bedrock Knowledge Bases provide retrieval-augmented generation (RAG) for semantic search across video transcripts and captions. The system uses two separate Knowledge Bases:
1. **Speech KB**: Search what was said
2. **Caption KB**: Search what was shown

## Architecture

```
S3 Documents (txt + metadata.json)
        ↓
Bedrock Knowledge Base
        ├─→ Embedding (Titan Text)
        ├─→ Vector Storage (S3 Vectors)
        └─→ Retrieval API
                ↓
        search_by_speech/caption tools
```

## Why S3 Folder Structure Matters

### The Problem

Bedrock KB S3 data sources **do not support wildcards** in `inclusionPrefixes`.

**This FAILS:**
```
inclusionPrefix: "*/speech_index/"  # ❌ Wildcard not supported
```

### The Solution

Structure S3 paths with index type as TOP-LEVEL prefix:

```
s3://processed-bucket/
├── speech_index/          ← KB scans this prefix
│   ├── video-1/
│   ├── video-2/
│   └── video-3/
└── caption_index/         ← KB scans this prefix
    ├── video-1/
    ├── video-2/
    └── video-3/
```

**This WORKS:**
```
inclusionPrefix: "speech_index/"   # ✅ Fixed prefix, all videos included
inclusionPrefix: "caption_index/"  # ✅ Fixed prefix, all videos included
```

## File Format Requirements

Bedrock KB requires specific file formats:

### Text Files (.txt)
- Plain text content
- UTF-8 encoding
- One document per file

### Metadata Sidecar (.txt.metadata.json)
- Same filename + `.metadata.json`
- JSON format
- Contains filterable metadata

**Example:**
```
speech_index/video-123/full_transcript.txt
speech_index/video-123/full_transcript.txt.metadata.json
```

## Speech Knowledge Base

### Configuration

**KB Name:** `mvip-speech-index`  
**Data Source:** S3  
**Prefix:** `speech_index/`  
**Embedding Model:** Titan Text Embeddings v2  
**Vector Store:** S3 (managed by Bedrock)  
**Chunking:** Default (300 tokens, 20% overlap)

### CDK Setup

```python
from aws_cdk import aws_bedrock as bedrock

speech_kb = bedrock.CfnKnowledgeBase(
    self, "SpeechKB",
    name="mvip-speech-index",
    role_arn=kb_role.role_arn,
    knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
        type="VECTOR",
        vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
            embedding_model_arn=f"arn:aws:bedrock:{region}::foundation-model/amazon.titan-embed-text-v2:0"
        )
    ),
    storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
        type="OPENSEARCH_SERVERLESS",  # or use S3 for simpler setup
        opensearch_serverless_configuration=...
    )
)

speech_ds = bedrock.CfnDataSource(
    self, "SpeechDataSource",
    knowledge_base_id=speech_kb.attr_knowledge_base_id,
    name="speech-transcripts",
    data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
        type="S3",
        s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
            bucket_arn=processed_bucket.bucket_arn,
            inclusion_prefixes=["speech_index/"]
        )
    )
)
```

### Document Format

**full_transcript.txt:**
```
Welcome to our video on artificial intelligence and machine learning. 
Today we'll explore how these technologies are transforming industries...
```

**full_transcript.txt.metadata.json:**
```json
{
  "metadataAttributes": {
    "video_id": "video-123",
    "content_type": "transcript",
    "source": "aws_transcribe"
  }
}
```

## Caption Knowledge Base

### Configuration

**KB Name:** `mvip-caption-index`  
**Data Source:** S3  
**Prefix:** `caption_index/`  
**Embedding Model:** Titan Text Embeddings v2  
**Vector Store:** S3  
**Chunking:** Default

### Document Format

**frame_0042.txt:**
```
A man in a blue suit stands at a podium addressing an audience. 
Behind him is a large screen displaying charts and graphs.
```

**frame_0042.txt.metadata.json:**
```json
{
  "metadataAttributes": {
    "video_id": "video-123",
    "frame_number": 42,
    "frame_timestamp_sec": 7.0,
    "content_type": "caption",
    "source": "claude_vision"
  }
}
```

## Ingestion and Syncing

### Automatic Sync

Bedrock KB automatically syncs S3 data sources:
- **Frequency:** Every 24 hours by default
- **Trigger:** API call to `StartIngestionJob`

### Manual Sync

```python
bedrock_agent = boto3.client('bedrock-agent')

response = bedrock_agent.start_ingestion_job(
    knowledgeBaseId='KB_ID',
    dataSourceId='DS_ID'
)

job_id = response['ingestionJob']['ingestionJobId']
```

### Checking Sync Status

```python
response = bedrock_agent.get_ingestion_job(
    knowledgeBaseId='KB_ID',
    dataSourceId='DS_ID',
    ingestionJobId=job_id
)

status = response['ingestionJob']['status']
# Status: STARTING, IN_PROGRESS, COMPLETE, FAILED
```

## Retrieval API

### Basic Query

```python
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime')

response = bedrock_agent_runtime.retrieve(
    knowledgeBaseId='KB_ID',
    retrievalQuery={
        'text': 'What did they say about artificial intelligence?'
    },
    retrievalConfiguration={
        'vectorSearchConfiguration': {
            'numberOfResults': 5
        }
    }
)

for result in response['retrievalResults']:
    print(result['content']['text'])
    print(result['location']['s3Location']['uri'])
    print(result['score'])
```

### Filtering by Metadata

```python
response = bedrock_agent_runtime.retrieve(
    knowledgeBaseId='KB_ID',
    retrievalQuery={
        'text': 'outdoor scenes'
    },
    retrievalConfiguration={
        'vectorSearchConfiguration': {
            'numberOfResults': 10,
            'filter': {
                'equals': {
                    'key': 'video_id',
                    'value': 'specific-video-123'
                }
            }
        }
    }
)
```

### Response Format

```json
{
  "retrievalResults": [
    {
      "content": {
        "text": "Today we'll explore how AI is transforming industries..."
      },
      "location": {
        "s3Location": {
          "uri": "s3://bucket/speech_index/video-123/full_transcript.txt"
        },
        "type": "S3"
      },
      "score": 0.87,
      "metadata": {
        "video_id": "video-123",
        "content_type": "transcript"
      }
    }
  ]
}
```

## Cost Analysis

### Components

1. **Embedding Generation**: Titan Text v2
   - $0.0001 per 1K tokens
   - Typical transcript: 1000 words = ~1300 tokens = $0.00013

2. **Vector Storage**: S3
   - ~100KB per embedded document
   - $0.023 per GB-month

3. **Retrieval Queries**:
   - $0.0001 per query (approximate)

### Example Cost (100 videos)

- Embedding: 100 videos × $0.00013 = $0.013
- Storage: 100 videos × 100KB = 10MB = $0.0002/month
- Queries: 1000 queries/month = $0.10
- **Total**: ~$0.11/month

## Troubleshooting

### Issue: KB Not Finding Documents

**Check:**
1. File format (.txt + .txt.metadata.json)
2. S3 prefix matches KB configuration
3. Ingestion job completed successfully
4. IAM permissions for KB to read S3

**Debug:**
```bash
# Check what KB sees
aws bedrock-agent list-data-sources --knowledge-base-id KB_ID

# Check ingestion history
aws bedrock-agent list-ingestion-jobs \
  --knowledge-base-id KB_ID \
  --data-source-id DS_ID
```

### Issue: Low Retrieval Quality

**Solutions:**
1. Improve document quality (chunk_transcript, captions)
2. Adjust chunking strategy
3. Add more context in documents
4. Use metadata filters

### Issue: Slow Queries

**Solutions:**
1. Reduce `numberOfResults`
2. Add metadata filters
3. Check vector index size

## Related Documentation

- [03-speech-transcription.md](03-speech-transcription.md) - Transcript preparation
- [04-frame-captioning.md](04-frame-captioning.md) - Caption preparation
- [08-intelligent-agent.md](08-intelligent-agent.md) - Using KB in agent

## Next Steps

After KB setup:
1. Documents automatically indexed
2. Ready for semantic search
3. Tools can query via Bedrock Agent Runtime API
