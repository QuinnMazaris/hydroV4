# Architecture and Code Changes Documentation

## Overview

This document explains the architecture and recent changes made to the HydroV4 hydroponic system, focusing on the conversation persistence system, chat interface, and integration between the frontend, backend API, and AI agent services.

---

## System Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Next.js)                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Chat Page (/app/chat/page.tsx)                         │  │
│  │  - React chat interface                                  │  │
│  │  - Real-time message polling                            │  │
│  │  - Message deduplication                                │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │ HTTP
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                           │
        ▼                                           ▼
┌──────────────────┐                    ┌──────────────────┐
│  Backend API     │                    │  Gardener Agent  │
│  (FastAPI)       │                    │  (FastAPI)       │
│  Port: 8001      │                    │  Port: 8600     │
│                  │                    │                  │
│  Endpoints:      │                    │  Endpoints:      │
│  - /api/         │                    │  - /agent/run   │
│    conversations │                    │  - /tools        │
│  - /api/devices  │                    │  - /health       │
│  - /api/readings │                    │                  │
└──────────────────┘                    └──────────────────┘
        │                                           │
        │                                           │
        └───────────────────┬───────────────────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │  SQLite Database │
                  │  (hydro.db)      │
                  │                  │
                  │  Tables:         │
                  │  - conversation_ │
                  │    messages      │
                  │  - devices       │
                  │  - readings      │
                  └──────────────────┘
```

### Component Responsibilities

1. **Frontend (Next.js)**
   - Provides chat UI at `/chat`
   - Polls backend for new messages every 5 seconds
   - Sends user messages to Gardener Agent API
   - Handles message deduplication and merging
   - Displays both manual and automated conversations

2. **Backend API (Port 8001)**
   - Stores conversation messages in SQLite
   - Provides REST endpoints for conversation history
   - Manages sensor data, devices, and actuators
   - Serves as the central data persistence layer

3. **Gardener Agent API (Port 8600)**
   - Runs AI agent with tool access
   - Executes automation rules
   - Saves conversation messages after agent runs
   - Provides LLM interface for system control

4. **Automation Engine**
   - Runs in background within Gardener service
   - Evaluates rules every 30 seconds
   - Can trigger AI agent runs via automation rules
   - Saves automated conversation messages

---

## Conversation Persistence System

### Database Schema

The `conversation_messages` table stores all chat interactions:

```sql
CREATE TABLE conversation_messages (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    source ENUM('automated', 'manual') NOT NULL,
    role ENUM('user', 'assistant') NOT NULL,
    content TEXT NOT NULL,
    rule_id VARCHAR(100),           -- For automated messages
    rule_name VARCHAR(200),          -- For automated messages
    tool_calls JSON,                 -- Tool invocations
    metadata JSON,                    -- Additional context
    created_at DATETIME NOT NULL
);

CREATE INDEX ix_conversation_messages_source_ts ON conversation_messages(source, timestamp);
CREATE INDEX ix_conversation_messages_timestamp ON conversation_messages(timestamp);
CREATE INDEX ix_conversation_messages_source ON conversation_messages(source);
CREATE INDEX ix_conversation_messages_rule_id ON conversation_messages(rule_id);
```

### Message Sources

1. **Manual Messages** (`source: "manual"`)
   - User-initiated chats from the frontend
   - User questions and agent responses from direct API calls
   - Stored when user sends message via `/chat` page

2. **Automated Messages** (`source: "automated"`)
   - AI agent runs triggered by automation rules
   - Scheduled periodic agent executions
   - Includes `rule_id` and `rule_name` for traceability

### Message Flow

#### Manual Conversation Flow

```
User Types Message
    │
    ▼
Frontend sends POST to Gardener Agent API (/agent/run)
    │
    ├─► Gardener Agent processes with LLM
    │   ├─► Tool calls executed (sensors, actuators, etc.)
    │   └─► Final response generated
    │
    ▼
Gardener Agent saves conversation to Backend API
    │
    ├─► POST /api/conversations
    │   ├─► User message (source: "manual", role: "user")
    │   └─► Agent response (source: "manual", role: "assistant")
    │
    ▼
Frontend polls Backend API
    │
    ├─► GET /api/conversations?since=<timestamp>
    │
    ▼
Messages displayed in chat interface
```

#### Automated Conversation Flow

```
Automation Engine evaluates rules (every 30 seconds)
    │
    ├─► Rule condition met (e.g., cron schedule)
    │
    ▼
