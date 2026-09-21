import React from 'react';
import { Brain, Sparkles, ShieldCheck, Zap, Database, Code, Activity, Cpu, Award } from 'lucide-react';

interface HeaderProps {
  activeTab: string;
  setActiveTab: (tab: 'gateway' | 'memories' | 'poisoning' | 'security' | 'retrieval' | 'decay' | 'graph' | 'knapsack' | 'multimodal' | 'rlaif' | 'differentiation' | 'infrastructure' | 'buyout' | 'docs') => void;
  stats: {
    totalMemories: number;
    quarantinedCount: number;
    avgRetrievalLatencyMs: number;
    totalTokenSavings: number;
  };
}

export const Header: React.FC<HeaderProps> = ({ activeTab, setActiveTab, stats }) => {
  return (
    <header className="bg-slate-900 border-b border-slate-800 text-white sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Logo & Title */}
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <Brain className="w-6 h-6 text-white" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-bold text-lg tracking-tight">SynapseMemory</span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-medium">
                  Active RAG Core v2.4
                </span>
              </div>
              <p className="text-xs text-slate-400">Universal Memory & Continuous Learning Layer for ChatGPT, Claude & Gemini</p>
            </div>
          </div>

          {/* Quick Metrics Bar */}
          <div className="hidden lg:flex items-center space-x-6 text-xs text-slate-300 bg-slate-800/80 px-4 py-2 rounded-lg border border-slate-700/60">
            <div className="flex items-center space-x-1.5">
              <Database className="w-4 h-4 text-indigo-400" />
              <span>Memories: <strong className="text-white">{stats.totalMemories}</strong></span>
            </div>
            <div className="flex items-center space-x-1.5">
              <Zap className="w-4 h-4 text-amber-400" />
              <span>Latency: <strong className="text-white">{stats.avgRetrievalLatencyMs}ms</strong></span>
            </div>
            <div className="flex items-center space-x-1.5">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              <span>Tokens Saved: <strong className="text-white">{stats.totalTokenSavings.toLocaleString()}</strong></span>
            </div>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="flex space-x-1 overflow-x-auto py-2 border-t border-slate-800/80">
          <button
            onClick={() => setActiveTab('gateway')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'gateway'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Sparkles className="w-4 h-4" />
            <span>LLM Gateway & Test Bench</span>
          </button>

          <button
            onClick={() => setActiveTab('memories')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'memories'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Database className="w-4 h-4" />
            <span>Memory Graph & Store</span>
          </button>

          <button
            onClick={() => setActiveTab('poisoning')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'poisoning'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <ShieldCheck className="w-4 h-4" />
            <span>Poisoning & Belief Revision Lab</span>
          </button>

          <button
            onClick={() => setActiveTab('security')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'security'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            <span>Enterprise Security & GDPR</span>
          </button>

          <button
            onClick={() => setActiveTab('retrieval')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'retrieval'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Sparkles className="w-4 h-4 text-indigo-400" />
            <span>Hybrid Search & RRF</span>
          </button>

          <button
            onClick={() => setActiveTab('decay')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'decay'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Database className="w-4 h-4 text-purple-400" />
            <span>Temporal Decay & Half-Life</span>
          </button>

          <button
            onClick={() => setActiveTab('graph')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'graph'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Sparkles className="w-4 h-4 text-cyan-400" />
            <span>GraphRAG & ZKP Audit</span>
          </button>

          <button
            onClick={() => setActiveTab('knapsack')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'knapsack'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Cpu className="w-4 h-4 text-amber-400" />
            <span>Knapsack Token Budget</span>
          </button>

          <button
            onClick={() => setActiveTab('multimodal')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'multimodal'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Sparkles className="w-4 h-4 text-pink-400" />
            <span>Multi-Modal Ingestion</span>
          </button>

          <button
            onClick={() => setActiveTab('rlaif')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'rlaif'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Sparkles className="w-4 h-4 text-violet-400" />
            <span>RLAIF Active Feedback</span>
          </button>

          <button
            onClick={() => setActiveTab('differentiation')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'differentiation'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Sparkles className="w-4 h-4 text-emerald-400" />
            <span>2026 Market Dominance Suite</span>
          </button>

          <button
            onClick={() => setActiveTab('infrastructure')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'infrastructure'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Database className="w-4 h-4 text-indigo-400" />
            <span>Scale Infrastructure</span>
          </button>

          <button
            onClick={() => setActiveTab('buyout')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all ${
              activeTab === 'buyout'
                ? 'bg-amber-500 text-slate-950 shadow-md shadow-amber-500/20'
                : 'text-amber-400 hover:text-white hover:bg-amber-500/10'
            }`}
          >
            <Award className="w-4 h-4" />
            <span>Enterprise License ($1K)</span>
          </button>

          <button
            onClick={() => setActiveTab('docs')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === 'docs'
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            <Code className="w-4 h-4" />
            <span>Developer SDK & Proxy</span>
          </button>
        </div>
      </div>
    </header>
  );
};
