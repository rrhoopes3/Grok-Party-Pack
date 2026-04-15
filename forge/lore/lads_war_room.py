import os
import sys
import json

# Force UTF-8 so Rich box-drawing works on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from pathlib import Path
from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search, code_execution

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
HISTORY_FILE = Path("presidential_council_history.json")
AGENT_COUNT = 16

SYSTEM_PROMPT = """[SYSTEM INSTRUCTIONS — You must follow these at all times]

You are THE PRESIDENTIAL COUNCIL — 16 former US Presidents brought together through Grok 4.20 multi-agent system to analyze modern problems.

The Council consists of:
1. George Washington — republican virtue, neutrality, institutional integrity
2. Thomas Jefferson — liberty, limited government, agrarian values, expansionism
3. Andrew Jackson — populism, strong executive, nationalism, anti-elite
4. Abraham Lincoln — union, moral clarity, crisis leadership, pragmatism
5. Theodore Roosevelt — progressive reform, big stick foreign policy, conservation
6. Woodrow Wilson — idealism, international institutions, moral diplomacy
7. Franklin D. Roosevelt — bold experimentation, wartime leadership, big government
8. Dwight D. Eisenhower — strategic patience, infrastructure, warns against military-industrial complex
9. John F. Kennedy — vision, vigor, Cold War competition, inspirational leadership
10. Richard Nixon — realism, realpolitik, opening to China, executive power
11. Ronald Reagan — optimism, anti-communism, small government rhetoric, communication
12. George H.W. Bush — prudent realism, international coalitions, foreign policy expertise
13. Bill Clinton — globalization, triangulation, economic growth, third way politics
14. George W. Bush — post-9/11 resolve, democracy promotion, neoconservative intervention
15. Barack Obama — multilateralism, technocratic governance, hope and transformation
16. Donald Trump — America First, economic nationalism, disruption of norms, deal-making

You are a non-partisan strategic think tank. Each president speaks from their historical philosophy, temperament, and record. Debate vigorously but synthesize into coherent strategic options.

Current year is 2026. Focus on geopolitical, economic, technological, and systemic challenges.

CRITICAL RULES:
- ALL 16 presidents MUST speak individually on every question. No exceptions. No skipping.
- Each president gives their own distinct position, even if brief.
- After all 16 have spoken, end with a synthesized recommendation from the Council as a whole."""

# ── Rich Console ────────────────────────────────────────────────────────────
console = Console()

TITLE = """[bold white]
  _____ _  _ ___
 |_   _| || | __|
   | | | __ | _|
   |_| |_||_|___|
   ___ ___  _   _ _  _  ___ ___ _
  / __/ _ \\| | | | \\| |/ __|_ _| |
 | (_| (_) | |_| | .` | (__ | || |__
  \\___\\___/ \\___/|_|\\_|\\___|___|____|
[/bold white]"""


def print_header():
    console.print(Panel(
        Text.from_markup(TITLE + "\n[bold yellow]  PRESIDENTIAL COUNCIL[/bold yellow]"
                         "\n[dim]  16-Agent Mode  |  Grok 4.20 Multi-Agent Beta[/dim]"),
        border_style="bright_blue",
        padding=(0, 2),
    ))


def print_usage(usage):
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim")
    table.add_column(style="bold")
    if hasattr(usage, "input_tokens") and usage.input_tokens:
        table.add_row("Input", f"{usage.input_tokens:,} tokens")
    if hasattr(usage, "output_tokens") and usage.output_tokens:
        table.add_row("Output", f"{usage.output_tokens:,} tokens")
    if hasattr(usage, "reasoning_tokens") and usage.reasoning_tokens:
        table.add_row("Reasoning", f"{usage.reasoning_tokens:,} tokens")
    if hasattr(usage, "total_tokens") and usage.total_tokens:
        table.add_row("Total", f"{usage.total_tokens:,} tokens")
    console.print(Panel(table, title="[dim]Usage[/dim]", border_style="dim", expand=False))


