"""
Lambda Function: Chunk Transcript for Speech Index

Purpose: Parse AWS Transcribe output and create 10-second chunks with embeddings
         Matches Kubrick's approach: audio_chunks view with start_time_sec, end_time_sec

Triggered by: Manual invocation or EventBridge rule (after transcription completes)

Input Event:
{
    "video_id": "test-video",
    "transcript_s3_key": "test-video/transcript.json"
}

Output:
- Transcript chunks stored in S3 for Speech Index (Bedrock KB #1)
- DynamoDB updated with chunk_count, speech_index_s3_prefix
- Status updated to "speech_index_ready"

Feature: 1.4 - Speech Index (10-second audio chunks)
"""

import json
import os
import boto3
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Tuple

# AWS clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
bedrock_agent = boto3.client('bedrock-agent')
dynamodb = boto3.resource('dynamodb')

# Environment variables
PROCESSED_BUCKET = os.environ['PROCESSED_BUCKET']
METADATA_TABLE = os.environ['METADATA_TABLE']
BEDROCK_EMBEDDING_MODEL = os.environ.get('BEDROCK_EMBEDDING_MODEL', 'amazon.titan-embed-text-v2:0')
SPEECH_KB_ID = os.environ.get('SPEECH_KB_ID')  # Speech Knowledge Base ID

# Kubrick-aligned chunking parameters
CHUNK_DURATION_SECONDS = 10  # 10-second chunks
CHUNK_OVERLAP_SECONDS = 1    # 1-second overlap between chunks

# Cost tracking
COST_PER_EMBEDDING = 0.0001  # Approximate cost per Titan Text embedding


def parse_transcribe_output(transcript_json: dict) -> List[Dict]:
    """
    Parse AWS Transcribe output to extract words with timestamps
    
    Args:
        transcript_json: Transcribe output JSON
    
    Returns:
        List of word dictionaries with start_time, end_time, content
    """
    words = []
    
    # Transcribe format: results.items contains words with timestamps
    if 'results' in transcript_json and 'items' in transcript_json['results']:
        for item in transcript_json['results']['items']:
            if item['type'] == 'pronunciation':
                word = {
                    'content': item['alternatives'][0]['content'],
                    'start_time': float(item['start_time']),
                    'end_time': float(item['end_time']),
                    'confidence': float(item['alternatives'][0].get('confidence', 1.0))
                }
                words.append(word)
            elif item['type'] == 'punctuation':
                # Attach punctuation to previous word
                if words:
                    words[-1]['content'] += item['alternatives'][0]['content']
    
    return words


def create_chunks(words: List[Dict], chunk_duration: float = 10.0, overlap: float = 1.0) -> List[Dict]:
    """
    Create 10-second chunks with 1-second overlap (Kubrick approach)
    
    Args:
        words: List of words with timestamps
        chunk_duration: Duration of each chunk in seconds (default 10)
        overlap: Overlap between chunks in seconds (default 1)
    
    Returns:
        List of chunk dictionaries
    """
    if not words:
        return []
    
    chunks = []
    chunk_index = 0
    
    # Start from the beginning
    current_start = 0.0
    
    while True:
        chunk_end = current_start + chunk_duration
        
        # Find words in this time window
        chunk_words = []
        for word in words:
            word_start = word['start_time']
            word_end = word['end_time']
            
            # Include word if it starts within chunk or overlaps with chunk
            if word_start < chunk_end and word_end > current_start:
                chunk_words.append(word)
        
        if not chunk_words:
            # No more words to process
            break
        
        # Create chunk text
        chunk_text = ' '.join(w['content'] for w in chunk_words)
        
        # Calculate actual start and end times from words
        actual_start = chunk_words[0]['start_time']
        actual_end = chunk_words[-1]['end_time']
        
        chunk = {
            'chunk_index': chunk_index,
            'chunk_text': chunk_text,
            'start_time_sec': actual_start,
            'end_time_sec': min(actual_end, current_start + chunk_duration),
            'word_count': len(chunk_words),
            'duration': min(actual_end, current_start + chunk_duration) - actual_start
        }
        
        chunks.append(chunk)
        chunk_index += 1
        
        # Move to next chunk with overlap
        # Next chunk starts (chunk_duration - overlap) seconds later
        current_start += (chunk_duration - overlap)
        
        # Stop if we've passed the last word
        if current_start >= words[-1]['end_time']:
            break
    
    print(f"Created {len(chunks)} chunks from {len(words)} words")
    return chunks


