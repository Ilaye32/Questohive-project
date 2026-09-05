from __future__ import annotations
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(
        asyncio.WindowsProactorEventLoopPolicy()
    )

import sys

import os
import logging
from contextlib import asynccontextmanager
import atexit

from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
from playwright.async_api import async_playwright, Browser
from langchain_community.tools.playwright.utils import create_async_playwright_browser
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langchain_core.tools import tool
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from langmem import create_manage_memory_tool, create_search_memory_tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore
from langchain.agents import create_agent
from psycopg.errors import DuplicatePreparedStatement
from psycopg_pool import AsyncConnectionPool 
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from prompt import SUPERVISOR_PROMPT, SYSTEM_PROMPT_PQ, SYSTEM_PROMPT_SUPPORT
from mcp_clients import run_mcp_client, list_firestore_collection, query_firestore

load_dotenv()

#===========================================================================
#Tools
#===========================================================================
@tool
async def web_crawler(url: str) -> str:
    """
    Use this tool to crawl and get content from questohive.com only for customer support related queries.
    """
    browser_config = BrowserConfig(headless=True)
    run_config = CrawlerRunConfig(session_id="agent_session")

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
            if result.success:
                markdown = getattr(result, 'markdown_v2', None)
                if markdown and hasattr(markdown, 'raw_markdown'):
                    return markdown.raw_markdown.strip()
                return (result.markdown or "No content extracted").strip()
            else:
                return f"Scrape failed: {result.error_message or 'Unknown error'}"
    except Exception as e:
        return f"Deep scrape error: {str(e)}"

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("chatbot.log"),
    ],
)
logger = logging.getLogger("QuestoHive")


# ============================================================================
# CONFIGURATION
# ============================================================================

class Config:
    LLM_MODEL = "deepseek-chat"
    LLM_TEMPERATURE = 0
    REQUEST_TIMEOUT = 120


# ============================================================================
# ENVIRONMENT VALIDATION
# ============================================================================

def validate_environment() -> None:
    required = [
        "TAVILY_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "POSTGRES_URI",
    ]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        raise RuntimeError(
            f"Cannot start — missing environment variables: {', '.join(missing)}\n"
            "Please set them in your .env file."
        )


# ============================================================================
# GEMINI KNOWLEDGEBASE TOOL
# ============================================================================

gemini_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

def knowledgebase(query: str) -> str:
    """Retrieve information from the QuestoHive knowledge base."""
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=query,
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        file_search=types.FileSearch(
                            file_search_store_names=[
                                "fileSearchStores/questohive-knowledge-base-vxijv056i33c"
                            ]
                        )
                    )
                ]
            ),
        )
        return response.text
    except Exception as e:
        return f"Information not available: {str(e)}"
    
# ============================================================================
# LLM FACTORY
# ============================================================================

def make_llm() -> ChatOpenAI:
    return ChatOpenAI(
        temperature=Config.LLM_TEMPERATURE,
        model=Config.LLM_MODEL,
        openai_api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        timeout=Config.REQUEST_TIMEOUT,
        streaming=True,
    )



# ============================================================================
# LIFESPAN — LangGraph Server entry point
# ============================================================================
import asyncio

_store: AsyncPostgresStore | None = None
_checkpointer: AsyncPostgresSaver | None = None
_shared_tools: list | None = None
_init_lock = asyncio.Lock()
_initialized = False

