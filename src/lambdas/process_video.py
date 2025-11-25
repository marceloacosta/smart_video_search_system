"""
Feature 1.1: Video Upload + Transcription
Triggered by S3 upload, extracts audio and starts AWS Transcribe job

Metadata Schema (aligned with Kubrick original):
- video_id: Primary key (derived from filename)
- title: Human-readable title (from filename)
- upload_timestamp: When video was uploaded
- duration_seconds: Video duration (extracted with ffprobe in future)
- size_bytes: File size
- status: Processing state (uploaded, transcribing, ready, error)
- s3_bucket: Raw video bucket
- s3_key: Raw video key
- processed_bucket: Processed data bucket
- transcript_s3_key: Path to transcript
- transcribe_job_name: AWS Transcribe job identifier
- frame_count: Number of extracted frames (added in Feature 1.2)
- frames_s3_prefix: Path to frames folder (added in Feature 1.2)
- speaker_count: Number of speakers (from Transcribe diarization)
- detected_objects: List of objects (from Rekognition in Feature 1.3)
- detected_scenes: List of scenes (from Rekognition in Feature 1.3)
- processing_cost_estimate: Estimated processing cost
- created_at: First creation timestamp
- updated_at: Last update timestamp
"""
import json
import os
import urllib.parse
from datetime import datetime
from decimal import Decimal
import boto3

# AWS clients
s3 = boto3.client('s3')
transcribe = boto3.client('transcribe')
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

# Environment variables
VIDEOS_BUCKET = os.environ['VIDEOS_BUCKET']
PROCESSED_BUCKET = os.environ['PROCESSED_BUCKET']
METADATA_TABLE = os.environ['METADATA_TABLE']

# DynamoDB table
table = dynamodb.Table(METADATA_TABLE)


def extract_title_from_filename(filename):
    """
    Extract a human-readable title from filename
    Example: "youtube-short-video.mp4" -> "Youtube Short Video"
    """
    # Remove extension
    name = filename.rsplit('.', 1)[0]
    # Replace separators with spaces
    name = name.replace('-', ' ').replace('_', ' ')
    # Title case
    return name.title()


