#!/usr/bin/env python3
import argparse
import sys
import os

# Align python sys path to resolve absolute imports correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.hybrid_search import HybridSearch
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.decay_engine import DecayEngine

def main():
    parser = argparse.ArgumentParser(
        description="SynapseMemory: Sovereign Command-Line Cognitive Console & Debugger."
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Ingest subcommand
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a cognitive memory segment with semantic deduplication")
    ingest_parser.add_argument("--content", required=True, type=str, help="Text content of the memory")
    ingest_parser.add_argument("--category", type=str, default="interaction", help="Cognitive category")
    ingest_parser.add_argument("--confidence", type=float, default=0.95, help="Confidence factor (0.0 to 1.0)")

    # Query subcommand
    query_parser = subparsers.add_parser("query", help="Query long-term memory with Knapsack context budget constraints")
    query_parser.add_argument("--prompt", required=True, type=str, help="The search or context prompt")
    query_parser.add_argument("--budget", type=int, default=1500, help="Maximum tokens allowed in context pack")

    # List subcommand
    subparsers.add_parser("list", help="List all indexed sqlite memories")

    # Clear subcommand
    subparsers.add_parser("clear", help="Clear the entire local SQLite memory table")

    # Prune subcommand
    prune_parser = subparsers.add_parser("prune", help="Manually force a temporal Ebbinghaus decay pruning sweep")
    prune_parser.add_argument("--limit", type=int, default=10, help="Set database maximum record limit")

    args = parser.parse_args()

    # Initialize store, embedder and engines
    store = SQLiteMemoryStore("synapse_memory.db")
    embedder = SynapseEmbedder(provider="local")
    manager = MemoryManager(store=store, embedder=embedder)
    searcher = HybridSearch()
    decay = DecayEngine()
    packer = KnapsackPacker()

    if args.command == "ingest":
        mem_id, action, cost = manager.ingest_with_deduplication(
            content=args.content,
            category=args.category,
            confidence=args.confidence
        )
        print(f"[+] Ingestion Finished:")
        print(f"    - Memory ID  : {mem_id}")
        print(f"    - Event Type : {action}")
        print(f"    - Token Cost : {cost} tokens")

    elif args.command == "query":
        memories = store.get_all_memories()
        if not memories:
            print("[-] Store is empty. Ingest memories before querying.")
            return

        candidates = searcher.fused_search(args.prompt, memories, top_k=25)

        # Apply temporal decay calculations
        decayed = []
        for c in candidates:
            current = decay.calculate_retention(
                base_relevance=c["relevance_score"],
                created_epoch=c["created_at"],
                access_count=c["access_count"]
            )
            c_copy = c.copy()
            c_copy["relevance_score"] = current
            decayed.append(c_copy)

        packed = packer.pack(decayed, args.budget)

        print(f"\n[+] Retained {len(packed)} optimal context segments under a {args.budget}-token budget:")
        for idx, m in enumerate(packed):
            print(f"  {idx + 1}. [{m['id']}] (Cost: {m['token_cost']} tokens, Relevance: {m['relevance_score']:.3f}):")
            print(f"     \"{m['content']}\"")

    elif args.command == "list":
        memories = store.get_all_memories()
        print(f"[+] Active Database Records ({len(memories)} items):")
        for m in memories:
            print(f"  - [{m['id']}] Category: {m['category'].upper()} | Cost: {m['token_cost']} | Content: \"{m['content'][:60]}...\"")

    elif args.command == "clear":
        os.remove("synapse_memory.db") if os.path.exists("synapse_memory.db") else None
        print("[+] Erased local sqlite database table cleanly.")

    elif args.command == "prune":
        manager.max_record_limit = args.limit
        evicted = manager.auto_prune_store()
        print(f"[+] Manual Prune Complete: Evicted {evicted} memories. Active store contains max {args.limit} items.")

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
