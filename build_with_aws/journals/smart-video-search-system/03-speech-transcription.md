# Speech Transcription Pipeline

**Component:** AWS Transcribe Integration  
**Purpose:** Convert speech to text with word-level timestamps for semantic search

## Overview

The speech transcription pipeline uses AWS Transcribe to generate word-level transcripts with precise timestamps, which are then indexed in a Bedrock Knowledge Base for semantic search.

## Architecture

```
process_video Lambda
        ↓
AWS Transcribe Job (async)
        ↓
check_transcription Lambda (polling)
        ↓
Transcribe output → S3
        ↓
chunk_transcript Lambda
        ↓
Bedrock Knowledge Base (Speech Index)
```

## AWS Transcribe Integration

### Starting a Transcription Job

**In process_video.py:**
```python
import boto3

transcribe_client = boto3.client('transcribe')

response = transcribe_client.start_transcription_job(
    TranscriptionJobName=f"{video_id}-transcription",
    Media={
        'MediaFileUri': f's3://{raw_bucket}/{video_id}/raw/{filename}'
    },
    MediaFormat='mp4',  # or 'mov', 'avi', 'flac', 'wav'
    LanguageCode='en-US',
    Settings={
        'ShowSpeakerLabels': False,
        'MaxSpeakerLabels': 2,
        'ChannelIdentification': False
    },
    OutputBucketName=processed_bucket,
    OutputKey=f'{video_id}/transcriptions/'
)
```

**Configuration Options:**
- **LanguageCode**: 'en-US' (can be auto-detected or specified)
- **MediaFormat**: Extracted from file extension
- **ShowSpeakerLabels**: False (can enable for multi-speaker diarization)
- **Output**: JSON file with word-level timestamps

### Transcription Job Parameters

| Parameter | Value | Reasoning |
|-----------|-------|-----------|
| LanguageCode | en-US | Primary language (can be parameterized) |
| MediaFormat | mp4/mov/avi | Detected from file extension |
| ShowSpeakerLabels | False | Single speaker assumed (faster processing) |
| MaxSpeakerLabels | 2 | If speaker labels enabled |
| OutputBucketName | processed-bucket | Store with other processed artifacts |

## Transcribe Output Format

### JSON Structure

```json
{
  "jobName": "video-id-transcription",
  "accountId": "123456789012",
  "results": {
    "transcripts": [
      {
        "transcript": "Welcome to our video on artificial intelligence..."
      }
    ],
    "items": [
      {
        "start_time": "0.12",
        "end_time": "0.56",
        "alternatives": [
          {
            "confidence": "0.9987",
            "content": "Welcome"
          }
        ],
        "type": "pronunciation"
      },
      {
        "start_time": "0.56",
        "end_time": "0.63",
        "alternatives": [
          {
            "confidence": "0.9995",
            "content": "to"
          }
        ],
        "type": "pronunciation"
      },
      {
        "alternatives": [
          {
            "confidence": "0.0",
            "content": ","
          }
        ],
        "type": "punctuation"
      }
    ]
  },
  "status": "COMPLETED"
}
```

**Key Fields:**
- `results.transcripts[0].transcript`: Full text without timestamps
- `results.items[]`: Word-level array with start_time, end_time, content
- `type`: "pronunciation" (timed word) or "punctuation" (no timing)
- `confidence`: Transcription confidence score (0-1)

### S3 Output Location

```
s3://mvip-processed-{account}-{region}/
└── {video_id}/
    └── transcriptions/
        └── {video_id}-transcription.json
```

## Polling for Completion

### check_transcription Lambda

**File:** `src/lambdas/check_transcription.py`

**Purpose:** Poll Transcribe job until completion

**Flow:**
```python
def lambda_handler(event, context):
    video_id = event['video_id']
    job_name = f"{video_id}-transcription"
    
    transcribe = boto3.client('transcribe')
    
    try:
        response = transcribe.get_transcription_job(
            TranscriptionJobName=job_name
        )
        
        status = response['TranscriptionJob']['TranscriptionJobStatus']
        
        if status == 'COMPLETED':
            # Invoke chunk_transcript Lambda
            lambda_client.invoke(
                FunctionName='chunk-transcript',
                InvocationType='Event',
                Payload=json.dumps({
                    'video_id': video_id,
                    'transcript_uri': response['TranscriptionJob']['Transcript']['TranscriptFileUri']
                })
            )
            return {'status': 'completed'}
            
        elif status == 'FAILED':
            # Update DynamoDB with error
            return {'status': 'failed', 'error': 'Transcription failed'}
            
        else:  # IN_PROGRESS
            # Re-invoke self after delay
            time.sleep(10)
            return lambda_handler(event, context)
            
    except Exception as e:
        print(f"Error checking transcription: {str(e)}")
        raise
```

**Polling Strategy:**
- Check every 10 seconds
- Max polling time: Lambda timeout (15 minutes)
- Alternative: Use Step Functions Wait state

