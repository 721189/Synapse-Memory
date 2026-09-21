import React, { useState, useEffect } from 'react';
import { ShieldCheck, Award, Key, Signature, FileText, Download, Check, HelpCircle, ArrowRight, DollarSign } from 'lucide-react';
import { CommercialLicense } from '../types';

export const CommercialBuyoutTab: React.FC = () => {
  const [license, setLicense] = useState<CommercialLicense | null>(null);
  const [licensedTo, setLicensedTo] = useState('');
  const [loading, setLoading] = useState(true);
  const [buying, setBuying] = useState(false);
  const [signing, setSigning] = useState(false);

  const fetchLicense = async () => {
    try {
      const res = await fetch('/api/licensing/details');
      const data = await res.json();
      if (data.success && data.license) {
        setLicense(data.license);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const executeBuyout = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!licensedTo) return;
    setBuying(true);
    try {
      const res = await fetch('/api/licensing/buyout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ licensedTo })
      });
      const data = await res.json();
      if (data.success) {
        setLicense(data.license);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setBuying(false);
    }
  };

  const signContract = async () => {
    setSigning(true);
    try {
      const res = await fetch('/api/licensing/sign-contract', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        setLicense(data.license);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSigning(false);
    }
  };

  useEffect(() => {
    fetchLicense();
  }, []);

  return (
    <div className="space-y-8">
      {/* Premium Header */}
      <div className="bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-900 text-white rounded-3xl p-8 shadow-xl border border-indigo-500/20 text-center max-w-4xl mx-auto space-y-4">
        <div className="inline-flex items-center space-x-2 bg-indigo-500/10 border border-indigo-500/30 px-3 py-1 rounded-full text-xs text-indigo-300 font-semibold uppercase tracking-wider">
          <Award className="w-3.5 h-3.5 mr-1 text-amber-400" />
          <span>SynapseMemory Commercial Licensing</span>
        </div>
        <h1 className="text-3xl font-extrabold tracking-tight md:text-4xl">
          Complete Production Commercial License & IP Buyout
        </h1>
        <p className="text-slate-300 text-sm max-w-2xl mx-auto leading-relaxed">
          Unlock unlimited self-hosted deployment nodes, full-stack proprietary source code access, signed intellectual property assignment, and enterprise-level 99.99% uptime SLAs.
        </p>
        <div className="text-3xl font-extrabold text-amber-400 flex items-center justify-center space-x-2">
          <DollarSign className="w-7 h-7" />
          <span>1,000 USD</span>
          <span className="text-xs text-slate-400 font-medium self-end mb-1">/ One-Time Perpetual Buyout</span>
        </div>
      </div>

      <div className="max-w-4xl mx-auto grid grid-cols-1 md:grid-cols-2 gap-8 items-start">
        
        {/* Licensing Highlights / What is Included */}
        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-8 space-y-6">
          <h3 className="font-bold text-slate-900 text-lg">What's Included in the Buyout:</h3>
          
          <ul className="space-y-4">
            <li className="flex items-start space-x-3 text-xs text-slate-600 leading-normal">
              <div className="w-5 h-5 rounded-full bg-indigo-50 flex items-center justify-center shrink-0 border border-indigo-200">
                <Check className="w-3.5 h-3.5 text-indigo-600" />
              </div>
              <div>
                <strong className="text-slate-900 block">Perpetual Source Code Ownership</strong>
                Full intellectual property (IP) assignment. Modify, fork, and embed SynapseMemory within any commercial product without royalty fees.
              </div>
            </li>
            <li className="flex items-start space-x-3 text-xs text-slate-600 leading-normal">
              <div className="w-5 h-5 rounded-full bg-indigo-50 flex items-center justify-center shrink-0 border border-indigo-200">
                <Check className="w-3.5 h-3.5 text-indigo-600" />
              </div>
              <div>
                <strong className="text-slate-900 block">Production-Ready HNSW Engine</strong>
                Access to direct binary configurations, high-throughput pgvector indexing libraries, and self-managed Kubernetes manifests.
              </div>
            </li>
            <li className="flex items-start space-x-3 text-xs text-slate-600 leading-normal">
              <div className="w-5 h-5 rounded-full bg-indigo-50 flex items-center justify-center shrink-0 border border-indigo-200">
                <Check className="w-3.5 h-3.5 text-indigo-600" />
              </div>
              <div>
                <strong className="text-slate-900 block">Sovereign On-Premise Deployment</strong>
                Run within any secure VPC (AWS GovCloud, Google Virtual Private Cloud, air-gapped secure servers) completely disconnected from external tracking.
              </div>
            </li>
            <li className="flex items-start space-x-3 text-xs text-slate-600 leading-normal">
              <div className="w-5 h-5 rounded-full bg-indigo-50 flex items-center justify-center shrink-0 border border-indigo-200">
                <Check className="w-3.5 h-3.5 text-indigo-600" />
              </div>
              <div>
                <strong className="text-slate-900 block">99.99% Enterprise Support & SLA</strong>
                Direct slack channels, automated hotfixes, and customized engineering advisory from the core ML research team.
              </div>
            </li>
          </ul>
        </div>

        {/* Dynamic checkout or active license detail state */}
        {!license ? (
          <div className="bg-slate-50 rounded-3xl border border-slate-200 shadow-sm p-8 space-y-6">
            <h3 className="font-bold text-slate-900 text-lg flex items-center space-x-2">
              <ShieldCheck className="w-5 h-5 text-indigo-600" />
              <span>Instant Commercial Buyout</span>
            </h3>
            
            <p className="text-xs text-slate-500 leading-relaxed">
              Fill in your organization credentials to construct the digital SLA agreement, finalize licensing, and generate your secure perpetual production key.
            </p>

            <form onSubmit={executeBuyout} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Licensed To (Company/Developer):</label>
                <input
                  type="text"
                  required
                  placeholder="e.g., Tesla ML Systems Core"
                  value={licensedTo}
                  onChange={e => setLicensedTo(e.target.value)}
                  className="w-full bg-white border border-slate-300 rounded-xl px-3 py-2.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Simulated Credit Card Integration:</label>
                <div className="bg-white border border-slate-300 rounded-xl px-3 py-2.5 text-xs text-slate-400 font-mono flex items-center justify-between">
                  <span>•••• •••• •••• 4242 (Stripe Test Visa)</span>
                  <span className="text-[10px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded font-sans font-bold">1k USD</span>
                </div>
              </div>

              <button
                type="submit"
                disabled={buying}
                className="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-3 rounded-xl text-xs font-bold transition flex items-center justify-center space-x-1.5 cursor-pointer shadow-md shadow-indigo-600/15"
              >
                <span>{buying ? 'Processing Licensing...' : 'Authorize Perpetual Buyout ($1,000)'}</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            </form>
          </div>
        ) : (
          <div className="bg-slate-900 text-white rounded-3xl border border-slate-800 shadow-xl p-8 space-y-6">
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-lg flex items-center space-x-2">
                <Check className="w-5 h-5 text-emerald-400" />
                <span>Commercial Activation Center</span>
              </h3>
              <span className="text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/25 px-2 py-0.5 rounded font-bold">LICENSE SECURED</span>
            </div>

            <div className="p-4 bg-slate-800/60 border border-slate-700 rounded-2xl space-y-3">
              <div className="flex justify-between text-xs text-slate-300">
                <span>License Owner:</span>
                <strong className="text-white">{license.licensedTo}</strong>
              </div>
              <div className="flex justify-between text-xs text-slate-300">
                <span>License Status:</span>
                <span className="font-mono text-emerald-400 font-bold uppercase text-[10px]">Active & Verified</span>
              </div>
              <div className="flex justify-between text-xs text-slate-300">
                <span>SLA Tier Assigned:</span>
                <strong className="text-amber-400">{license.slaTier}</strong>
              </div>
              <div className="flex flex-col space-y-1 pt-2 border-t border-slate-700">
                <span className="text-xs text-slate-400 font-bold">Perpetual Production Key:</span>
                <div className="bg-slate-950 font-mono text-[11px] text-indigo-300 p-2 rounded border border-slate-800 select-all overflow-x-auto">
                  {license.licenseKey}
                </div>
              </div>
            </div>

            {/* SLA Signature Module */}
            <div className="p-4 bg-slate-800/40 border border-slate-700/50 rounded-2xl space-y-3">
              <div className="flex items-center space-x-2 text-xs font-bold text-slate-200">
                <Signature className="w-4 h-4 text-amber-400" />
                <span>Assign Intellectual Property (IP) Transfer Contract:</span>
              </div>
              <p className="text-[10px] text-slate-400 leading-normal">
                By executing this legal contract, SynapseMemory officially and irrevocably assigns 100% of its copyright, technology vectors, and repository structures directly to your target entity.
              </p>

              {license.ipAssignmentSigned ? (
                <div className="flex items-center space-x-1.5 p-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl text-xs font-bold justify-center">
                  <Check className="w-4 h-4" />
                  <span>IP Assignment Contract Signed & Legally Binding</span>
                </div>
              ) : (
                <button
                  onClick={signContract}
                  disabled={signing}
                  className="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 py-2 rounded-xl text-xs font-bold transition flex items-center justify-center space-x-1.5 cursor-pointer"
                >
                  <span>{signing ? 'Signing Contract...' : 'Digitally Execute Legal IP Signoff'}</span>
                </button>
              )}
            </div>

            {/* SDK Bundle Downloader */}
            <div className="space-y-2">
              <span className="text-xs font-semibold text-slate-300 block">Download Enterprise SDK & Docker Bundles:</span>
              <div className="grid grid-cols-2 gap-2">
                <button className="p-2 border border-slate-700 bg-slate-800 hover:bg-slate-700 rounded-xl text-xs text-slate-200 transition flex items-center justify-center space-x-1.5 cursor-pointer">
                  <Download className="w-3.5 h-3.5 text-indigo-400" />
                  <span>Sovereign SDK (.zip)</span>
                </button>
                <button className="p-2 border border-slate-700 bg-slate-800 hover:bg-slate-700 rounded-xl text-xs text-slate-200 transition flex items-center justify-center space-x-1.5 cursor-pointer">
                  <Download className="w-3.5 h-3.5 text-indigo-400" />
                  <span>K8s Helm Manifests</span>
                </button>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
};
