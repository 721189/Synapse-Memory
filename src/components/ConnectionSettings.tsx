import React, { useState, useEffect } from 'react';
import { X, KeyRound, Server, Save, CheckCircle2 } from 'lucide-react';
import { getApiKey, setApiKey, getTenant, setTenant } from '../lib/api';

interface ConnectionSettingsProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Lets an operator point the dashboard at a secured Synapse engine by entering
 * the API key + tenant that src/lib/api.ts injects on every request. Persisted
 * to localStorage so it survives reloads.
 */
export const ConnectionSettings: React.FC<ConnectionSettingsProps> = ({ open, onClose }) => {
  const [apiKey, setApiKeyInput] = useState('');
  const [tenant, setTenantInput] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (open) {
      setApiKeyInput(getApiKey());
      setTenantInput(getTenant());
      setSaved(false);
    }
  }, [open]);

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    setApiKey(apiKey.trim());
    setTenant(tenant.trim());
    setSaved(true);
    setTimeout(() => {
      setSaved(false);
      onClose();
    }, 900);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4">
      <div className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-md overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-slate-50">
          <div className="flex items-center space-x-2">
            <Server className="w-5 h-5 text-indigo-600" />
            <h2 className="font-bold text-slate-900">Engine Connection</h2>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 transition cursor-pointer">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSave} className="p-6 space-y-5">
          <p className="text-xs text-slate-500">
            Enter the API key issued by your Synapse engine (<code className="bg-slate-100 px-1 rounded">syn_live_…</code>)
            and the tenant to scope requests to. These are sent as{' '}
            <code className="bg-slate-100 px-1 rounded">X-Synapse-API-Key</code> and{' '}
            <code className="bg-slate-100 px-1 rounded">X-Tenant-ID</code> headers on every call.
          </p>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5 flex items-center space-x-1.5">
              <KeyRound className="w-4 h-4 text-slate-400" />
              <span>API Key</span>
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder="syn_live_…"
              autoComplete="off"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none font-mono"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">Tenant ID</label>
            <input
              type="text"
              value={tenant}
              onChange={(e) => setTenantInput(e.target.value)}
              placeholder="default"
              autoComplete="off"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none font-mono"
            />
            <p className="text-xs text-slate-400 mt-1">Leave blank to use the key's default tenant.</p>
          </div>

          <div className="flex items-center justify-end space-x-3 pt-2">
            {saved && (
              <span className="flex items-center space-x-1.5 text-sm text-emerald-600 font-medium">
                <CheckCircle2 className="w-4 h-4" />
                <span>Saved</span>
              </span>
            )}
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 rounded-lg transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="flex items-center space-x-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg shadow-sm transition cursor-pointer"
            >
              <Save className="w-4 h-4" />
              <span>Save &amp; Connect</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