**Status Values:**
- `IN_PROGRESS`: Job still running
- `COMPLETED`: Success, proceed to chunking
- `FAILED`: Error in transcription
- `QUEUED`: Waiting to start

## Preparing for Bedrock Knowledge Base

### chunk_transcript Lambda

**File:** `src/lambdas/chunk_transcript.py`

**Purpose:** Convert Transcribe JSON to Bedrock KB-compatible format

**Key Decision:** Upload FULL transcript (not manual chunks)
- Bedrock KB handles its own chunking strategy
- Maintains semantic coherence
- Better for RAG retrieval

**Process:**
1. Download Transcribe JSON from S3
2. Extract full transcript text
3. Create metadata file with video information
4. Upload to S3 in KB-compatible format:
   - `full_transcript.txt` - Plain text
   - `full_transcript.txt.metadata.json` - Sidecar metadata

**Code:**
```python
import boto3
import json

def lambda_handler(event, context):
    video_id = event['video_id']
    transcript_uri = event['transcript_uri']
    
    s3 = boto3.client('s3')
    
    # Download Transcribe JSON
    transcript_bucket, transcript_key = parse_s3_uri(transcript_uri)
    response = s3.get_object(Bucket=transcript_bucket, Key=transcript_key)
    transcript_data = json.loads(response['Body'].read())
    
    # Extract full transcript
    full_text = transcript_data['results']['transcripts'][0]['transcript']
    
    # Save full transcript text
    text_key = f"speech_index/{video_id}/full_transcript.txt"
    s3.put_object(
        Bucket=processed_bucket,
        Key=text_key,
        Body=full_text.encode('utf-8'),
        ContentType='text/plain'
    )
    
    # Create metadata sidecar
    metadata = {
        "metadataAttributes": {
            "video_id": video_id,
            "content_type": "transcript",
            "source": "aws_transcribe"
        }
    }
    
    metadata_key = f"speech_index/{video_id}/full_transcript.txt.metadata.json"
    s3.put_object(
        Bucket=processed_bucket,
        Key=metadata_key,
        Body=json.dumps(metadata).encode('utf-8'),
        ContentType='application/json'
    )
    
    print(f"Uploaded transcript for {video_id} to {text_key}")
    
    # Trigger KB sync (optional - KB syncs automatically)
    # bedrock_agent.start_ingestion_job(...)
    
    return {'status': 'success'}
```

### S3 Path Structure for Speech Index

**Critical:** Bedrock KB inclusion prefix is `speech_index/`

```
s3://mvip-processed-{account}-{region}/
└── speech_index/
    └── {video_id}/
        ├── full_transcript.txt
        └── full_transcript.txt.metadata.json
```

**Why this structure:**
- KB scans `speech_index/` prefix
- All videos under this prefix are automatically indexed
- No need to update KB configuration per video
- See [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md) for details

### Metadata Schema

**full_transcript.txt.metadata.json:**
```json
{
  "metadataAttributes": {
    "video_id": "unique-video-id-123",
    "content_type": "transcript",
    "source": "aws_transcribe"
  }
}
```

**Purpose:**
- Filter search results by video_id
- Distinguish transcript chunks from other content
- Track data source for auditing

## Intelligent Timestamp Extraction

### Problem
Bedrock KB returns text snippets, but not the exact timestamps from the original Transcribe JSON.

### Solution
The `search_by_speech` tool uses Claude to match KB snippets back to the Transcribe JSON:

**In search_by_speech.py:**
```python
def extract_timestamp_with_claude(snippet, transcribe_json, bedrock_runtime):
    """Use Claude to find the exact timestamp for a text snippet."""
    
    items = transcribe_json['results']['items']
    
    # Build word-level transcript with timestamps
    word_list = []
    for item in items:
        if item['type'] == 'pronunciation':
            word_list.append({
                'word': item['alternatives'][0]['content'],
                'start': float(item['start_time']),
                'end': float(item['end_time'])
            })
    
    # Ask Claude to find matching words
    prompt = f"""Find the words in this transcript that match the snippet:

Snippet: "{snippet}"

Transcript words (with timestamps):
{json.dumps(word_list, indent=2)}

Return JSON with: {{"start_time": <seconds>, "end_time": <seconds>}}
Match the semantic meaning, not exact words."""
    
    response = bedrock_runtime.invoke_model(
        modelId='anthropic.claude-3-sonnet-20240229-v1:0',
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 200,
            "messages": [{
                "role": "user",
                "content": prompt
            }]
        })
    )
    
    result = json.loads(response['body'].read())
    timestamp_data = json.loads(result['content'][0]['text'])
    
    return timestamp_data['start_time'], timestamp_data['end_time']
```

**Flow:**
1. Bedrock KB returns text snippet
2. Retrieve full Transcribe JSON from S3
3. Claude matches snippet to word timestamps
4. Return precise start/end times to frontend

## Performance Characteristics

