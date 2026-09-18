from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from core.context_scope import ContextAccessPolicy


def workspace_build_target(
    *,
    query_text: str,
    interpretation: Any,
    access_policy: ContextAccessPolicy | None = None,
    extract_requested_builder_root_fn: Callable[..., str],
    search_user_heuristics_fn: Callable[..., list[dict[str, Any]]],
    workspace_root: str = "",
) -> dict[str, str]:
    lowered = str(query_text or "").lower()
    topic_hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
    # The workspace root is what lets a rooted destination INSIDE the workspace be honoured rather
    # than dropped; without it the extractor can only read workspace-relative paths.
    try:
        requested_root_dir = extract_requested_builder_root_fn(query_text, workspace_root=workspace_root)
    except TypeError:  # a caller-supplied stub with the older single-argument signature
        requested_root_dir = extract_requested_builder_root_fn(query_text)
    platform = "generic"
    if "discord" in lowered or "discord bot" in topic_hints:
        platform = "discord"
    elif "telegram" in lowered or "tg bot" in lowered or "telegram bot" in topic_hints:
        platform = "telegram"

    heuristic_hits = (
        search_user_heuristics_fn(
            query_text,
            access_policy=access_policy,
            topic_hints=list(topic_hints),
            limit=4,
        )
        if access_policy is not None
        else []
    )
    preferred_stacks = [
        str(item.get("signal") or "").strip().lower()
        for item in heuristic_hits
        if str(item.get("category") or "") == "preferred_stack"
    ]
    if "python" in lowered:
        language = "python"
    elif (
        "typescript" in lowered
        or "node" in lowered
        or "javascript" in lowered
        or (preferred_stacks and preferred_stacks[0] in {"typescript", "javascript"})
    ):
        language = "typescript"
    else:
        language = "python"

    return {
        "platform": platform,
        "language": language,
        "root_dir": (
            requested_root_dir.rstrip("/")
            if requested_root_dir
            else f"generated/{platform}-bot"
            if platform in {"telegram", "discord"}
            else _unrooted_build_dir(query_text)
        ),
    }


_SLUG_STOP_WORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "for", "with", "that", "this", "please", "smallest",
        "create", "write", "make", "build", "add", "generate", "prove", "reproduces", "run",
        "it", "me", "my", "in", "to", "of", "on", "do", "not", "yet", "test", "tests",
    }
)


def _unrooted_build_dir(query_text: str) -> str:
    """A per-request directory when the operator named no destination.

    Every unrooted build used to land in the single shared `generated/workspace-starter`, so
    successive unrelated builds silently overwrote each other file by file. Confirmed on disk
    2026-08-01: a security scaffold, an Apache-codec proof and a calculator all wrote into the same
    folder, leaving a project whose README, tests and source described three different things.

    The slug is derived from the request so two different asks cannot collide, with a short digest
    to separate two phrasings of the same ask. Deliberately NOT a timestamp -- the same request
    re-run should land in the same place so a retry overwrites its own previous attempt rather than
    littering the workspace.
    """

    import hashlib

    words = re.findall(r"[a-z0-9]+", str(query_text or "").lower())
    keep = [w for w in words if w not in _SLUG_STOP_WORDS and len(w) > 2][:4]
    slug = "-".join(keep) or "build"
    digest = hashlib.sha256(str(query_text or "").strip().lower().encode("utf-8")).hexdigest()[:6]
    return f"generated/{slug[:48]}-{digest}"


