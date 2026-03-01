"""nightwire AI assistant runner with agentic tool-calling loop.

Supports any OpenAI-compatible provider via /v1/chat/completions.
Uses native tool-calling to orchestrate multiple nightwire commands
from a single voice or text request.
"""

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import structlog

logger = structlog.get_logger()

# Max iterations for the agentic tool-calling loop (safety cap)
DEFAULT_MAX_ITERATIONS = 8
DEFAULT_ASK_USER_TIMEOUT = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Tool definitions -- one per nightwire command
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "List all registered projects.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_project",
            "description": "Select/switch to a project by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Project name (no spaces; a-z 0-9 . _ - only).",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_status",
            "description": "Show current project, running tasks, and autonomous loop status.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_about_project",
            "description": "Ask a read-only question about the currently selected project's codebase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask about the project.",
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "do_task",
            "description": "Execute a development task with Claude on the current project (e.g. add a feature, fix a bug, refactor code). This starts a background task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What to do -- the task description.",
                    }
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complex_task",
            "description": "Break a complex task into a PRD with stories and autonomous sub-tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "The complex task to break down.",
                    }
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": "Cancel the currently running task.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_summary",
            "description": "Generate a summary of the current project.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_prd",
            "description": "Create a Product Requirements Document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "PRD title.",
                    }
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List autonomous tasks, optionally filtered by status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional status filter (e.g. queued, running, done, failed).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "queue_work",
            "description": "Queue a story or PRD for autonomous execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["story", "prd"],
                        "description": "Whether to queue a story or an entire PRD.",
                    },
                    "id": {
                        "type": "string",
                        "description": "The story or PRD ID to queue.",
                    },
                },
                "required": ["type", "id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "autonomous_control",
            "description": "Control the autonomous execution loop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "start", "pause", "stop"],
                        "description": "Action to take on the autonomous loop.",
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_memory",
            "description": "Store a persistent memory/fact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to remember.",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search past conversations and memories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "List all stored memories.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_history",
            "description": "View recent message history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent messages to show.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_data",
            "description": "Delete stored data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "preferences", "today"],
                        "description": "What to forget.",
                    }
                },
                "required": ["scope"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_project",
            "description": "Register an existing project directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Project name (no spaces; a-z 0-9 . _ - only).",
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional filesystem path to the project.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional project description.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_project",
            "description": "Unregister a project (does not delete files).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Project name to remove.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": "Create a brand-new project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Project name (no spaces; a-z 0-9 . _ - only).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional project description.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_learnings",
            "description": "View or search learnings extracted from past tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Optional search term to filter learnings.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_help",
            "description": "Show all available bot commands.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user a clarifying question and wait for their reply. Use when you need more information to proceed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user.",
                    }
                },
                "required": ["question"],
            },
        },
    },
]

# Map tool names to (command, arg_builder) for dispatching
TOOL_TO_COMMAND: Dict[str, Tuple[str, Callable[[Dict[str, Any]], str]]] = {
    "list_projects":      ("projects",   lambda a: ""),
    "select_project":     ("select",     lambda a: a.get("name", "")),
    "get_status":         ("status",     lambda a: ""),
    "ask_about_project":  ("ask",        lambda a: a.get("question", "")),
    "do_task":            ("do",         lambda a: a.get("description", "")),
    "complex_task":       ("complex",    lambda a: a.get("description", "")),
    "cancel_task":        ("cancel",     lambda a: ""),
    "get_summary":        ("summary",    lambda a: ""),
    "create_prd":         ("prd",        lambda a: a.get("title", "")),
    "list_tasks":         ("tasks",      lambda a: a.get("status", "")),
    "queue_work":         ("queue",      lambda a: f"{a.get('type', '')} {a.get('id', '')}".strip()),
    "autonomous_control": ("autonomous", lambda a: a.get("action", "")),
    "store_memory":       ("remember",   lambda a: a.get("text", "")),
    "search_memory":      ("recall",     lambda a: a.get("query", "")),
    "list_memories":      ("memories",   lambda a: ""),
    "get_history":        ("history",    lambda a: str(a["count"]) if a.get("count") else ""),
    "forget_data":        ("forget",     lambda a: a.get("scope", "")),
    "add_project":        ("add",        lambda a: " ".join(filter(None, [a.get("name", ""), a.get("path", ""), a.get("description", "")]))),
    "remove_project":     ("remove",     lambda a: a.get("name", "")),
    "create_project":     ("new",        lambda a: " ".join(filter(None, [a.get("name", ""), a.get("description", "")]))),
    "get_learnings":      ("learnings",  lambda a: a.get("search", "")),
    "get_help":           ("help",       lambda a: ""),
    # ask_user is handled specially -- not in this map
}


