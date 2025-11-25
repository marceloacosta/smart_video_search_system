# Video Ingestion Pipeline

**Component:** Video Upload and Processing  
**Purpose:** Accept user videos, extract frames, and trigger downstream processing

## Overview

The video ingestion pipeline accepts video uploads via presigned S3 URLs, stores them in S3, and orchestrates the complete processing workflow including frame extraction, transcription, and indexing.

## Architecture

```
User Upload → API Gateway → get_upload_url Lambda
                               ↓
                         Presigned S3 URL
                               ↓
                    S3 Raw Videos Bucket
                               ↓
                         S3 Event Trigger
                               ↓
                       upload_video Lambda
                               ↓
                        DynamoDB (metadata)
                               ↓
                       process_video Lambda
                    ┌─────────┴──────────┐
                    ▼                    ▼
            extract_frames       AWS Transcribe
                    ↓                    ↓
            generate_captions    check_transcription
                    ↓                    ↓
            embed_captions       chunk_transcript
                    ↓                    ↓
        embed_and_index_images   Bedrock KB (speech)
                    ↓
            Bedrock KB (captions)
```

## Components

### 1. get_upload_url Lambda

**File:** `src/lambdas/get_upload_url.py`

**Purpose:** Generate presigned S3 URL for secure video uploads

**Flow:**
1. User requests upload URL via API Gateway
2. Lambda generates unique video ID
3. Creates presigned S3 PUT URL (expires in 1 hour)
4. Returns URL and video ID to user

**Request:**
```json
POST /upload
{
  "filename": "my-video.mp4",
  "contentType": "video/mp4"
}
```

**Response:**
```json
{
  "uploadUrl": "https://s3.amazonaws.com/bucket/video-id/...?X-Amz-Signature=...",
  "videoId": "unique-video-id-123",
  "expiresIn": 3600
}
```

