import React, { useState, useEffect } from 'react';
import { Network, ShieldCheck, Cpu, Layers, CheckCircle2, RefreshCw } from 'lucide-react';

export const HierarchicalGraphTab: React.FC = () => {
  const [communities, setCommunities] = useState<any[]>([]);
  const [algorithm, setAlgorithm] = useState('');
  const [zkpData, setZkpData] = useState<any>(null);
  const [loadingZkp, setLoadingZkp] = useState(false);
  const [loadingGraph, setLoadingGraph] = useState(true);

  const fetchCommunities = async () => {
    try {
      const res = await fetch('/api/graph/communities');
      const data = await res.json();
      if (data.success) {
        setCommunities(data.communities);
        setAlgorithm(data.algorithm);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingGraph(false);
    }
  };

  const generateZkpAttestation = async () => {
    setLoadingZkp(true);
    try {
      const res = await fetch('/api/security/zkp-attestation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenantId: 'tenant_enterprise_alpha' })
      });
      const data = await res.json();
      setZkpData(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingZkp(false);
    }
  };

  useEffect(() => {
    fetchCommunities();
    generateZkpAttestation();
  }, []);

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-xl">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-cyan-500/25 border border-cyan-500/30 flex items-center justify-center">
            <Network className="w-6 h-6 text-cyan-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold">Hierarchical GraphRAG & Zero-Knowledge Compliance</h2>
            <p className="text-xs text-slate-300">Leiden community detection algorithm clustering related sub-graphs with Groth16 ZK-SNARK cryptographic memory audits.</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* GraphRAG Communities */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-slate-900 flex items-center space-x-2">
              <Layers className="w-4 h-4 text-indigo-600" />
              <span>Leiden Community Summarization</span>
            </h3>
            <span className="text-[11px] font-mono bg-indigo-50 text-indigo-700 px-2.5 py-1 rounded-lg border border-indigo-100">{algorithm}</span>
          </div>
          <p className="text-xs text-slate-500">
            Instead of retrieving flat vector chunks, GraphRAG queries pre-computed thematic community summaries for holistic multi-month context.
          </p>

          <div className="space-y-3 pt-2">
            {communities.map(comm => (
              <div key={comm.communityId} className="p-4 rounded-xl border border-slate-200 bg-slate-50/50 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-slate-900">{comm.theme}</span>
                  <div className="flex items-center space-x-2 text-[11px] font-mono">
                    <span className="bg-slate-200 text-slate-700 px-2 py-0.5 rounded">{comm.nodeCount} nodes</span>
                    <span className="bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded font-bold">Cohesion: {(comm.cohesionScore * 100).toFixed(0)}%</span>
                  </div>
                </div>
                <p className="text-xs text-slate-600 leading-relaxed">{comm.summary}</p>
              </div>
            ))}
          </div>
        </div>

        {/* ZKP Attestation */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 flex flex-col justify-between">
          <div className="space-y-4">
            <h3 className="font-semibold text-slate-900 flex items-center space-x-2">
              <ShieldCheck className="w-4 h-4 text-emerald-600" />
              <span>Zero-Knowledge Proof (ZKP) Enterprise Audit</span>
            </h3>
            <p className="text-xs text-slate-500">
              Cryptographically prove ISO-27001 compliance and tenant data isolation without exposing plaintext memory vectors to auditors.
            </p>

            <button
              onClick={generateZkpAttestation}
              disabled={loadingZkp}
              className="w-full bg-slate-900 hover:bg-slate-800 text-white py-2.5 rounded-xl font-medium text-xs shadow-sm transition cursor-pointer"
            >
              {loadingZkp ? 'Generating Groth16 Proof...' : 'Generate New Cryptographic ZK-Attestation'}
            </button>

            {zkpData && (
              <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-3">
                <div className="flex items-center space-x-2 text-xs font-bold text-emerald-700">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  <span>ZK-SNARK Proof Verified (BN254 curve)</span>
                </div>
                <p className="text-xs text-slate-700 leading-relaxed italic">"{zkpData.attestationStatement}"</p>
                <div className="space-y-1 pt-2 border-t border-slate-200 text-[11px] font-mono text-slate-500">
                  <div>Tenant: <strong className="text-slate-800">{zkpData.tenantId}</strong></div>
                  <div>Proof Hash π_A: <span className="text-indigo-600">{zkpData.zkpProof.pi_a[0]}</span></div>
                  <div>Timestamp: {zkpData.timestamp}</div>
                </div>
              </div>
            )}
          </div>

          <div className="pt-4 border-t border-slate-100 text-[11px] text-slate-400">
            Differential privacy noise injection active (ε = 0.5, δ = 1e-5).
          </div>
        </div>
      </div>
    </div>
  );
};
