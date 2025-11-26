# Infrastructure Deployment Guide

This directory contains the AWS CDK infrastructure code for the Smart Video Search System.

## Prerequisites

1. **AWS Account** with appropriate permissions
2. **AWS CLI** configured with your credentials
3. **Python 3.11+**
4. **Node.js 18+** and npm
5. **AWS CDK** installed globally: `npm install -g aws-cdk`

## Required AWS Services

Before deployment, enable access to these models in Amazon Bedrock (us-east-1):

1. **Claude 3.5 Sonnet** (or Claude Sonnet 4) - For agent reasoning and caption generation
2. **Titan Text Embeddings v2** - For text embeddings in Knowledge Bases
3. **Titan Multimodal Embeddings G1** - For image embeddings

Go to: AWS Console → Bedrock → Model access → Enable models

## Pre-Deployment: Create Bedrock Knowledge Bases

The CDK stack requires existing Bedrock Knowledge Bases. Create them before deployment:

### 1. Create Speech Knowledge Base

```bash
# In AWS Console: Bedrock → Knowledge bases → Create knowledge base
Name: video-search-speech-kb
Storage: Amazon S3
Vector store: Bedrock creates and manages vector store
Embeddings model: Titan Text Embeddings v2
```

**Important**: Choose "Bedrock creates and manages vector store" - Bedrock will auto-create the S3 vector storage for text embeddings.

Note the Knowledge Base ID (e.g., `GEABRHGWCO`) and Data Source ID.

### 2. Create Caption Knowledge Base

```bash
# In AWS Console: Bedrock → Knowledge bases → Create knowledge base  
Name: video-search-caption-kb
Storage: Amazon S3
Vector store: Bedrock creates and manages vector store
Embeddings model: Titan Text Embeddings v2
```

**Important**: Choose "Bedrock creates and manages vector store" - Bedrock will auto-create the S3 vector storage for text embeddings.

Note the Knowledge Base ID (e.g., `OELLH9GHHQ`) and Data Source ID.

## Setup

### 1. Install Python Dependencies

```bash
cd infrastructure
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create Configuration File

Create `infrastructure/cdk.context.json` (this file is gitignored):

```json
{
  "speech_kb_id": "YOUR_SPEECH_KB_ID",
  "caption_kb_id": "YOUR_CAPTION_KB_ID",
  "speech_ds_id": "YOUR_SPEECH_DATASOURCE_ID",
  "caption_ds_id": "YOUR_CAPTION_DATASOURCE_ID",
  "agentcore_api_url": "YOUR_AGENTCORE_URL (optional)"
}
```

**Alternative:** Set as environment variables instead:

```bash
export SPEECH_KB_ID="YOUR_SPEECH_KB_ID"
export CAPTION_KB_ID="YOUR_CAPTION_KB_ID"
export SPEECH_DS_ID="YOUR_SPEECH_DATASOURCE_ID"
export CAPTION_DS_ID="YOUR_CAPTION_DATASOURCE_ID"
export AGENTCORE_API_URL="YOUR_AGENTCORE_URL"  # Optional
```

### 3. Bootstrap CDK (First Time Only)

```bash
cdk bootstrap aws://ACCOUNT_ID/us-east-1
```

Replace `ACCOUNT_ID` with your AWS account ID.

## Deployment

### Deploy All Stacks

```bash
cdk deploy --all
```

This deploys:
- **InfrastructureStack**: 19 Lambda functions, S3 buckets, DynamoDB table, API Gateway
- **FrontendStack**: S3 bucket + CloudFront distribution for the web UI

**What CDK Creates:**
- ✅ 4 S3 buckets (raw videos, processed data, vectors bucket name, frontend assets)
- ✅ 19 Lambda functions (video processing, search tools, API)
- ✅ 1 DynamoDB table (video metadata)
- ✅ API Gateway REST API
- ✅ CloudFront distribution
- ✅ IAM roles and policies
- ✅ CloudWatch log groups

**What CDK Does NOT Create (must exist before deployment):**
- ❌ Bedrock Knowledge Bases (speech and caption) - Create manually in AWS Console
- ❌ AgentCore Gateway - Optional, configure separately if using

**What Gets Auto-Created on First Use:**
- 🔄 S3 Vectors index for image embeddings - Auto-created when `embed_and_index_images` Lambda first calls `put_vectors()`
- 🔄 Bedrock KB vector stores - Auto-created by Bedrock when KBs are created

### Deploy Individual Stacks

```bash
# Infrastructure only
cdk deploy InfrastructureStack

# Frontend only
cdk deploy FrontendStack
```

## Post-Deployment

### Configure Knowledge Base Data Sources

After deployment, update your Bedrock Knowledge Base data sources:

1. **Speech KB**: Point to `s3://{project-name}-processed-{account}-{region}/*/speech_index/`
2. **Caption KB**: Point to `s3://{project-name}-processed-{account}-{region}/*/caption_index/`

Note: Bedrock KB doesn't support wildcards in prefixes. You may need to configure the data source to scan the entire processed bucket.

### Get Stack Outputs