**Implementation Details:**
- Uses `boto3.client('s3').generate_presigned_url()`
- Video ID format: `{timestamp}-{random-uuid}`
- S3 key: `{video_id}/raw/{filename}`
- Content-Type validation (video/*)

**Security:**
- Presigned URL expires after 1 hour
- Only allows PUT operation
- Validates file extension

### 2. upload_video Lambda

**File:** `src/lambdas/upload_video.py`

**Trigger:** S3 Object Created event on raw videos bucket

**Purpose:** Register video metadata and trigger processing

**Flow:**
1. S3 event triggered when video uploaded
2. Extract video metadata (size, timestamp, filename)
3. Create DynamoDB record with status="uploaded"
4. Invoke process_video Lambda asynchronously

**DynamoDB Schema:**
```python
{
  "video_id": "unique-video-id-123",  # Partition key
  "filename": "my-video.mp4",
  "upload_timestamp": "2025-11-25T10:30:00Z",
  "file_size": 52428800,  # bytes
  "status": "uploaded",
  "s3_bucket": "raw-videos-bucket",
  "s3_key": "video-id/raw/my-video.mp4",
  "processing_started": None,
  "processing_completed": None
}
```

**Status Values:**
- `uploaded` - Video in S3, pending processing
- `processing` - Currently being processed
- `completed` - All indexes created
- `failed` - Processing error (with error details)

### 3. process_video Lambda

**File:** `src/lambdas/process_video.py`

**Purpose:** Orchestrate all video processing tasks

**Flow:**
1. Update DynamoDB status to "processing"
2. Invoke extract_frames Lambda
3. Start AWS Transcribe job
4. Invoke check_transcription Lambda (polls until complete)
5. Update status to "completed" when all done

**Async Invocations:**
```python
# Extract frames
lambda_client.invoke(
    FunctionName='extract-frames',
    InvocationType='Event',  # Async
    Payload=json.dumps({
        'video_id': video_id,
        's3_bucket': bucket,
        's3_key': key
    })
)

# Start transcription
transcribe_client.start_transcription_job(
    TranscriptionJobName=f"{video_id}-transcription",
    Media={'MediaFileUri': f's3://{bucket}/{key}'},
    MediaFormat='mp4',
    LanguageCode='en-US',
    Settings={
        'ShowSpeakerLabels': False,
        'MaxSpeakerLabels': 2
    },
    OutputBucketName=processed_bucket,
    OutputKey=f'{video_id}/transcriptions/'
)
```

**Error Handling:**
- Try/catch for each operation
- Update DynamoDB with error status
- CloudWatch logs for debugging

### 4. extract_frames Lambda

**File:** `src/lambdas/extract_frames.py`

**Purpose:** Extract frames from video using FFmpeg

**Configuration:**
- Frame extraction: Evenly distributed across video duration (Kubrick approach)
- Default frames: 45-120 per video (regardless of video length)
- Format: JPEG
- Quality: 85%
- Resolution: Original (no resize)

**FFmpeg Command (dynamic FPS):**
```bash
# Example: 60-second video with 45 frames
# FPS = 45 / 60 = 0.75 fps (1 frame every ~1.33 seconds)
ffmpeg -i input.mp4 -vf fps=0.75 -frames:v 45 -q:v 2 frame_%04d.jpg
```

**Key Difference from Fixed FPS:**
- Fixed 6fps: 10-minute video = 3,600 frames (expensive!)
- Evenly distributed: 10-minute video = 45 frames (cost-effective!)

**Flow:**
1. Download video from S3 to /tmp/
2. Run FFmpeg to extract frames
3. Upload frames to S3: `{video_id}/frames/frame_XXXX.jpg`
4. Invoke generate_captions Lambda
5. Clean up /tmp/ directory

**S3 Structure:**
```
s3://processed-bucket/
└── {video_id}/
    └── frames/
        ├── frame_0001.jpg
        ├── frame_0002.jpg
        ├── frame_0003.jpg
        └── ...
```

**Performance:**
- Lambda timeout: 15 minutes
- Memory: 3008 MB (for FFmpeg)
- Ephemeral storage: 2048 MB
- Average time: 10-30 seconds for 1-minute video

**FFmpeg Layer:**
- Custom Lambda layer with FFmpeg binary
- Compiled for Amazon Linux 2
- Size: ~50 MB

### 5. S3 Bucket Structure

**Raw Videos Bucket:**
```
s3://mvip-raw-videos-{account-id}-{region}/
└── {video_id}/
    └── raw/
        └── {original-filename}
```

**Processed Bucket:**
```
s3://mvip-processed-{account-id}-{region}/
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

## DynamoDB Video Metadata Table

**Table Name:** `VideoMetadata`

**Schema:**
```
Partition Key: video_id (String)
Sort Key: None

Attributes:
- video_id: String
- filename: String
- upload_timestamp: String (ISO 8601)
- file_size: Number
- status: String
- s3_bucket: String
- s3_key: String
- processing_started: String
- processing_completed: String
- duration_seconds: Number (added after transcription)
- frame_count: Number (added after extraction)
- error_message: String (if status='failed')
```

**Indexes:**
- GSI: status-index (query videos by status)
- GSI: upload_timestamp-index (query by upload time)

**Access Patterns:**
1. Get video by ID: `GetItem(video_id)`
2. List all videos: `Scan()` (paginated)
3. List by status: `Query(status-index)`
4. Recent uploads: `Query(upload_timestamp-index, ScanIndexForward=False)`

## Error Handling

### Common Errors

**1. Video Format Not Supported**
- **Cause:** FFmpeg can't decode video
- **Solution:** Validate format on upload, support common formats (mp4, mov, avi)
- **Status:** Set to 'failed' with error message

**2. Video Too Large**
- **Cause:** Exceeds S3 object size limit or Lambda /tmp/ storage
- **Solution:** Implement chunked upload, use EFS for large files
- **Status:** Reject at upload URL generation

**3. Transcription Failed**
- **Cause:** No audio track, unsupported language
- **Solution:** Check transcription job status, log error
- **Status:** Continue with frame processing only

**4. Lambda Timeout**
- **Cause:** Video too long for Lambda limits
- **Solution:** Use Step Functions for long-running workflows
- **Status:** Retry with increased timeout

### Retry Logic

```python
from botocore.exceptions import ClientError
import time

def invoke_with_retry(lambda_client, function_name, payload, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType='Event',
                Payload=json.dumps(payload)
            )
            return response
        except ClientError as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)  # Exponential backoff
```

## Performance Optimization

### Parallel Processing
- Frame extraction and transcription run in parallel
- Caption generation processes frames in batches
- Image embedding runs concurrently with caption embedding

### Cost Optimization
- Evenly distributed frames (45 frames vs 30fps = 99%+ cost reduction)
- S3 Intelligent-Tiering for rarely accessed frames
- Lambda memory tuned for optimal price/performance

### Monitoring
```python
# CloudWatch metrics
cloudwatch.put_metric_data(
    Namespace='VideoProcessing',
    MetricData=[
        {
            'MetricName': 'ProcessingDuration',
            'Value': duration_seconds,
            'Unit': 'Seconds',
            'Dimensions': [
                {'Name': 'VideoId', 'Value': video_id}
            ]
        }
    ]
)
```

## Testing

### Manual Test
1. Get upload URL:
   ```bash
   curl -X POST https://api-gateway-url/upload \
     -H "Content-Type: application/json" \
     -d '{"filename": "test.mp4", "contentType": "video/mp4"}'
   ```

2. Upload video:
   ```bash
   curl -X PUT "<presigned-url>" \
     --upload-file test.mp4 \
     -H "Content-Type: video/mp4"
   ```

3. Check status:
   ```bash
   curl https://api-gateway-url/videos/{video_id}
   ```

### Automated Tests
- Unit tests for each Lambda function
- Integration tests for full pipeline
- Load tests for concurrent uploads

## Troubleshooting

### Video Not Processing
1. Check CloudWatch logs for upload_video Lambda
2. Verify S3 event trigger is enabled
3. Check DynamoDB for video record
4. Look for Lambda invocation errors

### Frames Not Extracted
1. Check CloudWatch logs for extract_frames Lambda
2. Verify FFmpeg layer is attached
3. Check /tmp/ storage space
4. Validate video codec compatibility

### Slow Processing
1. Review Lambda memory allocation
2. Check for throttling (concurrent execution limits)
3. Monitor S3 transfer speeds
4. Optimize FFmpeg parameters

## Related Documentation

- [03-speech-transcription.md](03-speech-transcription.md) - Transcribe integration
- [04-frame-captioning.md](04-frame-captioning.md) - Claude Vision processing
- [05-image-embeddings.md](05-image-embeddings.md) - Image vector generation

## Next Steps

After video ingestion completes:
1. Frames are ready for caption generation (see Journal 04)
2. Transcription job running (see Journal 03)
3. Video metadata searchable via list_videos tool

