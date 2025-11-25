# Frame Captioning with Claude Vision

**Component:** Automated frame description generation  
**Purpose:** Create semantic descriptions of video frames for natural language search

## Overview

The frame captioning pipeline uses Claude Vision (via Amazon Bedrock) to generate natural language descriptions of video frames. These captions are then indexed in a Bedrock Knowledge Base for semantic search queries like "show me outdoor scenes" or "find frames with people talking".

## Architecture

```
extract_frames Lambda
        ↓
Frames in S3
        ↓
generate_captions Lambda (Claude Vision)
        ↓
Caption JSON files
        ↓
embed_captions Lambda
        ↓
Bedrock Knowledge Base (Caption Index)
```

## Why Claude Vision Over Rekognition

### Service Comparison

| Feature | Amazon Rekognition | Claude Vision |
|---------|-------------------|---------------|
| **Output Format** | Structured JSON (labels, scores) | Natural language descriptions |
| **Detection Type** | Objects, faces, text, scenes | Semantic understanding |
| **Query Style** | Exact matches (label="person") | Natural language ("people talking") |
| **Context** | Individual elements | Relationships and narrative |
| **Use Cases** | Filtering, moderation, OCR | Semantic search, storytelling |
| **Cost** | $0.001/image (DetectLabels) | ~$0.005-0.008/image |

### Example Comparison

**Same Frame Analysis:**

**Rekognition Output:**
```json
{
  "labels": [
    {"name": "Road", "confidence": 97.52},
    {"name": "Freeway", "confidence": 94.55},
    {"name": "Person", "confidence": 93.01}
  ],
  "faces": [
    {"age_range": "25-33", "gender": "Male", "emotions": ["CALM", "SURPRISED"]}
  ]
}
```

**Claude Vision Output:**
```
"This image shows a high-speed Hyperloop test track or similar transportation 
testing facility. A pod or vehicle can be seen moving rapidly along an elevated 
track or tube system, creating a blur effect due to its speed."
```

### Why Claude for This System

1. **Primary Use Case**: Semantic search with natural language queries
2. **User Intent**: Users ask "show me celebration scenes" not "find frames with label=celebration"
3. **Contextual Understanding**: Claude understands relationships, not just objects
4. **Bedrock Integration**: Native integration with Knowledge Base RAG
5. **Search Quality**: Better matches for conversational queries

### Rekognition Use Cases

Rekognition would be better for:
- Content moderation (inappropriate content)
- Text extraction (signs, documents)
- Face recognition (identity matching)
- Precise object counting
- Compliance filtering

### Hybrid Approach (Future)

For systems requiring both:
```
Frame → Claude (semantic description) + Rekognition (structured metadata)
         ↓                                ↓
   KB Semantic Search              Precise Filtering
         ↓                                ↓
           Combined Search Results
```

**See:** `examples/rekognition_vs_claude.py` for live comparison

## Generate Captions Lambda

**File:** `src/lambdas/generate_captions.py`

### Purpose
Process all extracted frames through Claude Vision to generate descriptions

### Flow

```python
def lambda_handler(event, context):
    video_id = event['video_id']
    frames_prefix = f"{video_id}/frames/"
    
    s3 = boto3.client('s3')
    bedrock = boto3.client('bedrock-runtime')
    
    # List all frames
    frames = list_frames(s3, processed_bucket, frames_prefix)
    
    # Process in batches to avoid timeout
    captions = []
    for frame_key in frames:
        caption = generate_caption(s3, bedrock, processed_bucket, frame_key)
        captions.append(caption)
        
        # Save individual caption
        save_caption(s3, video_id, caption)
    
    # Trigger next step
    lambda_client.invoke(
        FunctionName='embed-captions',
        InvocationType='Event',
        Payload=json.dumps({
            'video_id': video_id,
            'captions_count': len(captions)
        })
    )
    
    return {'captions_generated': len(captions)}
```

### Batch Processing

**Challenge:** Long videos have many frames, Lambda has 15-minute timeout

