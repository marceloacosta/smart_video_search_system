"""
Feature 1.2: Frame Extraction
Extract frames from video at configured intervals (default: 1 frame per 5 seconds)

Triggered by: EventBridge rule when transcription completes
Outputs: Frame images to S3, updates DynamoDB metadata
"""
import json
import os
import subprocess
import tempfile
import urllib.parse
from datetime import datetime
from decimal import Decimal
import boto3

# AWS clients
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

# Environment variables
VIDEOS_BUCKET = os.environ['VIDEOS_BUCKET']
PROCESSED_BUCKET = os.environ['PROCESSED_BUCKET']
METADATA_TABLE = os.environ['METADATA_TABLE']
FRAME_INTERVAL_SECONDS = int(os.environ.get('FRAME_INTERVAL_SECONDS', '5'))
MAX_FRAMES_PER_VIDEO = int(os.environ.get('MAX_FRAMES_PER_VIDEO', '120'))
FRAME_QUALITY = int(os.environ.get('FRAME_QUALITY', '85'))
DEV_MODE = os.environ.get('DEV_MODE', 'false').lower() == 'true'
DEV_DURATION_LIMIT = int(os.environ.get('DEV_DURATION_LIMIT_SECONDS', '30'))

# DynamoDB table
table = dynamodb.Table(METADATA_TABLE)

# For PoC: Use bundled static FFmpeg binary
import re
# FFmpeg binary is bundled in the bin/ directory
FFMPEG_BIN = os.path.join(os.path.dirname(__file__), 'bin', 'ffmpeg')
print(f"Using bundled FFmpeg: {FFMPEG_BIN}")


def get_video_duration_from_ffmpeg_output(ffmpeg_stderr):
    """
    Parse video duration from ffmpeg stderr output
    Format: Duration: 00:01:23.45, ...
    """
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', ffmpeg_stderr)
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        print(f"Parsed video duration: {duration} seconds")
        return duration
    return None


