import React, { useState, useEffect } from 'react';
import { Database, Cpu, Activity, Zap, RefreshCw, Send, CheckCircle2, AlertCircle, Play } from 'lucide-react';
import { DistributedJob, HNSWIndexMetric } from '../types';

export const ScaleInfrastructureTab: React.FC = () => {
  const [jobs, setJobs] = useState<DistributedJob[]>([]);
  const [metrics, setMetrics] = useState<HNSWIndexMetric | null>(null);
  const [loading, setLoading] = useState(true);
  const [tuning, setTuning] = useState(false);
  const [dispatching, setDispatching] = useState(false);

  const [mValue, setMValue] = useState(16);
  const [efConstruction, setEfConstruction] = useState(64);

  const fetchJobs = async () => {
    try {
      const res = await fetch('/api/infrastructure/jobs');
      const data = await res.json();
      if (data.success) {
        setJobs(data.jobs);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const fetchMetrics = async () => {
    try {
      const res = await fetch('/api/infrastructure/hnsw-metrics');
      const data = await res.json();
      if (data.success) {
        setMetrics(data.metrics);
        setMValue(data.metrics.m);
        setEfConstruction(data.metrics.efConstruction);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const tuneMetrics = async () => {
    setTuning(true);
    try {
      const res = await fetch('/api/infrastructure/hnsw-metrics/tune', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ m: mValue, efConstruction })
      });
      const data = await res.json();
      if (data.success) {
        setMetrics(data.metrics);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setTuning(false);
    }
  };

  const dispatchTask = async (taskType: string) => {
    setDispatching(true);
    try {
      const res = await fetch('/api/infrastructure/jobs/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ taskType })
      });
      const data = await res.json();
      if (data.success) {
        fetchJobs();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setDispatching(false);
    }
  };

  useEffect(() => {
    fetchJobs();
    fetchMetrics();
    // Simulate active task progress increments
    const interval = setInterval(() => {
      setJobs(prevJobs =>
        prevJobs.map(j => {
          if (j.status === 'active') {
            const nextProgress = j.progress + 5;
            return {
              ...j,
              progress: nextProgress >= 100 ? 100 : nextProgress,
              status: nextProgress >= 100 ? 'completed' : 'active'
            };
          }
          return j;
        })
      );
    }, 3000);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-8">
      {/* Page Title */}
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl border border-indigo-500/20">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-indigo-500/25 border border-indigo-500/30 flex items-center justify-center">
            <Database className="w-6 h-6 text-indigo-400" />
          </div>
          <div>
            <h2 className="text-xl font-extrabold tracking-tight">Scale-Out Infrastructure & HNSW Optimization</h2>
            <p className="text-xs text-slate-300">Tuning native graph indices for sub-millisecond similarity lookups and managing asynchronous Redis/Celery worker task clusters.</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        
        {/* HNSW Parameter Tuning */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-6">
          <div className="flex items-center justify-between">
            <h3 className="font-bold text-slate-900 flex items-center space-x-2">
              <Zap className="w-4.5 h-4.5 text-amber-500" />
              <span>Native HNSW pgvector Tuning Panel</span>
            </h3>
            <span className="text-[10px] bg-amber-50 text-amber-700 font-mono px-2 py-0.5 rounded border border-amber-100">Index Optimizers</span>
          </div>

          <p className="text-xs text-slate-500 leading-relaxed">
            HNSW (Hierarchical Navigable Small World) structures memory graphs into multiple layers for logarithmic routing complexity. Fine-tune parameters to balance retrieval recall rates and millisecond search latencies.
          </p>

          <div className="space-y-4">
            {/* Sliders */}
            <div className="space-y-2">
              <div className="flex justify-between text-xs font-semibold text-slate-700">
                <span>Max Connections per Node (M):</span>
                <span className="font-mono text-indigo-600 font-bold">{mValue} links</span>
              </div>
              <input
                type="range"
                min="8"
                max="64"
                step="8"
                value={mValue}
                onChange={e => setMValue(Number(e.target.value))}
                className="w-full accent-indigo-600"
              />
              <span className="text-[10px] text-slate-400 block">Higher M increases recall and graph density at the cost of indexing RAM overhead.</span>
            </div>

            <div className="space-y-2">
              <div className="flex justify-between text-xs font-semibold text-slate-700">
                <span>Dynamic Candidate List size (efConstruction):</span>
                <span className="font-mono text-indigo-600 font-bold">{efConstruction} candidates</span>
              </div>
              <input
                type="range"
                min="32"
                max="256"
                step="16"
                value={efConstruction}
                onChange={e => setEfConstruction(Number(e.target.value))}
                className="w-full accent-indigo-600"
              />
              <span className="text-[10px] text-slate-400 block">Controls query candidate exploration list width during initial database construction.</span>
            </div>

            <button
              onClick={tuneMetrics}
              disabled={tuning}
              className="w-full bg-slate-900 hover:bg-slate-800 text-white py-2.5 rounded-xl font-medium text-xs shadow-sm transition flex items-center justify-center space-x-1.5 cursor-pointer"
            >
              <RefreshCw className={`w-4 h-4 ${tuning ? 'animate-spin' : ''}`} />
              <span>{tuning ? 'Rebuilding Index Graph...' : 'Recompile HNSW Metrics Schema'}</span>
            </button>
          </div>

          {/* Indexing Metrics Displays */}
          {metrics && (
            <div className="grid grid-cols-3 gap-3 pt-4 border-t border-slate-100 text-center">
              <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
                <span className="text-[10px] text-slate-400 uppercase tracking-wider block font-bold">Recall Rate</span>
                <span className="text-base font-extrabold text-emerald-600">{(metrics.recallRate * 100).toFixed(2)}%</span>
              </div>
              <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
                <span className="text-[10px] text-slate-400 uppercase tracking-wider block font-bold">Search SLA</span>
                <span className="text-base font-extrabold text-indigo-600">{metrics.queryLatencyMs}ms</span>
              </div>
              <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
                <span className="text-[10px] text-slate-400 uppercase tracking-wider block font-bold">Total Nodes</span>
                <span className="text-base font-extrabold text-slate-800">{metrics.totalIndexNodes.toLocaleString()}</span>
              </div>
            </div>
          )}
        </div>

        {/* Redis / Celery Job Scheduler */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4 flex flex-col justify-between">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-slate-900 flex items-center space-x-2">
                <Activity className="w-4.5 h-4.5 text-indigo-600" />
                <span>Distributed Worker Scheduler Logs</span>
              </h3>
              <span className="text-[10px] bg-indigo-50 text-indigo-700 font-mono px-2 py-0.5 rounded border border-indigo-100">Redis & Celery Queue</span>
            </div>

            <p className="text-xs text-slate-500 leading-relaxed">
              Dispatch resource-intensive computations asynchronously to an elastic, auto-scaling worker cluster. Prevents API connection blocks during complex operations.
            </p>

            <div className="grid grid-cols-3 gap-2">
              <button
                onClick={() => dispatchTask('dream_consolidation')}
                disabled={dispatching}
                className="p-2 border border-slate-200 bg-slate-50 hover:bg-slate-100 rounded-xl text-[11px] font-semibold text-slate-800 transition text-center flex flex-col items-center cursor-pointer"
              >
                <Cpu className="w-4 h-4 mb-1 text-purple-600" />
                <span>Consolidation</span>
              </button>
              <button
                onClick={() => dispatchTask('hnsw_reindex')}
                disabled={dispatching}
                className="p-2 border border-slate-200 bg-slate-50 hover:bg-slate-100 rounded-xl text-[11px] font-semibold text-slate-800 transition text-center flex flex-col items-center cursor-pointer"
              >
                <Database className="w-4 h-4 mb-1 text-indigo-600" />
                <span>HNSW Reindex</span>
              </button>
              <button
                onClick={() => dispatchTask('zkp_generation')}
                disabled={dispatching}
                className="p-2 border border-slate-200 bg-slate-50 hover:bg-slate-100 rounded-xl text-[11px] font-semibold text-slate-800 transition text-center flex flex-col items-center cursor-pointer"
              >
                <Zap className="w-4 h-4 mb-1 text-emerald-600" />
                <span>ZKP Attest</span>
              </button>
            </div>

            <div className="space-y-3 pt-2">
              <span className="text-xs font-semibold text-slate-700 block">Queue Traffic & Active Pipelines:</span>
              <div className="space-y-2.5 max-h-[220px] overflow-y-auto pr-1">
                {jobs.map(job => (
                  <div key={job.jobId} className="p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-2 text-xs">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        <span className="font-mono text-indigo-600 font-bold">{job.jobId}</span>
                        <span className="bg-slate-200 text-slate-700 px-1.5 py-0.2 rounded font-mono text-[9px]">{job.workerName}</span>
                      </div>
                      <span className={`px-2 py-0.5 rounded font-mono text-[9px] font-bold ${
                        job.status === 'completed' ? 'bg-emerald-100 text-emerald-800' :
                        job.status === 'active' ? 'bg-amber-100 text-amber-800 animate-pulse' :
                        'bg-slate-100 text-slate-700'
                      }`}>
                        {job.status.toUpperCase()}
                      </span>
                    </div>

                    <div className="flex justify-between text-[11px] text-slate-600">
                      <span>Task: <strong className="text-slate-800">{job.taskType}</strong></span>
                      <span>Payload: {job.payloadSize} elements</span>
                    </div>

                    {job.status === 'active' && (
                      <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                        <div className="bg-indigo-600 h-full transition-all duration-300" style={{ width: `${job.progress}%` }} />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
};