**Solution:** Process frames in batches

```python
BATCH_SIZE = 100  # Process 100 frames at a time

def process_batch(frames_batch):
    captions = []
    for frame in frames_batch:
        try:
            caption = generate_caption_for_frame(frame)
            captions.append(caption)
        except Exception as e:
            logger.error(f"Failed to caption {frame}: {e}")
            # Continue with next frame
    return captions

# Split frames into batches
for i in range(0, len(all_frames), BATCH_SIZE):
    batch = all_frames[i:i+BATCH_SIZE]
    batch_captions = process_batch(batch)
    all_captions.extend(batch_captions)
```

### Claude Vision API Call

```python
import base64
import json

def generate_caption(s3_client, bedrock_client, bucket, frame_key):
    # Download frame from S3
    response = s3_client.get_object(Bucket=bucket, Key=frame_key)
    image_bytes = response['Body'].read()
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    # Extract frame number for timestamp
    frame_num = extract_frame_number(frame_key)  # e.g., "0042" from "frame_0042.jpg"
    timestamp_sec = frame_num / 6  # 6 fps
    
    # Call Claude Vision
    prompt = """Describe this video frame in 2-3 sentences. Focus on:
- Main subjects and their actions
- Setting and environment
- Mood or atmosphere
- Any notable objects or text

Be specific and descriptive for search purposes."""
    
    response = bedrock_client.invoke_model(
        modelId='us.anthropic.claude-3-5-sonnet-20241022-v2:0',
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        })
    )
    
    result = json.loads(response['body'].read())
    caption_text = result['content'][0]['text']
    
    return {
        'frame_key': frame_key,
        'frame_number': frame_num,
        'frame_timestamp_sec': timestamp_sec,
        'caption': caption_text
    }
```

### Caption Output Format

**Per-frame JSON:**
```json
{
  "frame_key": "video-123/frames/frame_0042.jpg",
  "frame_number": 42,
  "frame_timestamp_sec": 7.0,
  "caption": "A man in a blue suit stands at a podium addressing an audience. Behind him is a large screen displaying charts and graphs. The setting appears to be a corporate conference room with professional lighting.",
  "model": "claude-3-5-sonnet-20241022-v2:0",
  "generated_at": "2025-11-25T10:45:23Z"
}
```

**S3 Location:**
```
s3://mvip-processed-{account}-{region}/
└── {video_id}/
    └── captions/
        ├── frame_0001.json
        ├── frame_0002.json
        └── ...
```

## Embed Captions Lambda

**File:** `src/lambdas/embed_captions.py`

### Purpose
Convert caption JSON to Bedrock KB-compatible format

### Process

1. Read all caption JSON files for the video
2. For each caption, create:
   - `.txt` file with caption text
   - `.txt.metadata.json` sidecar with frame metadata
3. Upload to `caption_index/` prefix for KB ingestion

### Code

```python
def lambda_handler(event, context):
    video_id = event['video_id']
    captions_prefix = f"{video_id}/captions/"
    
    s3 = boto3.client('s3')
    
    # List all caption JSON files
    caption_files = list_s3_objects(s3, processed_bucket, captions_prefix)
    
    for caption_file in caption_files:
        # Read caption JSON
        response = s3.get_object(Bucket=processed_bucket, Key=caption_file)
        caption_data = json.loads(response['Body'].read())
        
        # Extract frame number from filename
        frame_num = extract_frame_number(caption_file)
        
        # Create .txt file
        text_content = caption_data['caption']
        txt_key = f"caption_index/{video_id}/frame_{frame_num:04d}.txt"
        
        s3.put_object(
            Bucket=processed_bucket,
            Key=txt_key,
            Body=text_content.encode('utf-8'),
            ContentType='text/plain'
        )
        
        # Create .txt.metadata.json sidecar
        metadata = {
            "metadataAttributes": {
                "video_id": video_id,
                "frame_number": frame_num,
                "frame_timestamp_sec": caption_data['frame_timestamp_sec'],
                "content_type": "caption",
                "source": "claude_vision"
            }
        }
        
        metadata_key = f"caption_index/{video_id}/frame_{frame_num:04d}.txt.metadata.json"
        s3.put_object(
            Bucket=processed_bucket,
            Key=metadata_key,
            Body=json.dumps(metadata).encode('utf-8'),
            ContentType='application/json'
        )
    
    print(f"Embedded {len(caption_files)} captions for {video_id}")
    
    # Trigger image embedding (next step in pipeline)
    lambda_client.invoke(
        FunctionName='embed-and-index-images',
        InvocationType='Event',
        Payload=json.dumps({'video_id': video_id})
    )
    
    return {'status': 'success', 'captions_embedded': len(caption_files)}
```