```bash
cdk deploy --all --outputs-file outputs.json
```

Outputs include:
- `WebsiteURL` - CloudFront URL for the frontend
- `ApiEndpoint` - API Gateway URL
- `RawBucketName` - S3 bucket for uploading videos
- `ProcessedBucketName` - S3 bucket for processed data
- `VideoTableName` - DynamoDB table name

## Testing

### 1. Verify Deployment

```bash
# List all stacks
cdk list

# View stack diff
cdk diff InfrastructureStack
```

### 2. Test the System

1. Open the `WebsiteURL` from the outputs
2. Upload a test video (MP4 format, < 2GB)
3. Wait for processing (~5-10 minutes for a 1-minute video)
4. Try searching:
   - **Manual Mode**: Select "Speech", "Caption", or "Image" and search
   - **Auto Mode**: Let the agent decide which search to use

## Troubleshooting

### Missing Knowledge Base IDs

If you see warnings about missing KB IDs during deployment:

```
⚠️  Warning: Knowledge Base IDs not configured
```

Create `cdk.context.json` with your KB IDs (see Setup step 2).

### CDK Bootstrap Not Found

```bash
# Bootstrap your AWS environment
cdk bootstrap aws://ACCOUNT_ID/REGION
```

### Lambda Function Fails to Deploy

Check that:
- `../src/lambdas/` directory exists with all Lambda code
- Python dependencies in `src/lambdas/requirements.txt` are compatible

### CloudFront Distribution Creation Slow

CloudFront distributions take 15-20 minutes to fully deploy. This is normal.

## Updating the Infrastructure

### Apply Changes

```bash
# See what will change
cdk diff

# Deploy changes
cdk deploy --all
```

### Update Lambda Code Only

```bash
# Re-deploy specific function
cdk deploy InfrastructureStack --hotswap
```

Note: `--hotswap` skips CloudFormation for faster Lambda updates (dev only).

## Cleanup

### Delete All Resources

```bash
cdk destroy --all
```

**Warning:** This deletes all data including uploaded videos and processed results. The S3 buckets have `auto_delete_objects=True` so all content will be removed.

### Manual Cleanup

If `cdk destroy` fails, manually delete:
1. CloudFormation stacks in AWS Console
2. S3 buckets (empty them first if auto-delete failed)
3. CloudWatch log groups with prefix `/aws/lambda/{project-name}-*`

## Cost Estimate

### Deployment Costs (One-Time)
- Free within AWS Free Tier

### Monthly Operating Costs (After Processing 100 Videos)
- **S3 Storage**: ~$0.40/month (5-8GB of frames, transcripts, embeddings)
- **DynamoDB**: ~$0 (PAY_PER_REQUEST, minimal reads/writes)
- **CloudWatch Logs**: ~$0.50/month (2-year retention, 731 days)
- **CloudFront**: ~$0.10/month (minimal traffic)
- **API Gateway**: ~$0 (minimal requests)

**Total**: ~$1-3/month for idle system with 100 processed videos

### Per-Video Processing Costs
- **Transcribe**: $0.048 per 2-minute video
- **Claude Vision (captions)**: $0.48-0.72 (80-120 frames × $0.006/frame)
- **Titan Embeddings**: $0.005 (negligible)
- **Lambda execution**: ~$0.01

**Total**: ~$0.50-0.80 per 2-minute video

### Query Costs
- **Per 1,000 queries**: ~$2.70
  - Bedrock KB retrieval: $1.00
  - Claude Sonnet 4 routing: $1.50
  - S3 Vectors: $0.20

## Architecture

The CDK creates:

**InfrastructureStack:**
- 4 S3 Buckets (raw videos, processed data, vectors, website)
- 1 DynamoDB Table (video metadata with status GSI)
- 19 Lambda Functions (processing pipeline + MCP tools + API)
- 1 API Gateway (REST API)
- IAM Roles and Policies
- CloudWatch Log Groups (2-year retention)

**FrontendStack:**
- 1 S3 Bucket (frontend assets)
- 1 CloudFront Distribution (CDN)
- Origin Access Identity (secure S3 access)

## Security

- All S3 buckets have encryption enabled (AES256)
- S3 buckets block public access
- Lambda functions use least-privilege IAM roles
- CloudFront uses HTTPS redirect
- API Gateway has CORS configured for frontend access

## Customization

### Change Project Name

Edit `infrastructure/app.py`:

```python
project_name = "my-video-search"  # Default: "mvip"
```

### Adjust Lambda Memory/Timeout

Edit `infrastructure/infrastructure/infrastructure_stack.py`:

```python
# Example: Increase memory for caption generation
memory_size=2048,  # Default: 1024
timeout=Duration.seconds(1200),  # Default: 900
```

### Change AWS Region

```bash
export CDK_DEFAULT_REGION=us-west-2
cdk deploy --all
```

## Support

For issues or questions:
1. Check CloudWatch logs: `/aws/lambda/{project-name}-*`
2. Review CloudFormation events in AWS Console
3. Run `cdk doctor` to diagnose CDK issues
4. See main [README.md](../README.md) for architecture details