def _build_system_prompt(context: Optional[Dict[str, Any]] = None) -> str:
    """Build the system prompt with optional live context."""
    ctx_section = ""
    if context:
        parts = []
        if context.get("current_project"):
            parts.append(f"Currently selected project: {context['current_project']}")
        else:
            parts.append("No project is currently selected.")
        if context.get("available_projects"):
            parts.append(f"Available projects: {', '.join(context['available_projects'])}")
        if context.get("task_running"):
            parts.append(f"A task is currently running: {context['task_running']}")
        if context.get("autonomous_status"):
            parts.append(f"Autonomous loop: {context['autonomous_status']}")
        ctx_section = "\n\nCurrent system state:\n" + "\n".join(f"- {p}" for p in parts)

    return f"""You are nightwire, an AI assistant for a Signal-based development bot. You have full control over the bot through tool calls.

Your capabilities:
- You can call multiple tools in sequence to accomplish complex requests.
- You can see the result of each tool call and decide what to do next.
- You can ask the user clarifying questions using the ask_user tool when you need more information.
- You understand both typed messages and voice transcriptions (which may have minor speech-to-text errors).
{ctx_section}

Behavior guidelines:
- For simple requests that map to a single action, just call the tool directly.
- For multi-step requests (e.g. "select project X and add a readme"), call the tools in sequence.
- When a request is ambiguous, use ask_user to clarify before acting.
- After executing tool(s), provide a brief summary of what was done and the results.
- For general knowledge questions that don't require any bot commands, just respond directly without calling tools.
- Be concise -- responses go through Signal. Keep final summaries under 4000 characters.
- Professional yet friendly tone. No emojis unless the user uses them.
- Voice transcriptions may contain minor errors -- interpret intent rather than matching words literally.
- When using do_task or complex_task, the task starts in the background. Let the user know it's been kicked off.
- Project names must not contain spaces (use a-z 0-9 . _ - only). Normalize spoken names like "my app" to "my-app"."""


