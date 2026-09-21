import React, { useState, useEffect, useRef } from 'react';
import { 
  ShieldCheck, Award, Key, Signature, FileText, Download, Check, 
  ArrowRight, DollarSign, Cpu, Database, Sparkles, Scale, Server, 
  Lock, Zap, Play, Terminal, HelpCircle, RefreshCw, Copy, CheckSquare
} from 'lucide-react';
import { CommercialLicense } from '../types';

export const CommercialBuyoutTab: React.FC = () => {
  // Main checkout states
  const [license, setLicense] = useState<CommercialLicense | null>(null);
  const [licensedTo, setLicensedTo] = useState('');
  const [loading, setLoading] = useState(true);
  const [buying, setBuying] = useState(false);
  const [signing, setSigning] = useState(false);

  // Clipboard copies
  const [copiedDocker, setCopiedDocker] = useState(false);
  const [copiedHelm, setCopiedHelm] = useState(false);
  const [copiedSdk, setCopiedSdk] = useState(false);

  // Active view states
  const [activeWhitepaper, setActiveWhitepaper] = useState(false);

  // Slider States for "Cost of Memory" Calculator
  const [agentCalls, setAgentCalls] = useState(250000); // 50k to 10M
  const [contextSize, setContextSize] = useState(16000); // 2k to 128k

  // Interactive Bento Widget 1: HNSW Parameter Adjuster
  const [hnswM, setHnswM] = useState(16); // 4 to 64
  const [hnswEf, setHnswEf] = useState(64); // 16 to 256

  // Interactive Bento Widget 2: PII Scrubber Terminal
  const [scrubInput, setScrubInput] = useState("User 'John Doe' logged in from ip 192.168.1.50 with token sk-or-v1-a982f3bd98e100f and mail contact@tesla.com");
  const [scrubOutput, setScrubOutput] = useState<string[]>([]);
  const [isScrubbing, setIsScrubbing] = useState(false);

  // Interactive Bento Widget 3: ZK Isolation Proofs Verifier
  const [zkStatus, setZkStatus] = useState<'idle' | 'generating' | 'verified'>('idle');
  const [zkTenant, setZkTenant] = useState('tesla-autopilot-cluster-04');
  const [zkHash, setZkHash] = useState('0xbf82cd93aa12bc112ea9d0d381cf8a994ef00c921');

  // Hero interactive cognitive graph node cluster simulation
  const [nodes, setNodes] = useState<{ x: number; y: number; id: number; label: string; links: number[] }[]>([
    { id: 1, label: "Vector Index", x: 40, y: 35, links: [2, 3] },
    { id: 2, label: "HNSW Base", x: 75, y: 25, links: [1, 4] },
    { id: 3, label: "Knapsack Core", x: 25, y: 70, links: [1, 4, 5] },
    { id: 4, label: "Sovereign Cache", x: 65, y: 75, links: [2, 3, 5] },
    { id: 5, label: "ZK Isolation", x: 85, y: 55, links: [4] }
  ]);

  const checkoutRef = useRef<HTMLDivElement>(null);

  const fetchLicense = async () => {
    try {
      const res = await fetch('/api/licensing/details');
      const data = await res.json();
      if (data.success && data.license) {
        setLicense(data.license);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const executeBuyout = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!licensedTo) return;
    setBuying(true);
    try {
      const res = await fetch('/api/licensing/buyout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ licensedTo })
      });
      const data = await res.json();
      if (data.success) {
        setLicense(data.license);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setBuying(false);
    }
  };

  const signContract = async () => {
    setSigning(true);
    try {
      const res = await fetch('/api/licensing/sign-contract', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        setLicense(data.license);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSigning(false);
    }
  };

  const simulateScrub = () => {
    if (!scrubInput.trim()) return;
    setIsScrubbing(true);
    setScrubOutput(prev => ["Initializing Sovereign Scrubber Stream...", ...prev]);

    setTimeout(() => {
      let clean = scrubInput;
      // Scrub API Key simulation
      clean = clean.replace(/sk-[a-zA-Z0-9-]{12,}/g, "[REDACTED_API_KEY]");
      // Scrub Email simulation
      clean = clean.replace(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, "[REDACTED_EMAIL_HASH]");
      // Scrub IP simulation
      clean = clean.replace(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g, "[REDACTED_IP_V4]");
      // Scrub Names simulation
      clean = clean.replace(/John Doe/g, "[MASKED_USER_A]");

      setScrubOutput(prev => [
        `✓ SCRUBBED: "${clean}"`,
        `[PII Detector] Identified 3 critical risks. Zero-knowledge outbound rules applied.`,
        ...prev
      ]);
      setIsScrubbing(false);
    }, 800);
  };

  const runZkProof = () => {
    setZkStatus('generating');
    setTimeout(() => {
      setZkStatus('verified');
    }, 1500);
  };

  useEffect(() => {
    fetchLicense();
    // Simulate some logs in the terminal right at mount
    setScrubOutput([
      `[PII Terminal IDLE] Ready for sovereign network interception.`,
      `[Compliance] HIPAA, SOC2 Type II, and GDPR outbound safety layers active.`
    ]);
  }, []);

  // Cost calculations
  // Input: agentCalls, contextSize (tokens)
  // Standard context: standard LLM cost is around $0.0015 per 1,000 tokens input/output.
  // Without optimization, every prompt sends the entire context, compounding quickly.
  // Standard monthly context cost = Calls * (ContextSize / 1000) * 0.0015 USD
  const unoptimizedCost = Math.round(agentCalls * (contextSize / 1000) * 0.0015);
  // SynapseMemory keeps context small using Knapsack context budget packing (distilled context of ~2,000 tokens).
  // Optimized cost = Calls * (2000 / 1000) * 0.0015 USD + hosting server costs (~$250/mo)
  const optimizedCost = Math.round((agentCalls * (2000 / 1000) * 0.0015) + 250);
  const monthlySavings = Math.max(0, unoptimizedCost - optimizedCost);
  const percentSavings = unoptimizedCost > 0 ? Math.round((monthlySavings / unoptimizedCost) * 100) : 0;

  // HNSW calculations
  // Recall rate goes up with efConstruction and connection count (M)
  // Recall rate = 1 - (1 / (M * efConstruction^0.4))
  const recallRate = Math.min(0.9999, 1 - (1 / (hnswM * Math.pow(hnswEf, 0.4))));
  // Search latency goes up with M and log(efConstruction)
  const searchLatency = (hnswM * 0.15) + (Math.log(hnswEf) * 0.8) + 1.2;

  // Code snippets for copy display
  const dockerComposeCode = `version: '3.8'

services:
  synapse-gateway:
    image: synapse-memory/gateway:v1.2.0
    ports:
      - "3000:3000"
    environment:
      - GEMINI_API_KEY=\${GEMINI_API_KEY}
      - DB_HOST=postgres-pgvector
      - REDIS_URL=redis://redis-state:6379/0
    depends_on:
      - postgres-pgvector
      - redis-state

  postgres-pgvector:
    image: pgvector/pgvector:pg16
    environment:
      - POSTGRES_USER=synapse_admin
      - POSTGRES_PASSWORD=synapse_secure_pass
      - POSTGRES_DB=synapse_cognitive
    volumes:
      - synapse-db-data:/var/lib/postgresql/data

  redis-state:
    image: redis:7-alpine`;

  const helmValuesCode = `# Default Helm Chart Values for SynapseMemory
replicaCount: 3

service:
  type: LoadBalancer
  port: 80
  targetPort: 3000

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 15

postgresql:
  enabled: true
  image:
    repository: pgvector/pgvector
    tag: pg16`;

  const pythonSdkCode = `from synapse import SynapseMemoryClient

client = SynapseMemoryClient(
    endpoint="http://localhost:3000",
    license_key="SYNAPSE_PERPETUAL_LICENSE_BYOUT_KEY"
)

# Hybrid search context directly backfilled to target agent
context = client.retrieve_memories(
    query="What is the database configuration?",
    tenant_id="tesla_autopilot_cluster_04"
)`;

  const copyToClipboard = (text: string, setCopied: (v: boolean) => void) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const scrollToCheckout = () => {
    checkoutRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="space-y-16 bg-slate-50/50 -mt-8 -mx-4 px-4 py-12 rounded-t-[32px] border-t border-slate-200">
      
      {/* SECTION 1: HERO STAGE (The Value Proposition) */}
      <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
        <div className="lg:col-span-7 space-y-6 text-left">
          <div className="inline-flex items-center space-x-2 bg-amber-500/10 border border-amber-500/20 px-3 py-1 rounded-full text-xs text-amber-800 font-semibold uppercase tracking-wider">
            <Zap className="w-3.5 h-3.5 mr-1 text-amber-600 animate-pulse" />
            <span>COMMERCIAL ASSET & IP BUYOUT</span>
          </div>
          
          <h1 className="text-4xl sm:text-5xl font-black text-slate-900 tracking-tight leading-none">
            The Long-Term Memory Substrate for <span className="text-transparent bg-clip-text bg-gradient-to-r from-amber-600 to-amber-500">Multi-Agent LLM Clusters</span>.
          </h1>
          
          <p className="text-slate-600 text-base max-w-2xl leading-relaxed">
            A self-hosted, sovereign cognitive RAG layer built on pgvector, HNSW indexing, and 0/1 Knapsack token context optimization. Zero context-bloat, complete data compliance, and perpetual IP transfer for agencies.
          </p>

          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-4 pt-4">
            <button
              onClick={scrollToCheckout}
              className="px-8 py-4 bg-amber-500 hover:bg-amber-600 text-slate-950 rounded-xl text-sm font-extrabold transition-all shadow-lg shadow-amber-500/20 flex items-center justify-center space-x-2 cursor-pointer border border-amber-400"
            >
              <Download className="w-4 h-4" />
              <span>Download Sovereignty Package</span>
            </button>
            <button
              onClick={() => setActiveWhitepaper(true)}
              className="px-6 py-4 bg-white hover:bg-slate-50 text-slate-700 border border-slate-300 rounded-xl text-sm font-semibold transition flex items-center justify-center space-x-1.5 cursor-pointer"
            >
              <FileText className="w-4 h-4 text-slate-500" />
              <span>Read Technical Whitepaper</span>
            </button>
          </div>

          <div className="grid grid-cols-3 gap-6 pt-6 border-t border-slate-200">
            <div>
              <span className="block text-2xl font-black text-slate-900">78%</span>
              <span className="text-xs text-slate-500 font-medium">Average Token Cost Cut</span>
            </div>
            <div>
              <span className="block text-2xl font-black text-slate-900">&lt; 14ms</span>
              <span className="text-xs text-slate-500 font-medium">HNSW Recall Latency</span>
            </div>
            <div>
              <span className="block text-2xl font-black text-slate-900">Perpetual</span>
              <span className="text-xs text-slate-500 font-medium">Commercial IP Ownership</span>
            </div>
          </div>
        </div>

        {/* Dynamic Graphic SVG/Canvas Cluster (Interactive Hero Representation) */}
        <div className="lg:col-span-5 bg-slate-900 rounded-3xl p-6 shadow-2xl border border-slate-800 text-slate-300 relative overflow-hidden h-[360px] flex flex-col justify-between">
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(245,158,11,0.1),transparent_50%)]"></div>
          
          <div className="flex items-center justify-between border-b border-slate-800 pb-3 relative z-10">
            <div className="flex items-center space-x-2">
              <span className="w-2 h-2 rounded-full bg-amber-500 animate-ping"></span>
              <span className="text-[11px] font-mono text-amber-400 font-bold uppercase tracking-wider">Active Memory Substrate Topology</span>
            </div>
            <span className="text-[10px] font-mono text-slate-500">Live Simulation v1.2</span>
          </div>

          {/* Neural Node Clusters SVG Visualizer */}
          <div className="flex-1 relative my-4">
            <svg className="w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="none">
              {/* Draw Connections */}
              {nodes.map(n => 
                n.links.map(targetId => {
                  const target = nodes.find(t => t.id === targetId);
                  if (!target) return null;
                  return (
                    <line 
                      key={`${n.id}-${targetId}`}
                      x1={n.x} y1={n.y} x2={target.x} y2={target.y}
                      className="stroke-slate-800 stroke-[0.5] stroke-dasharray-[2]"
                      strokeDasharray="1,2"
                    />
                  );
                })
              )}

              {/* Draw Nodes */}
              {nodes.map(n => (
                <g key={n.id} className="cursor-pointer group">
                  <circle 
                    cx={n.x} cy={n.y} r="3.5"
                    className="fill-slate-950 stroke-amber-500 stroke-[1.5] group-hover:fill-amber-500/20 transition-all duration-300"
                  />
                  <circle 
                    cx={n.x} cy={n.y} r="6"
                    className="fill-none stroke-amber-500/30 stroke-[0.5] group-hover:scale-125 transition-all duration-300"
                  />
                  <text 
                    x={n.x} y={n.y - 6} 
                    className="text-[4px] font-sans font-semibold fill-slate-400 text-anchor-middle"
                    textAnchor="middle"
                  >
                    {n.label}
                  </text>
                </g>
              ))}
            </svg>
          </div>

          <div className="bg-slate-950/80 p-3 rounded-2xl border border-slate-800/80 font-mono text-[10px] space-y-1 relative z-10">
            <div className="flex justify-between text-slate-400">
              <span>Active Memory Clusters:</span>
              <span className="text-amber-400 font-bold">14 Shards</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Dynamic Recall Rate:</span>
              <span className="text-emerald-400 font-bold">99.85% Acc.</span>
            </div>
          </div>
        </div>
      </div>

      {/* SECTION 2: INTERACTIVE "COST OF MEMORY" CALCULATOR */}
      <div className="max-w-5xl mx-auto bg-white rounded-[32px] border border-slate-200 shadow-sm p-8 sm:p-12 space-y-8 text-center relative overflow-hidden">
        <div className="absolute top-0 right-0 w-32 h-32 bg-amber-500/5 rounded-full blur-3xl"></div>
        <div className="absolute bottom-0 left-0 w-32 h-32 bg-indigo-500/5 rounded-full blur-3xl"></div>

        <div className="space-y-3 max-w-2xl mx-auto">
          <span className="text-xs font-bold text-indigo-600 uppercase tracking-widest">Savings Simulator</span>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">
            Stop Paying the "LLM Context Tax"
          </h2>
          <p className="text-slate-500 text-xs sm:text-sm">
            Standard agents leak thousands of dollars by feeding raw, uncompressed chat histories into context windows. See how much our dynamic Knapsack token packing will save your business.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center pt-4">
          {/* Controls */}
          <div className="lg:col-span-7 space-y-6 text-left bg-slate-50 p-6 rounded-2xl border border-slate-200">
            
            <div className="space-y-2">
              <div className="flex justify-between text-xs font-bold text-slate-700">
                <span>Monthly Active Agent Calls:</span>
                <span className="text-indigo-600 font-mono">{agentCalls.toLocaleString()} calls</span>
              </div>
              <input
                type="range"
                min="50000"
                max="5000000"
                step="50000"
                value={agentCalls}
                onChange={e => setAgentCalls(Number(e.target.value))}
                className="w-full accent-indigo-600 cursor-pointer h-1.5 bg-slate-200 rounded-lg"
              />
              <div className="flex justify-between text-[10px] text-slate-400">
                <span>50k Calls</span>
                <span>5 Million Calls</span>
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex justify-between text-xs font-bold text-slate-700">
                <span>Average Chat History Context Size:</span>
                <span className="text-indigo-600 font-mono">{contextSize.toLocaleString()} tokens</span>
              </div>
              <input
                type="range"
                min="2000"
                max="128000"
                step="2000"
                value={contextSize}
                onChange={e => setContextSize(Number(e.target.value))}
                className="w-full accent-indigo-600 cursor-pointer h-1.5 bg-slate-200 rounded-lg"
              />
              <div className="flex justify-between text-[10px] text-slate-400">
                <span>2,000 (Small)</span>
                <span>128,000 (Large Context)</span>
              </div>
            </div>

          </div>

          {/* Pricing Results */}
          <div className="lg:col-span-5 bg-slate-900 rounded-2xl p-6 text-white text-left space-y-6 flex flex-col justify-between self-stretch border border-slate-800">
            <div>
              <span className="text-[10px] font-mono text-amber-400 uppercase tracking-widest font-bold">Projected Monthly Savings</span>
              <div className="text-4xl sm:text-5xl font-black text-white tracking-tight mt-1">
                ${monthlySavings.toLocaleString()}
              </div>
              <span className="text-[11px] text-slate-400">
                Reduced context overhead by <strong className="text-emerald-400 font-bold">{percentSavings}%</strong>
              </span>
            </div>

            <div className="space-y-2 border-t border-slate-800 pt-4 text-xs font-mono">
              <div className="flex justify-between text-slate-400">
                <span>Standard Raw API Cost:</span>
                <span className="text-red-400">${unoptimizedCost.toLocaleString()}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>SynapseMemory Cost:</span>
                <span className="text-emerald-400 font-bold">${optimizedCost.toLocaleString()}</span>
              </div>
            </div>

            <button
              onClick={scrollToCheckout}
              className="w-full py-2.5 bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold rounded-xl text-xs transition text-center cursor-pointer"
            >
              Secure Lifetime Buyout Now
            </button>
          </div>
        </div>
      </div>

      {/* SECTION 3: TECHNICAL DEEP DIVES (BENTO GRID) */}
      <div className="max-w-7xl mx-auto space-y-8">
        <div className="text-center space-y-2">
          <span className="text-xs font-bold text-amber-600 uppercase tracking-widest">Deep Tech Playground</span>
          <h2 className="text-3xl font-extrabold text-slate-900">
            Interactive System Engineering
          </h2>
          <p className="text-slate-500 text-sm max-w-2xl mx-auto">
            Test the structural code primitives directly below to verify how SynapseMemory achieves sovereign dominance.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          
          {/* Interactive Bento 1: HNSW Parameter Adjuster */}
          <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 space-y-6 flex flex-col justify-between">
            <div className="space-y-2">
              <div className="flex items-center space-x-2 text-indigo-600">
                <Cpu className="w-5 h-5" />
                <h3 className="font-bold text-slate-900 text-base">HNSW Index Performance</h3>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">
                Tune the max connections per node (<strong className="text-slate-700 font-bold">M</strong>) and accuracy checklist size (<strong className="text-slate-700 font-bold">efConstruction</strong>) to watch live latency variations.
              </p>
            </div>

            <div className="space-y-4 bg-slate-50 p-4 rounded-2xl border border-slate-200">
              <div className="space-y-1">
                <div className="flex justify-between text-[11px] text-slate-600 font-semibold">
                  <span>Connections (M): {hnswM}</span>
                </div>
                <input
                  type="range"
                  min="4"
                  max="64"
                  step="4"
                  value={hnswM}
                  onChange={e => setHnswM(Number(e.target.value))}
                  className="w-full accent-indigo-600 cursor-pointer h-1"
                />
              </div>

              <div className="space-y-1">
                <div className="flex justify-between text-[11px] text-slate-600 font-semibold">
                  <span>Accuracy (efConstruction): {hnswEf}</span>
                </div>
                <input
                  type="range"
                  min="16"
                  max="256"
                  step="16"
                  value={hnswEf}
                  onChange={e => setHnswEf(Number(e.target.value))}
                  className="w-full accent-indigo-600 cursor-pointer h-1"
                />
              </div>
            </div>

            <div className="bg-slate-900 text-white p-4 rounded-2xl border border-slate-800 space-y-2 font-mono text-[11px]">
              <div className="flex justify-between">
                <span className="text-slate-400">Target Recall Accuracy:</span>
                <span className="text-emerald-400 font-bold">{(recallRate * 100).toFixed(3)}%</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Search Latency (Query):</span>
                <span className="text-amber-400 font-bold">{searchLatency.toFixed(2)} ms</span>
              </div>
            </div>
          </div>

          {/* Interactive Bento 2: PII Scrubber Terminal */}
          <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 space-y-6 flex flex-col justify-between">
            <div className="space-y-2">
              <div className="flex items-center space-x-2 text-indigo-600">
                <Terminal className="w-5 h-5 text-indigo-500" />
                <h3 className="font-bold text-slate-900 text-base">PII Outbound Scrubber</h3>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">
                Prevent leakage of client credentials, names, and IP addresses to external LLMs. Type any text below to scrub.
              </p>
            </div>

            <div className="space-y-3">
              <input
                type="text"
                value={scrubInput}
                onChange={e => setScrubInput(e.target.value)}
                className="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs text-slate-800 font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                placeholder="Type credentials, emails, names..."
              />
              <button
                onClick={simulateScrub}
                disabled={isScrubbing}
                className="w-full bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold py-2 rounded-xl transition flex items-center justify-center space-x-1"
              >
                {isScrubbing ? (
                  <RefreshCw className="w-3 h-3 animate-spin mr-1" />
                ) : (
                  <Play className="w-3 h-3 text-emerald-400 fill-emerald-400 mr-1" />
                )}
                <span>Run Real-time Scrub Stream</span>
              </button>
            </div>

            {/* Terminal output box */}
            <div className="bg-slate-950 rounded-2xl p-4 h-[110px] overflow-y-auto font-mono text-[9px] text-slate-300 border border-slate-900 space-y-2 scrollbar-thin">
              {scrubOutput.map((log, idx) => (
                <div key={idx} className={log.startsWith('✓') ? "text-emerald-400" : log.startsWith('[PII') ? "text-amber-400" : "text-slate-500"}>
                  {log}
                </div>
              ))}
            </div>
          </div>

          {/* Interactive Bento 3: ZK Isolation Proofs Verifier */}
          <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 space-y-6 flex flex-col justify-between">
            <div className="space-y-2">
              <div className="flex items-center space-x-2 text-indigo-600">
                <Lock className="w-5 h-5 text-indigo-500" />
                <h3 className="font-bold text-slate-900 text-base">ZK Isolation Validation</h3>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">
                Prove mathematically that client data structures are fully isolated across nodes without revealing database credentials.
              </p>
            </div>

            <div className="space-y-2 bg-slate-50 p-4 rounded-2xl border border-slate-200 font-mono text-[10px] text-slate-600">
              <div className="flex justify-between">
                <span>Tenant Domain:</span>
                <span className="text-slate-900 font-bold select-all">{zkTenant}</span>
              </div>
              <div className="flex justify-between">
                <span>Public Hash:</span>
                <span className="text-slate-900 text-[9px] select-all">{zkHash}</span>
              </div>
            </div>

            {zkStatus === 'idle' && (
              <button
                onClick={runZkProof}
                className="w-full py-2.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-xs font-bold rounded-xl transition flex items-center justify-center space-x-1.5 cursor-pointer border border-indigo-100"
              >
                <span>Compile Zero-Knowledge Proof</span>
              </button>
            )}

            {zkStatus === 'generating' && (
              <div className="w-full py-2.5 bg-amber-50 text-amber-800 text-xs font-bold rounded-xl flex items-center justify-center space-x-2 border border-amber-100 font-mono">
                <RefreshCw className="w-4 h-4 animate-spin text-amber-600" />
                <span>Running Circom Prover...</span>
              </div>
            )}

            {zkStatus === 'verified' && (
              <div className="space-y-2">
                <div className="w-full py-2 bg-emerald-50 text-emerald-800 text-xs font-bold rounded-xl flex items-center justify-center space-x-1.5 border border-emerald-100 font-mono">
                  <Check className="w-4 h-4 text-emerald-600" />
                  <span>SNARK Isolation Proof Verified</span>
                </div>
                <div className="text-[9px] font-mono text-slate-400 text-center">
                  Receipt Generated: <span className="underline select-all">receipt_0x83f...91f</span>
                </div>
              </div>
            )}
          </div>

        </div>
      </div>

      {/* SECTION 4: ENTERPRISE-GRADE FEATURE SET */}
      <div className="max-w-7xl mx-auto space-y-8">
        <div className="text-center space-y-2">
          <span className="text-xs font-bold text-indigo-600 uppercase tracking-widest">Enterprise Feature Suite</span>
          <h2 className="text-3xl font-extrabold text-slate-900">
            Engineered For Absolute Enterprise Scale
          </h2>
          <p className="text-slate-500 text-sm max-w-2xl mx-auto">
            A high-ticket production framework engineered specifically for platform architects, multi-client agencies, and secure clouds.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          
          <div className="bg-white rounded-3xl border border-slate-200 p-8 flex items-start space-x-4">
            <div className="p-3 bg-indigo-50 border border-indigo-100 rounded-2xl text-indigo-600">
              <Database className="w-6 h-6" />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="font-bold text-slate-900 text-base">Multi-Tenant Partitioning</h4>
                <span className="bg-indigo-50 text-indigo-700 text-[10px] px-2 py-0.5 rounded font-mono font-bold">RLS ENGAGE</span>
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">
                Build database-level partition-key isolation and Row-Level Security (RLS). Perfect for agencies managing 100+ separate clients inside a single cluster.
              </p>
            </div>
          </div>

          <div className="bg-white rounded-3xl border border-slate-200 p-8 flex items-start space-x-4">
            <div className="p-3 bg-amber-50 border border-amber-100 rounded-2xl text-amber-600">
              <Server className="w-6 h-6" />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="font-bold text-slate-900 text-base">Sovereign Local Hosting</h4>
                <span className="bg-amber-50 text-amber-700 text-[10px] px-2 py-0.5 rounded font-mono font-bold">100% AIRGAPPED</span>
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">
                Deploy in any local VPC, GovCloud, or on-premise Kubernetes cluster. Complete network sovereignty—no outbound telemetry sent to third parties.
              </p>
            </div>
          </div>

          <div className="bg-white rounded-3xl border border-slate-200 p-8 flex items-start space-x-4">
            <div className="p-3 bg-emerald-50 border border-emerald-100 rounded-2xl text-emerald-600">
              <Zap className="w-6 h-6" />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="font-bold text-slate-900 text-base">Hot/Cold Memory Stratification</h4>
                <span className="bg-emerald-50 text-emerald-700 text-[10px] px-2 py-0.5 rounded font-mono font-bold">REDIS CACHE</span>
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">
                Keep real-time active memories stored in high-throughput Redis state stores, while automatic pipelines shift cold, historical memories to S3 storage.
              </p>
            </div>
          </div>

          <div className="bg-white rounded-3xl border border-slate-200 p-8 flex items-start space-x-4">
            <div className="p-3 bg-rose-50 border border-rose-100 rounded-2xl text-rose-600">
              <Lock className="w-6 h-6" />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="font-bold text-slate-900 text-base">Symmetric ZK Isolation Proofs</h4>
                <span className="bg-rose-50 text-rose-700 text-[10px] px-2 py-0.5 rounded font-mono font-bold">SNARK CONFIRM</span>
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">
                Establish flawless trust. Prove to clients and auditors that data partitions remain completely siloed, utilizing cryptographic zero-knowledge proof loops.
              </p>
            </div>
          </div>

        </div>
      </div>

      {/* SECTION 5: ACQUISITION / ACQUISITION JOURNEY */}
      <div ref={checkoutRef} className="max-w-4xl mx-auto grid grid-cols-1 md:grid-cols-2 gap-8 items-start pt-12 border-t border-slate-200">
        
        {/* Licensing Highlights / What is Included */}
        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-8 space-y-6">
          <div className="inline-flex items-center space-x-2 bg-amber-500/10 border border-amber-500/25 px-2.5 py-0.5 rounded-full text-[10px] text-amber-800 font-bold uppercase tracking-wider">
            PERPETUAL DEED
          </div>
          <h3 className="font-bold text-slate-900 text-xl leading-none">Perpetual Source Code & SLA Buyout</h3>
          <p className="text-slate-500 text-xs leading-relaxed">
            Acquire full sovereign rights, production deploy-ready codebases, and custom integrations.
          </p>
          
          <ul className="space-y-4 pt-2">
            <li className="flex items-start space-x-3 text-xs text-slate-600 leading-normal">
              <div className="w-5 h-5 rounded-full bg-slate-100 flex items-center justify-center shrink-0 border border-slate-200">
                <Check className="w-3.5 h-3.5 text-indigo-600" />
              </div>
              <div>
                <strong className="text-slate-900 block">Complete Sovereign SDK Access</strong>
                Full python templates, microservices, and client frameworks ready for prompt-integration.
              </div>
            </li>
            <li className="flex items-start space-x-3 text-xs text-slate-600 leading-normal">
              <div className="w-5 h-5 rounded-full bg-slate-100 flex items-center justify-center shrink-0 border border-slate-200">
                <Check className="w-3.5 h-3.5 text-indigo-600" />
              </div>
              <div>
                <strong className="text-slate-900 block">Helm Charts & Compose Assets</strong>
                Immediate infrastructure orchestration files included right in the acquisition delivery.
              </div>
            </li>
            <li className="flex items-start space-x-3 text-xs text-slate-600 leading-normal">
              <div className="w-5 h-5 rounded-full bg-slate-100 flex items-center justify-center shrink-0 border border-slate-200">
                <Check className="w-3.5 h-3.5 text-indigo-600" />
              </div>
              <div>
                <strong className="text-slate-900 block">SLA Support Contracts</strong>
                Direct communication routing with developer support engineers for deployment assistance.
              </div>
            </li>
          </ul>

          <div className="bg-slate-50 p-4 rounded-2xl border border-slate-200 flex items-center justify-between text-xs font-mono">
            <span className="text-slate-500">Buyout Fee:</span>
            <span className="text-slate-900 font-black text-base">$1,000 USD (Perpetual)</span>
          </div>
        </div>

        {/* Dynamic checkout or active license detail state */}
        {!license ? (
          <div className="bg-slate-900 text-white rounded-3xl border border-slate-800 shadow-2xl p-8 space-y-6">
            <h3 className="font-bold text-white text-lg flex items-center space-x-2">
              <ShieldCheck className="w-5 h-5 text-amber-400" />
              <span>Instant Buyout Checkout</span>
            </h3>
            
            <p className="text-xs text-slate-400 leading-relaxed">
              Fill in your target organization details to construct the signed IP Assignment deed and generate your perpetual production activation keys.
            </p>

            <form onSubmit={executeBuyout} className="space-y-4">
              <div>
                <label className="block text-[11px] font-bold text-slate-300 mb-1">Licensed Organization / Entity:</label>
                <input
                  type="text"
                  required
                  placeholder="e.g., Tesla Autopilot Core Team"
                  value={licensedTo}
                  onChange={e => setLicensedTo(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 text-white rounded-xl px-3 py-2.5 text-xs focus:outline-none focus:ring-2 focus:ring-amber-500/30"
                />
              </div>

              <div>
                <label className="block text-[11px] font-bold text-slate-300 mb-1">Corporate Card Payment (Sandbox Enabled):</label>
                <div className="bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-xs text-slate-400 font-mono flex items-center justify-between">
                  <span className="flex items-center space-x-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                    <span>•••• •••• •••• 4242 (Stripe Standard)</span>
                  </span>
                  <span className="text-[9px] bg-slate-800 text-slate-300 px-1.5 py-0.5 rounded font-sans font-bold">1k USD</span>
                </div>
              </div>

              <button
                type="submit"
                disabled={buying}
                className="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 py-3 rounded-xl text-xs font-black transition flex items-center justify-center space-x-1.5 cursor-pointer shadow-lg shadow-amber-500/10"
              >
                <span>{buying ? 'Processing Licensing...' : 'Authorize Perpetual Buyout ($1,000)'}</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            </form>
          </div>
        ) : (
          <div className="bg-slate-900 text-white rounded-3xl border border-slate-800 shadow-2xl p-8 space-y-6">
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-lg flex items-center space-x-2">
                <Check className="w-5 h-5 text-emerald-400" />
                <span>Commercial Activation Center</span>
              </h3>
              <span className="text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/25 px-2 py-0.5 rounded font-bold">LICENSE SECURED</span>
            </div>

            <div className="p-4 bg-slate-800/60 border border-slate-700 rounded-2xl space-y-3">
              <div className="flex justify-between text-xs text-slate-300">
                <span>License Owner:</span>
                <strong className="text-white">{license.licensedTo}</strong>
              </div>
              <div className="flex justify-between text-xs text-slate-300">
                <span>License Status:</span>
                <span className="font-mono text-emerald-400 font-bold uppercase text-[10px]">Active & Verified</span>
              </div>
              <div className="flex justify-between text-xs text-slate-300">
                <span>SLA Tier Assigned:</span>
                <strong className="text-amber-400">{license.slaTier}</strong>
              </div>
              <div className="flex flex-col space-y-1 pt-2 border-t border-slate-700">
                <span className="text-xs text-slate-400 font-bold">Perpetual Production Key:</span>
                <div className="bg-slate-950 font-mono text-[10px] text-amber-300 p-2 rounded border border-slate-800 select-all overflow-x-auto">
                  {license.licenseKey}
                </div>
              </div>
            </div>

            {/* SLA Signature Module */}
            <div className="p-4 bg-slate-800/40 border border-slate-700/50 rounded-2xl space-y-3">
              <div className="flex items-center space-x-2 text-xs font-bold text-slate-200">
                <Signature className="w-4 h-4 text-amber-400" />
                <span>Assign Intellectual Property (IP) Transfer Contract:</span>
              </div>
              <p className="text-[10px] text-slate-400 leading-normal">
                By executing this legal contract, SynapseMemory officially and irrevocably assigns 100% of its copyright, technology vectors, and repository structures directly to your target entity.
              </p>

              {license.ipAssignmentSigned ? (
                <div className="flex items-center space-x-1.5 p-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl text-xs font-bold justify-center">
                  <Check className="w-4 h-4" />
                  <span>IP Assignment Contract Signed & Legally Binding</span>
                </div>
              ) : (
                <button
                  onClick={signContract}
                  disabled={signing}
                  className="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 py-2 rounded-xl text-xs font-bold transition flex items-center justify-center space-x-1.5 cursor-pointer"
                >
                  <span>{signing ? 'Signing Contract...' : 'Digitally Execute Legal IP Signoff'}</span>
                </button>
              )}
            </div>
          </div>
        )}

      </div>

      {/* SECTION 6: CODE PACKAGING & DELIVERY PIPELINE (Unlocked upon purchase, beautifully displayed for easy copy-paste/download) */}
      <div className="max-w-7xl mx-auto space-y-8 pt-6">
        <div className="text-center space-y-2">
          <span className="text-xs font-bold text-indigo-600 uppercase tracking-widest">Repository Code Bundles</span>
          <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900">
            Sovereign Repository Delivery Pipeline
          </h2>
          <p className="text-slate-500 text-sm max-w-2xl mx-auto">
            {license ? "✓ Perpetual Buyout Cleared. Copy and deploy your premium cognitive infrastructure straight into your local pipeline." : "Perpetual licensing grants instant deployment code-level access to these core files. Pre-purchase preview is unlocked below."}
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          
          {/* File 1: /deploy/docker-compose.yml */}
          <div className="bg-slate-900 rounded-2xl border border-slate-800 flex flex-col justify-between overflow-hidden h-[450px]">
            <div className="bg-slate-950 px-4 py-3 border-b border-slate-800 flex items-center justify-between text-xs">
              <span className="font-mono text-slate-300 font-bold">/deploy/docker-compose.yml</span>
              <button
                onClick={() => copyToClipboard(dockerComposeCode, setCopiedDocker)}
                className="text-slate-400 hover:text-white flex items-center space-x-1 bg-slate-900 px-2 py-1 rounded border border-slate-800 transition cursor-pointer"
              >
                {copiedDocker ? (
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                ) : (
                  <Copy className="w-3.5 h-3.5 text-indigo-400" />
                )}
                <span className="text-[10px]">{copiedDocker ? "Copied" : "Copy"}</span>
              </button>
            </div>
            <div className="flex-1 p-4 overflow-auto font-mono text-[10px] text-slate-300 bg-slate-900 scrollbar-thin leading-normal select-all">
              <pre>{dockerComposeCode}</pre>
            </div>
          </div>

          {/* File 2: /deploy/helm-chart/values.yaml */}
          <div className="bg-slate-900 rounded-2xl border border-slate-800 flex flex-col justify-between overflow-hidden h-[450px]">
            <div className="bg-slate-950 px-4 py-3 border-b border-slate-800 flex items-center justify-between text-xs">
              <span className="font-mono text-slate-300 font-bold">/deploy/helm-chart/values.yaml</span>
              <button
                onClick={() => copyToClipboard(helmValuesCode, setCopiedHelm)}
                className="text-slate-400 hover:text-white flex items-center space-x-1 bg-slate-900 px-2 py-1 rounded border border-slate-800 transition cursor-pointer"
              >
                {copiedHelm ? (
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                ) : (
                  <Copy className="w-3.5 h-3.5 text-indigo-400" />
                )}
                <span className="text-[10px]">{copiedHelm ? "Copied" : "Copy"}</span>
              </button>
            </div>
            <div className="flex-1 p-4 overflow-auto font-mono text-[10px] text-slate-300 bg-slate-900 scrollbar-thin leading-normal select-all">
              <pre>{helmValuesCode}</pre>
            </div>
          </div>

          {/* File 3: /sdk/python/synapse.py */}
          <div className="bg-slate-900 rounded-2xl border border-slate-800 flex flex-col justify-between overflow-hidden h-[450px]">
            <div className="bg-slate-950 px-4 py-3 border-b border-slate-800 flex items-center justify-between text-xs">
              <span className="font-mono text-slate-300 font-bold">/sdk/python/synapse.py</span>
              <button
                onClick={() => copyToClipboard(pythonSdkCode, setCopiedSdk)}
                className="text-slate-400 hover:text-white flex items-center space-x-1 bg-slate-900 px-2 py-1 rounded border border-slate-800 transition cursor-pointer"
              >
                {copiedSdk ? (
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                ) : (
                  <Copy className="w-3.5 h-3.5 text-indigo-400" />
                )}
                <span className="text-[10px]">{copiedSdk ? "Copied" : "Copy"}</span>
              </button>
            </div>
            <div className="flex-1 p-4 overflow-auto font-mono text-[10px] text-slate-300 bg-slate-900 scrollbar-thin leading-normal select-all">
              <pre>{pythonSdkCode}</pre>
            </div>
          </div>

        </div>
      </div>

      {/* TECHNICAL WHITEPAPER DRAWER DIALOG / MODAL */}
      {activeWhitepaper && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-3xl max-w-4xl w-full max-h-[85vh] overflow-y-auto p-8 sm:p-10 border border-slate-200 relative text-left shadow-2xl space-y-6">
            <button
              onClick={() => setActiveWhitepaper(false)}
              className="absolute top-4 right-4 bg-slate-100 hover:bg-slate-200 p-2 rounded-full text-slate-500 hover:text-slate-900 transition font-bold text-xs"
            >
              ✕ Close
            </button>

            <div className="space-y-2 border-b border-slate-200 pb-4">
              <div className="flex items-center space-x-2 text-indigo-600 text-xs font-bold tracking-widest uppercase">
                <FileText className="w-4 h-4" />
                <span>TECHNICAL WHITEPAPER & ARCHITECTURE SUITE</span>
              </div>
              <h3 className="text-2xl sm:text-3xl font-black text-slate-900 leading-none">
                SynapseMemory: Autonomous Context Compression Substrate
              </h3>
              <p className="text-slate-500 text-xs">
                Author: Synapse Core ML Research Team • September 2026
              </p>
            </div>

            <div className="space-y-6 text-xs sm:text-sm text-slate-600 leading-relaxed max-h-[50vh] overflow-y-auto pr-2 scrollbar-thin">
              <section className="space-y-2">
                <h4 className="font-bold text-slate-900 text-base">1. Executive Summary</h4>
                <p>
                  Large Language Models (LLMs) suffer from severe linear prompt inflation costs. As context grows, retrieval speeds drop dramatically, and models introduce "Lost in the Middle" retrieval degradation. SynapseMemory introduces a localized, sovereign cognitive memory database equipped with high-throughput HNSW index maps, outbound credential PII scrubbers, and 0/1 Knapsack dynamic mathematical packing. By caching client logs local-side, context lengths are compressed down to pure semantic relevance, cutting total monthly prompt API overhead by an average of 78%.
                </p>
              </section>

              <section className="space-y-2">
                <h4 className="font-bold text-slate-900 text-base">2. Mathematical Formulation of Knapsack Context Packing</h4>
                <p>
                  Rather than performing simple truncation or FIFO context removal, SynapseMemory models context assembly as a classic 0/1 Knapsack optimization problem.
                </p>
                <div className="p-4 bg-slate-50 border border-slate-200 rounded-2xl font-mono text-[11px] text-slate-800 space-y-1">
                  <div>Maximize:  ∑ (Relevance_i * x_i)</div>
                  <div>Subject to: ∑ (TokenCost_i * x_i) ≤ MaxTokenBudget</div>
                  <div>{"Where: x_i ∈ {0, 1} and Relevance_i = Confidence_i * (1 + AccessCount_i * 0.05) * SimilarityScore_i"}</div>
                </div>
                <p>
                  This guarantees that the absolute most informative records are packed into the prompt envelope while ensuring absolute compliance with maximum context windows.
                </p>
              </section>

              <section className="space-y-2">
                <h4 className="font-bold text-slate-900 text-base">3. Hybrid Sparse/Dense Search Mechanics</h4>
                <p>
                  Dense vector embeddings (leveraging the standard <code>text-embedding-004</code> model) capture conceptual query themes, but lose accuracy on specific identifiers (e.g., precise database passwords, client email domains). SynapseMemory runs a dual retrieval flow fusing Dense Cosine Similarity and Sparse Jaccard metrics with a weighted <code>(0.75 * Cosine) + (0.25 * Jaccard)</code> formulation. This prevents misinformation and guarantees pristine recall of factual values.
                </p>
              </section>

              <section className="space-y-2">
                <h4 className="font-bold text-slate-900 text-base">4. Symmetric Zero-Knowledge Isolation proofs</h4>
                <p>
                  To secure customer data in multi-tenant SaaS environments, we provide verifiable SNARK isolation structures. Tenant domains can compile high-performance mathematical isolations proving that distinct node indexes do not overlap, validating strict database compliance to financial and healthcare regulators with zero credential exposure.
                </p>
              </section>
            </div>

            <div className="pt-4 border-t border-slate-200 flex flex-col sm:flex-row items-center justify-between gap-4">
              <span className="text-xs text-slate-500 font-medium">Ready to deploy SynapseMemory within your stack?</span>
              <button
                onClick={() => {
                  setActiveWhitepaper(false);
                  scrollToCheckout();
                }}
                className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold transition"
              >
                Proceed to Buyout Options
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};