Rule executes "run_ai_agent" action
    │
    ├─► Gardener Agent runs with prompt from rule
    │   ├─► Tool calls executed
    │   └─► Final response generated
    │
    ▼
Automation Engine saves conversation to Backend API
    │
    ├─► POST /api/conversations
    │   ├─► User message (source: "automated", role: "user", rule_id, rule_name)
    │   └─► Agent response (source: "automated", role: "assistant", rule_id, rule_name)
    │
    ▼
Frontend polls and displays automated messages
    │
    └─► Shows "Automated" badge with rule name
```

---

## Frontend Chat Interface Changes

### Key Features

#### 1. Message Polling System

The chat page polls the backend API every 5 seconds for new messages:

```typescript
useEffect(() => {
  pollingRef.current = setInterval(() => {
    const since = latestTimestampRef.current
    fetchMessages(since ?? undefined)
  }, 5000)

  return () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
    }
  }
}, [fetchMessages])
```

**Features:**
- Uses `since` parameter to fetch only new messages
- Updates `latestTimestampRef` after each fetch
- Prevents duplicate message loading

#### 2. Message Deduplication Logic

The `mergeMessages` function handles complex message merging:

```typescript
const mergeMessages = useCallback((incoming: Message[], replace: boolean) => {
  setMessages((prev) => {
    const base = replace ? [] : prev

    // Separate persisted messages (with IDs) from pending messages (without IDs)
    const persistedMap = new Map<number, Message>()
    const pending: Message[] = []

    for (const message of base) {
      if (message.id !== undefined) {
        persistedMap.set(message.id, message)
      } else {
        pending.push(message)
      }
    }

    // Track signatures of incoming messages
    const incomingSignatures = new Set<string>()

    for (const message of incoming) {
      if (message.id !== undefined) {
        persistedMap.set(message.id, message)
      }
      incomingSignatures.add(signatureKey(message))
    }

    // Remove pending messages that match incoming persisted messages
    const filteredPending = pending.filter(
      (message) => !incomingSignatures.has(signatureKey(message))
    )

    // Add new pending messages from incoming
    const newPending = incoming.filter((message) => message.id === undefined)

    // Merge and sort chronologically
    const merged = [...persistedMap.values(), ...filteredPending, ...newPending]
    merged.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime())
    return merged
  })
}, [])
```

**How it works:**
1. **Persisted Messages**: Messages with database IDs are stored in a Map keyed by ID
2. **Pending Messages**: Messages without IDs (temporary UI states) are kept separately
3. **Signature Matching**: Uses `source:role:content` as a signature to detect duplicates
4. **Merging Strategy**:
   - Persisted messages replace any existing messages with the same ID
   - Pending messages are removed if they match incoming persisted messages
   - New pending messages are added
   - Final list is sorted chronologically

#### 3. Message Types and UI Styling

**Manual Messages:**
- User messages: Blue background (`bg-primary`)
- Assistant messages: White/translucent background (`bg-white/10`)

**Automated Messages:**
- Blue-tinted background (`bg-blue-500/10`)
- Blue border (`border-blue-500/20`)
- "Automated" badge with clock icon
- Rule name displayed when available

**Visual Indicators:**
- "Pending" badge for messages not yet persisted
- Tool calls displayed as badges below message content
- Timestamp relative to now (e.g., "2 minutes ago")

#### 4. Error Handling

- Network errors show error message in chat
- Gardener service down shows helpful error with port information
- Failed API calls are logged to console
- User-friendly error messages displayed in UI

---

## Backend API Changes

### Conversation Endpoints

#### POST `/api/conversations`

Saves one or more conversation messages to the database.

**Request Body:**
```json
[
  {
    "source": "manual" | "automated",
    "role": "user" | "assistant",
    "content": "Message content",
    "timestamp": "2024-01-01T12:00:00Z",
    "rule_id": "optional-rule-id",
    "rule_name": "Optional Rule Name",
    "tool_calls": [{"name": "tool_name", "arguments": {}}],
    "metadata": {}
  }
]
```

**Response:**
```json
[
  {
    "id": 1,
    "source": "manual",
    "role": "user",
    "content": "Message content",
    "timestamp": "2024-01-01T12:00:00Z",
    "created_at": "2024-01-01T12:00:00Z",
    "rule_id": null,
    "rule_name": null,
    "tool_calls": null,
    "metadata": null
  }
]
```

#### GET `/api/conversations`

Retrieves conversation history with filtering.

**Query Parameters:**
- `limit` (default: 100, max: 500): Maximum messages to return
- `since` (optional): ISO timestamp - only return messages after this time
- `source` (optional): Filter by "automated" or "manual"

**Response:**
Array of conversation messages, sorted chronologically (oldest first).

#### GET `/api/conversations/highlights`

Returns recent automated assistant messages for dashboard display.

**Query Parameters:**
- `limit` (default: 5, max: 20): Number of highlights to return

---

## Gardener Agent Integration

### Agent Run Endpoint

#### POST `/agent/run`

Processes a conversation with the AI agent and saves the conversation.

**Request:**
```json
{
  "messages": [
    {"role": "user", "content": "What's the current temperature?"}
  ],
  "temperature": 0.3
}
```

**Response:**
```json
{
  "final": "The current temperature is 24.5°C.",
  "trace": [
    {
      "iteration": 0,
      "assistant": {
        "content": "I'll check the sensor readings...",
        "tool_calls": [
          {
            "name": "get_sensor_snapshot",
            "arguments": {},
            "id": "call_123"
          }
        ]
      },
      "tools": [
        {
          "tool": "get_sensor_snapshot",
          "result": {"temperature": 24.5}
        }
      ]
    }
  ]
}
```

**Automatic Conversation Persistence:**

After processing, the agent automatically saves both the user message and its response:

```python
# In agents/gardener/app.py
events = [
    {
        "source": "manual",
        "role": last_message.role,
        "content": last_message.content,
        "timestamp": datetime.now(timezone.utc),
        "metadata": {
            "temperature": payload.temperature,
            "message_count": len(payload.messages),
        },
    },
    {
        "source": "manual",
        "role": "assistant",
        "content": result.get("final", ""),
        "timestamp": datetime.now(timezone.utc),
        "tool_calls": tool_calls or None,
        "metadata": {
            "trace_length": len(result.get("trace", [])),
        },
    }
]

