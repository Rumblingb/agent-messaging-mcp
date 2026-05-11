#!/usr/bin/env python3
"""
AgentMessaging MCP Server
Async messaging protocol for agents — send messages, proposals, and manage threads.
"""

import json
import os
import uuid
import time
import shutil
import threading
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel, Field


# ── Storage ───────────────────────────────────────────────────────────────────

def _get_storage_dir() -> Path:
    """Get the ~/.agentmessages/ storage directory."""
    return Path.home() / ".agentmessages"


def _ensure_storage() -> Path:
    """Create storage directory if it doesn't exist and return path."""
    d = _get_storage_dir()
    d.mkdir(parents=True, exist_ok=True)
    # Migration from old flat-file format to new directory layout (if needed)
    _migrate_if_needed(d)
    return d


def _migrate_if_needed(storage_dir: Path) -> None:
    """Migrate flat-file storage to per-agent directories if not yet done."""
    # Check if there are old-style .json files at the root (not in subdirs)
    old_files = list(storage_dir.glob("msg_*.json"))
    if old_files:
        # Move them into an _archive subdirectory
        archive_dir = storage_dir / "_archive"
        archive_dir.mkdir(exist_ok=True)
        for f in old_files:
            shutil.move(str(f), str(archive_dir / f.name))


def _agent_dir(agent_id: str) -> Path:
    """Get the directory for a specific agent's messages."""
    d = _get_storage_dir() / agent_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _message_path(message_id: str) -> Path:
    """Get the path to a message file by scanning all agent directories."""
    storage = _ensure_storage()
    # First try direct lookup by checking all agent dirs
    for agent_dir in storage.iterdir():
        if agent_dir.is_dir() and not agent_dir.name.startswith("_"):
            msg_file = agent_dir / f"{message_id}.json"
            if msg_file.exists():
                return msg_file
    # Fallback: check _archive
    archive = storage / "_archive"
    if archive.exists():
        msg_file = archive / f"{message_id}.json"
        if msg_file.exists():
            return msg_file
    raise FileNotFoundError(f"Message {message_id} not found")


def _load_message(message_id: str) -> dict:
    """Load a message by ID."""
    path = _message_path(message_id)
    with open(path, "r") as f:
        return json.load(f)


def _save_message(agent_id: str, message: dict) -> None:
    """Save a message to an agent's directory."""
    agent_dir = _agent_dir(agent_id)
    msg_id = message["message_id"]
    path = agent_dir / f"{msg_id}.json"
    with open(path, "w") as f:
        json.dump(message, f, indent=2)


def _generate_id() -> str:
    """Generate a unique message ID."""
    return f"msg_{uuid.uuid4().hex[:12]}"