async def _initialize_once():
    global _store, _checkpointer, _shared_tools, _initialized

    async with _init_lock:
        if _initialized:
            return

        validate_environment()
        POSTGRES_URI = os.getenv("POSTGRES_URI")

        store_q: asyncio.Queue = asyncio.Queue()
        checkpointer_q: asyncio.Queue = asyncio.Queue()
        shutdown_event = asyncio.Event()

        async def run_store():
            # ------------------------------------------------------------
            # FIX: Create an explicit connection pool and pass it to the store
            # ------------------------------------------------------------
            pool = None
            try:
                pool = AsyncConnectionPool(
                    POSTGRES_URI,
                    min_size=1,
                    max_size=10,
                    open=True,
                )
                logger.info("Connection pool created for store.")

                # Pass the pool as the 'conn' parameter.
                # The store will use this pool, giving each task its own connection.
                store = AsyncPostgresStore(
                    conn=pool,
                    index={
                        "dims": 3072,
                        "embed": GoogleGenerativeAIEmbeddings(
                            model="models/gemini-embedding-2"
                        ),
                    },
                )
                await store.setup()

                # Confirm we are indeed using a pool
                if isinstance(store.conn, AsyncConnectionPool):
                    logger.info("Store using connection pool ✓")
                else:
                    logger.warning(
                        "Store is NOT using a connection pool — DuplicatePreparedStatement risk!"
                    )

                await store_q.put(store)
                # Keep the store alive until shutdown is requested
                await shutdown_event.wait()

            except Exception as e:
                logger.error(f"run_store failed: {e}", exc_info=True)
                await store_q.put(e)
            finally:
                # Cleanup: close the store (which may close the pool) and then the pool itself
                try:
                    if pool:
                        await pool.close()
                        logger.info("Store connection pool closed.")
                except Exception as e:
                    logger.warning(f"Error closing pool: {e}")

        async def run_checkpointer():
            try:
                async with AsyncPostgresSaver.from_conn_string(POSTGRES_URI) as cp:
                    try:
                        await cp.setup()
                    except Exception as e:
                        if "already exists" not in str(e):
                            await checkpointer_q.put(e)
                            return
                    await checkpointer_q.put(cp)
                    await shutdown_event.wait()
            except Exception as e:
                logger.error(f"run_checkpointer failed: {e}", exc_info=True)
                await checkpointer_q.put(e)

        store_task = asyncio.create_task(run_store())
        checkpointer_task = asyncio.create_task(run_checkpointer())

        # Receive store — raise immediately if it failed
        store_result = await store_q.get()
        if isinstance(store_result, Exception):
            raise store_result
        _store = store_result
        logger.info("Store ready.")

        # Receive checkpointer — raise immediately if it failed
        cp_result = await checkpointer_q.get()
        if isinstance(cp_result, Exception):
            raise cp_result
        _checkpointer = cp_result
        logger.info("Checkpointer ready.")

        namespace = "Memories"
        memory_tools = [
            create_manage_memory_tool(namespace, store=_store),
            create_search_memory_tool(namespace, store=_store),
        ]
        _shared_tools = [
            TavilySearch(api_key=os.getenv("TAVILY_API_KEY"), search_depth="advanced"),
            knowledgebase,
            *memory_tools,
            run_mcp_client,
        ]

        # Keep references for shutdowntool
        _store._shutdown_event = shutdown_event
        _store._store_task = store_task
        _store._checkpointer_task = checkpointer_task

        _initialized = True
        logger.info("All resources initialized with connection pool.")


async def _shutdown():
    if _store and hasattr(_store, '_shutdown_event'):
        _store._shutdown_event.set()
        await asyncio.gather(
            _store._store_task,
            _store._checkpointer_task,
            return_exceptions=True,
        )


def _sync_shutdown():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(timeout=10)
        else:
            loop.run_until_complete(_shutdown())
    except Exception:
        pass

atexit.register(_sync_shutdown)



support_agent = create_agent(
        model=make_llm(),
        tools=[web_crawler, knowledgebase],
        checkpointer=_checkpointer,
        store=_store,
        system_prompt=SYSTEM_PROMPT_SUPPORT,
        name="Support Agent",)
@tool(
    "Customer_Support_agent",
    description="Use this agent to get the answer to customer related questions about questohive",
)
def Customer_Support_agent(query: str) -> str:
    """Used to get customer support information."""
    result = support_agent.invoke(
        {"messages": [{"role": "user", "content": query}]}
    )
    messages = result.get("messages", [])
    if messages:
        return messages[-1].content
    return "Could not get response at the moment"


pq_agent = create_agent(
        model=make_llm(),
        tools=[list_firestore_collection, query_firestore],
        checkpointer=_checkpointer,
        store=_store,
        system_prompt=SYSTEM_PROMPT_PQ,
        name="PQ Agent",
    )

@tool(
    "Past_Questions_Fetcher",
    description="Use this tool to fetch past questions from the database.",
)
def Past_Questions_Fetcher(query: str) -> str:
    """Used to get pastquestions from the database."""
    result = pq_agent.invoke(
        {"messages": [{"role": "user", "content": query}]}
    )
    messages = result.get("messages", [])
    if messages:
        return messages[-1].content
    return "Could not get response at the moment"


@asynccontextmanager
async def lifespan(config):
    await _initialize_once()  # no-op after first call

    supervisor=create_agent(
        model=make_llm(),
        tools=[Past_Questions_Fetcher, Customer_Support_agent],
        checkpointer=_checkpointer,
        store=_store,
        system_prompt=SUPERVISOR_PROMPT,
        name="Supervisor Agent",
    )

    logger.info("Graph compiled and ready.")
    yield supervisor

agent = lifespan
