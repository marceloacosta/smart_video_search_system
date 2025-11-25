# Architecture Overview

**System:** Multi-Modal Video Intelligence Platform (MVIP)  
**Purpose:** Semantic search across video content using speech, visual frames, and image similarity

## System Architecture

The Smart Video Search System is a serverless application built on AWS that enables natural language querying of video content through three modalities:
1. **Speech transcription** - Search what was said
2. **Frame captions** - Search what was shown  
3. **Image similarity** - Find visually similar moments

### High-Level Architecture

```
┌─────────────┐
│   User UI   │ (CloudFront + S3 static site)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ API Gateway │
└──────┬──────┘
       │
       ▼
┌──────────────────┐
│  agent_api.py    │ (Intelligent Router Lambda)
│  (Claude Agent)  │
└────────┬─────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌─────────┐  ┌──────────────────┐
│ Manual  │  │   Auto Mode      │
│ Mode    │  │   (AgentCore     │
│ Tools   │  │    Gateway)      │
└────┬────┘  └────────┬─────────┘
     │                │
     └────────────────┘
            │
     ┌──────┴───────┬──────────────┐
     ▼              ▼              ▼
┌──────────┐  ┌───────────┐  ┌──────────┐
│ Speech   │  │  Caption  │  │  Image   │
│ Search   │  │  Search   │  │  Search  │
│ (KB)     │  │  (KB)     │  │(S3 Vec)  │
└──────────┘  └───────────┘  └──────────┘
```

## AWS Services Used

### Core Services
- **S3**: Raw video storage, processed frames, transcripts
- **Lambda**: Serverless compute for all processing
- **DynamoDB**: Video metadata and processing status
- **API Gateway**: REST API for frontend

### AI/ML Services
- **Amazon Transcribe**: Speech-to-text with word-level timestamps
- **Amazon Bedrock**: 
  - Claude Vision (frame captioning)
  - Claude Sonnet (intelligent agent routing)
  - Titan Multimodal Embeddings (image vectors)
- **Bedrock Knowledge Bases**: RAG for speech and caption search
- **S3 Vectors**: Vector search for image similarity
- **AgentCore Gateway**: MCP (Model Context Protocol) server for tool invocation

### Supporting Services
- **CloudFront**: CDN for frontend delivery
- **IAM**: Permissions and access control
- **CloudWatch**: Logging and monitoring

## Data Flow

### 1. Video Ingestion Pipeline

```
Upload Video → S3 → process_video Lambda
                    ├─→ extract_frames → generate_captions → embed_captions
                    ├─→ AWS Transcribe → chunk_transcript
                    └─→ embed_images → S3 Vectors indexing
```

### 2. Search Flow

```
User Query → agent_api → Claude analyzes intent
                         ├─→ Auto Mode: AgentCore Gateway selects tool
                         └─→ Manual Mode: Direct tool invocation
                                         ↓
                         ┌───────────────┴──────────────┐
                         ▼                              ▼
               search_by_speech/caption        search_by_image
               (Bedrock Knowledge Base)        (S3 Vectors)
                         ▼                              ▼
                    Results with timestamps ← Combined → Video player
```

## Key Design Decisions

### 1. Serverless Architecture
- **Why**: Pay-per-use, automatic scaling, no server management
- **Benefit**: Cost-effective for variable workloads

### 2. Three-Index Approach
- **Speech Index**: Full transcripts in Bedrock KB for semantic search
- **Caption Index**: Frame descriptions in Bedrock KB for visual search
- **Image Index**: Frame embeddings in S3 Vectors for similarity search
- **Why**: Each modality requires different search characteristics

### 3. Claude Vision for Captions
- **Alternative Considered**: Amazon Rekognition (labels, faces, OCR)
- **Why Claude**: Semantic understanding > structured labels for natural language queries
- **See**: `rekognition-comparison-example.md` for detailed comparison

### 4. Intelligent Agent Routing
- **Why**: Users don't know which index to query
- **How**: Claude analyzes query intent and selects appropriate tool(s)
- **Example**: "Find the scene where they discuss AI" → speech_search
- **Example**: "Show me outdoor scenes" → caption_search

### 5. S3 Folder Structure
```
s3://processed-bucket/
├── {video_id}/
│   ├── frames/
│   │   ├── frame_0001.jpg
│   │   └── ...
│   └── transcriptions/
│       └── transcribe-output.json
├── speech_index/
│   └── {video_id}/
│       ├── full_transcript.txt
│       └── full_transcript.txt.metadata.json
└── caption_index/
    └── {video_id}/
        ├── frame_0001.txt
        ├── frame_0001.txt.metadata.json
        └── ...
```

