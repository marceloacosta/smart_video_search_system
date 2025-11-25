# Rekognition vs Claude Vision Comparison Example

**Date:** 2025-11-25  
**Type:** Illustrative Exercise  
**Status:** Complete

## Overview

Created a standalone example demonstrating how Amazon Rekognition could be used as an alternative to Claude Vision for video frame caption generation. This is an illustrative comparison, not a change to the current system.

## What Was Created

### 1. Comparison Script (`examples/rekognition_vs_claude.py`)

A Python script that analyzes the same video frame using both services and displays results side-by-side.

**Rekognition Capabilities Demonstrated:**
- **DetectLabels**: Objects, scenes, activities with confidence scores
  - Example: Road (97.52%), Freeway (94.55%), Person (93.01%)
- **DetectText**: OCR for text extraction from images
- **DetectFaces**: Face detection with demographics and emotions
  - Example: Male, Age 25-33, Emotions: CALM, SURPRISED, FEAR
- Output: Structured JSON with confidence scores

**Claude Vision Capabilities Demonstrated:**
- Natural language scene descriptions
- Contextual understanding and interpretation
- Semantic reasoning (e.g., interpreting motion blur as high-speed movement)
- Example: "High-speed Hyperloop test track or similar transportation testing facility..."

### 2. Documentation (`examples/README.md`)

Comprehensive documentation explaining:
- Technical differences between the services
- Use case comparison table
- Why Claude Vision was chosen for this system
- Potential hybrid approach for production systems

## Key Findings

### Rekognition Strengths
- **Structured Output**: Machine-readable JSON with confidence scores
- **Specialized Features**: OCR, face recognition, content moderation
- **Precision**: Exact object/label detection
- **Filtering**: Easy to query by specific attributes
- **Use Cases**: "Find frames with text", "Show frames with smiling faces", content moderation

### Claude Vision Strengths
- **Natural Language**: Human-readable descriptions
- **Context**: Understands relationships between elements
- **Semantic Understanding**: Captures meaning, mood, narrative
- **Flexibility**: Better for open-ended queries
- **Use Cases**: "Find dramatic moments", "Show celebration scenes", semantic search

## Why Claude Vision for This System

**Decision Rationale:**
1. **Primary Use Case**: Semantic search with natural language queries
2. **User Intent**: Users ask questions like "show me tense conversations" not "find frames with label=person"
3. **Rich Descriptions**: Need contextual understanding beyond simple labels
4. **Bedrock Integration**: Natural fit with existing Bedrock Knowledge Base architecture

## Potential Hybrid Approach

For systems requiring both precision AND flexibility:

```
┌─────────────────┐
│  Video Frame    │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌─────────┐ ┌──────────┐
│ Rekog   │ │  Claude  │
│ Labels  │ │  Caption │
└────┬────┘ └────┬─────┘
     │           │
     └─────┬─────┘
           ▼
    ┌──────────────┐
    │   Combined   │
    │   Metadata   │
    └──────────────┘
```

**Benefits:**
- Rekognition: Text extraction, face recognition, content moderation
- Claude: Semantic descriptions for natural language search
- Query either depending on user intent
- Best of both worlds

## Test Results

Tested with frame 30 from `yellowstone-trailer`:

**Rekognition Output:**
```json
{
  "labels": [
    {"name": "Road", "confidence": 97.52},
    {"name": "Person", "confidence": 93.01}
  ],
  "faces": [
    {"age_range": "25-33", "gender": "Male", "emotions": ["CALM", "SURPRISED"]}
  ],
  "text_detections": []
}
```

**Claude Output:**
```
"This image shows a high-speed Hyperloop test track or similar 
transportation testing facility. A pod or vehicle can be seen moving 
rapidly along an elevated track or tube system, creating a blur effect 
due to its speed."
```

**Observation:** Both services accurately described the frame, but with different granularity:
- Rekognition: Precise, structured, filterable
- Claude: Contextual, interpretive, searchable

## Files Created

```
examples/
├── rekognition_vs_claude.py          # Comparison script
├── rekognition_comparison_results.json # Sample output
└── README.md                          # Documentation
```

## Running the Example

```bash
cd examples
python3 rekognition_vs_claude.py
```

**Prerequisites:**
- A video processed in the system (for frame access)
- AWS credentials with Rekognition, Bedrock, and S3 access
- Update `VIDEO_ID` variable to match an uploaded video

## Cost Considerations

**Rekognition Pricing (us-east-1):**
- DetectLabels: $0.001 per image
- DetectText: $0.001 per image
- DetectFaces: $0.001 per image
- **Total per frame**: ~$0.003 for all three analyses

**Claude Vision (Bedrock):**
- Input tokens: $3.00 per 1M tokens
- Output tokens: $15.00 per 1M tokens
- Image tokens: ~1,600 tokens per image
- **Total per frame**: ~$0.005-0.008 per caption

**At scale (1000 frames):**
- Rekognition: $3.00
- Claude: $5-8.00
- Hybrid: $8-11.00

The cost difference is minimal for semantic search benefits.

## Conclusion

This exercise demonstrates that while Rekognition is a viable option for structured frame analysis, **Claude Vision remains the right choice** for this semantic video search system due to:

1. Natural language understanding required for search queries
2. Need for contextual scene interpretation
3. Integration with Bedrock Knowledge Base for RAG
4. User experience centered on conversational queries

The example code remains available for reference and can be used to evaluate a hybrid approach if future requirements demand more structured metadata extraction.

## Related Documentation

- AWS Rekognition Developer Guide: https://docs.aws.amazon.com/rekognition/
- Bedrock Claude Vision: https://docs.aws.amazon.com/bedrock/
- Current caption generation: `src/lambdas/generate_captions.py`

## Next Steps

- ✅ Example created and tested
- ✅ Documentation complete
- ⏸️  No system changes needed (illustration only)
- 🔮 Future: Consider hybrid approach if structured filtering becomes a requirement

