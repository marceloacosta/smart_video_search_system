# Intelligent Agent Routing

**Component:** Claude-powered agent for automatic tool selection  
**Purpose:** Analyze user queries and route to appropriate search tools

## Overview

The agent_api Lambda contains an intelligent Claude-based agent that:
1. Analyzes user queries to understand intent
2. Selects the appropriate search tool(s)
3. Executes searches via AgentCore Gateway (Auto Mode) or directly (Manual Mode)
4. Formats results for the frontend

## Architecture

```
Frontend Query
        ↓
agent_api Lambda
        ├─→ Manual Mode: Direct tool invocation
        └─→ Auto Mode: Claude analyzes intent
                ↓
          Claude Sonnet 4
                ↓
        Selects appropriate tool(s)
                ↓
        ┌───────┴────────┬─────────────┐
        ▼                ▼             ▼
  search_by_speech  search_by_caption  search_by_image
        ↓                ↓             ↓
      Bedrock KB      Bedrock KB    S3 Vectors
        ↓                ↓             ▼
            Results formatted for UI
```

## Two Operation Modes

### Manual Mode

User explicitly selects search type:
- Speech: Search transcripts
- Caption: Search frame descriptions  
- Image: Visual similarity search

**Frontend Request:**
```json
POST /search
{
  "mode": "manual",
  "search_type": "speech",
  "query": "artificial intelligence",
  "video_id": "optional-video-123"
}
```

**agent_api.py Flow:**
```python
def handle_manual_search(search_type, query, video_id=None):
    if search_type == 'speech':
        return search_by_speech(query, video_id)
    elif search_type == 'caption':
        return search_by_caption(query, video_id)
    elif search_type == 'image':
        return search_by_image(query, video_id)
```

### Auto Mode

Agent analyzes query and selects best tool(s):

**Frontend Request:**
```json
POST /chat
{
  "mode": "auto",
  "query": "Find the part where they discuss machine learning"
}
```

**agent_api.py Flow:**
```python
def handle_auto_search(query):
    # Claude analyzes query
    tool_selection = analyze_query_with_claude(query)
    
    # Execute selected tool(s)
    results = execute_tools(tool_selection)
    
    # Format for UI
    return format_results(results)
```

## Claude-Based Tool Selection

### System Prompt

```python
SYSTEM_PROMPT = """You are an intelligent video search assistant. 
Analyze user queries and select the appropriate search tool(s):

Tools available:
1. search_by_speech: Search video transcripts (what was SAID)
   - Use for: dialogue, spoken words, conversations, audio content
   - Examples: "what did they say about AI", "find the discussion on climate"

2. search_by_caption: Search frame descriptions (what was SHOWN)
   - Use for: visual scenes, actions, settings, people, objects
   - Examples: "show me outdoor scenes", "find frames with people presenting"

3. search_by_image: Visual similarity search
   - Use for: finding similar-looking frames
   - Requires: user uploads reference image

4. list_videos: List available videos

5. get_video_metadata: Get info about specific video

6. get_full_transcript: Retrieve complete transcript

Guidelines:
- Choose the most relevant tool based on query intent
- Can select multiple tools if needed
- Include all necessary parameters
- Return structured JSON with tool calls"""
```

### Tool Selection Logic

```python
def analyze_query_with_claude(query):
    bedrock = boto3.client('bedrock-runtime')
    
    prompt = f"""User query: "{query}"

Which tool(s) should be used? Return JSON:
{{
  "tools": [
    {{
      "name": "search_by_speech",
      "params": {{
        "query": "extracted search terms",
        "max_results": 5
      }},
      "reasoning": "why this tool"
    }}
  ]
}}"""
    
    response = bedrock.invoke_model(
        modelId='us.anthropic.claude-sonnet-4-20250514-v1:0',
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "system": SYSTEM_PROMPT,
            "messages": [{
                "role": "user",
                "content": prompt
            }]
        })
    )
    
    result = json.loads(response['body'].read())
    tool_selection = json.loads(result['content'][0]['text'])
    
    return tool_selection
```

### Example Tool Selections

**Query:** "Find where they talk about artificial intelligence"
```json
{
  "tools": [
    {
      "name": "search_by_speech",
      "params": {"query": "artificial intelligence"},
      "reasoning": "User wants to find spoken content about AI"
    }
  ]
}
```

**Query:** "Show me outdoor mountain scenes"
```json
{
  "tools": [
    {
      "name": "search_by_caption",
      "params": {"query": "outdoor mountain scenes"},
      "reasoning": "User wants visual scenes matching description"
    }
  ]
}
```

**Query:** "What did they say about AI and show me the presentation slides"
```json
{
  "tools": [
    {
      "name": "search_by_speech",
      "params": {"query": "artificial intelligence"},
      "reasoning": "Find spoken content about AI"
    },
    {
      "name": "search_by_caption",
      "params": {"query": "presentation slides"},
      "reasoning": "Find visual frames showing slides"
    }
  ]
}
```

## Tool Execution

### Direct Invocation (Manual Mode)

```python
def search_by_speech(query, video_id=None):
    bedrock_agent = boto3.client('bedrock-agent-runtime')
    
    # Query Bedrock Knowledge Base
    response = bedrock_agent.retrieve(
        knowledgeBaseId=os.environ['SPEECH_KB_ID'],
        retrievalQuery={'text': query},
        retrievalConfiguration={
            'vectorSearchConfiguration': {
                'numberOfResults': 5,
                'filter': {
                    'equals': {'key': 'video_id', 'value': video_id}
                } if video_id else None
            }
        }
    )
    
    # Extract timestamps using Claude
    results = []
    for item in response['retrievalResults']:
        snippet = item['content']['text']
        video_id = item['metadata']['video_id']
        
        # Get full transcript and find timestamp
        timestamp = extract_timestamp(snippet, video_id)
        
        results.append({
            'video_id': video_id,
            'timestamp': timestamp,
            'text': snippet,
            'score': item['score']
        })
    
    return results
```