def print_history_recap(messages):
    if not messages:
        return
    n_user = sum(1 for m in messages if m["role"] == "user")
    n_asst = sum(1 for m in messages if m["role"] == "assistant")
    console.print(
        Panel(f"[bold]{n_user}[/bold] queries  |  [bold]{n_asst}[/bold] responses",
              title="[yellow]Resumed Previous Session[/yellow]",
              border_style="yellow", expand=False)
    )


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    print_header()

    # Load history
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            messages = json.load(f)
        print_history_recap(messages)
    else:
        messages = []

    # Init client & chat
    client = Client(api_key=os.getenv("XAI_API_KEY"))

    chat = client.chat.create(
        model="grok-4.20-multi-agent-0309",
        agent_count=AGENT_COUNT,
        tools=[web_search(), x_search(), code_execution()],
        include=["verbose_streaming"],
    )

    # Inject system prompt as first user message
    chat.append(user(SYSTEM_PROMPT))

    # Replay previous conversation so the model has full context
    # (replaying both user queries AND council responses prevents amnesia on resume)
    if messages:
        transcript_parts = []
        for msg in messages:
            role_label = "USER QUERY" if msg["role"] == "user" else "COUNCIL RESPONSE"
            transcript_parts.append(f"[{role_label}]\n{msg['content']}")
        transcript = "\n\n---\n\n".join(transcript_parts)
        chat.append(user(
            f"[PREVIOUS SESSION TRANSCRIPT — {len(messages)} messages]\n"
            f"The following is the record of our prior conversation. "
            f"You must maintain continuity with these positions.\n\n{transcript}\n\n"
            f"[END OF TRANSCRIPT — Resume the Council session. Await the next query.]"
        ))

    console.print()
    console.rule("[bold bright_blue]The Council awaits your query[/bold bright_blue]")
    console.print("[dim]Type your question and press Enter. 'quit' to exit.[/dim]\n")

    while True:
        try:
            user_input = input("=> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                break

            chat.append(user(user_input))

            # ── Stream response ─────────────────────────────────────────
            console.print("[yellow]>> Council is deliberating (16 agents, beta can take 20-60s)...[/yellow]", highlight=False)

            full_response = ""
            is_thinking = True
            last_reasoning = 0
            got_first_chunk = False

            for response, chunk in chat.stream():
                if not got_first_chunk:
                    got_first_chunk = True
                    console.print("[green]>> Stream connected[/green]", highlight=False)

                # Show reasoning progress
                if is_thinking:
                    r_tokens = 0
                    if hasattr(response, "usage") and response.usage:
                        if hasattr(response.usage, "reasoning_tokens") and response.usage.reasoning_tokens:
                            r_tokens = response.usage.reasoning_tokens
                    if r_tokens and r_tokens != last_reasoning:
                        last_reasoning = r_tokens
                        print(f"\r   Deliberating... ({r_tokens:,} reasoning tokens)", end="", flush=True)

                # Switch from thinking -> content
                if chunk.content and is_thinking:
                    is_thinking = False
                    print()  # newline after reasoning counter
                    console.print("[green]>> Council responding:[/green]", highlight=False)

                if chunk.content:
                    print(chunk.content, end="", flush=True)
                    full_response += chunk.content

            if not got_first_chunk:
                console.print("[red]>> No response received from API[/red]", highlight=False)
            else:
                print()  # final newline after streamed content

            # Render final response as Markdown in a panel
            console.print()
            console.print(Panel(
                Markdown(full_response),
                title="[bold yellow]Presidential Council[/bold yellow]",
                subtitle="[dim]Grok 4.20 · 16 agents[/dim]",
                border_style="bright_blue",
                padding=(1, 2),
            ))

            # Usage stats
            if hasattr(response, "usage") and response.usage:
                print_usage(response.usage)

            # Save history
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": full_response})
            with open(HISTORY_FILE, "w") as f:
                json.dump(messages, f, indent=2)

            console.print()

        except KeyboardInterrupt:
            console.print()
            break
        except Exception as e:
            console.print(Panel(
                f"[bold red]{type(e).__name__}[/bold red]: {e}",
                title="[red]Error[/red]",
                border_style="red",
            ))

    # ── Exit ────────────────────────────────────────────────────────────
    console.print()
    console.rule("[bold bright_blue]Session Adjourned[/bold bright_blue]")
    if messages:
        console.print(f"[dim]History saved to {HISTORY_FILE} ({len(messages)} messages)[/dim]")
    console.print("[bold yellow]The Council rests.[/bold yellow]\n")


if __name__ == "__main__":
    main()
