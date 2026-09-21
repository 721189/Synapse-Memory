import React, { useState, useEffect } from 'react';
import { Clock, TrendingDown, RefreshCw, Database } from 'lucide-react';
import { MemoryNode } from '../types';

export const TemporalDecayTab: React.FC = () => {
  const [decayData, setDecayData] = useState<any[]>([]);
  const [formula, setFormula] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchDecay = async () => {
    try {
      const res = await fetch('/api/memory/temporal-decay');
      const data = await res.json();
      if (data.success) {
        setDecayData(data.memories);
        setFormula(data.decayFormula);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDecay();
  }, []);

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-purple-500/25 border border-purple-500/30 flex items-center justify-center">
            <Clock className="w-6 h-6 text-purple-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold">Temporal Decay & Memory Half-Life (Forgetting Curves)</h2>
            <p className="text-xs text-slate-300">Simulating Ebbinghaus biological forgetting curves: unused memories naturally fade in priority while reinforced facts stay pinned.</p>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-slate-900">Active Decay Formula</h3>
            <p className="text-xs text-slate-500 font-mono mt-1 bg-slate-50 p-2 rounded-lg border border-slate-200 inline-block">{formula}</p>
          </div>
          <button
            onClick={fetchDecay}
            className="bg-slate-100 hover:bg-slate-200 text-slate-700 px-4 py-2 rounded-xl text-xs font-medium transition flex items-center space-x-2 cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Recalculate Half-Life</span>
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 pt-4">
          {decayData.map(node => (
            <div key={node.id} className="bg-slate-50 rounded-2xl p-5 border border-slate-200 flex flex-col justify-between space-y-4">
              <div>
                <span className="text-[11px] font-bold text-indigo-600 uppercase tracking-wider">{node.category}</span>
                <p className="text-xs text-slate-800 font-medium mt-1 line-clamp-3">"{node.content}"</p>
              </div>

              <div className="space-y-2 pt-3 border-t border-slate-200/60 text-xs">
                <div className="flex justify-between text-slate-500">
                  <span>Age:</span>
                  <strong className="text-slate-800">{node.daysElapsed} days</strong>
                </div>
                <div className="flex justify-between text-slate-500">
                  <span>Base Confidence:</span>
                  <strong className="text-slate-800">{(node.confidence * 100).toFixed(0)}%</strong>
                </div>
                <div className="flex justify-between text-slate-500">
                  <span>Access Count:</span>
                  <strong className="text-slate-800">{node.accessCount}</strong>
                </div>
                <div className="flex justify-between pt-2 border-t border-slate-200/60">
                  <span className="font-semibold text-slate-700">Effective Priority:</span>
                  <strong className="text-emerald-600">{(node.effectiveConfidence * 100).toFixed(0)}%</strong>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
