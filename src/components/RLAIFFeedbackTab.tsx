import React, { useState } from 'react';
import { ThumbsUp, ThumbsDown, Sparkles, CheckCircle2 } from 'lucide-react';
import { MemoryNode } from '../types';

export const RLAIFFeedbackTab: React.FC<{ memories: MemoryNode[]; onRefresh: () => void }> = ({ memories, onRefresh }) => {
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [feedbackLog, setFeedbackLog] = useState<string>('');

  const handleFeedback = async (memoryId: string, feedback: 'positive' | 'negative') => {
    setLoadingId(memoryId);
    try {
      const res = await fetch('/api/rlaif/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ memoryId, feedback })
      });
      const data = await res.json();
      if (data.success) {
        setFeedbackLog(data.rlaifUpdate);
        onRefresh();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-violet-500/25 border border-violet-500/30 flex items-center justify-center">
            <Sparkles className="w-6 h-6 text-violet-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold">Active Learning via Preference Feedback (RLAIF)</h2>
            <p className="text-xs text-slate-300">Reinforcement Learning from AI Feedback: implicitly adjusting memory node importance weights based on user interactions.</p>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-900">Active Memory Importance Tuning</h3>
          {feedbackLog && (
            <span className="text-xs font-mono bg-emerald-50 text-emerald-700 px-3 py-1 rounded-lg border border-emerald-200 flex items-center space-x-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
              <span>{feedbackLog}</span>
            </span>
          )}
        </div>

        <div className="space-y-3">
          {memories.map(m => (
            <div key={m.id} className="p-4 rounded-xl border border-slate-200 bg-slate-50/50 flex items-center justify-between">
              <div className="space-y-1 pr-4">
                <div className="flex items-center space-x-2">
                  <span className="text-[11px] font-bold text-indigo-600 uppercase tracking-wider">{m.category}</span>
                  <span className="text-xs font-mono bg-slate-200 text-slate-700 px-2 py-0.5 rounded">Confidence: {(m.confidence * 100).toFixed(0)}%</span>
                </div>
                <p className="text-xs text-slate-800 font-medium">"{m.content}"</p>
              </div>

              <div className="flex items-center space-x-2 shrink-0">
                <button
                  onClick={() => handleFeedback(m.id, 'positive')}
                  disabled={loadingId === m.id}
                  className="p-2 rounded-xl bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 transition cursor-pointer flex items-center space-x-1 text-xs font-medium"
                  title="Reinforce memory weight"
                >
                  <ThumbsUp className="w-4 h-4" />
                  <span>Useful</span>
                </button>
                <button
                  onClick={() => handleFeedback(m.id, 'negative')}
                  disabled={loadingId === m.id}
                  className="p-2 rounded-xl bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 transition cursor-pointer flex items-center space-x-1 text-xs font-medium"
                  title="Demote memory weight"
                >
                  <ThumbsDown className="w-4 h-4" />
                  <span>Noise</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