### S3 Path Structure for Caption Index

**Critical:** Bedrock KB inclusion prefix is `caption_index/`

```
s3://mvip-processed-{account}-{region}/
└── caption_index/
    └── {video_id}/
        ├── frame_0001.txt
        ├── frame_0001.txt.metadata.json
        ├── frame_0002.txt
        ├── frame_0002.txt.metadata.json
        └── ...
```

**Why this structure:**
- KB scans `caption_index/` prefix
- All videos' captions automatically indexed
- No KB configuration update needed per video
- See [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md)

### Metadata Schema

**frame_0042.txt:**
```
A man in a blue suit stands at a podium addressing an audience. Behind him is a large screen displaying charts and graphs. The setting appears to be a corporate conference room with professional lighting.
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

## Performance & Cost

### Processing Time

- **Caption Generation**: ~2-3 seconds per frame
- **100 frames** (typical 1-minute video at 6fps): ~3-5 minutes
- **Bottleneck**: Claude Vision API throughput

### Cost Analysis

**Claude Vision (Bedrock):**
- Model: Claude 3.5 Sonnet
- Input: ~1,600 tokens per image + prompt (~50 tokens)
- Output: ~100-150 tokens (caption)
- **Cost per frame**: ~$0.005-0.008
- **100 frames**: ~$0.50-0.80

**Comparison:**
- Rekognition: $0.001/frame = $0.10 for 100 frames
- **Claude is 5-8x more expensive** but provides semantic value

**Trade-off:** Higher cost for better search experience

### Optimization Strategies

**1. Frame Sampling**
- Current: 6 fps (1 frame every ~167ms)
- Could reduce to 3 fps (1 frame every ~333ms)
- **Savings**: 50% fewer frames to caption

**2. Batch Inference**
- Use Bedrock Batch Inference (50% discount)
- Trade-off: Results available after delay

**3. Selective Captioning**
- Only caption frames with significant changes
- Use frame difference detection
- **Savings**: 30-50% fewer frames

**4. Caching**
- Store captions permanently
- Only regenerate if frame changes

## Quality Control

### Prompt Engineering

**Good Prompt:**
```python
prompt = """Describe this video frame in 2-3 sentences for semantic search. Include:
- Main subjects and their actions (who/what)
- Setting and environment (where)
- Mood, atmosphere, or notable details (how)
Be specific and avoid generic descriptions."""
```

**Bad Prompt:**
```python
prompt = "What's in this image?"  # Too vague, inconsistent results
```

### Caption Quality Examples

**High Quality:**
```
"A chef in a white uniform demonstrates knife skills in a modern kitchen. 
She's dicing vegetables on a wooden cutting board while explaining technique 
to a camera. Stainless steel appliances and herb plants are visible in the 
background."
```
- Specific subjects
- Clear actions
- Environmental details
- Searchable keywords

**Low Quality:**
```
"A person is doing something in a room."
```
- Too generic
- No useful details
- Poor for search

### Monitoring Caption Quality

```python
def assess_caption_quality(caption):
    """Simple heuristic for caption quality."""
    issues = []
    
    if len(caption) < 50:
        issues.append("too_short")
    
    if len(caption.split()) < 10:
        issues.append("too_few_words")
    
    generic_phrases = ["a person", "something", "some things", "an area"]
    if any(phrase in caption.lower() for phrase in generic_phrases):
        issues.append("too_generic")
    
    return {
        'quality_score': max(0, 100 - len(issues) * 30),
        'issues': issues
    }
