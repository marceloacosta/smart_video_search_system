"""
Video Delete Lambda
Handles comprehensive deletion of videos from the system:
1. Delete raw video from S3
2. Delete all processed data (transcripts, frames, embeddings)
3. Delete metadata from DynamoDB
4. Trigger Knowledge Base sync to remove indexed data
"""

import json
import os
import boto3
from typing import Dict, Any

# AWS clients
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
bedrock_agent = boto3.client('bedrock-agent')
s3vectors = boto3.client('s3vectors')

# Environment variables
VIDEOS_BUCKET = os.environ.get('VIDEOS_BUCKET')
PROCESSED_BUCKET = os.environ.get('PROCESSED_BUCKET')
METADATA_TABLE = os.environ.get('METADATA_TABLE')
SPEECH_KB_ID = os.environ.get('SPEECH_KB_ID')  # mvip-speech-index
CAPTION_KB_ID = os.environ.get('CAPTION_KB_ID')  # mvip-caption-index
S3_VECTOR_BUCKET = os.environ.get('S3_VECTOR_BUCKET', 'mvip-image-vectors')
S3_VECTOR_INDEX = os.environ.get('S3_VECTOR_INDEX', 'image-embeddings')

table = dynamodb.Table(METADATA_TABLE)


def delete_s3_folder(bucket: str, prefix: str) -> int:
    """
    Recursively delete all objects under a prefix in S3.
    Returns count of deleted objects.
    """
    deleted_count = 0
    continuation_token = None
    
    while True:
        list_kwargs = {
            'Bucket': bucket,
            'Prefix': prefix
        }
        if continuation_token:
            list_kwargs['ContinuationToken'] = continuation_token
        
        response = s3.list_objects_v2(**list_kwargs)
        
        if 'Contents' not in response:
            break
        
        # Delete objects in batches of 1000 (S3 limit)
        objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]
        
        if objects_to_delete:
            s3.delete_objects(
                Bucket=bucket,
                Delete={'Objects': objects_to_delete}
            )
            deleted_count += len(objects_to_delete)
            print(f"  Deleted {len(objects_to_delete)} objects from s3://{bucket}/{prefix}")
        
        # Check if there are more objects to delete
        if not response.get('IsTruncated'):
            break
        continuation_token = response.get('NextContinuationToken')
    
    return deleted_count


def delete_image_vectors(video_id: str) -> int:
    """
    Delete all image vectors for a video from S3 Vectors.
    Returns count of deleted vectors.
    """
    try:
        print(f"  Listing vectors for {video_id}...")
        
        # List all vectors for this video
        vector_keys = []
        next_token = None
        
        while True:
            list_kwargs = {
                'vectorBucketName': S3_VECTOR_BUCKET,
                'indexName': S3_VECTOR_INDEX,
                'maxResults': 100
            }
            if next_token:
                list_kwargs['nextToken'] = next_token
            
            response = s3vectors.list_vectors(**list_kwargs)
            
            # Filter vectors by video_id prefix
            for vector in response.get('vectors', []):
                key = vector.get('key', '')
                if key.startswith(f"{video_id}_frame_"):
                    vector_keys.append(key)
            
            next_token = response.get('nextToken')
            if not next_token:
                break
        
        if not vector_keys:
            print(f"  ℹ️  No image vectors found for {video_id}")
            return 0
        
        print(f"  Found {len(vector_keys)} image vectors to delete")
        
        # Delete vectors in batches (max 100 per request)
        deleted_count = 0
        batch_size = 100
        
        for i in range(0, len(vector_keys), batch_size):
            batch = vector_keys[i:i+batch_size]
            s3vectors.delete_vectors(
                vectorBucketName=S3_VECTOR_BUCKET,
                indexName=S3_VECTOR_INDEX,
                keys=batch
            )
            deleted_count += len(batch)
            print(f"  ✅ Deleted batch of {len(batch)} vectors")
        
        print(f"  ✅ Deleted {deleted_count} image vectors total")
        return deleted_count
        
    except Exception as e:
        print(f"  ⚠️  Error deleting image vectors: {e}")
        return 0


