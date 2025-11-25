#!/usr/bin/env python3
"""
Illustrative comparison: Amazon Rekognition vs Claude Vision for frame analysis
This is a standalone example showing how Rekognition could be used for video frame captioning.
"""

import boto3
import json
from typing import Dict, Any

# Initialize AWS clients
s3_client = boto3.client('s3')
rekognition_client = boto3.client('rekognition', region_name='us-east-1')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')


def analyze_with_rekognition(bucket: str, key: str) -> Dict[str, Any]:
    """
    Use Amazon Rekognition to detect labels, text, and objects in an image.
    
    Rekognition provides:
    - DetectLabels: Objects, scenes, activities, concepts (e.g., "Person", "Car", "Beach")
    - DetectText: Text in images (OCR)
    - DetectFaces: Face detection and analysis
    - DetectModerationLabels: Content moderation
    """
    print(f"\n{'='*60}")
    print("AMAZON REKOGNITION ANALYSIS")
    print(f"{'='*60}\n")
    
    results = {}
    
    # 1. Detect Labels (objects, scenes, concepts)
    print("🔍 Detecting Labels...")
    labels_response = rekognition_client.detect_labels(
        Image={'S3Object': {'Bucket': bucket, 'Name': key}},
        MaxLabels=10,
        MinConfidence=75
    )
    
    results['labels'] = []
    for label in labels_response['Labels']:
        results['labels'].append({
            'name': label['Name'],
            'confidence': round(label['Confidence'], 2),
            'categories': [cat['Name'] for cat in label.get('Categories', [])]
        })
    
    print(f"Found {len(results['labels'])} labels:")
    for label in results['labels'][:5]:
        print(f"  • {label['name']} ({label['confidence']}%)")
    
    # 2. Detect Text (OCR)
    print("\n📝 Detecting Text...")
    try:
        text_response = rekognition_client.detect_text(
            Image={'S3Object': {'Bucket': bucket, 'Name': key}}
        )
        
        results['text_detections'] = []
        for text in text_response['TextDetections']:
            if text['Type'] == 'LINE':  # Only get lines, not individual words
                results['text_detections'].append({
                    'text': text['DetectedText'],
                    'confidence': round(text['Confidence'], 2)
                })
        
        if results['text_detections']:
            print(f"Found {len(results['text_detections'])} text lines:")
            for text in results['text_detections'][:3]:
                print(f"  • \"{text['text']}\" ({text['confidence']}%)")
        else:
            print("  No text detected")
    except Exception as e:
        print(f"  Text detection error: {str(e)}")
        results['text_detections'] = []
    
    # 3. Detect Faces
    print("\n👤 Detecting Faces...")
    try:
        faces_response = rekognition_client.detect_faces(
            Image={'S3Object': {'Bucket': bucket, 'Name': key}},
            Attributes=['ALL']
        )
        
        results['faces'] = []
        for face in faces_response['FaceDetails']:
            results['faces'].append({
                'age_range': f"{face['AgeRange']['Low']}-{face['AgeRange']['High']}",
                'gender': face['Gender']['Value'],
                'emotions': sorted(
                    face['Emotions'],
                    key=lambda x: x['Confidence'],
                    reverse=True
                )[:3],
                'confidence': round(face['Confidence'], 2)
            })
        
        if results['faces']:
            print(f"Found {len(results['faces'])} face(s):")
            for i, face in enumerate(results['faces'], 1):
                emotions = ', '.join([e['Type'] for e in face['emotions']])
                print(f"  • Face {i}: {face['gender']}, Age {face['age_range']}, Emotions: {emotions}")
        else:
            print("  No faces detected")
    except Exception as e:
        print(f"  Face detection error: {str(e)}")
        results['faces'] = []
    
    return results


