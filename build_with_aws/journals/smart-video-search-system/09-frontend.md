# Frontend Application

**Component:** User Interface for video search and playback  
**Purpose:** Provide an intuitive interface for uploading videos, searching content, and navigating search results

## Overview

The frontend is a single-page application (SPA) hosted on S3 and served via CloudFront. It interacts with the backend via API Gateway and provides:
1. Video upload interface with progress tracking
2. Dual search modes (Manual and Auto)
3. Video player with timestamp navigation
4. Search result visualization

## Architecture

```
User Browser
    ↓
CloudFront (CDN)
    ↓
S3 Bucket (Static Assets)
    ↓
index.html + styles + scripts
    ↓
API Gateway (REST API)
    ↓
agent_api Lambda
```

## Key Components

### 1. Upload Interface

**Features:**
- Drag & Drop support
- Progress bar
- Presigned URL integration

**Flow:**
1. User selects file
2. Frontend calls `POST /upload` to get presigned URL
3. Frontend uploads file directly to S3 using PUT
4. Progress bar updates via XHR/Fetch events
5. Success message triggers video list refresh

**Code Snippet:**
```javascript
async function uploadVideo(file) {
    // 1. Get presigned URL
    const response = await fetch(`${API_URL}/upload`, {
        method: 'POST',
        body: JSON.stringify({
            filename: file.name,
            contentType: file.type
        })
    });
    const { uploadUrl, videoId } = await response.json();

    // 2. Upload to S3
    await fetch(uploadUrl, {
        method: 'PUT',
        body: file,
        headers: { 'Content-Type': file.type }
    });

    return videoId;
}
```

### 2. Search Modes

#### Manual Mode
User explicitly selects the search index.

- **Speech Search**: Queries transcripts
- **Caption Search**: Queries frame descriptions
- **Image Search**: Queries visual similarity (upload image)

**UI Elements:**
- Radio buttons for search type
- Search input
- Video ID filter (optional)

#### Auto Mode
User enters a natural language query, and the agent determines the best strategy.

- **Input**: "Find the part where they discuss AI"
- **Process**: Calls `/chat` endpoint
- **Display**: Renders mixed results (speech, caption, etc.) based on agent response

### 3. Result Display & Navigation

**Features:**
- List of matching segments
- Thumbnail previews (future)
- Clickable timestamps
- Confidence scores

**Click-to-Play Logic:**
When a user clicks a result, the video player seeks to the exact timestamp.

```javascript
function playVideoAt(videoId, timestamp) {
    const videoPlayer = document.getElementById('videoPlayer');
    
    // Load video if not already loaded
    if (currentVideoId !== videoId) {
        videoPlayer.src = getVideoUrl(videoId);
        currentVideoId = videoId;
    }
    
    // Seek to timestamp
    videoPlayer.currentTime = timestamp;
    videoPlayer.play();
}
```

### 4. Video Player

**Features:**
- HTML5 Video element
- Custom controls (optional)
- Overlay for bounding boxes (future for object detection)

## API Integration

**Config:**
```javascript
const API_URL = 'https://{api-id}.execute-api.{region}.amazonaws.com/prod';
```

**Endpoints Used:**
- `GET /videos`: List available videos
- `POST /upload`: Get upload URL
- `POST /search`: Manual search
- `POST /chat`: Auto mode search

## Result Parsing

### Standardized Result Format

Both search modes return a consistent structure for rendering:

```javascript
{
    results: [
        {
            video_id: "vid-123",
            timestamp: 45.5,    // Seconds
            text: "Mathed text snippet...",
            score: 0.89,        // Confidence
            type: "speech"      // or "caption", "image"
        },
        // ...
    ]
}
```

### Handling Timestamps

- **Speech**: Timestamps extracted via Claude from Transcribe JSON
- **Caption**: Timestamps from frame metadata (evenly distributed across video duration)
- **Image**: Timestamps from frame metadata (same as captions - both use identical frames)

**Display:**
Format seconds to `MM:SS`:
```javascript
function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}
```

## Styling

- **Framework**: CSS (no heavy frameworks to keep it lightweight)
- **Design**: Clean, dark mode compatible
- **Responsive**: Adapts to screen size

## Deployment

**CDK Construction:**
1. S3 Bucket for assets
2. CloudFront Distribution
3. S3 Deployment (uploads `index.html`)

```python
# InfrastructureStack.py
website_bucket = s3.Bucket(self, "WebsiteBucket", ...)

distribution = cloudfront.Distribution(self, "Distribution",
    default_behavior=cloudfront.BehaviorOptions(
        origin=origins.S3Origin(website_bucket)
    ),
    default_root_object="index.html"
)

s3deploy.BucketDeployment(self, "DeployWebsite",
    sources=[s3deploy.Source.asset("../agent/web")],
    destination_bucket=website_bucket,
    distribution=distribution
)
```

## Security

- **CORS**: API Gateway configured to allow requests from CloudFront domain
- **HTTPS**: Enforced by CloudFront
- **Content Security Policy**: Headers to restrict sources

## Testing

**Manual Testing Steps:**
1. Open CloudFront URL
2. Drag & Drop a video file
3. Wait for "Ready" status
4. Switch to "Manual" → "Speech"
5. Type query → Verify results appear
6. Click result → Verify video jumps to correct time

## Troubleshooting

### Issue: Video 403 Forbidden
**Cause**: CloudFront OAI/OAC not configured correctly or S3 permissions.
**Fix**: Check Bucket Policy allows CloudFront principal.

### Issue: Search returns 0 results
**Cause**: Indexing incomplete or query mismatch.
**Fix**: Check DynamoDB status, verify KB sync.

### Issue: Timestamps incorrect
**Cause**: Frame metadata not properly calculated.
**Fix**: Ensure timestamps are calculated using evenly distributed frame approach: `(frame_num - 1) * duration / (total_frames - 1)`

## Related Documentation

- [08-intelligent-agent.md](08-intelligent-agent.md) - Backend API
- [02-video-ingestion.md](02-video-ingestion.md) - Upload flow

## Next Steps

1. Implement thumbnail generation for results
2. Add bounding box overlays for object detection
3. Support multi-video playback playlists
