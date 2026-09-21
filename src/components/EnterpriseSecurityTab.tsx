import React, { useState } from 'react';
import { ShieldCheck, Lock, Trash2, CheckCircle2, AlertTriangle, FileText, Cpu, Key } from 'lucide-react';

interface EnterpriseSecurityTabProps {
  onRefresh: () => void;
}

export const EnterpriseSecurityTab: React.FC<EnterpriseSecurityTabProps> = ({ onRefresh }) => {
  const [testText, setTestText] = useState("Hey team, my API key is sk-proj-9921847109283471029 and my password is secret123. Contact me at 555-019-2834.");
  const [scrubResult, setScrubResult] = useState<any>(null);
  const [loadingScrub, setLoadingScrub] = useState(false);
  const [tenantId, setTenantId] = useState("tenant_enterprise_alpha");
  const [gdprResult, setGdprResult] = useState<any>(null);
  const [loadingGdpr, setLoadingGdpr] = useState(false);

  const handleScrubPII = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoadingScrub(true);
    try {
      const res = await fetch('/api/security/scrub-pii', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: testText })
      });
      const data = await res.json();
      setScrubResult(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingScrub(false);
    }
  };

  const handleGdprDelete = async () => {
    if (!window.confirm(`Execute GDPR Right-to-be-Forgotten cascade delete for tenant: ${tenantId}?`)) return;
    setLoadingGdpr(true);
    try {
      const res = await fetch('/api/security/gdpr-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenantId })
      });
      const data = await res.json();
      setGdprResult(data);
      onRefresh();
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingGdpr(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center">
            <Lock className="w-6 h-6 text-emerald-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold">Enterprise Security, Governance & Privacy Lab</h2>
            <p className="text-xs text-slate-300">Row-Level Security (RLS) tenant isolation, automated edge PII scrubbing, and cryptographic GDPR cascade wipes.</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* PII Scrubbing Simulator */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-col justify-between">
          <div>
            <h3 className="font-semibold text-slate-900 mb-2 flex items-center space-x-2">
              <ShieldCheck className="w-4 h-4 text-emerald-600" />
              <span>Automated PII Edge Scrubbing & CMEK</span>
            </h3>
            <p className="text-xs text-slate-500 mb-4">
              Before raw chat streams reach memory workers, the edge proxy detects and redacts API keys, passwords, credit cards, and SSNs.
            </p>

            <form onSubmit={handleScrubPII} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Raw Input Text with Sensitive PII</label>
                <textarea
                  rows={3}
                  value={testText}
                  onChange={e => setTestText(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                  required
                />
              </div>

              <button
                type="submit"
                disabled={loadingScrub}
                className="w-full bg-slate-900 hover:bg-slate-800 text-white py-2.5 rounded-xl font-medium text-xs shadow-sm transition cursor-pointer"
              >
                {loadingScrub ? 'Scrubbing & Encrypting...' : 'Run Edge PII Masking & CMEK Encryption'}
              </button>
            </form>

            {scrubResult && (
              <div className="mt-4 p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-3">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-slate-600">PII Detected:</span>
                  <span className={`px-2 py-0.5 rounded-full font-bold ${scrubResult.detectedPII ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'}`}>
                    {scrubResult.detectedPII ? 'Yes (Redacted)' : 'None'}
                  </span>
                </div>
                <div>
                  <span className="block text-[11px] font-semibold text-slate-400 mb-1">Scrubbed Output:</span>
                  <p className="text-xs font-mono bg-white p-2.5 rounded-lg border border-slate-200 text-slate-800 break-all">
                    {scrubResult.scrubbedText}
                  </p>
                </div>
                <div className="flex justify-between items-center text-[11px] text-slate-400 pt-2 border-t border-slate-200">
                  <span>Encryption Key:</span>
                  <strong className="font-mono text-indigo-600">{scrubResult.encryptionCMEK}</strong>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* GDPR Right to be Forgotten */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-col justify-between">
          <div>
            <h3 className="font-semibold text-slate-900 mb-2 flex items-center space-x-2">
              <Trash2 className="w-4 h-4 text-red-600" />
              <span>GDPR "Right to be Forgotten" Graph Cascading</span>
            </h3>
            <p className="text-xs text-slate-500 mb-4">
              Securely purge all vector chunks, knowledge graph sub-nodes, and episodic logs with cryptographic 3-pass zero-fill overrides.
            </p>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">Tenant / User UUID Scope</label>
                <input
                  type="text"
                  value={tenantId}
                  onChange={e => setTenantId(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500"
                />
              </div>

              <button
                type="button"
                onClick={handleGdprDelete}
                disabled={loadingGdpr}
                className="w-full bg-red-600 hover:bg-red-700 text-white py-2.5 rounded-xl font-medium text-xs shadow-sm transition cursor-pointer flex items-center justify-center space-x-2"
              >
                <Trash2 className="w-4 h-4" />
                <span>{loadingGdpr ? 'Executing Cascade Wipe...' : 'Execute GDPR Cascading Wipe'}</span>
              </button>

              {gdprResult && (
                <div className="p-4 rounded-xl bg-red-50 border border-red-200 space-y-2">
                  <div className="flex items-center space-x-2 text-red-800 font-semibold text-xs">
                    <CheckCircle2 className="w-4 h-4 text-red-600" />
                    <span>Cascade Wipe Successful</span>
                  </div>
                  <p className="text-xs text-red-700">{gdprResult.auditTrail}</p>
                  <div className="text-[11px] text-red-600 font-mono">
                    Nodes Wiped: {gdprResult.deletedCount} | Remaining Nodes: {gdprResult.remainingCount}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="pt-4 border-t border-slate-100 text-xs text-slate-400">
            Row-Level Security (RLS): Enforced at PostgreSQL driver layer with cryptographically signed tenant context headers.
          </div>
        </div>
      </div>
    </div>
  );
};