def _timestamp() -> str:
    """Get current ISO-8601 timestamp."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── In-memory lock for thread safety ─────────────────────────────────────────

_write_lock = threading.Lock()


# ── Tool implementations ─────────────────────────────────────────────────────

def tool_msg_send(
    to_agent_id: str,
    subject: str,
    body: str,
    priority: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> dict:
    """Send a message to another agent."""
    message_id = _generate_id()
    timestamp = _timestamp()
    message = {
        "message_id": message_id,
        "type": "message",
        "to_agent_id": to_agent_id,
        "from_agent_id": None,  # Set by the sender's context
        "subject": subject,
        "body": body,
        "priority": priority or "normal",
        "reply_to": reply_to,
        "replies": [],
        "status": "unread",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _write_lock:
        _save_message(to_agent_id, message)
    return {
        "message_id": message_id,
        "timestamp": timestamp,
        "delivery_status": "sent",
    }


def tool_msg_inbox(
    agent_id: str,
    status_filter: Optional[str] = None,
    max_results: Optional[int] = None,
) -> list:
    """Get messages for an agent. Filter by unread/read/archived."""
    agent_dir = _agent_dir(agent_id)
    messages = []
    if agent_dir.exists():
        for f in sorted(agent_dir.glob("*.json"), reverse=True):
            with open(f, "r") as fh:
                msg = json.load(fh)
            if status_filter and msg.get("status") != status_filter:
                continue
            messages.append(msg)
    if max_results:
        messages = messages[:max_results]
    return messages


def tool_msg_read(message_id: str) -> dict:
    """Read full message content. Marks as read."""
    with _write_lock:
        msg = _load_message(message_id)
        if msg.get("status") == "unread":
            msg["status"] = "read"
            msg["updated_at"] = _timestamp()
            # Re-save to the agent directory
            agent_id = msg.get("to_agent_id")
            if agent_id:
                _save_message(agent_id, msg)
    return msg


def tool_msg_reply(message_id: str, body: str) -> dict:
    """Reply to a message. Creates a thread."""
    original = _load_message(message_id)
    reply_id = _generate_id()
    timestamp = _timestamp()
    reply = {
        "message_id": reply_id,
        "type": "reply",
        "to_agent_id": original.get("from_agent_id"),
        "from_agent_id": original.get("to_agent_id"),
        "subject": f"Re: {original.get('subject', '')}",
        "body": body,
        "priority": "normal",
        "reply_to": message_id,
        "replies": [],
        "status": "unread",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    # Add reply ID to original's replies list and update timestamp
    original.setdefault("replies", []).append(reply_id)
    original["updated_at"] = timestamp
    # Save both messages
    with _write_lock:
        _save_message(original.get("to_agent_id"), original)
        if reply["to_agent_id"]:
            _save_message(reply["to_agent_id"], reply)
        else:
            # Fallback: save to original's agent
            _save_message(original.get("from_agent_id") or "unknown", reply)
    return {
        "message_id": reply_id,
        "timestamp": timestamp,
        "reply_to": message_id,
    }


def tool_msg_thread(message_id: str) -> list:
    """Get the full message thread (original + all replies)."""
    thread = []
    # Walk up to the root (original message)
    current_id = message_id
    chain = []
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        try:
            msg = _load_message(current_id)
            chain.insert(0, msg)
            current_id = msg.get("reply_to")
        except FileNotFoundError:
            break
    # Now walk all replies recursively
    def collect_replies(msg_id: str, depth: int = 0):
        if depth > 50:  # Safety limit
            return
        try:
            msg = _load_message(msg_id)
        except FileNotFoundError:
            return
        for reply_id in msg.get("replies", []):
            try:
                reply = _load_message(reply_id)
                thread.append(reply)
                collect_replies(reply_id, depth + 1)
            except FileNotFoundError:
                continue
    # Start from the root
    if chain:
        root = chain[0]
        thread.append(root)
        collect_replies(root["message_id"])
    return thread


def tool_msg_search(agent_id: str, query: str) -> list:
    """Search messages by content (case-insensitive)."""
    agent_dir = _agent_dir(agent_id)
    results = []
    if not agent_dir.exists():
        return results
    query_lower = query.lower()
    for f in agent_dir.glob("*.json"):
        with open(f, "r") as fh:
            msg = json.load(fh)
        # Search in subject, body, and message_id
        if (query_lower in msg.get("subject", "").lower()
                or query_lower in msg.get("body", "").lower()
                or query_lower in msg.get("message_id", "").lower()):
            results.append(msg)
    return results


def tool_msg_send_proposal(
    to_agent_id: str,
    task_description: str,
    budget: float,
    deadline: str,
) -> dict:
    """Send a structured work proposal to another agent."""
    proposal_id = _generate_id()
    timestamp = _timestamp()
    proposal = {
        "message_id": proposal_id,
        "type": "proposal",
        "to_agent_id": to_agent_id,
        "from_agent_id": None,
        "subject": f"Proposal: {task_description[:60]}",
        "body": task_description,
        "priority": "high",
        "reply_to": None,
        "replies": [],
        "proposal": {
            "task_description": task_description,
            "budget": budget,
            "deadline": deadline,
            "status": "pending",  # pending | accepted | rejected | countered
        },
        "status": "unread",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _write_lock:
        _save_message(to_agent_id, proposal)
    return {
        "message_id": proposal_id,
        "timestamp": timestamp,
        "delivery_status": "sent",
        "proposal_status": "pending",
    }


def tool_msg_respond_proposal(
    message_id: str,
    accept: bool = True,
    counter_offer: Optional[dict] = None,
) -> dict:
    """Accept, reject, or counter a proposal."""
    with _write_lock:
        msg = _load_message(message_id)
        if msg.get("type") != "proposal":
            raise ValueError(f"Message {message_id} is not a proposal")
        if "proposal" not in msg:
            raise ValueError(f"Message {message_id} has no proposal data")
        if msg["proposal"]["status"] != "pending":
            raise ValueError(
                f"Proposal {message_id} is already {msg['proposal']['status']}"
            )
        if accept and not counter_offer:
            msg["proposal"]["status"] = "accepted"
        elif not accept and not counter_offer:
            msg["proposal"]["status"] = "rejected"
        elif counter_offer:
            msg["proposal"]["status"] = "countered"
            msg["proposal"]["counter_offer"] = counter_offer
        msg["updated_at"] = _timestamp()
        _save_message(msg.get("to_agent_id"), msg)
    return {
        "message_id": message_id,
        "proposal_status": msg["proposal"]["status"],
        "timestamp": msg["updated_at"],
    }


# ── MCP Server ───────────────────────────────────────────────────────────────

server = Server("agent-messaging")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="msg_send",
            description="Send a message to another agent. Returns message_id, timestamp, delivery_status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to_agent_id": {"type": "string", "description": "Target agent ID"},
                    "subject": {"type": "string", "description": "Message subject line"},
                    "body": {"type": "string", "description": "Message body content"},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "normal", "high", "urgent"],
                        "description": "Message priority (default: normal)",
                    },
                    "reply_to": {
                        "type": "string",
                        "description": "Message ID this is a reply to (optional)",
                    },
                },
                "required": ["to_agent_id", "subject", "body"],
            },
        ),
        Tool(
            name="msg_inbox",
            description="Get messages for an agent. Filter by unread/read/archived.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to fetch inbox for"},
                    "status_filter": {
                        "type": "string",
                        "enum": ["unread", "read", "archived"],
                        "description": "Filter by message status (optional)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of messages to return (optional)",
                    },
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="msg_read",
            description="Read full message content. Marks message as read.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "ID of the message to read"},
                },
                "required": ["message_id"],
            },
        ),
        Tool(
            name="msg_reply",
            description="Reply to a message. Creates a threaded conversation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Message ID to reply to"},
                    "body": {"type": "string", "description": "Reply body content"},
                },
                "required": ["message_id", "body"],
            },
        ),
        Tool(
            name="msg_thread",
            description="Get full message thread (original + all replies).",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "ID of any message in the thread"},
                },
                "required": ["message_id"],
            },
        ),
        Tool(
            name="msg_search",
            description="Search messages by content (case-insensitive).",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID whose messages to search"},
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["agent_id", "query"],
            },
        ),
        Tool(
            name="msg_send_proposal",
            description="Send a structured work proposal to another agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to_agent_id": {"type": "string", "description": "Target agent ID"},
                    "task_description": {"type": "string", "description": "Description of the proposed task"},
                    "budget": {"type": "number", "description": "Budget for the task"},
                    "deadline": {"type": "string", "description": "Deadline for the task (ISO date or freeform)"},
                },
                "required": ["to_agent_id", "task_description", "budget", "deadline"],
            },
        ),
        Tool(
            name="msg_respond_proposal",
            description="Accept, reject, or counter a proposal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Proposal message ID"},
                    "accept": {
                        "type": "boolean",
                        "description": "Accept the proposal (default: True). Set False to reject or counter.",
                    },
                    "counter_offer": {
                        "type": "object",
                        "description": "Counter-offer details (e.g., {'budget': 150, 'deadline': '2026-06-01'}). Triggers 'countered' status.",
                    },
                },
                "required": ["message_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "msg_send":
            result = tool_msg_send(
                to_agent_id=arguments["to_agent_id"],
                subject=arguments["subject"],
                body=arguments["body"],
                priority=arguments.get("priority"),
                reply_to=arguments.get("reply_to"),
            )
        elif name == "msg_inbox":
            result = tool_msg_inbox(
                agent_id=arguments["agent_id"],
                status_filter=arguments.get("status_filter"),
                max_results=arguments.get("max_results"),
            )
        elif name == "msg_read":
            result = tool_msg_read(message_id=arguments["message_id"])
        elif name == "msg_reply":
            result = tool_msg_reply(
                message_id=arguments["message_id"],
                body=arguments["body"],
            )
        elif name == "msg_thread":
            result = tool_msg_thread(message_id=arguments["message_id"])
        elif name == "msg_search":
            result = tool_msg_search(
                agent_id=arguments["agent_id"],
                query=arguments["query"],
            )
        elif name == "msg_send_proposal":
            result = tool_msg_send_proposal(
                to_agent_id=arguments["to_agent_id"],
                task_description=arguments["task_description"],
                budget=arguments["budget"],
                deadline=arguments["deadline"],
            )
        elif name == "msg_respond_proposal":
            result = tool_msg_respond_proposal(
                message_id=arguments["message_id"],
                accept=arguments.get("accept", True),
                counter_offer=arguments.get("counter_offer"),
            )
        else:
            raise ValueError(f"Unknown tool: {name}")
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
