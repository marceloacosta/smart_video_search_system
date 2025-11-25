# Examples and Comparisons

This directory contains illustrative examples and comparisons for different AWS AI services.

## Rekognition vs Claude Vision

**File:** `rekognition_vs_claude.py`

A side-by-side comparison showing how Amazon Rekognition and Claude Vision analyze the same video frame.

### What it demonstrates:

**Amazon Rekognition:**
- Label detection (objects, scenes, activities)
- Text detection (OCR)
- Face detection and analysis (age, gender, emotions)
- Structured, machine-readable output with confidence scores

**Claude Vision:**
- Natural language scene descriptions
- Contextual understanding
- Semantic interpretation
- Human-like descriptions suitable for search

### Running the test:

```bash
cd examples
python3 rekognition_vs_claude.py
```

**Prerequisites:**
- A video must be processed in the system first
- Update the `VIDEO_ID` variable in the script to match an uploaded video
- AWS credentials configured with access to:
  - Amazon Rekognition
  - Amazon Bedrock (Claude)
  - S3 (processed frames bucket)

### Use Cases Comparison:

| Use Case | Best Service |
|----------|--------------|
| Content moderation | Rekognition |
| Text extraction (OCR) | Rekognition |
| Face recognition | Rekognition |
| Demographic analysis | Rekognition |
| Precise object counts | Rekognition |
| Semantic search | Claude Vision |
| Scene understanding | Claude Vision |
| Natural language queries | Claude Vision |
| Creative descriptions | Claude Vision |
| Context and reasoning | Claude Vision |

### Current Implementation

This system uses **Claude Vision** because:
- Primary use case is semantic search with natural language queries
- Need rich, searchable descriptions beyond labels
- Want to capture scene context and relationships
- Better for "find dramatic moments" vs "find frames with label X"

### Potential Hybrid Approach

For production systems requiring both precision and flexibility:
1. Use **Rekognition** for structured metadata (text, faces, labels)
2. Use **Claude** for semantic descriptions
3. Store both in the knowledge base
4. Query both depending on user intent

This would enable queries like:
- "Find frames with text that mention 'Breaking News'" (Rekognition OCR + search)
- "Show me tense conversations" (Claude semantic understanding)
- "Find smiling faces in outdoor scenes" (Rekognition + Claude combined)

