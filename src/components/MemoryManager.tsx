import React, { useState } from 'react';
import { Database, Plus, Trash2, Search, ShieldCheck, Tag, RefreshCw, Cpu, Layers } from 'lucide-react';
import { MemoryNode } from '../types';

interface MemoryManagerProps {
  memories: MemoryNode[];
  onRefresh: () => void;
}

export const MemoryManager: React.FC<MemoryManagerProps> = ({ memories, onRefresh }) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [filterCategory, setFilterCategory] = useState<string>('all');
  const [showAddModal, setShowAddModal] = useState(false);
  const [newCategory, setNewCategory] = useState<'preference' | 'project' | 'fact' | 'constraint'>('preference');
  const [newContent, setNewContent] = useState('');
  const [newSource, setNewSource] = useState('Manual Dashboard Injection');
  const [submitting, setSubmitting] = useState(false);

  const filteredMemories = memories.filter(m => {
    const matchesSearch = m.content.toLowerCase().includes(searchTerm.toLowerCase()) || m.source.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesCategory = filterCategory === 'all' || m.category === filterCategory;
    return matchesSearch && matchesCategory;
  });

  const handleAddMemory = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newContent.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch('/api/memories/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: newCategory, content: newContent, source: newSource })
      });
      const data = await res.json();
      if (data.success) {
        setNewContent('');
        setShowAddModal(false);
        onRefresh();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleClearStore = async () => {
    if (!window.confirm('Are you sure you want to clear all memory nodes?')) return;
    try {
      await fetch('/api/memories/clear', { method: 'POST' });
      onRefresh();
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Action Bar */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-slate-900">Vector & Knowledge Graph Memory Store</h2>
          <p className="text-xs text-slate-500">Inspect, manage, and audit atomic memory nodes extracted across all LLM sessions.</p>
        </div>
        <div className="flex items-center space-x-3">
          <button
            onClick={() => setShowAddModal(true)}
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2.5 rounded-xl font-medium text-sm flex items-center space-x-2 shadow-sm transition cursor-pointer"
          >
            <Plus className="w-4 h-4" />
            <span>Inject Memory Node</span>
          </button>
          <button
            onClick={handleClearStore}
            className="bg-slate-100 hover:bg-red-50 text-slate-600 hover:text-red-600 px-4 py-2.5 rounded-xl font-medium text-sm flex items-center space-x-2 transition cursor-pointer"
          >
            <Trash2 className="w-4 h-4" />
            <span>Reset Store</span>
          </button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 flex flex-wrap items-center justify-between gap-4">
        <div className="relative flex-1 max-w-md">
          <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-3.5" />
          <input
            type="text"
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            placeholder="Search memory contents or sources..."
            className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-10 pr-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
          />
        </div>

        <div className="flex items-center space-x-2 overflow-x-auto pb-1">
          {['all', 'preference', 'project', 'fact', 'constraint', 'episodic'].map(cat => (
            <button
              key={cat}
              onClick={() => setFilterCategory(cat)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition-all ${
                filterCategory === cat
                  ? 'bg-slate-900 text-white shadow-sm'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {/* Memory Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredMemories.map(mem => (
          <div key={mem.id} className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-col justify-between hover:border-indigo-300 transition group">
            <div>
              <div className="flex items-center justify-between mb-3">
                <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wider ${
                  mem.category === 'preference' ? 'bg-purple-100 text-purple-700' :
                  mem.category === 'project' ? 'bg-indigo-100 text-indigo-700' :
                  mem.category === 'constraint' ? 'bg-amber-100 text-amber-700' :
                  mem.category === 'episodic' ? 'bg-emerald-100 text-emerald-700' :
                  'bg-blue-100 text-blue-700'
                }`}>
                  {mem.category}
                </span>
                <span className={`text-xs font-semibold px-2 py-0.5 rounded-md ${
                  mem.status === 'active' ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-600'
                }`}>
                  {mem.status}
                </span>
              </div>
              <p className="text-sm text-slate-800 font-medium mb-4 leading-relaxed">
                "{mem.content}"
              </p>
            </div>

            <div className="space-y-3 pt-4 border-t border-slate-100 text-xs text-slate-500">
              <div className="flex justify-between items-center">
                <span>Confidence:</span>
                <span className="font-semibold text-emerald-600">{(mem.confidence * 100).toFixed(0)}%</span>
              </div>
              <div className="flex justify-between items-center">
                <span>Origin Source:</span>
                <span className="truncate max-w-[150px] text-slate-700" title={mem.source}>{mem.source}</span>
              </div>
              <div className="flex justify-between items-center">
                <span>Access Count:</span>
                <span className="font-semibold text-slate-700">{mem.accessCount} retrievals</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Add Modal */}
      {showAddModal && (
        <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-slate-200">
            <h3 className="text-lg font-bold text-slate-900 mb-2">Inject New Memory Node</h3>
            <p className="text-xs text-slate-500 mb-4">Add a verified fact or constraint to the permanent vector memory store.</p>
            
            <form onSubmit={handleAddMemory} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Category</label>
                <select
                  value={newCategory}
                  onChange={(e: any) => setNewCategory(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                >
                  <option value="preference">Preference</option>
                  <option value="project">Project</option>
                  <option value="fact">Fact</option>
                  <option value="constraint">Constraint</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Memory Content</label>
                <textarea
                  rows={3}
                  value={newContent}
                  onChange={e => setNewContent(e.target.value)}
                  placeholder="e.g. User requires strict unit testing with Jest for all backend API routes."
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Source Attribution</label>
                <input
                  type="text"
                  value={newSource}
                  onChange={e => setNewSource(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                />
              </div>

              <div className="flex justify-end space-x-3 pt-3 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-4 py-2 rounded-xl text-sm font-medium text-slate-600 hover:bg-slate-100 transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting || !newContent.trim()}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white px-5 py-2 rounded-xl text-sm font-medium shadow-sm transition disabled:opacity-50"
                >
                  {submitting ? 'Injecting...' : 'Confirm Injection'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