def sync_knowledge_base(kb_id: str, kb_name: str) -> bool:
    """
    Trigger a Knowledge Base sync to remove deleted data from the index.
    Returns True if sync started successfully.
    """
    if not kb_id:
        print(f"  ⚠️  No KB ID provided for {kb_name}, skipping sync")
        return False
    
    try:
        # Get the data source ID (assuming single data source per KB)
        data_sources = bedrock_agent.list_data_sources(
            knowledgeBaseId=kb_id
        )
        
        if not data_sources.get('dataSourceSummaries'):
            print(f"  ⚠️  No data sources found for {kb_name} KB")
            return False
        
        data_source_id = data_sources['dataSourceSummaries'][0]['dataSourceId']
        
        # Start ingestion job (sync)
        response = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id
        )
        
        job_id = response['ingestionJob']['ingestionJobId']
        print(f"  ✅ Started sync for {kb_name} KB (job: {job_id})")
        return True
        
    except Exception as e:
        print(f"  ❌ Error syncing {kb_name} KB: {e}")
        return False


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Delete a video and all associated data.
    
    Input (API Gateway event):
        {
            "pathParameters": {
                "video_id": "test-metadata-update"
            }
        }
    
    Returns:
        {
            "statusCode": 200,
            "body": {
                "message": "Video deleted successfully",
                "deleted": {
                    "raw_video": true,
                    "processed_files": 45,
                    "metadata": true,
                    "speech_kb_synced": true,
                    "caption_kb_synced": true
                }
            }
        }
    """
    print(f"=== delete_video Lambda invoked ===")
    print(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Extract video_id from path parameters
        video_id = event.get('pathParameters', {}).get('video_id')
        
        if not video_id:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'video_id is required'})
            }
        
        print(f"\n🗑️  Deleting video: {video_id}")
        
        deletion_summary = {
            'raw_video': False,
            'processed_files': 0,
            'metadata': False,
            'image_vectors': 0,
            'speech_kb_synced': False,
            'caption_kb_synced': False
        }
        
        # 1. Delete raw video from S3
        print(f"\n1️⃣  Deleting raw video...")
        try:
            raw_key = f"{video_id}.mp4"
            s3.delete_object(Bucket=VIDEOS_BUCKET, Key=raw_key)
            print(f"  ✅ Deleted s3://{VIDEOS_BUCKET}/{raw_key}")
            deletion_summary['raw_video'] = True
        except Exception as e:
            print(f"  ⚠️  Error deleting raw video: {e}")
        
        # 2. Delete all processed data (transcripts, frames, embeddings)
        print(f"\n2️⃣  Deleting processed data...")
        try:
            processed_prefix = f"{video_id}/"
            deleted_count = delete_s3_folder(PROCESSED_BUCKET, processed_prefix)
            print(f"  ✅ Deleted {deleted_count} processed files")
            deletion_summary['processed_files'] = deleted_count
        except Exception as e:
            print(f"  ⚠️  Error deleting processed data: {e}")
        
        # 3. Delete metadata from DynamoDB
        print(f"\n3️⃣  Deleting metadata...")
        try:
            table.delete_item(Key={'video_id': video_id})
            print(f"  ✅ Deleted metadata for {video_id}")
            deletion_summary['metadata'] = True
        except Exception as e:
            print(f"  ⚠️  Error deleting metadata: {e}")
        
        # 4. Delete image vectors from S3 Vectors
        print(f"\n4️⃣  Deleting image vectors...")
        deletion_summary['image_vectors'] = delete_image_vectors(video_id)
        
        # 5. Sync Knowledge Bases to remove indexed data
        print(f"\n5️⃣  Syncing Knowledge Bases...")
        deletion_summary['speech_kb_synced'] = sync_knowledge_base(SPEECH_KB_ID, "Speech")
        deletion_summary['caption_kb_synced'] = sync_knowledge_base(CAPTION_KB_ID, "Caption")
        
        print(f"\n✅ Video {video_id} deleted successfully")
        print(f"Summary: {json.dumps(deletion_summary, indent=2)}")
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'message': f'Video {video_id} deleted successfully',
                'deleted': deletion_summary
            })
        }
    
    except Exception as e:
        error_msg = f"Error deleting video: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': error_msg})
        }


# For local testing
if __name__ == '__main__':
    test_event = {
        'pathParameters': {
            'video_id': 'test-video'
        }
    }
    result = handler(test_event, None)
    print("\n=== Test Result ===")
    print(json.dumps(result, indent=2))