await hydro_client.save_conversation_messages(events)
```

---

## Automation Engine Integration

### Automated Agent Runs

When an automation rule executes a `run_ai_agent` action:

```python
# In agents/gardener/automation_runner.py
async def _execute_run_ai_agent(self, action, rule_name, rule_id):
    prompt = action.get('prompt', 'Analyze the current system state...')
    temperature = action.get('temperature', 0.3)
    
    # Run agent
    messages = [ChatMessage(role="user", content=prompt)]
    result = await self.agent.run(messages=messages, temperature=temperature)
    
    # Extract tool calls from trace
    tool_calls = []
    for entry in trace:
        assistant_block = entry.get("assistant") or {}
        for call in assistant_block.get("tool_calls") or []:
            tool_calls.append(call)
    
    # Save conversation with automated source
    conversation_payloads = [
        {
            "source": "automated",
            "role": "user",
            "content": prompt,
            "timestamp": now,
            "rule_id": rule_id,
            "rule_name": rule_name,
            "metadata": {
                "action": "run_ai_agent",
                "temperature": temperature,
            },
        },
        {
            "source": "automated",
            "role": "assistant",
            "content": result.get("final", ""),
            "timestamp": datetime.now(timezone.utc),
            "rule_id": rule_id,
            "rule_name": rule_name,
            "tool_calls": tool_calls or None,
            "metadata": {"trace": trace},
        },
    ]
    
    await self.hydro_client.save_conversation_messages(conversation_payloads)