def extract_frames(video_path, output_dir, max_frames=45, quality=85):
    """
    Extract frames evenly distributed across video duration (Kubrick approach)
    
    Unlike fixed fps extraction, this ensures frames are evenly spread across
    the entire video, regardless of length. For a 20-min video with 45 frames,
    we get 1 frame every ~26.7 seconds.
    
    Args:
        video_path: Path to video file
        output_dir: Directory to save frames
        max_frames: Number of frames to extract (default 45, matching Kubrick)
        quality: JPEG quality (1-100, higher = better)
    
    Returns:
        Tuple: (frame_count, duration_seconds)
    """
    try:
        # Step 1: Get video duration first
        duration_cmd = [
            FFMPEG_BIN,
            '-i', video_path,
            '-f', 'null',
            '-'
        ]
        
        duration_result = subprocess.run(duration_cmd, capture_output=True, text=True)
        duration = get_video_duration_from_ffmpeg_output(duration_result.stderr)
        
        if not duration:
            raise ValueError("Could not determine video duration")
        
        print(f"Video duration: {duration:.2f} seconds")
        
        # Step 2: Calculate fps for even distribution
        # Extract exactly max_frames evenly across the entire duration
        fps = max_frames / duration if duration > 0 else 0.2
        frame_interval = duration / max_frames if max_frames > 0 else 5.0
        
        print(f"Extracting {max_frames} frames evenly distributed")
        print(f"  FPS: {fps:.4f}")
        print(f"  Frame interval: ~{frame_interval:.2f} seconds")
        
        # FFmpeg quality scale is inverted: 2 = best, 31 = worst
        ffmpeg_quality = max(2, min(31, 31 - int((quality - 1) * 29 / 99)))
        
        # Step 3: Extract frames
        cmd = [
            FFMPEG_BIN,
            '-i', video_path,
            '-vf', f'fps={fps}',  # Dynamic FPS for even distribution
            '-frames:v', str(max_frames),  # Exact frame count
            '-q:v', str(ffmpeg_quality),  # Quality
            '-f', 'image2',  # Image output format
            f'{output_dir}/frame_%04d.jpg'
        ]
        
        print(f"Command: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Count extracted frames
        frame_files = [f for f in os.listdir(output_dir) if f.startswith('frame_') and f.endswith('.jpg')]
        frame_count = len(frame_files)
        
        print(f"✓ Extracted {frame_count} frames evenly across {duration:.2f} seconds")
        
        return (frame_count, duration)
        
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error: {e.stderr}")
        duration = get_video_duration_from_ffmpeg_output(e.stderr)
        raise


def upload_frames_to_s3(local_dir, video_id):
    """
    Upload extracted frames to S3
    
    Args:
        local_dir: Local directory containing frames
        video_id: Video identifier
    
    Returns:
        S3 prefix where frames are stored
    """
    frames_prefix = f"{video_id}/frames"
    frame_files = sorted([f for f in os.listdir(local_dir) if f.startswith('frame_') and f.endswith('.jpg')])
    
    print(f"Uploading {len(frame_files)} frames to s3://{PROCESSED_BUCKET}/{frames_prefix}/")
    
    for frame_file in frame_files:
        local_path = os.path.join(local_dir, frame_file)
        s3_key = f"{frames_prefix}/{frame_file}"
        
        s3.upload_file(
            local_path,
            PROCESSED_BUCKET,
            s3_key,
            ExtraArgs={'ContentType': 'image/jpeg'}
        )
    
    print(f"Successfully uploaded {len(frame_files)} frames")
    return frames_prefix


def estimate_frame_extraction_cost(frame_count):
    """
    Estimate cost of frame extraction and processing
    
    Costs:
    - Lambda: negligible (included in base cost)
    - S3 storage: $0.023/GB-month (~$0.001 for frames)
    - Future Bedrock Vision: $0.006 per image
    - Future Bedrock Embeddings: $0.0002 per image
    """
    s3_cost = 0.001  # Approximate
    vision_cost = frame_count * 0.006  # Future Feature 1.3
    embeddings_cost = frame_count * 0.0002  # Future Feature 1.5
    
    total = s3_cost + vision_cost + embeddings_cost
    return total


def handler(event, context):
    """
    Handle frame extraction event
    
    Event can be:
    1. S3 upload event (automatic trigger)
    2. EventBridge rule (when Transcribe completes)
    3. Manual invocation with video_id
    """
    print(f"Event: {json.dumps(event)}")
    
    try:
        # Get video_id from event
        if 'Records' in event and len(event['Records']) > 0:
            # S3 event trigger
            import urllib.parse
            record = event['Records'][0]
            key = urllib.parse.unquote_plus(record['s3']['object']['key'])
            video_id = os.path.splitext(os.path.basename(key))[0]
            print(f"Triggered by S3 upload: {key} -> video_id: {video_id}")
        elif 'video_id' in event:
            # Manual invocation
            video_id = event['video_id']
        elif 'detail' in event and 'video_id' in event['detail']:
            # EventBridge event
            video_id = event['detail']['video_id']
        else:
            raise ValueError("No video_id found in event")
        
        print(f"Processing video: {video_id}")
        
        # Get video metadata from DynamoDB
        response = table.get_item(Key={'video_id': video_id})
        
        if 'Item' not in response:
            raise ValueError(f"Video {video_id} not found in metadata table")
        
        video_meta = response['Item']
        # Handle both naming conventions (s3_bucket or bucket)
        bucket = video_meta.get('s3_bucket', video_meta.get('bucket'))
        key = video_meta.get('s3_key', video_meta.get('key'))
        
        # Update status
        table.update_item(
            Key={'video_id': video_id},
            UpdateExpression='SET #status = :status, updated_at = :timestamp',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'extracting_frames',
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        # Create temp directories
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, 'video.mp4')
            frames_dir = os.path.join(temp_dir, 'frames')
            os.makedirs(frames_dir)
            
            # Download video from S3
            print(f"Downloading video from s3://{bucket}/{key}")
            s3.download_file(bucket, key, video_path)
            
            video_size_mb = os.path.getsize(video_path) / (1024 * 1024)
            print(f"Video downloaded: {video_size_mb:.2f} MB")
            
            # Calculate FPS and max frames
            fps = 1.0 / FRAME_INTERVAL_SECONDS
            max_frames = MAX_FRAMES_PER_VIDEO
            
            print(f"Configuration: interval={FRAME_INTERVAL_SECONDS}s, fps={fps}, max_frames={max_frames}")
            
            # Extract frames (this will also give us duration from ffmpeg output)
            frame_count, duration = extract_frames(
                video_path=video_path,
                output_dir=frames_dir,
                max_frames=max_frames,
                quality=FRAME_QUALITY
            )
            
            # Use duration if we got it, otherwise estimate from frames
            if not duration:
                print("Warning: Could not parse duration from ffmpeg output, estimating from frames")
                duration = frame_count * FRAME_INTERVAL_SECONDS
            
            # Upload frames to S3
            frames_prefix = upload_frames_to_s3(frames_dir, video_id)
            
            # Estimate costs
            estimated_cost = estimate_frame_extraction_cost(frame_count)
            
            # Update DynamoDB with results
            table.update_item(
                Key={'video_id': video_id},
                UpdateExpression='''
                    SET 
                        #status = :status,
                        duration_seconds = :duration,
                        frame_count = :frame_count,
                        frames_s3_prefix = :frames_prefix,
                        frame_extraction_cost_estimate = :cost,
                        frame_interval_seconds = :interval,
                        updated_at = :timestamp
                ''',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'ready',  # Simple for PoC, will be more complex with more features
                    ':duration': Decimal(str(round(duration, 2))),
                    ':frame_count': frame_count,
                    ':frames_prefix': frames_prefix,
                    ':cost': Decimal(str(round(estimated_cost, 4))),
                    ':interval': FRAME_INTERVAL_SECONDS,
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
            
            print(f"Frame extraction complete for {video_id}")
            print(f"Extracted {frame_count} frames, estimated cost: ${estimated_cost:.4f}")
            
            # Trigger generate_captions Lambda to create frame descriptions
            try:
                frames_s3_prefix = f"{video_id}/frames"
                lambda_client.invoke(
                    FunctionName='mvip-generate-captions',
                    InvocationType='Event',  # Async invocation
                    Payload=json.dumps({
                        'video_id': video_id,
                        'frames_s3_prefix': frames_s3_prefix
                    })
                )
                print(f"✅ Triggered generate_captions for {video_id}")
            except Exception as e:
                print(f"⚠️  Failed to trigger generate_captions: {e}")
                # Don't fail the whole process if caption generation trigger fails
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'video_id': video_id,
                    'duration_seconds': float(duration),
                    'frame_count': frame_count,
                    'frames_s3_prefix': frames_prefix,
                    'estimated_cost': float(estimated_cost)
                })
            }
    
    except Exception as e:
        print(f"Error processing video: {str(e)}")
        
        # Update DynamoDB with error
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
        
        raise

