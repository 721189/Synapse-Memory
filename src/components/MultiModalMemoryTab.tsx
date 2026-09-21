import React, { useState } from 'react';
import { Image as ImageIcon, Code, FileText, Plus, CheckCircle2 } from 'lucide-react';

export const MultiModalMemoryTab: React.FC<{ onRefresh: () => void }> = ({ onRefresh }) => {
  const [content, setContent] = useState("Architecture Diagram: React frontend communicating with Express gateway via secure WebSocket and gRPC.");
  const [modalType, setModalType] = useState<'image' | 'diagram' | 'git_diff'>('diagram');
  const [mediaUrl, setMediaUrl] = useState("https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=800&auto=format&fit=crop&q=60");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await fetch('/api/memories/multimodal-add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, modalType, mediaUrl, tenantId: 'tenant_enterprise_alpha' })
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
          <div className="w-10 h-10 rounded-xl bg-pink-500/25 border border-pink-500/30 flex items-center justify-center">
            <ImageIcon className="w-6 h-6 text-pink-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold">Multi-Modal Memory Ingestion & Vectorization</h2>
            <p className="text-xs text-slate-300">Ingesting UI screenshots, architecture diagrams, and git diffs into 768-dimensional CLIP embedding spaces alongside conversational transcripts.</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
          <h3 className="font-semibold text-slate-900 mb-2">Ingest Multi-Modal Artifact</h3>
          <p className="text-xs text-slate-500 mb-4">Upload or link visual and code artifacts to anchor them into the user's persistent semantic memory graph.</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Artifact Type</label>
              <div className="grid grid-cols-3 gap-2">
                {(['diagram', 'image', 'git_diff'] as const).map(t => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setModalType(t)}
                    className={`py-2 rounded-xl text-xs font-medium border transition cursor-pointer ${
                      modalType === t ? 'bg-indigo-600 text-white border-indigo-600 shadow-sm' : 'bg-slate-50 text-slate-700 border-slate-200 hover:bg-slate-100'
                    }`}
                  >
                    {t.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Description / Context</label>
              <textarea
                rows={3}
                value={content}
                onChange={e => setContent(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                required
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Media / Asset URL</label>
              <input
                type="text"
                value={mediaUrl}
                onChange={e => setMediaUrl(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                required
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-slate-900 hover:bg-slate-800 text-white py-3 rounded-xl font-medium text-xs shadow-sm transition cursor-pointer flex items-center justify-center space-x-2"
            >
              <Plus className="w-4 h-4" />
              <span>{loading ? 'Vectorizing Multi-Modal Asset...' : 'Ingest into Memory Store'}</span>
            </button>
          </form>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-col justify-between">
          <div>
            <h3 className="font-semibold text-slate-900 mb-2">Ingestion Pipeline Result</h3>
            <p className="text-xs text-slate-500 mb-4">Inspection of CLIP-ViT feature maps and OCR extraction.</p>

            {result ? (
              <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-3">
                <div className="flex items-center space-x-2 text-xs font-bold text-emerald-700">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  <span>Successfully Ingested & Vectorized</span>
                </div>
                <div className="aspect-video rounded-lg overflow-hidden border border-slate-200">
                  <img src={result.memory.mediaUrl} alt="Artifact preview" className="w-full h-full object-cover" />
                </div>
                <p className="text-xs text-slate-800 font-medium">"{result.memory.content}"</p>
                <div className="text-[11px] font-mono text-indigo-600 pt-2 border-t border-slate-200">
                  {result.embeddingGenerated}
                </div>
              </div>
            ) : (
              <div className="text-center py-16 text-slate-400">
                <ImageIcon className="w-10 h-10 mx-auto mb-3 opacity-40" />
                <p className="text-sm">Submit an artifact to test multi-modal embedding generation.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
