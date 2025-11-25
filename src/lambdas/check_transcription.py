"""
Lambda Function: Check Transcription Status

Purpose: Poll AWS Transcribe to check if transcription is complete,
         then trigger chunk_transcript Lambda to populate Speech KB.

Triggered by: process_video Lambda (async invocation with delay)

Input Event:
{
    "video_id": "test-video",
    "transcribe_job_name": "transcribe-test-video-123456789",
    "attempt": 1,  # Current polling attempt
    "max_attempts": 60  # Max 60 attempts = 30 minutes (30s intervals)
}

Output:
- Triggers chunk_transcript when transcription completes
- Re-invokes itself if still processing (with incremented attempt)
- Fails if max_attempts reached or transcription fails
"""

import json
import os
import boto3
import time
from typing import Dict, Any

# AWS clients
transcribe = boto3.client('transcribe')
lambda_client = boto3.client('lambda')
dynamodb = boto3.resource('dynamodb')

# Environment variables
METADATA_TABLE = os.environ['METADATA_TABLE']
POLL_INTERVAL_SECONDS = int(os.environ.get('POLL_INTERVAL_SECONDS', '30'))
MAX_ATTEMPTS = int(os.environ.get('MAX_ATTEMPTS', '60'))  # 30 minutes max

table = dynamodb.Table(METADATA_TABLE)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Check transcription status and trigger next step in pipeline
    """
    print(f"Event: {json.dumps(event)}")
    
    try:
        # Extract parameters
        video_id = event['video_id']
        transcribe_job_name = event['transcribe_job_name']
        attempt = event.get('attempt', 1)
        max_attempts = event.get('max_attempts', MAX_ATTEMPTS)
        
        print(f"Checking transcription status for {video_id} (attempt {attempt}/{max_attempts})")
        
        # Check transcription job status
        response = transcribe.get_transcription_job(
            TranscriptionJobName=transcribe_job_name
        )
        
        job = response['TranscriptionJob']
        status = job['TranscriptionJobStatus']
        
        print(f"Transcription status: {status}")
        
        if status == 'COMPLETED':
            # Success! Trigger chunk_transcript
            print(f"✅ Transcription complete for {video_id}")
            
            # Update DynamoDB
            table.update_item(
                Key={'video_id': video_id},
                UpdateExpression='SET transcription_status = :status',
                ExpressionAttributeValues={
                    ':status': 'completed'
                }
            )
            
            # Trigger chunk_transcript Lambda
            try:
                lambda_client.invoke(
                    FunctionName='mvip-chunk-transcript',
                    InvocationType='Event',  # Async
                    Payload=json.dumps({'video_id': video_id})
                )
                print(f"✅ Triggered chunk_transcript for {video_id}")
            except Exception as e:
                print(f"⚠️  Failed to trigger chunk_transcript: {e}")
                # Don't fail - transcription is done
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'video_id': video_id,
                    'status': 'completed',
                    'attempt': attempt
                })
            }
        
        elif status == 'FAILED':
            # Transcription failed
            error_msg = f"Transcription failed for {video_id}"
            print(f"❌ {error_msg}")
            
            table.update_item(
                Key={'video_id': video_id},
                UpdateExpression='SET transcription_status = :status, error_message = :error',
                ExpressionAttributeValues={
                    ':status': 'failed',
                    ':error': 'Transcription job failed'
                }
            )
            
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': error_msg,
                    'video_id': video_id
                })
            }
        
        elif status == 'IN_PROGRESS':
            # Still processing - check if we should continue polling
            if attempt >= max_attempts:
                error_msg = f"Transcription timeout for {video_id} after {attempt} attempts"
                print(f"❌ {error_msg}")
                
                table.update_item(
                    Key={'video_id': video_id},
                    UpdateExpression='SET transcription_status = :status, error_message = :error',
                    ExpressionAttributeValues={
                        ':status': 'timeout',
                        ':error': error_msg
                    }
                )
                
                return {
                    'statusCode': 408,
                    'body': json.dumps({
                        'error': error_msg,
                        'video_id': video_id
                    })
                }
            
            # Re-invoke this Lambda after a delay
            print(f"⏳ Transcription still in progress, will check again in {POLL_INTERVAL_SECONDS}s")
            
            # Note: Lambda can't sleep, so we use Step Functions or EventBridge for delays
            # For simplicity in PoC, we'll just re-invoke immediately and rely on
            # Transcribe's eventual completion
            time.sleep(5)  # Small delay to avoid rate limiting
            
            lambda_client.invoke(
                FunctionName=context.function_name,
                InvocationType='Event',  # Async
                Payload=json.dumps({
                    'video_id': video_id,
                    'transcribe_job_name': transcribe_job_name,
                    'attempt': attempt + 1,
                    'max_attempts': max_attempts
                })
            )
            
            return {
                'statusCode': 202,
                'body': json.dumps({
                    'video_id': video_id,
                    'status': 'polling',
                    'attempt': attempt
                })
            }
        
        else:
            # Unknown status
            print(f"⚠️  Unknown transcription status: {status}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': f'Unknown status: {status}',
                    'video_id': video_id
                })
            }
    
    except Exception as e:
        error_msg = f"Error checking transcription: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'body': json.dumps({'error': error_msg})
        }


# For local testing
if __name__ == '__main__':
    test_event = {
        'video_id': 'test-video',
        'transcribe_job_name': 'transcribe-test-video-123456789',
        'attempt': 1
    }
    result = handler(test_event, type('Context', (), {'function_name': 'test-function'})())
    print("\n=== Test Result ===")
    print(json.dumps(result, indent=2))