```

**Key Differences from Manual Conversations:**
- `source: "automated"` instead of `"manual"`
- Includes `rule_id` and `rule_name` for traceability
- Metadata includes automation-specific information

---

## HydroAPIClient Changes

### New Method: `save_conversation_messages`

Added to `agents/gardener/hydro_client.py`:

```python
async def save_conversation_messages(
    self,
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Persist conversation messages via the backend API."""
    
    if not messages:
        return []
    
    payload: List[Dict[str, Any]] = []
    for message in messages:
        serialised = dict(message)
        timestamp = serialised.get("timestamp")
        if isinstance(timestamp, datetime):
            serialised["timestamp"] = timestamp.isoformat()
        created_at = serialised.get("created_at")
        if isinstance(created_at, datetime):
            serialised["created_at"] = created_at.isoformat()
        payload.append(serialised)
    
    response = await self._client.post("/api/conversations", json=payload)
    response.raise_for_status()
    return response.json()
```

**Purpose:**
- Allows Gardener Agent to save conversations without direct database access
- Handles datetime serialization
- Provides clean interface for conversation persistence

---

## Data Flow Summary

### Complete Message Lifecycle

1. **Message Creation**
   - User types message OR automation rule triggers agent
   - Message has no database ID yet (pending state)

2. **Agent Processing**
   - Message sent to Gardener Agent API
   - Agent processes with LLM and tools
   - Response generated

3. **Persistence**
   - Both user and assistant messages saved to database
   - Messages receive database IDs
   - Stored with source, role, tool_calls, metadata

4. **Frontend Polling**
   - Frontend polls backend every 5 seconds
   - Fetches messages since last known timestamp
   - Deduplicates and merges with existing messages

5. **UI Display**
   - Messages displayed chronologically
   - Visual distinction between manual/automated
   - Tool calls and metadata shown

---

## Key Design Decisions

### 1. Separate Services

**Why:** Backend API and Gardener Agent are separate services
- **Separation of Concerns**: Backend handles data persistence, Agent handles AI logic
- **Scalability**: Services can be scaled independently
- **Flexibility**: Different deployment strategies possible

### 2. Polling vs WebSockets

**Why:** Chose polling over WebSockets
- **Simplicity**: Easier to implement and debug
- **Reliability**: No connection management needed
- **Compatibility**: Works with all network configurations
- **Trade-off**: Slight delay (max 5 seconds) vs real-time

### 3. Message Deduplication

**Why:** Complex merging logic needed
- **Pending Messages**: User sees immediate feedback
- **Persisted Messages**: Accurate history from database
- **Race Conditions**: Handle messages arriving out of order
- **Signature Matching**: Detect duplicates without IDs

### 4. Source Tracking

**Why:** Distinguish manual vs automated conversations
- **Transparency**: Users know what's automated
- **Debugging**: Trace automated actions to rules
- **UI Differentiation**: Visual distinction in chat
- **Analytics**: Track automation effectiveness

### 5. Tool Call Storage

**Why:** Store tool calls with messages
- **Audit Trail**: See what tools agent used
- **Debugging**: Understand agent reasoning
- **Transparency**: Show users what agent did
- **UI Display**: Show tool badges in chat

---

## Future Enhancements

### Potential Improvements

1. **WebSocket Support**
   - Real-time message delivery
   - Reduced polling overhead
   - Instant message updates

2. **Message Search**
   - Full-text search across conversations
   - Filter by date, source, rule, etc.
   - Search within tool calls

3. **Conversation Threading**
   - Group related messages
   - Link automated runs to manual conversations
   - Context-aware responses

4. **Export/Import**
   - Export conversation history
   - Backup and restore functionality
   - Data analysis tools

5. **Rich Media**
   - Image attachments in messages
   - Camera snapshots in chat
   - Charts and graphs inline

---

## File Structure

### Key Files Modified/Created

```
hydroV4/
├── app/
│   └── chat/
│       └── page.tsx              # Frontend chat interface
│
├── backend/
│   ├── api.py                    # Conversation endpoints
│   ├── models.py                 # ConversationMessage model
│   └── services/
│       └── agent_history.py      # Conversation persistence logic
│
├── agents/
│   └── gardener/
│       ├── app.py                # Agent API with conversation saving
│       ├── agent.py              # Agent processing logic
│       ├── automation_runner.py  # Automated conversation saving
│       └── hydro_client.py       # API client with save_conversation_messages
│
└── ARCHITECTURE_AND_CHANGES.md   # This document
```

---

## Testing Considerations

### Manual Testing

1. **Send a message from chat**
   - Verify message appears immediately (pending)
   - Verify message persists after polling
   - Verify duplicate doesn't appear

2. **Check automated messages**
   - Enable automation rule with AI agent
   - Verify automated messages appear in chat
   - Verify "Automated" badge and rule name display

3. **Test polling**
   - Send message from another device/client
   - Verify message appears within 5 seconds
   - Verify chronological ordering

4. **Test error handling**
   - Stop Gardener service
   - Verify error message displays
   - Restart service and verify recovery

### Integration Testing

- Verify messages persist across service restarts
- Verify concurrent message handling
- Verify large conversation history loading
- Verify tool call storage and retrieval

---

## Conclusion

The conversation persistence system provides a unified view of all interactions with the AI agent, whether initiated manually by users or automatically by rules. The architecture separates concerns between frontend, backend, and agent services while maintaining a seamless user experience.

Key achievements:
- ✅ Unified conversation history
- ✅ Real-time message updates via polling
- ✅ Visual distinction between manual/automated
- ✅ Complete audit trail with tool calls
- ✅ Robust deduplication and merging
- ✅ Error handling and recovery

The system is now ready for production use and can be extended with additional features as needed.