### Via AgentCore Gateway (Auto Mode)

```python
def execute_via_gateway(tool_name, params):
    gateway_url = os.environ['GATEWAY_URL']
    
    # Make SigV4 signed request
    response = make_signed_request(
        'POST',
        f"{gateway_url}/{tool_name}",
        json.dumps(params)
    )
    
    return response.json()
```

## Intelligent Timestamp Extraction

### The Challenge

Bedrock KB returns text snippets but not exact timestamps from Transcribe JSON.

### The Solution

Use Claude to match KB snippets back to word-level timestamps:

```python
def extract_timestamp(snippet, video_id):
    # Download Transcribe JSON
    transcribe_json = load_transcribe_json(video_id)
    
    # Build word-level timeline
    words = []
    for item in transcribe_json['results']['items']:
        if item['type'] == 'pronunciation':
            words.append({
                'word': item['alternatives'][0]['content'],
                'start': float(item['start_time']),
                'end': float(item['end_time'])
            })
    
    # Ask Claude to find matching words
    prompt = f"""Find words in transcript matching this snippet:

Snippet: "{snippet}"

Words with timestamps:
{json.dumps(words[:100], indent=2)}  # First 100 words

Return JSON: {{"start_time": <seconds>, "end_time": <seconds>}}
Match semantic meaning, not exact wording."""
    
    response = invoke_claude(prompt)
    timestamps = json.loads(response)
    
    return timestamps['start_time']
```

### Why This Works

1. **Semantic Matching**: Claude understands paraphrasing
2. **Context Awareness**: Considers surrounding words
3. **Flexible**: Handles transcript variations
4. **Accurate**: Returns precise timestamps

## Result Formatting

### For Manual Mode

```json
{
  "results": [
    {
      "video_id": "video-123",
      "timestamp": 45.2,
      "text": "We're exploring artificial intelligence...",
      "score": 0.87
    }
  ],
  "search_type": "speech",
  "query": "artificial intelligence"
}
```

### For Auto Mode

```json
{
  "response": "I found 3 mentions of artificial intelligence in the video.",
  "results": {
    "speech": [
      {
        "video_id": "video-123",
        "timestamp": 45.2,
        "text": "..."
      }
    ],
    "caption": [
      {
        "video_id": "video-123",
        "timestamp": 50.5,
        "text": "..."
      }
    ]
  },
  "tool_calls": [
    {
      "tool": "search_by_speech",
      "query": "artificial intelligence",
      "results_count": 3
    }
  ]
}
```

## Performance Optimization

### Caching

```python
import functools

@functools.lru_cache(maxsize=100)
def get_video_transcript(video_id):
    """Cache transcripts to avoid repeated S3 reads."""
    return load_from_s3(video_id)
```

### Parallel Tool Execution

```python
from concurrent.futures import ThreadPoolExecutor

def execute_tools_parallel(tool_selections):
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(execute_tool, tool['name'], tool['params']): tool
            for tool in tool_selections
        }
        
        results = {}
        for future in futures:
            tool = futures[future]
            results[tool['name']] = future.result()
        
        return results
```

## Error Handling

```python
def handle_search(query, mode='auto'):
    try:
        if mode == 'auto':
            tool_selection = analyze_query_with_claude(query)
            results = execute_tools(tool_selection)
        else:
            results = execute_manual_search(query)
        
        return {
            'status': 'success',
            'results': results
        }
    
    except bedrock.exceptions.ThrottlingException:
        return {
            'status': 'error',
            'message': 'Service temporarily unavailable. Please retry.'
        }
    
    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        return {
            'status': 'error',
            'message': 'Search failed. Please try again.'
        }
```

## Testing

### Unit Tests

```python
def test_tool_selection():
    query = "Find discussions about AI"
    selection = analyze_query_with_claude(query)
    
    assert len(selection['tools']) > 0
    assert selection['tools'][0]['name'] == 'search_by_speech'
```

### Integration Tests

```python
def test_end_to_end_search():
    response = lambda_handler({
        'body': json.dumps({
            'mode': 'auto',
            'query': 'artificial intelligence'
        })
    }, None)
    
    body = json.loads(response['body'])
    assert body['status'] == 'success'
    assert len(body['results']) > 0
```

## Monitoring

```python
cloudwatch.put_metric_data(
    Namespace='VideoSearch/Agent',
    MetricData=[
        {
            'MetricName': 'QueryLatency',
            'Value': latency_ms,
            'Unit': 'Milliseconds'
        },
        {
            'MetricName': 'ToolSelections',
            'Value': 1,
            'Dimensions': [
                {'Name': 'ToolName', 'Value': tool_name}
            ]
        }
    ]
)
```

## Related Documentation

- [07-agentcore-gateway.md](07-agentcore-gateway.md) - Tool infrastructure
- [06-bedrock-knowledge-bases.md](06-bedrock-knowledge-bases.md) - Search backends
- [09-frontend.md](09-frontend.md) - UI integration

## Next Steps

After agent setup:
1. Frontend sends queries to agent_api
2. Agent intelligently routes to tools
3. Results displayed with video navigation
