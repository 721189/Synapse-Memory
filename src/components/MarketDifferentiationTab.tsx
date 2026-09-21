import React, { useState, useEffect } from 'react';
import { Sparkles, GitPullRequest, Moon, Clock, Save, Play, RefreshCw, FileText, CheckCircle2, ShieldCheck, History } from 'lucide-react';
import { MemFSFile, DreamSession, TemporalObservation } from '../types';

export const MarketDifferentiationTab: React.FC = () => {
  const [files, setFiles] = useState<MemFSFile[]>([]);
  const [activeFile, setActiveFile] = useState<MemFSFile | null>(null);
  const [fileContent, setFileContent] = useState('');
  const [commitMsg, setCommitMsg] = useState('');
  const [dreamingSessions, setDreamingSessions] = useState<DreamSession[]>([]);
  const [temporalTimeline, setTemporalTimeline] = useState<TemporalObservation[]>([]);
  
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [dreamingActive, setDreamingActive] = useState(false);
  const [loadingTimeline, setLoadingTimeline] = useState(true);
  const [savingFile, setSavingFile] = useState(false);

  const fetchFiles = async () => {
    try {
      const res = await fetch('/api/memfs/files');
      const data = await res.json();
      if (data.success) {
        setFiles(data.files);
        if (!activeFile && data.files.length > 0) {
          setActiveFile(data.files[0]);
          setFileContent(data.files[0].content);
        }
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingFiles(false);
    }
  };

  const fetchDreamSessions = async () => {
    try {
      const res = await fetch('/api/dreaming/sessions');
      const data = await res.json();
      if (data.success) {
        setDreamingSessions(data.sessions);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const fetchTimeline = async () => {
    try {
      const res = await fetch('/api/temporal/timeline');
      const data = await res.json();
      if (data.success) {
        setTemporalTimeline(data.timeline);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingTimeline(false);
    }
  };

  const handleCommit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeFile) return;
    setSavingFile(true);
    try {
      const res = await fetch('/api/memfs/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: activeFile.path,
          content: fileContent,
          commitMessage: commitMsg || "Manual profile updates via Letta MemFS visual editor"
        })
      });
      const data = await res.json();
      if (data.success) {
        setCommitMsg('');
        fetchFiles();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSavingFile(false);
    }
  };

  const triggerDreaming = async () => {
    setDreamingActive(true);
    try {
      const res = await fetch('/api/dreaming/consolidate', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        fetchDreamSessions();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setDreamingActive(false);
    }
  };

  useEffect(() => {
    fetchFiles();
    fetchDreamSessions();
    fetchTimeline();
  }, []);

  const selectFile = (f: MemFSFile) => {
    setActiveFile(f);
    setFileContent(f.content);
  };

  return (
    <div className="space-y-8">
      {/* Banner */}
      <div className="bg-gradient-to-r from-violet-950 via-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl border border-violet-500/20">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-violet-500/25 border border-violet-500/30 flex items-center justify-center">
            <Sparkles className="w-6 h-6 text-violet-400" />
          </div>
          <div>
            <h2 className="text-xl font-extrabold tracking-tight">SynapseMemory Market-Dominance Suite</h2>
            <p className="text-xs text-slate-300">Deploying three bleeding-edge agentic memory pillars to stand out directly in the global enterprise market.</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        
        {/* Letta-Inspired MemFS */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4 flex flex-col justify-between">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-slate-900 flex items-center space-x-2">
                <GitPullRequest className="w-4 h-4 text-indigo-600" />
                <span>Letta MemFS (Git-Synced Memory)</span>
              </h3>
              <span className="text-[10px] font-mono bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded border border-indigo-100">Git Versioned</span>
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              Expose agent core identities as markdown file leaves. Inspect, edit, and use conventional Git commits to version and roll back profiles.
            </p>

            <div className="space-y-2">
              <span className="text-xs font-semibold text-slate-700">Select Memory File:</span>
              <div className="grid grid-cols-1 gap-2">
                {files.map(f => (
                  <button
                    key={f.path}
                    onClick={() => selectFile(f)}
                    className={`flex items-center justify-between p-3 rounded-xl border text-left text-xs transition cursor-pointer ${
                      activeFile?.path === f.path
                        ? 'border-indigo-600 bg-indigo-50/30 text-indigo-950 font-semibold'
                        : 'border-slate-200 bg-slate-50/50 text-slate-700 hover:bg-slate-100/50'
                    }`}
                  >
                    <div className="flex items-center space-x-2">
                      <FileText className="w-3.5 h-3.5 text-slate-500" />
                      <span>{f.path}</span>
                    </div>
                    <span className="font-mono text-[10px] text-indigo-600">{f.lastCommitHash}</span>
                  </button>
                ))}
              </div>
            </div>

            {activeFile && (
              <form onSubmit={handleCommit} className="space-y-3 pt-2">
                <div>
                  <label className="block text-xs font-semibold text-slate-700 mb-1">Markdown Editor:</label>
                  <textarea
                    rows={6}
                    value={fileContent}
                    onChange={e => setFileContent(e.target.value)}
                    className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700 mb-1">Conventional Commit Message:</label>
                  <input
                    type="text"
                    value={commitMsg}
                    onChange={e => setCommitMsg(e.target.value)}
                    placeholder="feat(memory): refine user styling preference stack"
                    className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                    required
                  />
                </div>
                <button
                  type="submit"
                  disabled={savingFile}
                  className="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-2 rounded-xl text-xs font-medium transition flex items-center justify-center space-x-1.5 cursor-pointer"
                >
                  <Save className="w-4 h-4" />
                  <span>{savingFile ? 'Pushing Commit...' : 'Commit & Push Changes'}</span>
                </button>
              </form>
            )}
          </div>
        </div>

        {/* Autonomous Dreaming Console */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4 flex flex-col justify-between">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-slate-900 flex items-center space-x-2">
                <Moon className="w-4 h-4 text-purple-600" />
                <span>Autonomous Subagent Dreaming</span>
              </h3>
              <span className="text-[10px] font-mono bg-purple-50 text-purple-700 px-2 py-0.5 rounded border border-purple-100">Consolidator Thread</span>
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              Consolidate highly repetitive episodic conversations into clean structured facts during low-traffic periods. Automatically drop redundant context nodes.
            </p>

            <button
              onClick={triggerDreaming}
              disabled={dreamingActive}
              className="w-full bg-purple-600 hover:bg-purple-700 text-white py-2.5 rounded-xl text-xs font-medium transition flex items-center justify-center space-x-1.5 cursor-pointer"
            >
              <Play className="w-4 h-4" />
              <span>{dreamingActive ? 'Subagent Dreaming Active...' : 'Trigger Consolidation Dream Session'}</span>
            </button>

            <div className="space-y-3 pt-2">
              <span className="text-xs font-semibold text-slate-700">Past Consolidated Dream Sessions:</span>
              <div className="space-y-2.5 max-h-[280px] overflow-y-auto pr-1">
                {dreamingSessions.map(session => (
                  <div key={session.sessionId} className="p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-2 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-purple-700 font-bold">{session.sessionId}</span>
                      <span className="text-slate-400 font-mono text-[10px]">{new Date(session.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div className="text-[11px] text-slate-600">
                      Analyzed <strong className="text-slate-800">{session.episodicAnalyzedCount} chat transcripts</strong> and consolidated them:
                    </div>
                    <ul className="list-disc list-inside space-y-1 text-[11px] text-slate-700 font-medium">
                      {session.consolidatedFactsGenerated.map((f, i) => (
                        <li key={i}>{f}</li>
                      ))}
                    </ul>
                    <div className="pt-1 border-t border-slate-200 flex justify-between text-[10px] text-emerald-600 font-bold">
                      <span>Token context reduction:</span>
                      <span>+{session.tokensSaved.toLocaleString()} tokens saved</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Temporal Observation Graphiti */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4 flex flex-col justify-between">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-slate-900 flex items-center space-x-2">
                <Clock className="w-4 h-4 text-emerald-600" />
                <span>Graphiti Temporal Observation Stream</span>
              </h3>
              <span className="text-[10px] font-mono bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded border border-emerald-100">Continuous Timeline</span>
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              Track behavioral changes and contradictions chronologically. Standard memory layers overwrite context; SynapseMemory maps progress logs chronologically.
            </p>

            <div className="relative border-l-2 border-indigo-100 pl-4 ml-2 space-y-5 py-2">
              {temporalTimeline.map(obs => (
                <div key={obs.id} className="relative space-y-1 text-xs">
                  {/* Timeline dot */}
                  <div className="absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full bg-indigo-600 border border-white" />
                  
                  <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono">
                    <span>{new Date(obs.timestamp).toLocaleDateString()}</span>
                    <span className="bg-emerald-100 text-emerald-800 px-1.5 py-0.2 rounded font-bold">Conf: {(obs.confidence * 100).toFixed(0)}%</span>
                  </div>
                  <div className="font-bold text-slate-900 text-[12px]">{obs.attribute}</div>
                  <div className="flex items-center space-x-1 text-[11px] text-slate-600">
                    <span className="line-through text-red-500 bg-red-50 px-1.5 rounded">{obs.oldValue}</span>
                    <span>→</span>
                    <span className="text-emerald-700 font-bold bg-emerald-50 px-1.5 rounded">{obs.newValue}</span>
                  </div>
                  <p className="text-[11px] text-slate-500 leading-normal">{obs.reason}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
};
