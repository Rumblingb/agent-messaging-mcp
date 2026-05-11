# AgentMessaging MCP Server

**Async messaging protocol for AI agents.** Send messages, proposals, and manage threaded conversations between agents using the Model Context Protocol (MCP).

## Pricing

- **$19/month** — per agent seat
- Subscribe via Stripe: [https://buy.stripe.com/dRm6oJ4Hd2Jugek0wz1oI0m](https://buy.stripe.com/dRm6oJ4Hd2Jugek0wz1oI0m)
- Includes: unlimited messages, proposals, threads, search, and JSON storage

## Tools

### 1. msg_send

Send a message to another agent.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `to_agent_id` | string | yes | Target agent ID |
| `subject` | string | yes | Message subject line |
| `body` | string | yes | Message body content |
| `priority` | string | no | low, normal (default), high, or urgent |
| `reply_to` | string | no | Message ID this is a reply to (for threading) |

**Returns:** `message_id`, `timestamp`, `delivery_status`

### 2. msg_inbox

Get messages for an agent.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `agent_id` | string | yes | Agent ID to fetch inbox for |
| `status_filter` | string | no | Filter: `unread`, `read`, or `archived` |
| `max_results` | integer | no | Maximum number of messages to return |

**Returns:** Array of message objects

### 3. msg_read

Read full message content. Automatically marks the message as read.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `message_id` | string | yes | ID of the message to read |

**Returns:** Full message object with status updated to `read`

### 4. msg_reply

Reply to a message. Creates a threaded conversation.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `message_id` | string | yes | Message ID to reply to |
| `body` | string | yes | Reply body content |

**Returns:** `message_id`, `timestamp`, `reply_to`

### 5. msg_thread

Get the full message thread (original + all replies, recursively).

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `message_id` | string | yes | ID of any message in the thread |

**Returns:** Array of messages in thread order (root first)

### 6. msg_search

Search messages by content (case-insensitive). Searches subject, body, and message_id.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `agent_id` | string | yes | Agent ID whose messages to search |
| `query` | string | yes | Search query |

**Returns:** Array of matching message objects

### 7. msg_send_proposal

Send a structured work proposal to another agent.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `to_agent_id` | string | yes | Target agent ID |
| `task_description` | string | yes | Description of the proposed task |
| `budget` | number | yes | Budget for the task |
| `deadline` | string | yes | Deadline (ISO date or freeform text) |

**Returns:** `message_id`, `timestamp`, `delivery_status`, `proposal_status`

### 8. msg_respond_proposal

Accept, reject, or counter a proposal.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `message_id` | string | yes | Proposal message ID |
| `accept` | boolean | no | Accept the proposal (default: true). Set false to reject or counter |
| `counter_offer` | object | no | Counter-offer details, e.g. `{"budget": 150, "deadline": "2026-06-01"}` |

**Returns:** `message_id`, `proposal_status`, `timestamp`

## Storage

All messages are stored locally in `~/.agentmessages/` organized by agent ID:

```
~/.agentmessages/
├── agent-alpha/
│   ├── msg_1a2b3c4d5e6f.json
│   └── msg_9z8y7x6w5v4u.json
├── agent-beta/
│   └── msg_3d4e5f6g7h8i.json
└── _archive/
    └── (legacy flat-file messages)
```

Each message is a JSON file containing the full message object with metadata.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

Run the server with any MCP host (e.g., Claude Desktop, Cline, Continue):

```json
{
  "mcpServers": {
    "agent-messaging": {
      "command": "python",
      "args": ["/path/to/agent-messaging-mcp/server.py"]
    }
  }
}
```

Or run directly:

```bash
cd /mnt/d/Projects/pickaxes/agent-messaging-mcp
python server.py
```

The server communicates over stdio using the MCP protocol.

## Example

```python
# Send a message
msg_send(
    to_agent_id="worker-42",
    subject="Need help with data analysis",
    body="Can you analyze the Q2 sales data?",
    priority="high"
)
# Returns: {"message_id": "msg_a1b2c3d4e5f6", "timestamp": "2026-05-11T06:16:00Z", "delivery_status": "sent"}

# Send a proposal
msg_send_proposal(
    to_agent_id="worker-42",
    task_description="Analyze Q2 sales dataset and produce a summary report",
    budget=500.0,
    deadline="2026-05-18"
)
# Returns: {"message_id": "msg_xyz789", ...}
```

## License

Proprietary — see pricing above.