def analyze_with_claude(bucket: str, key: str) -> str:
    """
    Use Claude Vision to generate a natural language description.
    
    Claude provides:
    - Natural language descriptions
    - Scene understanding
    - Contextual reasoning
    - Creative interpretation
    """
    print(f"\n{'='*60}")
    print("CLAUDE VISION ANALYSIS")
    print(f"{'='*60}\n")
    
    # Get the image from S3
    s3_response = s3_client.get_object(Bucket=bucket, Key=key)
    image_bytes = s3_response['Body'].read()
    
    import base64
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    # Get image extension for media type
    extension = key.split('.')[-1].lower()
    media_type_map = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'webp': 'image/webp'
    }
    media_type = media_type_map.get(extension, 'image/jpeg')
    
    print("🤖 Generating caption...")
    response = bedrock_runtime.invoke_model(
        modelId='us.anthropic.claude-3-5-sonnet-20241022-v2:0',
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": "Describe this image in 2-3 sentences, focusing on the main subjects, actions, and context."
                    }
                ]
            }]
        })
    )
    
    result = json.loads(response['body'].read())
    caption = result['content'][0]['text']
    
    print(f"\n{caption}\n")
    
    return caption


def generate_comparison_summary(rekognition_results: Dict, claude_caption: str) -> str:
    """Generate a comparison summary."""
    summary = f"""
{'='*60}
COMPARISON SUMMARY
{'='*60}

📊 REKOGNITION STRENGTHS:
• Structured, machine-readable output
• Confidence scores for each detection
• Specialized capabilities: text (OCR), faces, content moderation
• Consistent, predictable format
• Good for filtering, searching by specific attributes
• Example: "Find all frames with text", "Show frames with people smiling"

🎨 CLAUDE VISION STRENGTHS:
• Natural language descriptions
• Contextual understanding and reasoning
• Captures mood, style, and relationships
• Better for semantic search: "Find dramatic moments", "Show celebration scenes"
• More human-like interpretation
• Connects visual elements into a narrative

💡 IDEAL USE CASES:

Rekognition:
- Content moderation and safety
- Text extraction (signs, documents, subtitles)
- Face recognition and demographics
- Precise object detection
- Compliance and automated filtering

Claude Vision:
- Semantic video search
- Content summarization
- Creative applications
- Understanding scene context
- Natural language queries

🔄 HYBRID APPROACH (Best of Both):
Use Rekognition for structured metadata + Claude for searchable descriptions
This gives you both precision AND flexibility!
"""
    return summary


def main():
    """Run the comparison test."""
    # Configuration - Update with your bucket name
    BUCKET = os.environ.get('PROCESSED_BUCKET', 'your-processed-bucket-name')
    
    # Try to find an existing frame from a processed video
    # Let's use a frame from one of the uploaded videos
    VIDEO_ID = 'yellowstone-trailer'  # Change this to any uploaded video
    FRAME_KEY = f'{VIDEO_ID}/frames/frame_0030.jpg'  # Frame at ~5 seconds (assuming 6fps)
    
    print(f"""
{'='*60}
REKOGNITION vs CLAUDE VISION
Illustrative Comparison for Video Frame Analysis
{'='*60}

Testing with:
• Bucket: {BUCKET}
• Frame: {FRAME_KEY}

This example shows how both services analyze the same frame.
""")
    
    # Check if frame exists
    try:
        s3_client.head_object(Bucket=BUCKET, Key=FRAME_KEY)
    except Exception as e:
        print(f"\n❌ Error: Frame not found at {FRAME_KEY}")
        print(f"   Make sure you have a processed video in S3.")
        print(f"\n   To test this script:")
        print(f"   1. Upload a video through the UI")
        print(f"   2. Update VIDEO_ID variable to match your video")
        print(f"   3. Run this script again\n")
        return
    
    # Run both analyses
    rekognition_results = analyze_with_rekognition(BUCKET, FRAME_KEY)
    claude_caption = analyze_with_claude(BUCKET, FRAME_KEY)
    
    # Generate comparison
    comparison = generate_comparison_summary(rekognition_results, claude_caption)
    print(comparison)
    
    # Save detailed results
    output_file = 'rekognition_comparison_results.json'
    with open(output_file, 'w') as f:
        json.dump({
            'frame': FRAME_KEY,
            'rekognition': rekognition_results,
            'claude': claude_caption,
            'summary': 'See console output for comparison'
        }, f, indent=2)
    
    print(f"\n✅ Detailed results saved to: {output_file}\n")


if __name__ == '__main__':
    main()