```

## Error Handling

### Common Errors

**1. Image Format Issues**
```python
try:
    caption = generate_caption(frame)
except bedrock.exceptions.ValidationException as e:
    logger.error(f"Invalid image format: {e}")
    # Skip frame or retry with conversion
```

**2. API Throttling**
```python
from botocore.exceptions import ClientError
import time

def invoke_with_backoff(bedrock_client, **kwargs):
    for attempt in range(5):
        try:
            return bedrock_client.invoke_model(**kwargs)
        except ClientError as e:
            if e.response['Error']['Code'] == 'ThrottlingException':
                wait_time = (2 ** attempt) + random.random()
                time.sleep(wait_time)
            else:
                raise
    raise Exception("Max retries exceeded")
```

**3. Model Errors**
```python
try:
    response = bedrock_client.invoke_model(...)
    result = json.loads(response['body'].read())
    caption = result['content'][0]['text']
except (KeyError, json.JSONDecodeError) as e:
    logger.error(f"Failed to parse model response: {e}")
    caption = "[Caption generation failed]"
```

## Testing

### Manual Test

```python
# Test caption generation for a single frame
python3 << EOF
import boto3
import base64
import json

s3 = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime')

# Download test frame
response = s3.get_object(
    Bucket='processed-bucket',
    Key='test-video/frames/frame_0001.jpg'
)
image_bytes = response['Body'].read()

# Generate caption
response = bedrock.invoke_model(
    modelId='us.anthropic.claude-3-5-sonnet-20241022-v2:0',
    body=json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode()}},
                {"type": "text", "text": "Describe this frame in 2-3 sentences."}
            ]
        }]
    })
)

result = json.loads(response['body'].read())
print(result['content'][0]['text'])
EOF
```

### Automated Tests

```python
import unittest

class TestCaptionGeneration(unittest.TestCase):
    def test_caption_format(self):
        caption = generate_caption_for_test_frame()
        self.assertIsInstance(caption, str)
        self.assertGreater(len(caption), 50)
        self.assertLess(len(caption), 500)
    
    def test_metadata_structure(self):
        metadata = create_metadata_for_frame(42, 7.0)
        self.assertIn('video_id', metadata['metadataAttributes'])
        self.assertIn('frame_timestamp_sec', metadata['metadataAttributes'])
```

## Monitoring

### CloudWatch Metrics

```python
cloudwatch = boto3.client('cloudwatch')

cloudwatch.put_metric_data(
    Namespace='VideoProcessing/Captions',
    MetricData=[
        {
            'MetricName': 'CaptionGenerationDuration',
            'Value': duration_seconds,
            'Unit': 'Seconds',
            'Dimensions': [{'Name': 'VideoId', 'Value': video_id}]
        },
        {
            'MetricName': 'CaptionCount',
            'Value': caption_count,
            'Unit': 'Count'
        },
        {
            'MetricName': 'CaptionCost',
            'Value': total_cost,
            'Unit': 'None'
        }
    ]
)
```

## Troubleshooting

### Issue: Captions Too Generic
- Review prompt engineering
- Add more specific instructions
- Include example descriptions

### Issue: Slow Processing
- Check Bedrock service quotas
- Implement parallel processing
- Consider batch inference

### Issue: High Costs
- Reduce frame sampling rate
- Use selective captioning
- Enable batch inference mode

## Related Documentation

- [05-image-embeddings.md](05-image-embeddings.md) - Next step: image vectors
- [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md) - KB indexing
- [rekognition-comparison-example.md](rekognition-comparison-example.md) - Full comparison

## Comparison Script

Run the Rekognition vs Claude comparison:
```bash
cd examples
python3 rekognition_vs_claude.py
```

This generates a side-by-side comparison of both services on the same frame.