**Why this structure**: Bedrock Knowledge Base doesn't support wildcards in S3 prefixes. Using `speech_index/` and `caption_index/` as top-level prefixes allows KB to scan all videos without knowing video IDs.

## Component Breakdown

### Frontend (Manual vs Auto Mode)
- **Manual Mode**: User selects search type (Speech/Caption/Image)
- **Auto Mode**: Agent determines best search method
- **Result Display**: Clickable timestamps, video navigation

### Agent API (agent_api.py)
- Receives user queries
- **Auto Mode**: Uses Claude to analyze intent and call tools
- **Manual Mode**: Direct tool invocation
- Formats results for UI
- Handles tool execution via SigV4-authenticated requests to AgentCore Gateway

### Processing Lambdas
- **process_video**: Orchestrator, triggers all downstream processes
- **extract_frames**: FFmpeg with evenly distributed frame extraction (default: 45-120 frames per video)
- **generate_captions**: Claude Vision batch processing (same frames as embeddings)
- **embed_captions**: Prepares KB documents with metadata
- **chunk_transcript**: Prepares full transcript for KB
- **embed_images**: Titan embeddings → S3 Vectors (same frames as captions)

### Search Tools (MCP)
- **search_by_speech**: Bedrock KB → match snippet → extract exact timestamp from Transcribe JSON
- **search_by_caption**: Bedrock KB with frame metadata
- **search_by_image**: S3 Vectors similarity search
- **list_videos**: Query DynamoDB
- **get_video_metadata**: DynamoDB lookup
- **get_full_transcript**: S3 retrieval

## Scalability Considerations

### Concurrent Processing
- Lambda concurrent executions: 1000 default limit
- Step Functions (future): For complex orchestration
- SQS (future): For batch processing queues

### Storage
- S3: Unlimited storage, lifecycle policies for old videos
- DynamoDB: On-demand billing, auto-scaling
- S3 Vectors: Serverless vector storage, pay-per-query

### Cost Optimization
- Bedrock KB: Per-query pricing
- Lambda: Pay per execution + duration
- S3 Intelligent-Tiering for frame storage
- S3 Vectors: Pay per storage and query

## Security

### IAM Roles
- Least privilege principle
- Separate roles for each Lambda function
- Service-to-service authentication

### Data Protection
- S3 encryption at rest (SSE-S3)
- DynamoDB encryption
- API Gateway with CORS configured
- CloudFront HTTPS only

### Network
- Lambda in VPC (future): For private resources
- VPC endpoints (future): For AWS service access

## Monitoring & Debugging

### CloudWatch Logs
- All Lambda functions log to CloudWatch
- Log groups: `/aws/lambda/{function-name}`
- Retention: 7 days default

### Metrics
- Lambda invocations, duration, errors
- API Gateway requests, latency
- DynamoDB read/write capacity

### Debugging
- CloudWatch Insights for log analysis
- X-Ray (future): Distributed tracing

## Performance

### Typical Latencies
- Video upload: ~1-5 seconds (presigned URL)
- Frame extraction: ~10-30 seconds (depends on video length)
- Caption generation: ~2-5 minutes (batch processing)
- Transcription: ~1/4 of video duration
- Search query: ~1-3 seconds

### Optimization Strategies
- Parallel processing where possible
- Batch API calls (Bedrock batch inference)
- Evenly distributed frame extraction (45-120 frames per video regardless of length)
- CloudFront caching for static assets

## Future Enhancements

1. **Multi-video search**: Query across entire library
2. **Temporal queries**: "Find 30 seconds after they mention X"
3. **Hybrid search**: Combine multiple modalities in one query
4. **Real-time processing**: Stream processing as video uploads
5. **Advanced analytics**: Sentiment, entity recognition, scene detection

## Related Documentation

- [02-video-ingestion.md](02-video-ingestion.md) - Upload and processing pipeline
- [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md) - RAG setup
- [08-intelligent-agent.md](08-intelligent-agent.md) - Agent routing logic
- [rekognition-comparison-example.md](rekognition-comparison-example.md) - Why Claude Vision

## Architecture Diagrams

See `docs/diagrams/` for visual representations:
- `01_overall_architecture.png` - System overview
- (Additional diagrams to be added)

