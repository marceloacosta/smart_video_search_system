# Architecture Audit - Actual Implementation

## Actual Lambda Functions

### Core Processing Pipeline
1. **process_video.py** - S3 trigger, starts AWS Transcribe
2. **check_transcription.py** - Poll transcription status
3. **extract_frames.py** - Extract frames (evenly distributed, 45-120 per video)
4. **generate_captions.py** - Claude Vision captions
5. **chunk_transcript.py** - Prepare transcripts for Bedrock KB
6. **embed_captions.py** - Prepare captions for Bedrock KB, triggers image indexing
7. **embed_and_index_images.py** - Index images to S3 Vectors

### Upload & Management
8. **get_upload_url.py** - Generate presigned URL
9. **upload_video.py** - Handle upload
10. **delete_video.py** - Delete video

### Search & API
11. **agent_api.py** - Agent API gateway
12. **search_images.py** - Standalone image search API

### Unused/Deprecated?
13. **embed_images.py** - OLD? Stores embeddings for "Bedrock KB #3" (not used?)

## MCP Tools (Model Context Protocol)
Located in `src/lambdas/tools/`:
1. **search_by_speech.py** - Search transcripts via Bedrock KB
2. **search_by_caption.py** - Search captions via Bedrock KB
3. **search_by_image.py** - Search via S3 Vectors
4. **list_videos.py** - List all videos
5. **get_video_metadata.py** - Get video metadata
6. **get_full_transcript.py** - Get full transcript

## Actual Architecture Flow

### Upload Flow
```
User → get_upload_url → S3 presigned URL → upload_video → process_video
```

### Processing Flow
```
process_video (S3 trigger)
    ├─→ Start AWS Transcribe
    └─→ Trigger extract_frames
            ├─→ Trigger generate_captions
            │       └─→ Trigger embed_captions
            │               └─→ Trigger embed_and_index_images
            └─→ Check transcription (poll)
                    └─→ Trigger chunk_transcript
```

### Search Flow
```
Frontend → agent_api
    ├─→ Manual Mode: Direct MCP tool
    └─→ Auto Mode: Claude → MCP tool
            ├─→ search_by_speech (Bedrock KB - speech)
            ├─→ search_by_caption (Bedrock KB - captions)
            └─→ search_by_image (S3 Vectors)
```

## Storage Architecture

### S3 Buckets
1. **Raw Videos Bucket** - Original uploads
2. **Processed Bucket** - All processed data:
   - `{video_id}/frames/` - Extracted frames (shared by captions & embeddings)
   - `{video_id}/transcript.json` - AWS Transcribe output
   - `speech_index/{video_id}/` - Transcript docs for Bedrock KB
   - `caption_index/{video_id}/` - Caption docs for Bedrock KB
3. **S3 Vectors Bucket** - Image embeddings index
4. **Website Bucket** - Frontend static files

### DynamoDB
- **Metadata Table** - Video metadata and processing status

### Bedrock Knowledge Bases
1. **Speech KB** - Searches `speech_index/` prefix
2. **Caption KB** - Searches `caption_index/` prefix
3. **No Image KB** - Images use S3 Vectors directly (not Bedrock KB)

## Key Findings

### ✅ Correct Architecture
- Frame extraction: Evenly distributed (not fixed FPS)
- Image embeddings: S3 Vectors (not OpenSearch)
- Same frames for captions and embeddings
- Two separate Bedrock KBs (speech and caption)

### ⚠️ Potential Issues to Verify
1. **embed_images.py** - Purpose unclear, might be unused
2. **search_images.py** vs **tools/search_by_image.py** - Duplication?
3. **Bedrock KB for images** - embed_images.py mentions "Bedrock KB #3" but actual implementation uses S3 Vectors

### 🔍 Need to Audit in Journals
- Whether journals mention 3 Bedrock KBs or 2 KBs + S3 Vectors
- Whether AgentCore Gateway is properly documented
- Whether all Lambda functions are documented
- Whether processing flow is accurate