### Transcription Speed
- **Typical**: ~1/4 of video duration
- **Example**: 10-minute video = ~2.5 minutes to transcribe
- **Factors**: Audio quality, speech clarity, language

### Accuracy
- **Confidence scores**: Typically 0.95-0.99 for clear speech
- **Factors**: Background noise, accents, technical terms
- **Custom vocabularies**: Can improve accuracy for domain-specific terms

### Cost
- **AWS Transcribe**: $0.024 per minute of audio (standard)
- **Example**: 10-minute video = $0.24
- **Batching**: No additional cost savings
- **Storage**: S3 JSON output ~100KB per minute

## Error Handling

### Common Errors

**1. No Audio Track**
```json
{
  "status": "FAILED",
  "failureReason": "The media file does not contain audio."
}
```
**Solution:** Validate audio track before starting job

**2. Unsupported Format**
```json
{
  "status": "FAILED",
  "failureReason": "The media format is not supported."
}
```
**Solution:** Convert to supported format (mp4, wav, flac)

**3. File Too Large**
**Limit:** 2 GB for file upload, 4 hours duration
**Solution:** Use S3 URI (supports larger files)

**4. Language Mismatch**
Low confidence scores if wrong language specified
**Solution:** Use automatic language detection

### Retry Strategy

```python
def start_transcription_with_retry(video_id, s3_uri, max_retries=3):
    transcribe = boto3.client('transcribe')
    
    for attempt in range(max_retries):
        try:
            response = transcribe.start_transcription_job(
                TranscriptionJobName=f"{video_id}-transcription-attempt{attempt}",
                Media={'MediaFileUri': s3_uri},
                MediaFormat='mp4',
                LanguageCode='en-US',
                OutputBucketName=processed_bucket,
                OutputKey=f'{video_id}/transcriptions/'
            )
            return response
        except transcribe.exceptions.ConflictException:
            # Job already exists
            print(f"Transcription job already running for {video_id}")
            return
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
```

## Testing

### Manual Test

```bash
# 1. Start transcription job
aws transcribe start-transcription-job \
  --transcription-job-name "test-video-123" \
  --media MediaFileUri=s3://bucket/video.mp4 \
  --media-format mp4 \
  --language-code en-US \
  --output-bucket-name processed-bucket \
  --output-key test-video-123/transcriptions/

# 2. Check status
aws transcribe get-transcription-job \
  --transcription-job-name "test-video-123"

# 3. Download result
aws s3 cp s3://processed-bucket/test-video-123/transcriptions/test-video-123.json .

# 4. Test chunk_transcript
aws lambda invoke \
  --function-name chunk-transcript \
  --payload '{"video_id": "test-video-123", "transcript_uri": "s3://..."}' \
  response.json
```

## Monitoring

### CloudWatch Metrics

```python
cloudwatch = boto3.client('cloudwatch')

cloudwatch.put_metric_data(
    Namespace='VideoProcessing/Transcription',
    MetricData=[
        {
            'MetricName': 'TranscriptionDuration',
            'Value': duration_seconds,
            'Unit': 'Seconds'
        },
        {
            'MetricName': 'TranscriptionCost',
            'Value': cost_usd,
            'Unit': 'None'
        }
    ]
)
```

### Logging

```python
import logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

logger.info(f"Started transcription job: {job_name}")
logger.info(f"Transcription completed in {duration}s")
logger.error(f"Transcription failed: {error_message}")
```

## Optimization Tips

### 1. Use Custom Vocabularies
For domain-specific terms (brand names, technical jargon):
```python
transcribe.create_vocabulary(
    VocabularyName='technical-terms',
    LanguageCode='en-US',
    Phrases=[
        'Bedrock',
        'AgentCore',
        'OpenSearch',
        'multimodal'
    ]
)
```

### 2. Enable Speaker Labels (if needed)
For interviews, conversations:
```python
Settings={
    'ShowSpeakerLabels': True,
    'MaxSpeakerLabels': 2
}
```

### 3. Batch Processing
Process multiple videos in parallel (within AWS limits)

### 4. Cost Optimization
- Use standard mode ($0.024/min) vs real-time ($0.04/min)
- Only transcribe audio-heavy videos
- Cache results for re-use

## Troubleshooting

### Issue: Transcription Not Starting
- Check IAM permissions for Transcribe
- Verify S3 URI is accessible
- Check Transcribe service quotas

### Issue: Low Accuracy
- Review audio quality
- Check language code
- Add custom vocabulary
- Consider audio preprocessing

### Issue: Slow Processing
- Check Transcribe service health
- Review concurrent job limits
- Monitor queue depth

## Related Documentation

- [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md) - KB setup and indexing
- [08-intelligent-agent.md](08-intelligent-agent.md) - Using transcripts in search
- [02-video-ingestion.md](02-video-ingestion.md) - Overall processing pipeline

## Next Steps

After transcription completes:
1. Transcript indexed in Bedrock Knowledge Base
2. Available for semantic search via `search_by_speech` tool
3. Frontend can query: "What did they say about AI?"

