import React, { useState, useEffect } from 'react';
import { Cpu, CheckCircle2, Sliders, Zap, DollarSign } from 'lucide-react';

export const KnapsackTokenBudgetTab: React.FC = () => {
  const [maxTokens, setMaxTokens] = useState(1200);
  const [packingData, setPackingData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const runKnapsackPacking = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/retrieval/knapsack-pack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ maxTokens })
      });
      const data = await res.json();
      if (data.success) {
        setPackingData(data);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    runKnapsackPacking();
  }, []);

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-amber-500/25 border border-amber-500/30 flex items-center justify-center">
            <Cpu className="w-6 h-6 text-amber-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold">Knapsack DP Token Budget Optimizer & Multi-Agent Critic</h2>
            <p className="text-xs text-slate-300">Mathematically maximizing relevance score density while strictly respecting target LLM token budgets and API cost constraints.</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Controls */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4">
          <h3 className="font-semibold text-slate-950 flex items-center space-x-2">
            <Sliders className="w-4 h-4 text-indigo-600" />
            <span>Token Budget Configuration</span>
          </h3>
          <p className="text-xs text-slate-500">
            Set the maximum token capacity for the target LLM context window. The Knapsack Dynamic Programming solver selects the optimal subset.
          </p>

          <div className="space-y-2">
            <div className="flex justify-between text-xs font-semibold text-slate-700">
              <span>Max Token Budget:</span>
              <span className="font-mono text-indigo-600">{maxTokens} tokens</span>
            </div>
            <input
              type="range"
              min="400"
              max="4000"
              step="100"
              value={maxTokens}
              onChange={e => setMaxTokens(Number(e.target.value))}
              className="w-full accent-indigo-600"
            />
          </div>

          <button
            onClick={runKnapsackPacking}
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-2.5 rounded-xl font-medium text-xs shadow-sm transition cursor-pointer flex items-center justify-center space-x-2"
          >
            <Zap className="w-4 h-4" />
            <span>{loading ? 'Solving Knapsack DP...' : 'Optimize Token Packing'}</span>
          </button>

          {packingData && packingData.criticAudit && (
            <div className="p-4 rounded-xl bg-emerald-50 border border-emerald-200 space-y-2 text-xs">
              <div className="flex items-center space-x-2 font-bold text-emerald-800">
                <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                <span>Multi-Agent Critic: {packingData.criticAudit.status}</span>
              </div>
              <p className="text-emerald-700">{packingData.criticAudit.auditNote}</p>
              <div className="text-[11px] font-mono text-emerald-600 font-bold">
                Confidence Boost: {packingData.criticAudit.confidenceBoost}
              </div>
            </div>
          )}
        </div>

        {/* Packed Memories Output */}
        <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-slate-900">Optimal Memory Subset ({packingData?.packedCount || 0} items)</h3>
            <span className="text-xs font-mono bg-slate-100 text-slate-700 px-3 py-1 rounded-lg">
              Consumed: <strong>{packingData?.accumulatedTokens || 0}</strong> / {maxTokens} tokens
            </span>
          </div>

          <div className="space-y-3">
            {packingData?.packedMemories?.map((m: any, idx: number) => (
              <div key={m.id} className="p-4 rounded-xl border border-slate-200 bg-slate-50/50 flex flex-col justify-between space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-bold text-indigo-600 uppercase tracking-wider">#{idx + 1} • {m.category}</span>
                  <div className="flex items-center space-x-3 text-xs font-mono">
                    <span className="text-slate-500">Cost: <strong className="text-slate-800">{m.tokenCost} tokens</strong></span>
                    <span className="bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded font-bold">Density: {m.valueDensity}</span>
                  </div>
                </div>
                <p className="text-xs text-slate-800 font-medium">"{m.content}"</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
