"""
MCP Tool: get_full_transcript

Retrieves the complete transcript for a video.

Returns the full transcription with timestamps, speaker labels,
and confidence scores from AWS Transcribe.
"""

import os
import json
import boto3
from typing import Dict, Any

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ.get('METADATA_TABLE', 'mvip-video-metadata'))


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Get the complete transcript for a video.
    
    Input Schema:
        {
            "video_id": "my-video-id",     # Required
            "format": "full"                # Optional: "full" or "text_only"
        }
    
    Output Schema:
        {
            "video_id": "...",
            "transcript_text": "Full text of the transcript...",
            "duration_seconds": 123.45,
            "transcribe_job_name": "...",
            "results": {
                "transcripts": [...],
                "items": [...],        # Only if format="full"
                "speaker_labels": [...] # Only if format="full" and available
            }
        }
    
    Returns:
        HTTP-style response for AgentCore Gateway
    """
    print(f"=== get_full_transcript tool invoked ===")
    print(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Validate required input
        video_id = event.get('video_id')
        if not video_id:
            raise ValueError("video_id is required")
        
        format_type = event.get('format', 'text_only').lower()
        print(f"Fetching transcript for video: {video_id}, format: {format_type}")
        
        # Get video metadata to find transcript S3 location
        response = table.get_item(Key={'video_id': video_id})
        
        if 'Item' not in response:
            return {
                'statusCode': 404,
                'body': json.dumps({
                    'error': f"Video not found: {video_id}"
                })
            }
        
        item = response['Item']
        transcript_s3_key = item.get('transcript_s3_key')
        processed_bucket = item.get('processed_bucket')
        
        if not transcript_s3_key or not processed_bucket:
            return {
                'statusCode': 404,
                'body': json.dumps({
                    'error': f"Transcript not available for video: {video_id}"
                })
            }
        
        print(f"Reading transcript from s3://{processed_bucket}/{transcript_s3_key}")
        
        # Read transcript from S3
        try:
            s3_response = s3.get_object(
                Bucket=processed_bucket,
                Key=transcript_s3_key
            )
            transcript_data = json.loads(s3_response['Body'].read().decode('utf-8'))
        except s3.exceptions.NoSuchKey:
            return {
                'statusCode': 404,
                'body': json.dumps({
                    'error': f"Transcript file not found in S3: {transcript_s3_key}"
                })
            }
        
        # Extract transcript text
        transcripts = transcript_data.get('results', {}).get('transcripts', [])
        transcript_text = transcripts[0].get('transcript', '') if transcripts else ''
        
        print(f"✓ Transcript retrieved: {len(transcript_text)} characters")
        
        # Build response based on format
        result = {
            'video_id': video_id,
            'transcript_text': transcript_text,
            'duration_seconds': float(item.get('duration_seconds', 0)),
            'transcribe_job_name': item.get('transcribe_job_name', '')
        }
        
        # Include full results if requested
        if format_type == 'full':
            result['results'] = transcript_data.get('results', {})
        
        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
    
    except ValueError as e:
        # Input validation error
        error_msg = f"Invalid input: {str(e)}"
        print(f"❌ {error_msg}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': error_msg})
        }
    
    except Exception as e:
        # Unexpected error
        error_msg = f"Internal error: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }


# For local testing
if __name__ == '__main__':
    # Test with a video ID
    test_event = {
        'video_id': 'test-metadata-update',
        'format': 'text_only'
    }
    result = handler(test_event, None)
    print("\n=== Test Result ===")
    print(json.dumps(result, indent=2)[:500] + "...")

