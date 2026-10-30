import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDownToLine,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Timer,
  XCircle,
} from 'lucide-react';
import { apiGet } from '../lib/api';

type RunStatus = 'created' | 'running' | 'succeeded' | 'failed' | 'aborted' | 'timed_out' | 'limit_exceeded' | string;

interface AgentRun {
  run_id: string;
  schema_version: number;
  status: RunStatus;
  started_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  event_count: number;
}

interface MemoryEvidence {
  memory_id: string;
  rank: number;
  token_cost: number;
  packed: boolean;
  score_components?: Record<string, number>;
  source_type?: string;
  trust_level?: string;
  observed_at?: number;
  valid_from?: number;
  valid_until?: number;
  superseded_by?: string;
  retracted_at?: number;
}

interface RunEvent {
  sequence_number: number;
  event_id: string;
  event_type: string;
  timestamp: string;
  run_status?: string;
  step_index?: number;
  tool_call_index?: number;
  tool_name?: string;
  reason?: string;
  error_type?: string;
  retrieval_id?: string;
  retrieval_as_of?: number;
  protected_constraints_count?: number;
  protected_constraints_token_cost?: number;
  memory_evidence?: MemoryEvidence[];
}

const PAGE_SIZE = 50;
const EVENT_PAGE_SIZE = 500;

