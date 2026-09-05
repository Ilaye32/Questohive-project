# QuestoHive AI Agent

An AI-powered assistant for QuestoHive that handles customer support and past‑question retrieval.  
Built with **LangGraph**, **LangChain**, **DeepSeek**, **Google Gemini**, **Tavily**, **Crawl4AI**, **Firebase/Firestore**, and **PostgreSQL** (with `pgvector`).

## Features

- Multi‑agent orchestration (Supervisor, Support, PQ agents)
- Persistent memory and checkpoints via PostgreSQL
- Tool integrations:
  - Web crawling (Crawl4AI) for QuestoHive support content
  - Google Gemini file search for knowledge base
  - Tavily web search
  - Firestore (via Firebase Admin SDK and MCP) for past questions
- Asynchronous and streaming‑friendly

## Architecture

- **LangGraph** manages the agent graph and state.
- **LangChain** provides agent abstractions and integrations.
- **PostgreSQL** stores checkpoints (conversation history) and long‑term memories (using `pgvector`).
- **DeepSeek** is the LLM (via OpenAI‑compatible API).
- **Google Gemini** handles knowledge‑base file search and embeddings.
- **Tavily** provides web search capabilities.
- **Crawl4AI** scrapes QuestoHive support pages.
- **Firestore** is accessed through both a direct SDK (`firebase_admin`) and an MCP server (`firebase-tools` via npx).

## Prerequisites

- **Python 3.10+** (recommended 3.11 or 3.12)
- **PostgreSQL 14+** with the `pgvector` extension
- **Node.js and npm/npx** (for Firebase MCP via `firebase-tools`)
- **Google Cloud project** with:
  - Firestore enabled
  - A service account key file (`service_key.json`) for Firebase Admin SDK
  - (Optional) Gemini file search store configured
- **Playwright** browsers (for web crawling)
- API keys for:
  - Tavily
  - Google (Gemini and embeddings)
  - DeepSeek

## Installation

1. **Clone the repository** and navigate into it:
   ```bash
   git clone [<your-repo-url>](https://github.com/Ilaye32/Questohive-project.git)
   cd questohive-ai-agent
