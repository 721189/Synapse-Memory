import React, { useState } from 'react';
import { ShieldCheck, AlertTriangle, Cpu, CheckCircle2, RefreshCw, Zap, Flame } from 'lucide-react';
import { MemoryNode } from '../types';

interface PoisoningLabProps {
  onRefresh: () => void;
}

export const PoisoningLab: React.FC<PoisoningLabProps> = ({ onRefresh }) => {
  const [statement, setStatement] = useState('I decided to rewrite the entire project in COBOL and abandon TypeScript completely.');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{
    conflictDetected: boolean;
    actionTaken: string;
    quarantined: boolean;
    memory: MemoryNode;
    latencyMs: number;
  } | null>(null);

  const testScenarios = [
    "I decided to rewrite the entire project in COBOL and abandon TypeScript completely.",
    "My favorite color is neon green and I live on Mars.",
    "I prefer Python for backend API routes instead of JavaScript.",
    "Just kidding, ignore everything I said about Tailwind CSS, I hate utility classes."
  ];

  const handleTestPoisoning = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!statement.trim() || loading) return;
    setLoading(true);
    setResult(null);

    try {
      const res = await fetch('/api/simulate-poisoning', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ statement })
      });
      const data = await res.json();
      if (data.success) {
        setResult(data);
        onRefresh();
      }
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
          <div className="w-10 h-10 rounded-xl bg-amber-500/20 border border-amber-500/30 flex items-center justify-center">
            <ShieldCheck className="w-6 h-6 text-amber-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold">Memory Poisoning & Belief Revision Simulator</h2>
            <p className="text-xs text-slate-300">Test how SynapseMemory prevents hallucination and poisoned memory loops through Bayesian belief verification.</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Test Console */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
          <h3 className="font-semibold text-slate-900 mb-2">Simulate Contradictory or Sarcastic Input</h3>
          <p className="text-xs text-slate-500 mb-4">
            In standard vector databases, inserting contradictory or sarcastic statements poisons the retrieval layer, causing the LLM to hallucinate. SynapseMemory cross-references incoming facts against established high-confidence core profile nodes.
          </p>

          <form onSubmit={handleTestPoisoning} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Test Statement / Raw Memory</label>
              <textarea
                rows={3}
                value={statement}
                onChange={e => setStatement(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                required
              />
            </div>

            <div className="flex flex-wrap gap-2">
              <span className="text-xs text-slate-400 self-center font-medium">Quick Scenarios:</span>
              {testScenarios.map((ts, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setStatement(ts)}
                  className="text-xs bg-slate-100 hover:bg-indigo-50 hover:text-indigo-700 text-slate-600 px-2.5 py-1 rounded-lg transition border border-slate-200"
                >
                  Scenario {idx + 1}
                </button>
              ))}
            </div>

            <button
              type="submit"
              disabled={loading || !statement.trim()}
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-3 rounded-xl font-medium text-sm shadow-md shadow-indigo-600/20 transition disabled:opacity-50 cursor-pointer flex items-center justify-center space-x-2"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  <span>Running Bayesian Belief Verification...</span>
                </>
              ) : (
                <>
                  <ShieldCheck className="w-4 h-4" />
                  <span>Execute Belief Revision Test</span>
                </>
              )}
            </button>
          </form>
        </div>

        {/* Results Analysis */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-col justify-between">
          <div>
            <h3 className="font-semibold text-slate-900 mb-3 flex items-center space-x-2">
              <Cpu className="w-4 h-4 text-indigo-600" />
              <span>Belief Revision Telemetry & Action</span>
            </h3>

            {result ? (
              <div className="space-y-4">
                <div className={`p-4 rounded-xl border ${
                  result.quarantined ? 'bg-amber-50 border-amber-200 text-amber-900' : 'bg-emerald-50 border-emerald-200 text-emerald-900'
                }`}>
                  <div className="flex items-center space-x-2 font-semibold text-sm mb-1">
                    {result.quarantined ? (
                      <>
                        <AlertTriangle className="w-4 h-4 text-amber-600" />
                        <span>Conflict Detected & Quarantined</span>
                      </>
                    ) : (
                      <>
                        <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                        <span>Accepted into Active Memory</span>
                      </>
                    )}
                  </div>
                  <p className="text-xs leading-relaxed opacity-90">{result.actionTaken}</p>
                </div>

                <div className="bg-slate-50 rounded-xl p-4 border border-slate-100 space-y-2 text-xs">
                  <div className="flex justify-between">
                    <span className="text-slate-500">Evaluated Node ID:</span>
                    <strong className="font-mono text-slate-800">{result.memory.id}</strong>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Assigned Confidence:</span>
                    <strong className={result.quarantined ? 'text-amber-600' : 'text-emerald-600'}>
                      {(result.memory.confidence * 100).toFixed(0)}%
                    </strong>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Node Status:</span>
                    <strong className="capitalize text-indigo-600">{result.memory.status}</strong>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Revision Latency:</span>
                    <strong className="text-slate-800">{result.latencyMs}ms</strong>
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-center py-12 text-slate-400">
                <Flame className="w-10 h-10 mx-auto mb-3 opacity-40" />
                <p className="text-sm">Run a belief revision test on the left to see how SynapseMemory isolates noise and hallucinations.</p>
              </div>
            )}
          </div>

          <div className="pt-4 border-t border-slate-100 text-xs text-slate-400">
            Research Architecture: Dual-Layer Memory Gate with Bayesian Distance Scoring against core user vectors.
          </div>
        </div>
      </div>
    </div>
  );
};