function formatDate(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

function formatDuration(start?: string | null, end?: string | null): string {
  if (!start) return '—';
  const from = new Date(start).getTime();
  const to = end ? new Date(end).getTime() : Date.now();
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return '—';
  const seconds = Math.floor((to - from) / 1000);
  if (seconds < 60) return seconds + 's';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + 'm ' + (seconds % 60) + 's';
  return Math.floor(minutes / 60) + 'h ' + (minutes % 60) + 'm';
}

function statusTone(status: string): string {
  switch (status) {
    case 'succeeded': return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    case 'failed':
    case 'limit_exceeded':
    case 'timed_out': return 'bg-rose-50 text-rose-700 border-rose-200';
    case 'aborted': return 'bg-amber-50 text-amber-700 border-amber-200';
    case 'running': return 'bg-blue-50 text-blue-700 border-blue-200';
    default: return 'bg-slate-100 text-slate-700 border-slate-200';
  }
}

function eventLabel(event: RunEvent): string {
  return (event.event_type || 'event').replace(/[._]/g, ' ');
}

function eventTone(type: string): string {
  if (type.includes('failed') || type.includes('blocked') || type.includes('limit')) return 'border-rose-300 bg-rose-50';
  if (type.includes('passed') || type.includes('succeeded') || type.includes('finished')) return 'border-emerald-300 bg-emerald-50';
  if (type.includes('retrieval')) return 'border-violet-300 bg-violet-50';
  return 'border-slate-200 bg-white';
}

function safeExport(run: AgentRun, events: RunEvent[]) {
  // Explicit allowlist: never export arbitrary event fields or raw payloads.
  const safeEvents = events.map((event) => ({
    sequence_number: event.sequence_number,
    event_id: event.event_id,
    event_type: event.event_type,
    timestamp: event.timestamp,
    run_status: event.run_status,
    step_index: event.step_index,
    tool_call_index: event.tool_call_index,
    tool_name: event.tool_name,
    reason: event.reason,
    error_type: event.error_type,
    retrieval_id: event.retrieval_id,
    retrieval_as_of: event.retrieval_as_of,
    protected_constraints_count: event.protected_constraints_count,
    protected_constraints_token_cost: event.protected_constraints_token_cost,
    memory_evidence: (event.memory_evidence || []).map((item) => ({
      memory_id: item.memory_id,
      rank: item.rank,
      token_cost: item.token_cost,
      packed: item.packed,
      score_components: item.score_components,
      source_type: item.source_type,
      trust_level: item.trust_level,
      observed_at: item.observed_at,
      valid_from: item.valid_from,
      valid_until: item.valid_until,
      superseded_by: item.superseded_by,
      retracted_at: item.retracted_at,
    })),
  }));
  return {
    export_schema_version: 1,
    redaction: 'metadata_allowlist_only; raw prompts, memory text, tool arguments and tool results are excluded',
    exported_at: new Date().toISOString(),
    run: {
      run_id: run.run_id,
      schema_version: run.schema_version,
      status: run.status,
      started_at: run.started_at,
      created_at: run.created_at,
      updated_at: run.updated_at,
      event_count: run.event_count,
    },
    events: safeEvents,
    partial: events.length < run.event_count,
  };
}

export const AgentRunInspector: React.FC = () => {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [selectedRun, setSelectedRun] = useState<AgentRun | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [runOffset, setRunOffset] = useState(0);
  const [hasMoreRuns, setHasMoreRuns] = useState(false);
  const [eventOffset, setEventOffset] = useState(0);
  const [hasMoreEvents, setHasMoreEvents] = useState(false);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [partialNotice, setPartialNotice] = useState('');
  const selectionRef = useRef(0);

  const loadRuns = useCallback(async (append = false) => {
    setLoadingRuns(true);
    setError('');
    const offset = append ? runOffset + PAGE_SIZE : 0;
    try {
      const data = await apiGet('/api/agent-runs?limit=' + PAGE_SIZE + '&offset=' + offset);
      const page = Array.isArray(data?.runs) ? data.runs as AgentRun[] : [];
      setRuns((current) => append ? [...current, ...page] : page);
      setRunOffset(offset);
      setHasMoreRuns(page.length === PAGE_SIZE);
      if (!append && page.length === 0) {
        setSelectedRunId('');
        setSelectedRun(null);
        setEvents([]);
      } else if (!append && selectedRunId && !page.some((run) => run.run_id === selectedRunId)) {
        setSelectedRunId('');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load agent runs.');
    } finally {
      setLoadingRuns(false);
    }
  }, [runOffset, selectedRunId]);

  const loadDetail = useCallback(async (runId: string, appendEvents = false) => {
    const requestId = ++selectionRef.current;
    setLoadingDetail(!appendEvents);
    setLoadingMore(appendEvents);
    setDetailError('');
    if (!appendEvents) {
      setEvents([]);
      setPartialNotice('');
    }
    const offset = appendEvents ? eventOffset : 0;
    try {
      const [runData, eventData] = await Promise.all([
        apiGet('/api/agent-runs/' + encodeURIComponent(runId)),
        apiGet('/api/agent-runs/' + encodeURIComponent(runId) + '/events?limit=' + EVENT_PAGE_SIZE + '&offset=' + offset),
      ]);
      if (requestId !== selectionRef.current) return;
      const run = (runData?.run || runData) as AgentRun;
      const page = Array.isArray(eventData?.events) ? eventData.events as RunEvent[] : [];
      setSelectedRun(run);
      setEvents((current) => appendEvents ? [...current, ...page] : page);
      const nextOffset = offset + page.length;
      setEventOffset(nextOffset);
      setHasMoreEvents(nextOffset < Number(run.event_count || 0) && page.length > 0);
      if (nextOffset < Number(run.event_count || 0) && page.length === 0) {
        setPartialNotice('The trace is incomplete: the API returned no events for the next page.');
      } else if (nextOffset < Number(run.event_count || 0)) {
        setPartialNotice('Showing ' + nextOffset + ' of ' + run.event_count + ' events. Load the next page to continue.');
      } else {
        setPartialNotice('');
      }
    } catch (err) {
      if (requestId !== selectionRef.current) return;
      setDetailError(err instanceof Error ? err.message : 'Unable to load this run trace.');
    } finally {
      if (requestId === selectionRef.current) {
        setLoadingDetail(false);
        setLoadingMore(false);
      }
    }
  }, [eventOffset]);

  useEffect(() => {
    void loadRuns(false);
  }, []);

  useEffect(() => {
    if (selectedRunId) {
      setEventOffset(0);
      void loadDetail(selectedRunId, false);
    }
  }, [selectedRunId]);

  const selectRun = (runId: string) => {
    setSelectedRunId(runId);
    setSelectedRun(null);
  };

  const refresh = async () => {
    await loadRuns(false);
    if (selectedRunId) {
      setEventOffset(0);
      await loadDetail(selectedRunId, false);
    }
  };

  const tokenSummary = useMemo(() => {
    let memoryTokens = 0;
    let protectedTokens = 0;
    let memoryCount = 0;
    let packedCount = 0;
    for (const event of events) {
      if (event.event_type !== 'memory.retrieval') continue;
      protectedTokens += event.protected_constraints_token_cost || 0;
      for (const item of event.memory_evidence || []) {
        memoryTokens += item.token_cost || 0;
        memoryCount += 1;
        if (item.packed) packedCount += 1;
      }
    }
    return { memoryTokens, protectedTokens, memoryCount, packedCount };
  }, [events]);

  const exportTrace = () => {
    if (!selectedRun) return;
    const payload = safeExport(selectedRun, events);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'synapse-run-' + selectedRun.run_id + '-redacted.json';
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-indigo-700 text-sm font-semibold">
            <Activity className="w-4 h-4" />
            Agent reliability
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 mt-1">Run trace inspector</h1>
          <p className="text-sm text-slate-500 mt-1">Inspect execution events and retrieval evidence. Trace payloads are metadata-only.</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">Tenant-scoped by the configured API key</span>
          <button onClick={() => void refresh()} disabled={loadingRuns || loadingDetail} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
            <RefreshCw className={'w-4 h-4 ' + (loadingRuns ? 'animate-spin' : '')} /> Refresh
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800 flex gap-2">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <div><strong>Could not load runs.</strong><p className="mt-1">{error}</p><p className="mt-1 text-xs">Check the engine connection and ensure the API key has agent-runs:read.</p></div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)] gap-5 items-start">
        <aside className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
            <div><h2 className="font-semibold text-slate-900">Recent runs</h2><p className="text-xs text-slate-500">Newest activity first</p></div>
            <span className="text-xs text-slate-500 tabular-nums">{runs.length} loaded</span>
          </div>
          {loadingRuns && runs.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-500"><Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />Loading runs…</div>
          ) : runs.length === 0 ? (
            <div className="p-8 text-center"><Activity className="w-8 h-8 mx-auto text-slate-300 mb-2" /><p className="text-sm font-medium text-slate-700">No agent runs yet</p><p className="text-xs text-slate-500 mt-1">Runs appear after an SDK or API client creates and records a run.</p></div>
          ) : (
            <div className="divide-y divide-slate-100 max-h-[680px] overflow-y-auto">
              {runs.map((run) => (
                <button key={run.run_id} onClick={() => selectRun(run.run_id)} className={'w-full text-left p-4 hover:bg-slate-50 transition ' + (selectedRunId === run.run_id ? 'bg-indigo-50/70 border-l-2 border-indigo-600' : 'border-l-2 border-transparent')}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs font-semibold text-slate-800 truncate">{run.run_id}</span>
                    <span className={'shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ' + statusTone(run.status)}>{run.status.replace(/_/g, ' ')}</span>
                  </div>
                  <div className="flex items-center justify-between mt-2 text-xs text-slate-500">
                    <span className="inline-flex items-center gap-1"><Clock3 className="w-3 h-3" />{formatDuration(run.started_at, run.updated_at)}</span>
                    <span>{run.event_count} events</span>
                  </div>
                  <p className="text-[11px] text-slate-400 mt-1">Updated {formatDate(run.updated_at)}</p>
                </button>
              ))}
            </div>
          )}
          {hasMoreRuns && <button onClick={() => void loadRuns(true)} disabled={loadingRuns} className="w-full px-4 py-3 text-sm font-medium text-indigo-700 border-t border-slate-100 hover:bg-indigo-50 disabled:opacity-50">Load older runs</button>}
        </aside>

        <div className="min-w-0 space-y-4">
          {!selectedRunId ? (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-12 text-center">
              <Activity className="w-10 h-10 text-slate-300 mx-auto mb-3" />
              <h2 className="font-semibold text-slate-800">Select a run to inspect</h2>
              <p className="text-sm text-slate-500 mt-1">Execution and retrieval evidence will appear here.</p>
            </div>
          ) : loadingDetail && !selectedRun ? (
            <div className="rounded-2xl border border-slate-200 bg-white p-12 text-center text-sm text-slate-500"><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Loading trace…</div>
          ) : detailError ? (
            <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800"><strong>Trace unavailable.</strong><p className="mt-1">{detailError}</p><button onClick={() => void loadDetail(selectedRunId, false)} className="mt-3 underline">Retry</button></div>
          ) : selectedRun ? (
            <>
              <div className="rounded-2xl border border-slate-200 bg-white shadow-sm p-5">
                <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h2 className="font-semibold text-lg text-slate-900">Run details</h2>
                      <span className={'rounded-full border px-2.5 py-1 text-xs font-semibold uppercase ' + statusTone(selectedRun.status)}>{selectedRun.status.replace(/_/g, ' ')}</span>
                    </div>
                    <p className="font-mono text-sm text-slate-600 mt-2 break-all">{selectedRun.run_id}</p>
                    <p className="text-xs text-slate-500 mt-2">Started {formatDate(selectedRun.started_at)} · Updated {formatDate(selectedRun.updated_at)}</p>
                  </div>
                  <button onClick={exportTrace} className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 shrink-0"><ArrowDownToLine className="w-4 h-4" />Export redacted trace</button>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5">
                  <Metric label="Duration" value={formatDuration(selectedRun.started_at, selectedRun.updated_at)} />
                  <Metric label="Events" value={String(selectedRun.event_count)} />
                  <Metric label="Schema" value={'v' + selectedRun.schema_version} />
                  <Metric label="Loaded" value={String(events.length)} />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <MetricCard label="Memory token estimate" value={tokenSummary.memoryTokens.toLocaleString()} note="Sum of recorded retrieval evidence" />
                <MetricCard label="Protected context" value={tokenSummary.protectedTokens.toLocaleString()} note="Reserved tokens recorded by retrieval" />
                <MetricCard label="Packed memories" value={tokenSummary.packedCount + ' / ' + tokenSummary.memoryCount} note="Selected evidence / retrieved evidence" />
              </div>

              <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-600 flex gap-2">
                <ShieldCheck className="w-4 h-4 shrink-0 text-emerald-600" />
                Raw prompts, memory text, tool arguments and tool results are not available in this view or in the export. Token limits and remaining budget are shown only when the trace schema records them; this run records retrieval estimates, not a total run budget.
              </div>

              {partialNotice && <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{partialNotice}</div>}

              <div className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
                <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
                  <div><h3 className="font-semibold text-slate-900">Chronological events</h3><p className="text-xs text-slate-500">Ordered by persisted sequence number</p></div>
                  <span className="text-xs text-slate-500">{events.length} loaded</span>
                </div>
                {loadingDetail && events.length === 0 ? <div className="p-8 text-center text-sm text-slate-500">Loading events…</div> : events.length === 0 ? <div className="p-8 text-center text-sm text-slate-500">No events recorded for this run.</div> : (
                  <div className="divide-y divide-slate-100">
                    {events.map((event, index) => <EventRow key={event.event_id || event.sequence_number || index} event={event} />)}
                  </div>
                )}
                {hasMoreEvents && <button onClick={() => void loadDetail(selectedRunId, true)} disabled={loadingMore} className="w-full px-5 py-3 text-sm font-medium text-indigo-700 border-t border-slate-100 hover:bg-indigo-50 disabled:opacity-50">{loadingMore ? 'Loading…' : 'Load next event page'}</button>}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
};

const Metric: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="rounded-xl bg-slate-50 border border-slate-100 p-3">
    <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
    <p className="text-lg font-semibold text-slate-900 mt-1 tabular-nums">{value}</p>
  </div>
);

const MetricCard: React.FC<{ label: string; value: string; note: string }> = ({ label, value, note }) => (
  <div className="rounded-xl border border-slate-200 bg-white p-4">
    <p className="text-xs text-slate-500">{label}</p>
    <p className="text-2xl font-semibold text-slate-900 mt-1 tabular-nums">{value}</p>
    <p className="text-[11px] text-slate-400 mt-1">{note}</p>
  </div>
);

const EventRow: React.FC<{ event: RunEvent }> = ({ event }) => {
  const [expanded, setExpanded] = useState(false);
  const evidence = event.memory_evidence || [];
  const tokenCost = evidence.reduce((sum, item) => sum + (item.token_cost || 0), 0);
  const isRetrieval = event.event_type === 'memory.retrieval';
  const isFailure = event.event_type.includes('failed') || event.event_type.includes('blocked') || event.event_type.includes('limit_exceeded');

  return (
    <div className="px-5 py-4">
      <div className="flex gap-3">
        <div className="flex flex-col items-center shrink-0">
          <div className={'w-2.5 h-2.5 rounded-full mt-1.5 ' + (isFailure ? 'bg-rose-500' : isRetrieval ? 'bg-violet-500' : 'bg-slate-400')} />
          <div className="w-px flex-1 bg-slate-200 mt-2" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-1">
            <div className="flex items-center flex-wrap gap-2">
              <span className="text-xs font-mono text-slate-400">#{event.sequence_number}</span>
              <span className="text-sm font-semibold text-slate-800 capitalize">{eventLabel(event)}</span>
              {event.tool_name && <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-mono text-slate-600">{event.tool_name}</span>}
            </div>
            <time className="text-[11px] text-slate-400 shrink-0">{formatDate(event.timestamp)}</time>
          </div>
          <div className="flex flex-wrap gap-2 mt-2">
            {event.run_status && <span className={'rounded border px-2 py-0.5 text-[10px] uppercase ' + statusTone(event.run_status)}>{event.run_status.replace(/_/g, ' ')}</span>}
            {event.reason && <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">Reason: {event.reason.replace(/_/g, ' ')}</span>}
            {event.error_type && <span className="rounded bg-rose-50 px-2 py-0.5 text-[10px] text-rose-700">{event.error_type}</span>}
            {event.step_index != null && <span className="text-[10px] text-slate-500">Step {event.step_index}</span>}
            {event.tool_call_index != null && <span className="text-[10px] text-slate-500">Tool call {event.tool_call_index}</span>}
          </div>

          {isRetrieval && (
            <div className="mt-3 rounded-lg border border-violet-100 bg-violet-50/50 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-semibold text-violet-900">Retrieval evidence</span>
                <span className="text-[11px] text-violet-700">{evidence.length} memories · {tokenCost.toLocaleString()} estimated tokens</span>
              </div>
              {event.retrieval_as_of != null && <p className="text-[11px] text-violet-700 mt-1">As-of: {formatDate(new Date(event.retrieval_as_of * 1000).toISOString())}</p>}
              {event.protected_constraints_count != null && <p className="text-[11px] text-violet-700 mt-1">Protected constraints: {event.protected_constraints_count} · {event.protected_constraints_token_cost || 0} reserved tokens</p>}
              {evidence.length > 0 && (
                <div className="mt-2 space-y-1.5">
                  {evidence.map((item) => (
                    <div key={item.memory_id} className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 rounded bg-white/80 px-2.5 py-2 text-[11px]">
                      <div className="min-w-0"><span className="font-mono text-slate-700 break-all">{item.memory_id}</span><span className="text-slate-400 ml-2">rank {item.rank}</span>{item.trust_level && <span className="ml-2 text-slate-500">{item.trust_level} trust</span>}</div>
                      <div className="flex items-center gap-2 shrink-0"><span className="text-slate-500">{item.token_cost} tokens</span><span className={'rounded px-2 py-0.5 font-semibold ' + (item.packed ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500')}>{item.packed ? 'packed' : 'not packed'}</span></div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <button onClick={() => setExpanded((value) => !value)} className="inline-flex items-center gap-1 mt-2 text-[11px] font-medium text-indigo-700 hover:text-indigo-900">
            {expanded ? 'Hide safe metadata' : 'Show safe metadata'} <ChevronDown className={'w-3 h-3 transition-transform ' + (expanded ? 'rotate-180' : '')} />
          </button>
          {expanded && (
            <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-slate-900 p-3 text-[10px] leading-relaxed text-slate-100">{JSON.stringify(safeExport({ run_id: 'event-preview', schema_version: 1, status: '', started_at: null, created_at: null, updated_at: null, event_count: 1 }, [event]).events[0], null, 2)}</pre>
          )}
        </div>
      </div>
    </div>
  );
};
