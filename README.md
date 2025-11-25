# Smart Video Search System

A serverless multi-modal video intelligence platform built on AWS that enables semantic search across video content using speech transcripts, visual frame descriptions, and image similarity.

## Features

- **Upload & Process**: Automatically process uploaded videos to extract frames, transcribe speech, and generate frame descriptions
- **Three Search Modalities**:
  - **Speech Search**: Find what was said in videos using natural language queries
  - **Caption Search**: Search for visual scenes and actions using frame descriptions
  - **Image Search**: Find visually similar frames using vector similarity
- **Intelligent Agent**: Claude-powered agent automatically routes queries to the appropriate search tool
- **Video Navigation**: Click search results to jump to exact timestamps in the video player

## Architecture

The system uses AWS serverless services to provide scalable video processing and search:

- **Storage**: S3 for videos and processed data
- **Compute**: Lambda functions for all processing tasks
- **AI/ML**: 
  - Amazon Transcribe for speech-to-text
  - Amazon Bedrock (Claude Vision, Claude Sonnet, Titan Embeddings)
  - Bedrock Knowledge Bases for semantic search (speech and captions)
  - S3 Vectors for image similarity search
- **Infrastructure**: CloudFormation via AWS CDK
- **Frontend**: Static website hosted on S3 + CloudFront

## Project Structure

```
smart_video_search_system/
├── src/
│   └── lambdas/           # Lambda function code
│       ├── process_video.py
│       ├── extract_frames.py
│       ├── generate_captions.py
│       ├── chunk_transcript.py
│       ├── agent_api.py
│       └── tools/         # MCP search tools
├── agent/
│   └── web/              # Frontend application
│       └── index.html
├── infrastructure/       # AWS CDK infrastructure code
│   └── cdk.out/         # Build artifacts (not committed)
├── build_with_aws/
│   └── journals/        # Detailed documentation
└── examples/            # Comparison examples

```

## Prerequisites

- **AWS Account** with appropriate permissions
- **AWS CLI** configured
- **Python 3.11+**
- **Node.js 18+** and NPM
- **Docker** (for Lambda bundling)
- **AWS CDK** (`npm install -g aws-cdk`)

## Bedrock Model Access

Before deployment, enable access to the following models in Amazon Bedrock (us-east-1):

1. Claude 3 Sonnet (or 3.5 Sonnet) - Agent reasoning
2. Titan Text Embeddings v2 - Text embeddings for Knowledge Bases
3. Titan Multimodal Embeddings - Image embeddings

Go to AWS Console → Bedrock → Model access → Enable models

## Quick Start

### 1. Clone Repository

```bash
git clone https://github.com/marceloacosta/smart_video_search_system.git
cd smart_video_search_system
```

### 2. Install Dependencies

```bash
# Install CDK
npm install -g aws-cdk

# Install Python dependencies
cd infrastructure
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Bootstrap CDK (first time only)

```bash
cdk bootstrap aws://ACCOUNT-ID/us-east-1
```

### 4. Deploy

```bash
cdk deploy --all
```

The deployment will output:
- `WebsiteURL` - CloudFront URL for the frontend
- `ApiEndpoint` - API Gateway URL
- Resource identifiers (buckets, Knowledge Base IDs, etc.)

### 5. Test

1. Open the `WebsiteURL` in your browser
2. Upload a test video (MP4 format recommended)
3. Wait for processing to complete (~5-10 minutes for a 1-minute video)
4. Try searching using Manual Mode (select Speech/Caption/Image)
5. Try Auto Mode - let the agent decide which search to use

## Documentation

Detailed step-by-step guides are available in the journals:

- [01 - Architecture Overview](build_with_aws/journals/smart-video-search-system/01-architecture-overview.md)
- [02 - Video Ingestion](build_with_aws/journals/smart-video-search-system/02-video-ingestion.md)
- [03 - Speech Transcription](build_with_aws/journals/smart-video-search-system/03-speech-transcription.md)
- [04 - Frame Captioning](build_with_aws/journals/smart-video-search-system/04-frame-captioning.md)
- [05 - Image Embeddings](build_with_aws/journals/smart-video-search-system/05-image-embeddings.md)
- [06 - Bedrock Knowledge Bases](build_with_aws/journals/smart-video-search-system/06-bedrock-knowledge-bases.md)
- [07 - AgentCore Gateway](build_with_aws/journals/smart-video-search-system/07-agentcore-gateway.md)
- [08 - Intelligent Agent](build_with_aws/journals/smart-video-search-system/08-intelligent-agent.md)
- [09 - Frontend](build_with_aws/journals/smart-video-search-system/09-frontend.md)
- [10 - Deployment](build_with_aws/journals/smart-video-search-system/10-deployment.md)

## Cost Estimates

### Fixed Monthly Costs
- **S3 Storage**: ~$0.023 per GB/month (for videos, frames, embeddings)
  - Example: 10 videos × 100MB = 1GB = $0.023/month
  - **Note**: This is pay-as-you-go, no minimum costs

### Variable Costs (per-use)
- **Lambda**: Pay per request/duration (~$0.20 per GB-second)
- **S3**: Storage + requests (~$0.023 per GB/month)
- **Transcribe**: $0.024 per minute of audio
- **Bedrock**:
  - Claude Vision: ~$0.005-0.008 per frame
  - Claude Sonnet: $3 per 1M input tokens
  - Titan Embeddings: $0.0001 per 1K tokens

**Example**: Processing a 10-minute video with 3600 frames (6fps):
- Transcription: $0.24
- Frame captioning: ~$18-29
- Embeddings: ~$0.50
- **Total**: ~$19-30 per video

### Cost Optimization Tips
1. Reduce frame sampling rate (6fps → 3fps saves 50%)
2. Use Bedrock Batch Inference for captions (50% discount)
3. Set S3 lifecycle policies to transition old frames to Glacier
4. Delete unused videos and their associated embeddings

## Cleanup

To avoid ongoing charges:

```bash
# Empty S3 buckets first
aws s3 rm s3://YOUR-RAW-BUCKET --recursive
aws s3 rm s3://YOUR-PROCESSED-BUCKET --recursive
aws s3 rm s3://YOUR-WEBSITE-BUCKET --recursive

# Destroy stack
cdk destroy --all
```

## Troubleshooting

### Video processing fails
- Check CloudWatch logs for the specific Lambda function
- Verify FFmpeg layer is attached to `extract_frames` Lambda
- Ensure video format is supported (MP4, MOV, AVI)

### Search returns no results
- Verify Knowledge Base ingestion completed successfully
- Check S3 paths match KB inclusion prefixes
- Ensure videos have been fully processed

### Frontend not loading
- Check CloudFront distribution is deployed
- Verify S3 bucket policy allows CloudFront access
- Check API Gateway CORS configuration

## Contributing

Contributions are welcome! Please read the documentation journals to understand the system architecture before making changes.

## License

[Specify your license here]

## Acknowledgments

Built using:
- Amazon Bedrock (Claude, Titan)
- AWS Lambda
- Amazon Transcribe
- S3 Vectors
- AWS CDK

## Support

For issues and questions, please open a GitHub issue.

