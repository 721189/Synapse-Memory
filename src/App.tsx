import React, { useState, useEffect } from 'react';
import { Header } from './components/Header';
import { GatewayPlayground } from './components/GatewayPlayground';
import { MemoryManager } from './components/MemoryManager';
import { PoisoningLab } from './components/PoisoningLab';
import { EnterpriseSecurityTab } from './components/EnterpriseSecurityTab';
import { AdvancedRetrievalTab } from './components/AdvancedRetrievalTab';
import { TemporalDecayTab } from './components/TemporalDecayTab';
import { HierarchicalGraphTab } from './components/HierarchicalGraphTab';
import { KnapsackTokenBudgetTab } from './components/KnapsackTokenBudgetTab';
import { MultiModalMemoryTab } from './components/MultiModalMemoryTab';
import { RLAIFFeedbackTab } from './components/RLAIFFeedbackTab';
import { DeveloperDocs } from './components/DeveloperDocs';
import { MemoryNode, SystemStats } from './types';

export default function App() {
  const [activeTab, setActiveTab] = useState<'gateway' | 'memories' | 'poisoning' | 'security' | 'retrieval' | 'decay' | 'graph' | 'knapsack' | 'multimodal' | 'rlaif' | 'docs'>('gateway');
  const [memories, setMemories] = useState<MemoryNode[]>([]);
  const [stats, setStats] = useState<SystemStats>({
    totalMemories: 4,
    quarantinedCount: 0,
    avgRetrievalLatencyMs: 38,
    totalTokenSavings: 14250
  });
  const [loading, setLoading] = useState(true);

  const fetchAppData = async () => {
    try {
      const res = await fetch('/api/memories');
      const data = await res.json();
      if (data.memories) {
        setMemories(data.memories);
      }
      if (data.stats) {
        setStats(data.stats);
      }
    } catch (err) {
      console.error('Failed to load app data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAppData();
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-900 font-sans antialiased flex flex-col">
      <Header activeTab={activeTab} setActiveTab={setActiveTab} stats={stats} />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {activeTab === 'gateway' && <GatewayPlayground onRefreshStats={fetchAppData} />}
        {activeTab === 'memories' && <MemoryManager memories={memories} onRefresh={fetchAppData} />}
        {activeTab === 'poisoning' && <PoisoningLab onRefresh={fetchAppData} />}
        {activeTab === 'security' && <EnterpriseSecurityTab onRefresh={fetchAppData} />}
        {activeTab === 'retrieval' && <AdvancedRetrievalTab />}
        {activeTab === 'decay' && <TemporalDecayTab />}
        {activeTab === 'graph' && <HierarchicalGraphTab />}
        {activeTab === 'knapsack' && <KnapsackTokenBudgetTab />}
        {activeTab === 'multimodal' && <MultiModalMemoryTab onRefresh={fetchAppData} />}
        {activeTab === 'rlaif' && <RLAIFFeedbackTab memories={memories} onRefresh={fetchAppData} />}
        {activeTab === 'docs' && <DeveloperDocs />}
      </main>

      <footer className="bg-slate-900 border-t border-slate-800 text-slate-400 py-6 text-center text-xs">
        <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-4">
          <p>© 2026 SynapseMemory Research Core. Universal Active RAG & Long-Term Memory Layer.</p>
          <div className="flex items-center space-x-4">
            <span className="flex items-center space-x-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              <span className="text-emerald-400 font-medium">Gateway Operational</span>
            </span>
            <span>•</span>
            <span>Compatible with OpenAI, Anthropic & Google Gemini</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