def handler(event, context):
    """
    Handle S3 upload event:
    1. Extract video metadata from S3
    2. Start AWS Transcribe job
    3. Store metadata in DynamoDB
    """
    print(f"Event: {json.dumps(event)}")
    
    try:
        # Get S3 event details
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        
        print(f"Processing video: s3://{bucket}/{key}")
        
        # Extract video_id from filename
        video_id = os.path.splitext(os.path.basename(key))[0]
        
        # Check if already processed (cost control: don't reprocess)
        if is_already_processed(video_id):
            print(f"Video {video_id} already processed. Skipping.")
            return {
                'statusCode': 200,
                'body': json.dumps(f'Video {video_id} already processed')
            }
        
        # Get video metadata from S3
        head_response = s3.head_object(Bucket=bucket, Key=key)
        size_bytes = head_response['ContentLength']
        last_modified = head_response['LastModified']
        
        print(f"Video size: {size_bytes} bytes")
        
        # Start transcription job
        # Sanitize video_id for Transcribe job name (only alphanumeric, dots, dashes, underscores)
        safe_video_id = video_id.replace(' ', '_').replace('(', '').replace(')', '').replace('[', '').replace(']', '')
        transcript_job_name = f"transcribe-{safe_video_id}-{int(datetime.now().timestamp())}"
        transcript_output_key = f"{video_id}/transcript.json"
        
        print(f"Starting transcription job: {transcript_job_name}")
        
        transcribe.start_transcription_job(
            TranscriptionJobName=transcript_job_name,
            Media={
                'MediaFileUri': f's3://{bucket}/{key}'
            },
            MediaFormat='mp4',  # or detect from extension
            LanguageCode='en-US',
            
            # Output to processed bucket
            OutputBucketName=PROCESSED_BUCKET,
            OutputKey=transcript_output_key,
            
            # Cost control: Enable speaker diarization
            Settings={
                'ShowSpeakerLabels': True,
                'MaxSpeakerLabels': 5  # Limit speakers
            }
        )
        
        print(f"Transcription job started: {transcript_job_name}")
        
        # Store metadata in DynamoDB (aligned with Kubrick schema)
        timestamp = datetime.utcnow().isoformat()
        title = extract_title_from_filename(video_id)
        estimated_cost = estimate_transcription_cost(size_bytes)
        
        table.put_item(
            Item={
                # Core identification
                'video_id': video_id,
                'title': title,
                
                # S3 locations
                's3_bucket': bucket,
                's3_key': key,
                'processed_bucket': PROCESSED_BUCKET,
                'transcript_s3_key': transcript_output_key,
                
                # Video properties
                'size_bytes': Decimal(str(size_bytes)),
                # 'duration_seconds': will be added in Feature 1.2
                # 'frame_count': will be added in Feature 1.2
                # 'frames_s3_prefix': will be added in Feature 1.2
                
                # Processing status
                'status': 'transcribing',
                'transcribe_job_name': transcript_job_name,
                # 'speaker_count': will be added when Transcribe completes
                
                # Labels and analysis (added in Feature 1.3+)
                # 'detected_objects': [],
                # 'detected_scenes': [],
                # 'topics': [],
                
                # Timestamps
                'upload_timestamp': timestamp,
                'last_modified': last_modified.isoformat(),
                'created_at': timestamp,
                'updated_at': timestamp,
                
                # Cost tracking
                'processing_cost_estimate': Decimal(str(round(estimated_cost, 4)))
            }
        )
        
        print(f"Metadata stored in DynamoDB for video_id: {video_id} (title: {title})")
        print(f"Estimated transcription cost: ${estimated_cost:.4f}")
        
        # Trigger extract_frames Lambda asynchronously to get duration/frames
        try:
            lambda_client.invoke(
                FunctionName='mvip-extract-frames',
                InvocationType='Event',  # Async invocation
                Payload=json.dumps({'video_id': video_id})
            )
            print(f"✅ Triggered extract_frames for {video_id}")
        except Exception as e:
            print(f"⚠️  Failed to trigger extract_frames: {e}")
            # Don't fail the whole process if frame extraction trigger fails
        
        # Trigger check_transcription Lambda to poll for completion and trigger speech indexing
        try:
            lambda_client.invoke(
                FunctionName='mvip-check-transcription',
                InvocationType='Event',  # Async invocation
                Payload=json.dumps({
                    'video_id': video_id,
                    'transcribe_job_name': transcript_job_name,
                    'attempt': 1
                })
            )
            print(f"✅ Triggered check_transcription poller for {video_id}")
        except Exception as e:
            print(f"⚠️  Failed to trigger check_transcription: {e}")
            # Don't fail - transcription will still happen, just won't auto-index
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Started processing video {video_id}',
                'video_id': video_id,
                'transcribe_job': transcript_job_name,
                'estimated_cost': estimated_cost
            })
        }
        
    except Exception as e:
        print(f"Error processing video: {str(e)}")
        
        # Store error in DynamoDB if we have video_id
        try:
            if 'video_id' in locals():
                table.update_item(
                    Key={'video_id': video_id},
                    UpdateExpression='SET #status = :status, error_message = :error, updated_at = :timestamp',
                    ExpressionAttributeNames={'#status': 'status'},
                    ExpressionAttributeValues={
                        ':status': 'error',
                        ':error': str(e),
                        ':timestamp': datetime.utcnow().isoformat()
                    }
                )
        except:
            pass
        
        raise e


def is_already_processed(video_id):
    """
    Check if video was already processed (cost control)
    Returns True if status is 'ready' or 'transcribing'
    """
    try:
        response = table.get_item(Key={'video_id': video_id})
        if 'Item' in response:
            status = response['Item'].get('status')
            if status in ['ready', 'transcribing', 'processing']:
                return True
    except:
        pass
    return False


def estimate_transcription_cost(size_bytes, bitrate_kbps=128):
    """
    Estimate transcription cost based on file size
    AWS Transcribe: $0.024 per minute
    Assumes ~128 kbps audio bitrate
    """
    # Estimate duration in seconds
    bytes_per_second = (bitrate_kbps * 1000) / 8
    duration_seconds = size_bytes / bytes_per_second
    duration_minutes = duration_seconds / 60
    
    # Transcribe cost
    cost_per_minute = 0.024
    estimated_cost = duration_minutes * cost_per_minute
    
    return estimated_cost