def generate_embedding(text: str) -> List[float]:
    """
    Generate embedding using Bedrock Titan Text Embeddings
    
    Args:
        text: Text to embed
    
    Returns:
        Embedding vector (list of floats)
    """
    try:
        request_body = {
            "inputText": text
        }
        
        response = bedrock_runtime.invoke_model(
            modelId=BEDROCK_EMBEDDING_MODEL,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response['body'].read())
        embedding = response_body.get('embedding', [])
        
        return embedding
        
    except Exception as e:
        print(f"Error generating embedding: {str(e)}")
        return []


def handler(event, context):
    """
    Lambda handler: Chunk transcript and create speech index
    """
    print(f"Event: {json.dumps(event)}")
    
    # Parse event
    video_id = event['video_id']
    transcript_s3_key = event.get('transcript_s3_key')
    
    # If transcript_s3_key not provided, construct it
    if not transcript_s3_key:
        # Get from DynamoDB
        table = dynamodb.Table(METADATA_TABLE)
        response = table.get_item(Key={'video_id': video_id})
        
        if 'Item' not in response:
            raise ValueError(f"Video {video_id} not found in metadata table")
        
        video_metadata = response['Item']
        transcript_s3_key = video_metadata.get('transcript_s3_key')
        
        if not transcript_s3_key:
            raise ValueError(f"No transcript found for video {video_id}")
    
    print(f"Processing transcript: s3://{PROCESSED_BUCKET}/{transcript_s3_key}")
    
    # Step 1: Download transcript from S3
    response = s3_client.get_object(Bucket=PROCESSED_BUCKET, Key=transcript_s3_key)
    transcript_json = json.loads(response['Body'].read())
    
    # Step 2: Parse Transcribe output to get words with timestamps
    print("Parsing Transcribe output...")
    words = parse_transcribe_output(transcript_json)
    print(f"Extracted {len(words)} words from transcript")
    
    if not words:
        raise ValueError("No words found in transcript")
    
    # Step 3: Create 10-second chunks with 1-second overlap (Kubrick approach)
    print(f"Creating chunks: {CHUNK_DURATION_SECONDS}s duration, {CHUNK_OVERLAP_SECONDS}s overlap")
    chunks = create_chunks(
        words=words,
        chunk_duration=CHUNK_DURATION_SECONDS,
        overlap=CHUNK_OVERLAP_SECONDS
    )
    
    if not chunks:
        raise ValueError("No chunks created from transcript")
    
    print(f"Created {len(chunks)} chunks")
    
    # Step 4: Generate embeddings and create speech index documents
    print("Generating embeddings for chunks...")
    speech_index_docs = []
    
    for chunk in chunks:
        # Generate embedding
        embedding = generate_embedding(chunk['chunk_text'])
        
        if not embedding:
            print(f"Warning: Failed to generate embedding for chunk {chunk['chunk_index']}")
            continue
        
        # Create speech index document (for Bedrock KB #1)
        # NOTE: Bedrock KB expects "text" field for content, not "chunk_text"
        doc = {
            'video_id': video_id,
            'chunk_id': f"{video_id}_chunk_{chunk['chunk_index']:04d}",
            'chunk_index': chunk['chunk_index'],
            'text': chunk['chunk_text'],  # Bedrock KB expects "text" field
            'chunk_text': chunk['chunk_text'],  # Keep for backward compat
            'start_time_sec': chunk['start_time_sec'],
            'end_time_sec': chunk['end_time_sec'],
            'duration': chunk['duration'],
            'word_count': chunk['word_count'],
            'embedding': embedding,  # Vector for similarity search
            'embedding_dimension': len(embedding),
            'metadata': {
                'generated_at': datetime.utcnow().isoformat(),
                'embedding_model': BEDROCK_EMBEDDING_MODEL
            }
        }
        
        speech_index_docs.append(doc)
        
        print(f"✓ Chunk {chunk['chunk_index'] + 1}/{len(chunks)}: {chunk['start_time_sec']:.1f}s - {chunk['end_time_sec']:.1f}s ({chunk['word_count']} words)")
    
    print(f"Generated {len(speech_index_docs)} speech index documents")
    
    # Step 5: Upload speech index documents to S3
    speech_index_prefix = f"{video_id}/speech_index"
    
    for doc in speech_index_docs:
        doc_key = f"{speech_index_prefix}/chunk_{doc['chunk_index']:04d}.json"
        
        # Create a clean copy WITHOUT embeddings for Bedrock KB
        # Bedrock KB generates its own embeddings from the "text" field
        clean_doc = {k: v for k, v in doc.items() if k not in ['embedding', 'embedding_dimension']}
        
        s3_client.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=doc_key,
            Body=json.dumps(clean_doc, indent=2),
            ContentType='application/json'
        )
    
    print(f"✓ Uploaded {len(speech_index_docs)} documents to s3://{PROCESSED_BUCKET}/{speech_index_prefix}/")
    
    # Step 5.5: Trigger Bedrock KB ingestion to sync new documents
    try:
        print(f"Triggering Bedrock KB ingestion for Speech Index (KB: {SPEECH_KB_ID})...")
        
        # Get the data source ID for this KB
        data_sources_response = bedrock_agent.list_data_sources(knowledgeBaseId=SPEECH_KB_ID)
        data_source_id = data_sources_response['dataSourceSummaries'][0]['dataSourceId']
        
        # Start ingestion job
        ingestion_response = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=SPEECH_KB_ID,
            dataSourceId=data_source_id
        )
        
        ingestion_job_id = ingestion_response['ingestionJob']['ingestionJobId']
        print(f"✅ Started KB ingestion job: {ingestion_job_id}")
        print(f"   Speech search will be available in ~2 minutes")
    except Exception as e:
        print(f"⚠️  Failed to trigger KB ingestion: {e}")
        print(f"   Speech index uploaded but may need manual KB sync")
    
    # Step 6: Update DynamoDB with speech index metadata
    estimated_cost = len(speech_index_docs) * COST_PER_EMBEDDING
    
    table = dynamodb.Table(METADATA_TABLE)
    table.update_item(
        Key={'video_id': video_id},
        UpdateExpression="""
            SET #status = :status,
                chunk_count = :chunk_count,
                speech_index_s3_prefix = :speech_prefix,
                total_words = :total_words,
                processing_cost_estimate = processing_cost_estimate + :embedding_cost,
                updated_at = :timestamp
        """,
        ExpressionAttributeNames={
            '#status': 'status'
        },
        ExpressionAttributeValues={
            ':status': 'speech_index_ready',
            ':chunk_count': len(speech_index_docs),
            ':speech_prefix': speech_index_prefix,
            ':total_words': len(words),
            ':embedding_cost': Decimal(str(round(estimated_cost, 4))),
            ':timestamp': datetime.utcnow().isoformat()
        }
    )
    
    print(f"✓ DynamoDB updated: status=speech_index_ready, chunk_count={len(speech_index_docs)}")
    print(f"✓ Estimated cost: ${estimated_cost:.4f}")
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'video_id': video_id,
            'chunk_count': len(speech_index_docs),
            'total_words': len(words),
            'speech_index_s3_prefix': speech_index_prefix,
            'estimated_cost': round(estimated_cost, 4)
        })
    }

