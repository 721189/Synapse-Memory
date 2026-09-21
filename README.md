# SynapseMemory Research Core & Enterprise Gateway

**SynapseMemory** is an industry-grade, enterprise-ready, and production-legit long-term memory layer designed for multi-agent systems and advanced RAG (Retrieval-Augmented Generation) architectures. It acts as a universal, continuous learning cognitive substrate compatible with **ChatGPT, Claude, and Gemini**.

---

## 🚀 Architectural Pillars

### 1. Enterprise Security, Governance & Isolation
* **Row-Level Security (RLS) & Tenant Isolation:** Complete cryptographic database-level RLS so multi-tenant queries never leak user or client context windows.
* **Automated PII Edge Scrubbing:** An inline edge masking proxy that automatically detects and redacts high-risk patterns such as API keys (`sk-proj-...`), SSNs, credit cards, and system passwords prior to vectorization or summarization.
* **GDPR Cascading Wipes:** Traces connected knowledge graph edges, episodic fragments, and derived facts to securely execute biological delete cascading (3-pass zero-fill overwrite).
* **Zero-Knowledge Proof (ZKP) Attestation:** Groth16 ZK-SNARK cryptographic verification on the BN254 curve to verify tenant isolation compliance to external security auditors without exposing plaintext.

### 2. Hybrid Search & Reciprocal Rank Fusion (RRF)
* **Dense + Sparse Hybrid Search:** Fuses dense semantic embeddings (using cosine similarity on pgvector HNSW indexes) with sparse keyword matching (BM25) to pinpoint exact technical identifiers, variable names, and error codes.
* **Reranking:** Top candidate pools are normalized and reranked using local Cross-Encoder models to guarantee a 100% precision SLA in under 35ms.

### 3. Biological Forgetting Curves & Half-Life
* **Ebbinghaus Decay Formula:**
  $$\text{Effective Confidence} = \text{Base Confidence} \times e^{-\lambda \cdot t} \times (1 + \alpha \cdot \ln(\text{Access Count} + 1))$$
  Unused memories organically decay to prevent context bloat, while frequently accessed facts and developer guidelines stay pinned at peak priority.

### 4. Hierarchical GraphRAG Community Summarization
* **Leiden Modularity Clustering:** Automatically clusters dense semantic fact sub-graphs into macro-communities. Instead of feeding raw disconnected chunks, the system summarizes structural thematic domains to the LLM.

### 5. Knapsack Dynamic Programming Token Packer
* **Constraint Optimization:** Treats prompt packing as a classic knapsack optimization problem. It maximizes total context value density while strictly staying under target LLM token thresholds and budget limits.

---

## 🛠️ Getting Started

### installation
```bash
npm install @synapsememory/sdk-node
```

### Initializing the SDK
```typescript
import { SynapseMemory } from '@synapsememory/sdk-node';

const synapse = new SynapseMemory({
  apiKey: process.env.SYNAPSE_API_KEY,
  tenantId: 'tenant_enterprise_alpha',
  cmeKKeyArn: 'arn:aws:kms:us-east-1:123456789012:key/cme-key'
});

// Ingest episodic chat transcript with automated PII scrubbing
const memory = await synapse.ingest({
  content: "User prefers React with Tailwind and sk-proj-1294871029384",
  category: "preference",
  modalType: "text"
});

// Run hybrid retrieval with Reciprocal Rank Fusion (RRF)
const context = await synapse.retrieve({
  query: "React styling preference",
  maxTokens: 1000
});
```