def workspace_build_sources(web_notes: list[dict[str, Any]]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for note in list(web_notes or [])[:4]:
        url = str(note.get("result_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        selected.append(
            {
                "title": str(note.get("result_title") or note.get("origin_domain") or "Source").strip(),
                "url": url,
                "label": str(note.get("source_profile_label") or note.get("origin_domain") or "").strip(),
            }
        )
    return selected


def workspace_build_file_map(
    *,
    target: dict[str, str],
    user_request: str,
    web_notes: list[dict[str, Any]],
) -> dict[str, str]:
    platform = str(target.get("platform") or "generic")
    language = str(target.get("language") or "python")
    root_dir = str(target.get("root_dir") or "generated/build-brief").rstrip("/")
    sources = workspace_build_sources(web_notes)

    if platform == "telegram" and language == "python":
        return {
            f"{root_dir}/README.md": telegram_python_readme(user_request=user_request, root_dir=root_dir, sources=sources),
            f"{root_dir}/requirements.txt": "python-telegram-bot>=22.0,<23.0\n",
            f"{root_dir}/.env.example": "TELEGRAM_BOT_TOKEN=replace-me\nBOT_NAME=VOOL Local Bot\n",
            f"{root_dir}/src/bot.py": telegram_python_bot_source(sources=sources),
        }
    if platform == "telegram" and language == "typescript":
        return {
            f"{root_dir}/README.md": telegram_typescript_readme(user_request=user_request, root_dir=root_dir, sources=sources),
            f"{root_dir}/package.json": telegram_typescript_package_json(),
            f"{root_dir}/tsconfig.json": telegram_typescript_tsconfig(),
            f"{root_dir}/.env.example": "TELEGRAM_BOT_TOKEN=replace-me\nBOT_NAME=VOOL Local Bot\n",
            f"{root_dir}/src/bot.ts": telegram_typescript_bot_source(sources=sources),
        }
    if platform == "discord" and language == "python":
        return {
            f"{root_dir}/README.md": discord_python_readme(user_request=user_request, root_dir=root_dir, sources=sources),
            f"{root_dir}/requirements.txt": "discord.py>=2.5,<3.0\n",
            f"{root_dir}/.env.example": "DISCORD_BOT_TOKEN=replace-me\n",
            f"{root_dir}/src/bot.py": discord_python_bot_source(sources=sources),
        }
    if platform == "discord" and language == "typescript":
        return {
            f"{root_dir}/README.md": discord_typescript_readme(user_request=user_request, root_dir=root_dir, sources=sources),
            f"{root_dir}/package.json": discord_typescript_package_json(),
            f"{root_dir}/tsconfig.json": telegram_typescript_tsconfig(),
            f"{root_dir}/.env.example": "DISCORD_BOT_TOKEN=replace-me\n",
            f"{root_dir}/src/bot.ts": discord_typescript_bot_source(sources=sources),
        }
    if language == "typescript":
        return {
            f"{root_dir}/README.md": generic_workspace_readme(
                user_request=user_request,
                root_dir=root_dir,
                sources=sources,
                language=language,
            ),
            f"{root_dir}/package.json": generic_typescript_package_json(root_dir=root_dir),
            f"{root_dir}/tsconfig.json": telegram_typescript_tsconfig(),
            f"{root_dir}/src/index.ts": generic_typescript_source(user_request=user_request, sources=sources),
        }
    return {
        f"{root_dir}/README.md": generic_workspace_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
            language=language,
        ),
        f"{root_dir}/src/main.py": generic_python_source(user_request=user_request, sources=sources),
    }


def sources_section(sources: list[dict[str, str]]) -> str:
    if not sources:
        return "- No live sources were captured in this run.\n"
    return "\n".join(f"- {item['title']}: {item['url']}" for item in sources[:4]) + "\n"


def generic_workspace_readme(
    *,
    user_request: str,
    root_dir: str,
    sources: list[dict[str, str]],
    language: str,
) -> str:
    entrypoint = "src/index.ts" if language == "typescript" else "src/main.py"
    return (
        "# Workspace Starter\n\n"
        f"Bounded local {language} starter generated to unblock real work in `{root_dir}`.\n\n"
        "## Request\n\n"
        f"- {user_request.strip()}\n\n"
        "## Sources\n\n"
        f"{sources_section(sources)}\n"
        "## Files\n\n"
        f"- `{entrypoint}`: first executable entrypoint for this workspace.\n"
        "- `README.md`: visible grounding for what this starter is trying to do.\n"
    )


def generic_python_source(*, user_request: str, sources: list[dict[str, str]]) -> str:
    source_lines = "\n".join(f"# - {item['title']}: {item['url']}" for item in sources[:4]) or "# - No live sources captured in this run."
    return (
        '"""Workspace starter entrypoint.\n\n'
        f"Request: {user_request.strip()}\n"
        "Source references:\n"
        f"{source_lines}\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "def main() -> None:\n"
        '    print("VOOL workspace starter is ready for the next implementation step.")\n\n'
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def generic_typescript_package_json(*, root_dir: str) -> str:
    package_name = re.sub(r"[^a-z0-9_-]+", "-", root_dir.strip("/").split("/")[-1].lower()).strip("-") or "vool-workspace-starter"
    return (
        "{\n"
        f'  "name": "{package_name}",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": {\n'
        '    "dev": "tsx src/index.ts"\n'
        "  },\n"
        '  "devDependencies": {\n'
        '    "tsx": "^4.19.2",\n'
        '    "typescript": "^5.7.3"\n'
        "  }\n"
        "}\n"
    )


def generic_typescript_source(*, user_request: str, sources: list[dict[str, str]]) -> str:
    source_lines = "\n".join(f"// - {item['title']}: {item['url']}" for item in sources[:4]) or "// - No live sources captured in this run."
    return (
        "// Workspace starter entrypoint.\n"
        f"// Request: {user_request.strip()}\n"
        "// Source references:\n"
        f"{source_lines}\n\n"
        'console.log("VOOL workspace starter is ready for the next implementation step.");\n'
    )


def telegram_python_readme(*, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
    return (
        "# Telegram Bot Scaffold\n\n"
        "Local-first Telegram bot scaffold generated from the current research lane.\n\n"
        "## Why This Shape\n\n"
        "- Keep the first pass small, editable, and runnable on a local machine.\n"
        "- Anchor protocol details on Telegram's official docs instead of generic blog spam.\n"
        "- Keep implementation references visible in the repo instead of hiding them in chat history.\n\n"
        "## Request\n\n"
        f"- {user_request.strip()}\n\n"
        "## Sources\n\n"
        f"{sources_section(sources)}\n"
        "## Files\n\n"
        "- `src/bot.py`: minimal command + message handlers.\n"
        "- `.env.example`: environment variables for local runs.\n"
        "- `requirements.txt`: first-pass Python dependencies.\n\n"
        "## Run\n\n"
        "1. Create a virtualenv.\n"
        "2. Install `requirements.txt`.\n"
        "3. Export `TELEGRAM_BOT_TOKEN`.\n"
        f"4. Run `python {root_dir}/src/bot.py`.\n"
    )


def telegram_python_bot_source(*, sources: list[dict[str, str]]) -> str:
    source_lines = "\n".join(f"# - {item['title']}: {item['url']}" for item in sources[:4]) or "# - No live sources captured in this run."
    return (
        '"""Telegram bot scaffold.\n\n'
        "Source references:\n"
        f"{source_lines}\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import logging\n"
        "import os\n"
        "from typing import Final\n\n"
        "from telegram import Update\n"
        "from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters\n\n"
        'TOKEN_ENV: Final = "TELEGRAM_BOT_TOKEN"\n'
        'DEFAULT_REPLY: Final = "VOOL local scaffold is online."\n\n'
        "logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')\n\n"
        "async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n"
        '    await update.effective_message.reply_text("VOOL scaffold is live. Use /help for commands.")\n\n'
        "async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n"
        '    await update.effective_message.reply_text("Commands: /start, /help. Everything else echoes for now.")\n\n'
        "async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n"
        "    if update.effective_message is None or not update.effective_message.text:\n"
        "        return\n"
        '    await update.effective_message.reply_text(f"{DEFAULT_REPLY}\\n\\nYou said: {update.effective_message.text}")\n\n'
        "def build_application(token: str) -> Application:\n"
        "    app = ApplicationBuilder().token(token).build()\n"
        '    app.add_handler(CommandHandler("start", start))\n'
        '    app.add_handler(CommandHandler("help", help_command))\n'
        "    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))\n"
        "    return app\n\n"
        "def main() -> None:\n"
        "    token = os.getenv(TOKEN_ENV, '').strip()\n"
        "    if not token:\n"
        '        raise SystemExit("Set TELEGRAM_BOT_TOKEN before running the scaffold.")\n'
        "    app = build_application(token)\n"
        "    app.run_polling(allowed_updates=Update.ALL_TYPES)\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def telegram_typescript_readme(*, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
    return (
        "# Telegram Bot Scaffold (TypeScript)\n\n"
        "TypeScript-first Telegram scaffold generated from the research lane.\n\n"
        "## Request\n\n"
        f"- {user_request.strip()}\n\n"
        "## Sources\n\n"
        f"{sources_section(sources)}\n"
        "## Run\n\n"
        "1. Install dependencies with `npm install`.\n"
        "2. Copy `.env.example` to `.env`.\n"
        f"3. Run `npm run dev --prefix {root_dir}`.\n"
    )


def telegram_typescript_package_json() -> str:
    return (
        "{\n"
        '  "name": "vool-telegram-bot-scaffold",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": {\n'
        '    "dev": "tsx src/bot.ts"\n'
        "  },\n"
        '  "dependencies": {\n'
        '    "dotenv": "^16.4.5",\n'
        '    "grammy": "^1.32.0"\n'
        "  },\n"
        '  "devDependencies": {\n'
        '    "tsx": "^4.19.2",\n'
        '    "typescript": "^5.7.3"\n'
        "  }\n"
        "}\n"
    )


def telegram_typescript_tsconfig() -> str:
    return (
        "{\n"
        '  "compilerOptions": {\n'
        '    "target": "ES2022",\n'
        '    "module": "NodeNext",\n'
        '    "moduleResolution": "NodeNext",\n'
        '    "strict": true,\n'
        '    "esModuleInterop": true,\n'
        '    "skipLibCheck": true,\n'
        '    "outDir": "dist"\n'
        "  },\n"
        '  "include": ["src/**/*.ts"]\n'
        "}\n"
    )


def telegram_typescript_bot_source(*, sources: list[dict[str, str]]) -> str:
    source_lines = "\n".join(f"// - {item['title']}: {item['url']}" for item in sources[:4]) or "// - No live sources captured in this run."
    return (
        "// Telegram bot scaffold.\n"
        "// Source references:\n"
        f"{source_lines}\n\n"
        'import "dotenv/config";\n'
        'import { Bot } from "grammy";\n\n'
        'const token = process.env.TELEGRAM_BOT_TOKEN?.trim();\n'
        "if (!token) {\n"
        '  throw new Error("Set TELEGRAM_BOT_TOKEN before running the scaffold.");\n'
        "}\n\n"
        'const bot = new Bot(token);\n\n'
        'bot.command("start", (ctx) => ctx.reply("VOOL TypeScript scaffold is live."));\n'
        'bot.command("help", (ctx) => ctx.reply("Commands: /start, /help."));\n'
        'bot.on("message:text", (ctx) => ctx.reply(`VOOL local scaffold heard: ${ctx.message.text}`));\n\n'
        "bot.start();\n"
    )


def discord_python_readme(*, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
    return (
        "# Discord Bot Scaffold\n\n"
        "Python Discord scaffold generated from the research lane.\n\n"
        "## Request\n\n"
        f"- {user_request.strip()}\n\n"
        "## Sources\n\n"
        f"{sources_section(sources)}\n"
        "## Run\n\n"
        f"1. Install `requirements.txt`.\n2. Export `DISCORD_BOT_TOKEN`.\n3. Run `python {root_dir}/src/bot.py`.\n"
    )


def discord_python_bot_source(*, sources: list[dict[str, str]]) -> str:
    source_lines = "\n".join(f"# - {item['title']}: {item['url']}" for item in sources[:4]) or "# - No live sources captured in this run."
    return (
        '"""Discord bot scaffold.\n\n'
        "Source references:\n"
        f"{source_lines}\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import os\n\n"
        "import discord\n\n"
        'TOKEN_ENV = "DISCORD_BOT_TOKEN"\n\n'
        "intents = discord.Intents.default()\n"
        "intents.message_content = True\n"
        "client = discord.Client(intents=intents)\n\n"
        "@client.event\n"
        "async def on_ready() -> None:\n"
        '    print(f"Logged in as {client.user}")\n\n'
        "@client.event\n"
        "async def on_message(message: discord.Message) -> None:\n"
        "    if message.author == client.user:\n"
        "        return\n"
        '    if message.content.startswith("!ping"):\n'
        '        await message.channel.send("pong")\n\n'
        "def main() -> None:\n"
        "    token = os.getenv(TOKEN_ENV, '').strip()\n"
        "    if not token:\n"
        '        raise SystemExit("Set DISCORD_BOT_TOKEN before running the scaffold.")\n'
        "    client.run(token)\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def discord_typescript_readme(*, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
    return (
        "# Discord Bot Scaffold (TypeScript)\n\n"
        "TypeScript Discord scaffold generated from the research lane.\n\n"
        "## Request\n\n"
        f"- {user_request.strip()}\n\n"
        "## Sources\n\n"
        f"{sources_section(sources)}\n"
        "## Run\n\n"
        f"1. Install dependencies.\n2. Copy `.env.example` to `.env`.\n3. Run `npm run dev --prefix {root_dir}`.\n"
    )


def discord_typescript_package_json() -> str:
    return (
        "{\n"
        '  "name": "vool-discord-bot-scaffold",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": {\n'
        '    "dev": "tsx src/bot.ts"\n'
        "  },\n"
        '  "dependencies": {\n'
        '    "discord.js": "^14.18.0",\n'
        '    "dotenv": "^16.4.5"\n'
        "  },\n"
        '  "devDependencies": {\n'
        '    "tsx": "^4.19.2",\n'
        '    "typescript": "^5.7.3"\n'
        "  }\n"
        "}\n"
    )


def discord_typescript_bot_source(*, sources: list[dict[str, str]]) -> str:
    source_lines = "\n".join(f"// - {item['title']}: {item['url']}" for item in sources[:4]) or "// - No live sources captured in this run."
    return (
        "// Discord bot scaffold.\n"
        "// Source references:\n"
        f"{source_lines}\n\n"
        'import "dotenv/config";\n'
        'import { Client, GatewayIntentBits } from "discord.js";\n\n'
        'const token = process.env.DISCORD_BOT_TOKEN?.trim();\n'
        "if (!token) {\n"
        '  throw new Error("Set DISCORD_BOT_TOKEN before running the scaffold.");\n'
        "}\n\n"
        "const client = new Client({\n"
        "  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent],\n"
        "});\n\n"
        'client.once("ready", () => {\n'
        '  console.log(`Logged in as ${client.user?.tag ?? "unknown-user"}`);\n'
        "});\n\n"
        'client.on("messageCreate", async (message) => {\n'
        "  if (message.author.bot) {\n"
        "    return;\n"
        "  }\n"
        '  if (message.content === "!ping") {\n'
        '    await message.reply("pong");\n'
        "  }\n"
        "});\n\n"
        "client.login(token);\n"
    )


def generic_build_brief(*, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
    return (
        "# Generated Build Brief\n\n"
        "A code scaffold was not generated because the request did not match a supported bot scaffold yet.\n\n"
        "## Request\n\n"
        f"- {user_request.strip()}\n\n"
        "## Sources\n\n"
        f"{sources_section(sources)}\n"
        "## Next Moves\n\n"
        "- Lock the target runtime and language.\n"
        "- Confirm the delivery interface.\n"
        "- Generate a more specific scaffold on the next turn.\n"
    )
