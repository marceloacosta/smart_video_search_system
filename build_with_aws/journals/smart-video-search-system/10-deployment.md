# Deployment Guide

**Topic:** Deploying the complete system using AWS CDK  
**Purpose:** Instructions for setting up the environment and deploying the infrastructure

## Prerequisites

1. **AWS Account** with administrator access
2. **AWS CLI** installed and configured
3. **Node.js** (v18+) and **NPM**
4. **Python** (v3.11+)
5. **Docker** running (required for Lambda bundling)
6. **Git**

## Environment Setup

### 1. Clone Repository

```bash
git clone https://github.com/marceloacosta/smart_video_search_system.git
cd smart_video_search_system
```

### 2. Install CDK

```bash
npm install -g aws-cdk
```

### 3. Setup Python Virtual Environment

```bash
cd infrastructure
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Note:** `requirements.txt` should include:
- `aws-cdk-lib`
- `constructs`
- `aws-cdk.aws-bedrock-alpha` (if using alpha constructs)

## Configuration

The system uses `cdk.context.json` or environment variables for configuration.

### Create Context File

Create `infrastructure/cdk.context.json` (do not commit this if it has real IDs):

```json
{
  "project_name": "mvip-video-search",
  "environment": "prod",
  "bedrock_region": "us-east-1"
}
```

### Enable Bedrock Models

Ensure the following models are enabled in AWS Bedrock (us-east-1):
1. **Claude 3 Sonnet** (or 3.5 Sonnet)
2. **Titan Text Embeddings v2**
3. **Titan Multimodal Embeddings G1**

## Deployment Steps

### 1. Bootstrap CDK

If this is your first time using CDK in this region:

```bash
cdk bootstrap aws://{ACCOUNT_ID}/{REGION}
```

### 2. Deploy Infrastructure

```bash
cdk deploy --all
```

**What happens during deploy:**
1. CloudFormation template synthesized
2. S3 buckets created (raw, processed, vectors, website)
3. Lambda layers built (ffmpeg)
4. Lambda functions deployed
5. S3 Vectors index created
6. Bedrock Knowledge Bases created (speech and caption)
7. API Gateway and CloudFront setup

### 3. Post-Deployment Setup

After deployment, the output will show:
- `WebsiteURL`: The CloudFront URL for the frontend
- `ApiEndpoint`: The API Gateway URL
- `UploadBucket`: Name of raw video bucket

## Verification

### 1. Check Frontend
Open `WebsiteURL` in browser. You should see the upload interface.

### 2. Check API
```bash
curl {ApiEndpoint}/videos
```
Should return an empty list `[]` initially.

### 3. Upload Test Video
Upload a short video via the UI and monitor the processing pipeline.

## Cost Estimates

**Fixed Costs:**
- **None** - Fully serverless with no minimum charges

**Variable Costs (per-use):**
- **Lambda**: Pay per request/duration
- **S3 Storage**: ~$0.023 per GB-month
- **S3 Vectors**: ~$0.025 per GB-month + ~$0.0001 per query
- **Bedrock**: Token usage (Input/Output)
- **Transcribe**: $0.024/min

**Example:** 10 videos × 100MB = 1GB = ~$0.05/month storage + usage costs

**Recommendation:**
The system is cost-effective for development and production. All services scale to zero when not in use.

## Cleanup / Destruction

To remove all resources and stop billing:

```bash
# 1. Empty S3 Buckets (CDK won't delete non-empty buckets by default)
aws s3 rm s3://{raw-bucket} --recursive
aws s3 rm s3://{processed-bucket} --recursive
aws s3 rm s3://{website-bucket} --recursive

# 2. Destroy Stack
cdk destroy --all
```

## Troubleshooting Deployment

### Issue: Docker Error
**Error:** `docker: command not found` or permission denied.
**Fix:** Ensure Docker Desktop is running and user has permission.

### Issue: Bedrock Access Denied
**Error:** `AccessDeniedException` when invoking models.
**Fix:** Go to AWS Console > Bedrock > Model access, and request access to Claude and Titan models.

### Issue: S3 Vectors Permissions
**Error:** Lambda cannot access S3 Vectors.
**Fix:** Ensure the Lambda execution role has permissions for `s3vectors:PutVectors` and `s3vectors:QueryVectors`.

## CI/CD (Future)

- **GitHub Actions**: Pipeline to run `cdk deploy` on merge to main.
- **Linting**: `flake8` for Python, `eslint` for frontend.
- **Testing**: `pytest` for unit/integration tests.
