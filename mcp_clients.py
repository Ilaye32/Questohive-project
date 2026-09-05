import sys
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import tool
from typing import Optional
import os
from dotenv import load_dotenv
load_dotenv()


async def run_mcp_client():
    """This tool is used to Interact with the Questohive Database and Interact live with the AI"""
    # Determine the correct command for Windows compatibility
    npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
    
    # Configure both servers inside a single client configuration
    config = {
        "firebase": {
            "transport": "stdio",
            "command": npx_cmd,
            "args": ["-y", "firebase-tools@latest", "mcp"]
        }
    }
    
    # Initialize the adapter client
    # Note: langchain-mcp-adapters clients handle connection internally
    client = MultiServerMCPClient(config)
    
    # You MUST await get_tools()
    tools = await client.get_tools()
    
    print(f"Successfully connected! Loaded {len(tools)} tools total.")
    for tool in tools:
        print(f" - Tool Name: {tool.name}")
        
    return client, tools

import firebase_admin
from firebase_admin import credentials, firestore
from langchain_core.tools import tool

# Initialize once at module level
cred = credentials.Certificate("service_key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

@tool
def query_firestore(collection: str, document_id: str) -> str:
    """Query a single document from Firestore by collection name and document ID."""
    doc = db.collection(collection).document(document_id).get()
    if doc.exists:
        return str(doc.to_dict())
    return "Document not found."

@tool
def list_firestore_collection(collection: str) -> str:
    """List all documents in a Firestore collection."""
    docs = db.collection(collection).stream()
    results = {doc.id: doc.to_dict() for doc in docs}
    return str(results) if results else "Collection is empty or does not exist."

if __name__ == "__main__":
    # Execute the asynchronous orchestration
    client, tools = asyncio.run(run_mcp_client())
    print("The setup is complete.")
