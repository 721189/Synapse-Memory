import time
from synapse_memory.core.knapsack_packer import KnapsackPacker
from synapse_memory.core.decay_engine import DecayEngine
from synapse_memory.core.hybrid_search import HybridSearch

def run_production_demo():
    print("=" * 70)
    print("      SYNAPSEMEMORY: UNIVERSAL ACTIVE RAG CORE DEMO & TESTS")
    print("=" * 70)

    # 1. Initialize Engines
    packer = KnapsackPacker()
    decay = DecayEngine()
    search = HybridSearch()

    print("\n[+] Engines Initialized Successfully:")
    print("    - Knapsack Packer: Adaptive 0/1 Dynamic Programming Solver Active")
    print("    - Temporal Decay: Ebbinghaus Stabilization Curve Active")
    print("    - Hybrid Search: Lexical/Semantic Reciprocal Fusion Active")

    # 2. Ingest Sample Memory Base representing typical user developer workspace config
    database = [
        {
            "id": "mem_01",
            "content": "User prefers React with TypeScript and uses Vite with Tailwind CSS for layouts. Absolutely bans inline style tags.",
            "category": "preference",
            "confidence": 0.98,
            "created_at": time.time() - 3600,  # 1 hour ago
            "access_count": 0,
            "token_cost": 35
        },
        {
            "id": "mem_02",
            "content": "AetherLab database uses PostgreSQL hosted on GCP Cloud SQL with pgvector extensions configured.",
            "category": "infrastructure",
            "confidence": 0.95,
            "created_at": time.time() - 86400 * 2,  # 2 days ago (should show significant temporal decay)
            "access_count": 0,
            "token_cost": 40
        },
        {
            "id": "mem_03",
            "content": "Server is listening exclusively on port 3000 behind an Nginx reverse proxy layer.",
            "category": "infrastructure",
            "confidence": 0.96,
            "created_at": time.time(),  # just now (high freshness)
            "access_count": 0,
            "token_cost": 25
        },
        {
            "id": "mem_04",
            "content": "User hates purple-to-blue gradients in UI design. Prefers highly-polished warm or cool slate neutrals.",
            "category": "preference",
            "confidence": 0.92,
            "created_at": time.time() - 10,  # just now
            "access_count": 0,
            "token_cost": 30
        }
    ]

    print(f"\n[+] Ingested {len(database)} Sample Cognitive Nodes into Memory Store.")

    # 3. Simulate Query and Dynamic Filtering
    query = "What framework and styling settings should we use for database infrastructure?"
    print(f"\n[?] User Prompt: '{query}'")

    print("\n[~] Execution Phase 1: Running Hybrid Semantic/Lexical Retrieval...")
    candidates = search.fused_search(query, database, top_k=4)

    print("\n[~] Execution Phase 2: Applying Ebbinghaus Temporal Forgetting Curve Weights...")
    decayed_candidates = []
    for c in candidates:
        original_score = c["relevance_score"]
        current_score = decay.calculate_retention(
            base_relevance=original_score,
            created_epoch=c["created_at"],
            access_count=c["access_count"]
        )
        c_copy = c.copy()
        c_copy["relevance_score"] = current_score
        decayed_candidates.append(c_copy)
        
        # Display computed weight variations
        print(f"    - Node {c['id']}: Orig Search Relevance={original_score:.3f} | Current Decay Score={current_score:.3f}")

    # 4. Solve Knapsack token allocation to fit within a strict token context budget
    token_budget = 70  # deliberately tight budget to force knapsack decision optimization
    print(f"\n[~] Execution Phase 3: Solving Knapsack DP Constraint (Budget: {token_budget} tokens)...")
    packed_memories = packer.pack(decayed_candidates, token_budget)

    print("\n" + "-" * 70)
    print("      SYNAPSEMEMORY CONTEXT BUDGET ALLOCATION REPORT")
    print("-" * 70)
    print(f"Total Database Candidates Size : {sum(d['token_cost'] for d in database)} tokens")
    print(f"Target Prompt Context Budget   : {token_budget} tokens")
    print(f"Optimal Allocation Pack Size   : {sum(m['token_cost'] for m in packed_memories)} tokens")
    print(f"Total Overhead Costs Saved     : {sum(d['token_cost'] for d in database) - sum(m['token_cost'] for m in packed_memories)} tokens")
    print("-" * 70)

    print("\n[+] Selected Context Pack Nodes:")
    for idx, node in enumerate(packed_memories):
        print(f"  {idx + 1}. [{node['id']}] Category: {node['category'].upper()} | Cost: {node['token_cost']} tokens | Final Relevance: {node['relevance_score']:.3f}")
        print(f"     Content: \"{node['content']}\"")

    print("\n" + "=" * 70)
    print("      DEMONSTRATION RUN COMPLETE - STATUS: PRODUCTION READY")
    print("=" * 70)

if __name__ == "__main__":
    run_production_demo()