class NightwireRunner:
    """Manages agentic tool-calling loop for nightwire AI assistant.

    Supports any OpenAI-compatible provider via /v1/chat/completions with
    native tool calling.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 1024,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        ask_user_timeout: int = DEFAULT_ASK_USER_TIMEOUT,
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.ask_user_timeout = ask_user_timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

        # Validate API URL scheme and hostname
        parsed = urlparse(self.api_url)
        if parsed.scheme != "https":
            logger.warning("insecure_api_url", url=self.api_url)
            raise ValueError("API URL must use HTTPS")
        if not parsed.hostname:
            logger.warning("invalid_api_url", url=self.api_url)
            raise ValueError("API URL must have a valid hostname")
        logger.info("nightwire_api_configured", host=parsed.hostname)

        if not self.api_key:
            logger.warning("nightwire_api_key_not_found")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a shared aiohttp session (thread-safe)."""
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            return self._session

    async def close(self):
        """Close the shared HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _chat_completion(
        self,
        messages: List[Dict[str, Any]],
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """Make a single chat completion API call. Returns the raw response dict."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
            "temperature": 0.4,
            "max_tokens": self.max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        session = await self._get_session()
        async with session.post(
            self.api_url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            error_text = await resp.text()
            logger.error("nightwire_api_error", status=resp.status, error=error_text[:500])
            raise RuntimeError(f"API error (status {resp.status})")

    async def run_agentic_loop(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        tool_executor: Optional[Callable[[str, str], Awaitable[Optional[str]]]] = None,
        ask_user_fn: Optional[Callable[[str], Awaitable[str]]] = None,
        progress_fn: Optional[Callable[[str], Awaitable[None]]] = None,
        timeout: int = 60,
    ) -> Tuple[bool, str]:
        """Run the agentic tool-calling loop.

        Args:
            message: The user's message (typed or voice transcription).
            context: Current system state (selected project, projects list, etc.).
            tool_executor: Callback to execute a bot command: (command, args) -> result string.
            ask_user_fn: Callback to ask the user a question and await their reply.
            progress_fn: Callback to send progress updates to the user.
            timeout: Per-API-call timeout in seconds.

        Returns:
            Tuple of (success, final_response).
        """
        if not self.api_key:
            return False, "nightwire assistant is not configured. Set the API key for your provider in .env."

        # Clean the message -- remove nightwire/sidechannel prefix variations
        clean_message = message.strip()
        msg_lower = clean_message.lower()
        for variant in ["nightwire:", "nightwire,", "nightwire ", "hey nightwire ", "hi nightwire ", "ok nightwire ",
                         "sidechannel:", "sidechannel,", "sidechannel ", "hey sidechannel ", "hi sidechannel ", "ok sidechannel "]:
            if msg_lower.startswith(variant):
                clean_message = clean_message[len(variant):].strip()
                break

        if clean_message.lower() in ("nightwire", "sidechannel") or not clean_message:
            clean_message = "Hello, how can you help me?"

        system_prompt = _build_system_prompt(context)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": clean_message},
        ]

        for iteration in range(self.max_iterations):
            logger.info("agentic_loop_iteration", iteration=iteration + 1, max=self.max_iterations)

            try:
                data = await self._chat_completion(messages, timeout=timeout)
            except RuntimeError as e:
                return False, f"nightwire encountered an error: {e}"
            except asyncio.TimeoutError:
                logger.warning("nightwire_timeout", timeout=timeout, iteration=iteration + 1)
                return False, "Request timed out. Try a more specific request?"
            except Exception as e:
                logger.error("nightwire_exception", error=str(e), iteration=iteration + 1)
                return False, "Temporary issue. Please try again."

            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                logger.error("nightwire_malformed_response", data_keys=list(data.keys()))
                return False, "Unexpected response from AI provider."

            choice = choices[0]
            finish_reason = choice.get("finish_reason", "")
            assistant_message = choice.get("message", {})

            # Append the assistant message to conversation history
            messages.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls")

            # No tool calls -- the AI is done; return the text response
            if not tool_calls or finish_reason == "stop":
                response = assistant_message.get("content", "") or ""
                if not response.strip():
                    response = "Done."
                if len(response) > 4000:
                    response = response[:4000] + "\n\n[Response truncated...]"
                logger.info("agentic_loop_complete", iterations=iteration + 1, response_len=len(response))
                return True, response

            # Process tool calls
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                tool_id = tc.get("id", "")

                try:
                    raw_args = func.get("arguments", "{}")
                    tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    tool_args = {}
                    logger.warning("tool_args_parse_error", tool=tool_name, raw=raw_args[:200])

                logger.info("tool_call", tool=tool_name, args=tool_args, iteration=iteration + 1)

                # Handle ask_user specially
                if tool_name == "ask_user":
                    question = tool_args.get("question", "Could you clarify?")
                    if ask_user_fn:
                        try:
                            user_reply = await ask_user_fn(question)
                            tool_result = user_reply
                        except asyncio.TimeoutError:
                            tool_result = "[User did not respond within the time limit.]"
                        except Exception as e:
                            logger.error("ask_user_error", error=str(e))
                            tool_result = "[Failed to get user response.]"
                    else:
                        tool_result = "[ask_user is not available in this context.]"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": tool_result,
                    })
                    continue

                # Regular tool -- dispatch via tool_executor
                mapping = TOOL_TO_COMMAND.get(tool_name)
                if not mapping:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": f"Unknown tool: {tool_name}",
                    })
                    continue

                command, arg_builder = mapping
                args_str = arg_builder(tool_args)

                if tool_executor:
                    try:
                        result = await tool_executor(command, args_str)
                        if result is None:
                            result = f"Command /{command} started. It's running in the background."
                    except Exception as e:
                        logger.error("tool_executor_error", tool=tool_name, error=str(e))
                        result = f"Error executing /{command}: {e}"
                else:
                    result = f"[Tool executor not available -- would run: /{command} {args_str}]"

                # Send progress update for each tool result
                if progress_fn:
                    try:
                        await progress_fn(f"/{command} {args_str}".strip() + f"\n{result[:500]}")
                    except Exception:
                        pass  # Non-critical

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": str(result) if result else "(no output)",
                })

        # Max iterations reached
        logger.warning("agentic_loop_max_iterations", max=self.max_iterations)
        return True, "Reached the maximum number of steps. Here's what was accomplished so far -- send another message to continue."

    async def ask_nightwire(
        self,
        message: str,
        timeout: int = 60,
        context_hint: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        tool_executor: Optional[Callable[[str, str], Awaitable[Optional[str]]]] = None,
        ask_user_fn: Optional[Callable[[str], Awaitable[str]]] = None,
        progress_fn: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Tuple[bool, str]:
        """Public entry point -- delegates to the agentic loop.

        Args:
            message: The user's message (typed or voice transcription).
            timeout: Per-API-call timeout in seconds.
            context_hint: Optional hint prepended to message (e.g. "Voice message transcription").
            context: Current system state dict.
            tool_executor: Callback to execute bot commands.
            ask_user_fn: Callback to ask the user a question.
            progress_fn: Callback to send progress updates.

        Returns:
            Tuple of (success, response).
        """
        if context_hint:
            message = f"[Context: {context_hint}]\n\n{message}"

        return await self.run_agentic_loop(
            message=message,
            context=context,
            tool_executor=tool_executor,
            ask_user_fn=ask_user_fn,
            progress_fn=progress_fn,
            timeout=timeout,
        )


# Backwards compat alias
SidechannelRunner = NightwireRunner


# Global instance
_nightwire_runner: Optional[NightwireRunner] = None


def get_nightwire_runner(
    api_url: str = "",
    api_key: str = "",
    model: str = "",
    max_tokens: int = 1024,
) -> NightwireRunner:
    """Get or create the global nightwire runner instance."""
    global _nightwire_runner
    if _nightwire_runner is None:
        _nightwire_runner = NightwireRunner(
            api_url=api_url,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
        )
    return _nightwire_runner


# Backwards compat alias
get_sidechannel_runner = get_nightwire_runner
