import React, { useState } from 'react';
import { Send, Sparkles, Cpu, Clock, Zap, Database, ArrowRight, Bot, User, CheckCircle2 } from 'lucide-react';
import { MemoryNode } from '../types';

interface GatewayPlaygroundProps {
  onRefreshStats: () => void;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  retrievedMemories?: MemoryNode[];
  telemetry?: {
    latencyMs: number;
    tokenSavings: number;
    provider: string;
    apiUsed: boolean;
  };
}

export const GatewayPlayground: React.FC<GatewayPlaygroundProps> = ({ onRefreshStats }) => {
  const [provider, setProvider] = useState<'ChatGPT (OpenAI)' | 'Claude (Anthropic)' | 'Gemini (Google)'>('ChatGPT (OpenAI)');
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: 'assistant',
      content: 'Hello! I am connected to your SynapseMemory RAG Gateway. Ask me any question, and I will automatically retrieve your long-term private memories, suppress hallucinations, and inject precise context into whichever LLM provider you select.',
      retrievedMemories: [],
      telemetry: { latencyMs: 24, tokenSavings: 1450, provider: 'System Gateway', apiUsed: false }
    }
  ]);
  const [selectedInspectMemory, setSelectedInspectMemory] = useState<MemoryNode | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || loading) return;

    const userText = prompt;
    setPrompt('');
    setMessages(prev => [...prev, { role: 'user', content: userText }]);
    setLoading(true);

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: userText, provider, userId: 'dev_user_1' })
      });
      const data = await res.json();
      if (data.success) {
        setMessages(prev => [
          ...prev,
          {
            role: 'assistant',
            content: data.response,
            retrievedMemories: data.retrievedMemories,
            telemetry: data.telemetry
          }
        ]);
        onRefreshStats();
      } else {
        throw new Error(data.error || 'Failed to generate response');
      }
    } catch (err: any) {
      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: `Error connecting to gateway: ${err.message}. Please check your API key configuration in Secrets.`,
          retrievedMemories: [],
          telemetry: { latencyMs: 0, tokenSavings: 0, provider, apiUsed: false }
        }
      ]);
    } finally {
      setLoading(false);
    }
  };

  const samplePrompts = [
    "What coding stack and styling frameworks do I prefer?",
    "Summarize what I'm building with SynapseMemory",
    "What are my backend requirements regarding Node.js?",
    "Can you remember my timezone and preferences?"
  ];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Chat / Gateway Panel */}
      <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm flex flex-col h-[75vh]">
        {/* Gateway Bar */}
        <div className="p-4 border-b border-slate-100 flex flex-wrap items-center justify-between gap-4 bg-slate-50/70 rounded-t-2xl">
          <div className="flex items-center space-x-3">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Target LLM:</span>
            <div className="inline-flex rounded-lg p-1 bg-slate-200/70">
              {(['ChatGPT (OpenAI)', 'Claude (Anthropic)', 'Gemini (Google)'] as const).map(p => (
                <button
                  key={p}
                  onClick={() => setProvider(p)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                    provider === p ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  {p.split(' ')[0]}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center space-x-2 text-xs text-emerald-600 font-medium bg-emerald-50 px-2.5 py-1 rounded-full border border-emerald-200">
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>Active RAG Proxy Online</span>
          </div>
        </div>

        {/* Messages Stream */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {messages.map((m, idx) => (
            <div key={idx} className={`flex items-start space-x-3 ${m.role === 'user' ? 'flex-row-reverse space-x-reverse' : ''}`}>
              <div className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                m.role === 'user' ? 'bg-indigo-600 text-white' : 'bg-slate-900 text-white'
              }`}>
                {m.role === 'user' ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
              </div>
              <div className={`max-w-[80%] rounded-2xl p-4 text-sm ${
                m.role === 'user' ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-800'
              }`}>
                <div className="whitespace-pre-wrap leading-relaxed">{m.content}

                {/* Retrieved Memories Chip preview */}
                {m.retrievedMemories && m.retrievedMemories.length > 0 && (
                  <div className="mt-4 pt-3 border-t border-slate-200/80">
                    <p className="text-xs font-semibold text-slate-500 mb-2 flex items-center space-x-1">
                      <Database className="w-3.5 h-3.5 text-indigo-500" />
                      <span>Retrieved Private Memory Context ({m.retrievedMemories.length} nodes injected):</span>
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {m.retrievedMemories.map(mem => (
                        <button
                          key={mem.id}
                          onClick={() => setSelectedInspectMemory(mem)}
                          className="text-xs bg-white text-indigo-700 px-2.5 py-1 rounded-lg border border-indigo-200 shadow-2xs hover:bg-indigo-50 transition flex items-center space-x-1"
                        >
                          <span className="w-1.5 h-1.5 rounded-full bg-indigo-500"></span>
                          <span className="font-medium">[{mem.category}]</span>
                          <span className="text-slate-600 truncate max-w-[120px]">{mem.content}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Telemetry Footer */}
                {m.telemetry && (
                  <div className="mt-3 pt-2 border-t border-slate-200/60 flex items-center justify-between text-[11px] text-slate-500">
                    <span className="flex items-center space-x-1">
                      <Cpu className="w-3 h-3 text-indigo-500" />
                      <span>Via {m.telemetry.provider}</span>
                    </span>
                    <div className="flex items-center space-x-3">
                      <span className="flex items-center space-x-1">
                        <Clock className="w-3 h-3 text-amber-500" />
                        <span>{m.telemetry.latencyMs}ms</span>
                      </span>
                      <span className="flex items-center space-x-1">
                        <Zap className="w-3 h-3 text-emerald-500" />
                        <span>Saved ~{m.telemetry.tokenSavings} tokens</span>
                      </span>
                    </div>
                  </div>
                )}
                </div>
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex items-center space-x-3">
              <div className="w-8 h-8 rounded-xl bg-slate-900 text-white flex items-center justify-center animate-pulse">
                <Bot className="w-4 h-4" />
              </div>
              <div className="bg-slate-100 rounded-2xl p-4 text-sm text-slate-500 flex items-center space-x-2">
                <div className="w-2 h-2 rounded-full bg-indigo-600 animate-bounce"></div>
                <div className="w-2 h-2 rounded-full bg-indigo-600 animate-bounce [animation-delay:0.2s]"></div>
                <div className="w-2 h-2 rounded-full bg-indigo-600 animate-bounce [animation-delay:0.4s]"></div>
                <span className="ml-2 text-xs font-medium">Querying vector index, reranking memories, and querying {provider.split(' ')[0]}...</span>
              </div>
            </div>
          )}
        </div>

        {/* Input Bar */}
        <div className="p-4 border-t border-slate-100 bg-slate-50/50 rounded-b-2xl">
          <form onSubmit={handleSubmit} className="flex items-center space-x-2">
            <input
              type="text"
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              placeholder="Ask anything... (SynapseMemory will inject relevant private context automatically)"
              className="flex-1 bg-white border border-slate-200 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
            />
            <button
              type="submit"
              disabled={loading || !prompt.trim()}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-5 py-3 rounded-xl font-medium text-sm flex items-center space-x-2 shadow-md shadow-indigo-600/20 transition cursor-pointer"
            >
              <span>Send</span>
              <Send className="w-4 h-4" />
            </button>
          </form>

          {/* Sample Prompts */}
          <div className="mt-3 flex items-center space-x-2 overflow-x-auto pb-1">
            <span className="text-xs text-slate-400 shrink-0 font-medium">Try asking:</span>
            {samplePrompts.map((sp, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => setPrompt(sp)}
                className="text-xs bg-white text-slate-600 px-3 py-1 rounded-lg border border-slate-200 hover:bg-indigo-50 hover:text-indigo-700 hover:border-indigo-200 transition shrink-0"
              >
                {sp}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Side Inspector Panel */}
      <div className="space-y-6">
        {selectedInspectMemory ? (
          <div className="bg-white rounded-2xl border border-indigo-200 shadow-sm p-6 relative">
            <div className="absolute top-4 right-4">
              <button 
                onClick={() => setSelectedInspectMemory(null)}
                className="text-slate-400 hover:text-slate-600 text-xs font-semibold"
              >
                Close
              </button>
            </div>
            <div className="flex items-center space-x-2 mb-3">
              <span className="px-2.5 py-0.5 rounded-full bg-indigo-100 text-indigo-700 text-xs font-bold uppercase tracking-wider">
                {selectedInspectMemory.category}
              </span>
              <span className="text-xs text-slate-400 font-mono">ID: {selectedInspectMemory.id}</span>
            </div>
            <h3 className="font-semibold text-slate-900 mb-2">Injected Memory Node</h3>
            <p className="text-sm text-slate-600 bg-slate-50 p-3 rounded-xl border border-slate-100 mb-4 leading-relaxed">
              "{selectedInspectMemory.content}"
            </p>
            <div className="space-y-2 text-xs text-slate-500 border-t border-slate-100 pt-3">
              <div className="flex justify-between">
                <span>Confidence Score:</span>
                <strong className="text-emerald-600">{(selectedInspectMemory.confidence * 100).toFixed(0)}%</strong>
              </div>
              <div className="flex justify-between">
                <span>Source Origin:</span>
                <strong className="text-slate-700">{selectedInspectMemory.source}</strong>
              </div>
              <div className="flex justify-between">
                <span>Access Count:</span>
                <strong className="text-slate-700">{selectedInspectMemory.accessCount} times</strong>
              </div>
              <div className="flex justify-between">
                <span>Vector Embedding:</span>
                <strong className="font-mono text-indigo-600">dim-1536 (Normalized)</strong>
              </div>
            </div>
          </div>
        ) : (
          <div className="bg-gradient-to-br from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl">
            <div className="flex items-center space-x-2 text-indigo-400 mb-3">
              <Sparkles className="w-5 h-5" />
              <h3 className="font-semibold text-white">How Active RAG Works</h3>
            </div>
            <p className="text-xs text-slate-300 leading-relaxed mb-4">
              Unlike static RAG or massive 2M-token context dumps, SynapseMemory acts as a cognitive proxy. It intercepts your prompt, queries a hybrid vector + knowledge graph in under 40ms, and injects only the highest-signal memory nodes.
            </p>
            <div className="space-y-3 text-xs text-slate-300">
              <div className="flex items-start space-x-2">
                <div className="w-4 h-4 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center shrink-0 mt-0.5 font-bold">1</div>
                <div><strong>Zero Hallucination:</strong> Verified atomic facts overwrite ambiguous chat logs.</div>
              </div>
              <div className="flex items-start space-x-2">
                <div className="w-4 h-4 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center shrink-0 mt-0.5 font-bold">2</div>
                <div><strong>Model Agnostic:</strong> Works identically with OpenAI, Anthropic, Gemini, or local Llama models.</div>
              </div>
              <div className="flex items-start space-x-2">
                <div className="w-4 h-4 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center shrink-0 mt-0.5 font-bold">3</div>
                <div><strong>Continuous Learning:</strong> Background workers extract new facts from your chats automatically.</div>
              </div>
            </div>
          </div>
        )}

        {/* Live Telemetry Card */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
          <h3 className="font-semibold text-slate-900 text-sm mb-4 flex items-center space-x-2">
            <Database className="w-4 h-4 text-indigo-600" />
            <span>Architecture Metrics</span>
          </h3>
          <div className="space-y-4 text-xs">
            <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl border border-slate-100">
              <span className="text-slate-600">Vector Index Engine</span>
              <span className="font-mono text-indigo-600 font-semibold">pgvector + HNSW</span>
            </div>
            <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl border border-slate-100">
              <span className="text-slate-600">Reranker Model</span>
              <span className="font-mono text-indigo-600 font-semibold">BGE-Reranker-Large</span>
            </div>
            <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl border border-slate-100">
              <span className="text-slate-600">Belief Revision Engine</span>
              <span className="font-mono text-emerald-600 font-semibold">Bayesian Active Sync</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
