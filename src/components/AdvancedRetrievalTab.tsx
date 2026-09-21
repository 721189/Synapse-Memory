import React, { useState } from 'react';
import { Search, Cpu, Zap, Layers, CheckCircle2, ArrowRight } from 'lucide-react';

export const AdvancedRetrievalTab: React.FC = () => {
  const [query, setQuery] = useState("TypeScript styling and backend requirements");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<any>(null);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || loading) return;
    setLoading(true);
    try {
      const res = await fetch('/api/retrieval/hybrid-search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query })
      });
      const json = await res.json();
      if (json.success) setData(json);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-indigo-500/25 border border-indigo-500/30 flex items-center justify-center">
            <Search className="w-6 h-6 text-indigo-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold">Advanced Retrieval: Reciprocal Rank Fusion & Hybrid Search</h2>
            <p className="text-xs text-slate-300">Combining HNSW vector embeddings (semantic) with BM25 keyword matching (exact terms) using RRF & Cross-Encoder reranking.</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Search Console */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
          <h3 className="font-semibold text-slate-900 mb-2">Test Hybrid Retrieval Pipeline</h3>
          <p className="text-xs text-slate-500 mb-4">
            Enter a query to see how BM25 exact keyword scores and HNSW vector scores are mathematically fused.
          </p>

          <form onSubmit={handleSearch} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Search Query</label>
              <input
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                required
              />
            </div>

            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-3 rounded-xl font-medium text-xs shadow-md shadow-indigo-600/20 transition cursor-pointer flex items-center justify-center space-x-2"
            >
              <Search className="w-4 h-4" />
              <span>{loading ? 'Executing Hybrid Pipeline...' : 'Run Hybrid Search & RRF'}</span>
            </button>
          </form>

          {data && data.tracing && (
            <div className="mt-6 p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-2 text-xs">
              <span className="font-semibold text-slate-700 block mb-1">OpenTelemetry Latency Breakdown:</span>
              <div className="flex justify-between text-slate-600">
                <span>Vector Search (HNSW):</span>
                <span className="font-mono text-indigo-600">{data.tracing.vectorSearchMs}ms</span>
              </div>
              <div className="flex justify-between text-slate-600">
                <span>Sparse Match (BM25):</span>
                <span className="font-mono text-indigo-600">{data.tracing.bm25SearchMs}ms</span>
              </div>
              <div className="flex justify-between text-slate-600">
                <span>Rank Fusion (RRF):</span>
                <span className="font-mono text-indigo-600">{data.tracing.rrfFusionMs}ms</span>
              </div>
              <div className="flex justify-between text-slate-600">
                <span>Cross-Encoder Rerank:</span>
                <span className="font-mono text-indigo-600">{data.tracing.crossEncoderRerankMs}ms</span>
              </div>
              <div className="flex justify-between font-bold text-slate-900 pt-2 border-t border-slate-200">
                <span>Total Latency SLA:</span>
                <span className="font-mono text-emerald-600">{data.tracing.totalLatencyMs}ms (&lt; 50ms)</span>
              </div>
            </div>
          )}
        </div>

        {/* Results Panel */}
        <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
          <h3 className="font-semibold text-slate-900 mb-4 flex items-center space-x-2">
            <Layers className="w-4 h-4 text-indigo-600" />
            <span>Fused & Reranked Memory Candidates</span>
          </h3>

          {data && data.results ? (
            <div className="space-y-4">
              {data.results.map((r: any, idx: number) => (
                <div key={r.id} className="p-4 rounded-xl border border-slate-200 bg-slate-50/50 flex flex-col justify-between space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-indigo-600 uppercase tracking-wider">#{idx + 1} • {r.category}</span>
                    <div className="flex items-center space-x-3 text-xs font-mono">
                      <span className="text-slate-500">Vector: <strong className="text-slate-800">{r.vectorScore}</strong></span>
                      <span className="text-slate-500">BM25: <strong className="text-slate-800">{r.bm25Score}</strong></span>
                      <span className="bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded font-bold">RRF: {r.rerankScore}</span>
                    </div>
                  </div>
                  <p className="text-sm text-slate-800 font-medium">"{r.content}"</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-16 text-slate-400">
              <Search className="w-10 h-10 mx-auto mb-3 opacity-40" />
              <p className="text-sm">Run a hybrid search query to inspect RRF fusion and cross-encoder scores.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
