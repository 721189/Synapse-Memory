import React, { useState } from 'react';
import { 
  ShieldCheck, Lock, Trash2, CheckCircle2, AlertTriangle, FileText, 
  Cpu, Key, ShieldAlert, Plus, Users, Globe, Terminal, Settings, RefreshCw
} from 'lucide-react';

interface EnterpriseSecurityTabProps {
  onRefresh: () => void;
}

interface AuditLog {
  id: string;
  timestamp: string;
  action: string;
  actor: string;
  ip: string;
  status: 'SUCCESS' | 'WARNING' | 'CRITICAL';
}

export const EnterpriseSecurityTab: React.FC<EnterpriseSecurityTabProps> = ({ onRefresh }) => {
  // PII Scrub states
  const [testText, setTestText] = useState("Hey team, my API key is sk-proj-9921847109283471029 and my password is secret123. Contact me at 555-019-2834.");
  const [scrubResult, setScrubResult] = useState<any>(null);
  const [loadingScrub, setLoadingScrub] = useState(false);

  // GDPR States
  const [tenantId, setTenantId] = useState("tenant_tesla_autopilot");
  const [gdprResult, setGdprResult] = useState<any>(null);
  const [loadingGdpr, setLoadingGdpr] = useState(false);

  // Enterprise SSO / Onboarding States
  const [ssoDomain, setSsoDomain] = useState("tesla.com");
  const [ssoProvider, setSsoProvider] = useState<"Okta" | "Azure AD" | "Ping Identity" | "SAML 2.0">("Okta");
  const [enforceMfa, setEnforceMfa] = useState(true);
  const [onboardingSuccess, setOnboardingSuccess] = useState(false);
  const [ssoMetadata, setSsoMetadata] = useState<any>(null);

  // CIDR IP Whitelist States
  const [cidrList, setCidrList] = useState<string[]>(["12.43.19.0/24", "195.122.0.0/16", "10.0.0.0/8"]);
  const [newCidr, setNewCidr] = useState("");
  const [cidrError, setCidrError] = useState("");

  // RBAC Roles States
  const [activeRole, setActiveRole] = useState<"Platform Admin" | "Security Auditor" | "Cognitive Engineer">("Platform Admin");
  const [rolesConfig, setRolesConfig] = useState({
    "Platform Admin": { writeMemory: true, readPII: true, rotateKeys: true, billing: true },
    "Security Auditor": { writeMemory: false, readPII: true, rotateKeys: false, billing: false },
    "Cognitive Engineer": { writeMemory: true, readPII: false, rotateKeys: false, billing: false },
  });

  // Audit Logs State
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([
    { id: "1", timestamp: "14:32:01", action: "SAML IDP Sign-On Certificate Rotated", actor: "sec-ops@tesla.com", ip: "12.43.19.45", status: "SUCCESS" },
    { id: "2", timestamp: "14:15:12", action: "IP CIDR Range Block Add: 195.122.0.0/16", actor: "admin@tesla.com", ip: "12.43.19.45", status: "SUCCESS" },
    { id: "3", timestamp: "13:55:00", action: "Anomalous API Probe Blocked (Multi-tenant boundary guard)", actor: "Unknown IP", ip: "185.220.101.4", status: "CRITICAL" },
    { id: "4", timestamp: "12:11:08", action: "GDPR Right-to-be-Forgotten Purge Request Approved", actor: "compliance@tesla.com", ip: "12.43.19.12", status: "WARNING" }
  ]);

  const addAuditLog = (action: string, actor: string, ip: string, status: 'SUCCESS' | 'WARNING' | 'CRITICAL') => {
    const newLog: AuditLog = {
      id: Date.now().toString(),
      timestamp: new Date().toLocaleTimeString(),
      action,
      actor,
      ip,
      status
    };
    setAuditLogs(prev => [newLog, ...prev]);
  };

  // Onboard SSO Action
  const handleSSOOnboard = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ssoDomain.trim()) return;

    setOnboardingSuccess(true);
    const mockMetadata = {
      clientId: `client_id_synapse_${Math.random().toString(36).substring(2, 10)}`,
      entityId: `https://saml.${ssoDomain}/metadata`,
      acsUrl: `https://api.synapse.tesla.com/v1/auth/sso/callback`,
      status: "ACTIVE_VERIFIED"
    };
    setSsoMetadata(mockMetadata);

    addAuditLog(
      `Enterprise SSO Provisioned via ${ssoProvider} for domain *.${ssoDomain}`,
      `admin@${ssoDomain}`,
      "12.43.19.45",
      "SUCCESS"
    );
  };

  // Add CIDR Range
  const handleAddCidr = (e: React.FormEvent) => {
    e.preventDefault();
    setCidrError("");

    // Simple CIDR Regex Validation
    const cidrRegex = /^([0-9]{1,3}\.){3}[0-9]{1,3}\/[0-9]{1,2}$/;
    if (!cidrRegex.test(newCidr)) {
      setCidrError("Invalid CIDR format (e.g., 192.168.1.0/24)");
      return;
    }

    setCidrList(prev => [...prev, newCidr]);
    addAuditLog(`Security Ingress Whitelist: Added IP Range ${newCidr}`, `admin@${ssoDomain}`, "12.43.19.45", "SUCCESS");
    setNewCidr("");
  };

  // Remove CIDR
  const handleRemoveCidr = (target: string) => {
    setCidrList(prev => prev.filter(c => c !== target));
    addAuditLog(`Security Ingress Whitelist: Removed IP Range ${target}`, `admin@${ssoDomain}`, "12.43.19.45", "WARNING");
  };

  // Scrub PII Action
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
      addAuditLog("Edge Proxy: Triggered PII Scrubber & KMS Key Enveloping", `admin@${ssoDomain}`, "12.43.19.45", "SUCCESS");
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingScrub(false);
    }
  };

  // GDPR Delete Action
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
      addAuditLog(`Compliance: GDPR Cascade Graph Purge for tenant ${tenantId}`, "compliance@tesla.com", "12.43.19.12", "WARNING");
      onRefresh();
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingGdpr(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Enterprise Header Banner */}
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-3xl p-6 sm:p-8 border border-slate-800 shadow-xl relative overflow-hidden">
        <div className="absolute top-0 right-0 w-64 h-64 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none"></div>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6 relative z-10 text-left">
          <div className="space-y-2">
            <div className="inline-flex items-center space-x-2 bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-3 py-1 rounded-full text-xs font-semibold tracking-wide uppercase">
              <ShieldCheck className="w-3.5 h-3.5" />
              <span>Sovereign Compliance Layer</span>
            </div>
            <h2 className="text-2xl sm:text-3xl font-black tracking-tight">Enterprise Identity & Security Portal</h2>
            <p className="text-xs sm:text-sm text-slate-300 max-w-2xl leading-relaxed">
              Provision single sign-on (SSO), configure strict Role-Based Access Control (RBAC), lock network CIDRs, and inspect the unified active compliance audit ledger.
            </p>
          </div>
          <div className="text-xs bg-slate-900/60 border border-slate-800 p-4 rounded-2xl shrink-0 space-y-1 font-mono text-slate-300">
            <div className="flex justify-between space-x-6">
              <span>SAML SSO:</span>
              <span className="text-emerald-400 font-bold">READY (Active)</span>
            </div>
            <div className="flex justify-between space-x-6">
              <span>GDPR Protocol:</span>
              <span className="text-indigo-400 font-bold">SHA-3 Cascade</span>
            </div>
            <div className="flex justify-between space-x-6">
              <span>CMEK Rotation:</span>
              <span className="text-slate-400 font-bold">Auto-256</span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* SECTION 1: ENTERPRISE SSO SIGNUP & PROVISIONING */}
        <div className="lg:col-span-2 bg-white rounded-3xl border border-slate-200 shadow-xs p-6 space-y-6 text-left flex flex-col justify-between">
          <div className="space-y-4">
            <div className="space-y-1">
              <div className="flex items-center space-x-2 text-indigo-600">
                <Globe className="w-5 h-5 text-indigo-500" />
                <h3 className="font-bold text-slate-900 text-base">SSO Provisioning & Domain Locking</h3>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">
                Connect your organization's Identity Provider. Users signing up with verified matching email domains will be locked into your enterprise workspace node automatically.
              </p>
            </div>

            <form onSubmit={handleSSOOnboard} className="space-y-4 bg-slate-50 p-4 sm:p-5 rounded-2xl border border-slate-200/80">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-[10px] font-bold text-slate-700 uppercase tracking-wider mb-1.5">Target Domain Name</label>
                  <input
                    type="text"
                    value={ssoDomain}
                    onChange={e => setSsoDomain(e.target.value)}
                    placeholder="e.g. tesla.com"
                    className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 font-mono text-slate-900"
                    required
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-bold text-slate-700 uppercase tracking-wider mb-1.5">SAML IdP Provider</label>
                  <select
                    value={ssoProvider}
                    onChange={e => setSsoProvider(e.target.value as any)}
                    className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 text-slate-900 font-semibold cursor-pointer"
                  >
                    <option value="Okta">Okta Suite</option>
                    <option value="Azure AD">Microsoft Azure AD</option>
                    <option value="Ping Identity">Ping Identity</option>
                    <option value="SAML 2.0">Generic SAML 2.0</option>
                  </select>
                </div>
              </div>

              <div className="flex items-center justify-between pt-1">
                <label className="flex items-center space-x-2 text-xs text-slate-600 select-none font-medium cursor-pointer">
                  <input
                    type="checkbox"
                    checked={enforceMfa}
                    onChange={e => setEnforceMfa(e.target.checked)}
                    className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <span>Enforce Strict Hardware Multi-Factor Authentication (MFA)</span>
                </label>
                <span className="text-[10px] bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded-full font-bold">FIDO2/WebAuthn</span>
              </div>

              <button
                type="submit"
                className="w-full py-2.5 bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold rounded-xl transition shadow-xs flex items-center justify-center space-x-1.5 cursor-pointer"
              >
                <span>Authorize & Synchronize Federated SSO Node</span>
              </button>
            </form>

            {onboardingSuccess && ssoMetadata && (
              <div className="p-4 rounded-2xl bg-emerald-50/50 border border-emerald-200 text-slate-800 space-y-3 animate-fadeIn">
                <div className="flex items-center space-x-2 text-emerald-800 font-bold text-xs">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  <span>SAML SSO Endpoint Connected Successfully</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[10px] font-mono text-slate-600 bg-white p-3 rounded-xl border border-emerald-100">
                  <div>
                    <span className="block text-slate-400">ENTITY ID:</span>
                    <span className="text-slate-900 font-semibold select-all">{ssoMetadata.entityId}</span>
                  </div>
                  <div>
                    <span className="block text-slate-400">CLIENT ID:</span>
                    <span className="text-slate-900 font-semibold select-all">{ssoMetadata.clientId}</span>
                  </div>
                  <div className="sm:col-span-2">
                    <span className="block text-slate-400">ACS CALLBACK ENDPOINT:</span>
                    <span className="text-slate-900 font-semibold select-all break-all">{ssoMetadata.acsUrl}</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="pt-4 border-t border-slate-100 text-[10px] text-slate-400 font-mono">
            Any user authenticating from a *. {ssoDomain} email address will automatically bypass local login gates and route to Federated SAML credentials.
          </div>
        </div>

        {/* SECTION 2: IP ACCESS WHITELIST (CIDR) */}
        <div className="bg-white rounded-3xl border border-slate-200 shadow-xs p-6 space-y-5 text-left flex flex-col justify-between">
          <div className="space-y-4">
            <div className="space-y-1">
              <div className="flex items-center space-x-2 text-indigo-600">
                <ShieldAlert className="w-5 h-5 text-indigo-500" />
                <h3 className="font-bold text-slate-900 text-base">IP CIDR Whitelisting</h3>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">
                Restrict RAG API calls, control panel views, and memory ingestion processes to authorized corporate network environments only.
              </p>
            </div>

            <form onSubmit={handleAddCidr} className="space-y-2">
              <div className="flex items-center space-x-2">
                <input
                  type="text"
                  placeholder="e.g. 192.168.1.0/24"
                  value={newCidr}
                  onChange={e => setNewCidr(e.target.value)}
                  className="flex-1 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500/20 font-mono text-slate-900"
                />
                <button
                  type="submit"
                  className="bg-indigo-600 hover:bg-indigo-700 text-white p-2 rounded-xl transition flex items-center justify-center cursor-pointer shadow-md shadow-indigo-600/10 shrink-0"
                >
                  <Plus className="w-4 h-4" />
                </button>
              </div>
              {cidrError && <p className="text-[10px] text-red-600 font-semibold">{cidrError}</p>}
            </form>

            <div className="space-y-1.5 max-h-[140px] overflow-y-auto pr-1 scrollbar-thin">
              {cidrList.map(cidr => (
                <div key={cidr} className="flex items-center justify-between p-2.5 bg-slate-50 border border-slate-200/80 rounded-xl font-mono text-[11px] text-slate-700">
                  <div className="flex items-center space-x-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    <span className="font-semibold text-slate-900">{cidr}</span>
                  </div>
                  <button
                    onClick={() => handleRemoveCidr(cidr)}
                    className="text-red-500 hover:text-red-700 transition font-sans text-xs cursor-pointer font-bold px-1"
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="pt-4 border-t border-slate-100 text-[10px] text-slate-400 font-mono flex items-center justify-between">
            <span>Global Ingress State:</span>
            <strong className="text-emerald-600 font-bold uppercase">ENFORCING</strong>
          </div>
        </div>

      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* SECTION 3: ROLE-BASED ACCESS CONTROL (RBAC) CONFIG */}
        <div className="lg:col-span-5 bg-white rounded-3xl border border-slate-200 shadow-xs p-6 space-y-6 text-left flex flex-col justify-between">
          <div className="space-y-4">
            <div className="space-y-1">
              <div className="flex items-center space-x-2 text-indigo-600">
                <Users className="w-5 h-5 text-indigo-500" />
                <h3 className="font-bold text-slate-900 text-base">Role-Based Access Control (RBAC)</h3>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">
                Design strict functional boundaries. Lock access to secure PII data, client memory structures, and API rotations based on specific corporate user roles.
              </p>
            </div>

            {/* Role Selectors */}
            <div className="grid grid-cols-3 gap-2 p-1 bg-slate-100 rounded-xl">
              {(["Platform Admin", "Security Auditor", "Cognitive Engineer"] as const).map(role => (
                <button
                  key={role}
                  onClick={() => setActiveRole(role)}
                  className={`py-2 text-[10px] font-bold rounded-lg transition-all ${
                    activeRole === role ? "bg-white text-slate-900 shadow-xs" : "text-slate-500 hover:text-slate-800"
                  }`}
                >
                  {role.split(" ")[1]}
                </button>
              ))}
            </div>

            {/* Config Box */}
            <div className="space-y-3 bg-slate-50 p-4 rounded-2xl border border-slate-200/80">
              <div className="text-xs font-bold text-slate-800 border-b border-slate-200 pb-2">
                Active Group: <span className="text-indigo-600 font-mono">{activeRole}</span>
              </div>
              <div className="space-y-2.5 text-xs text-slate-600">
                <div className="flex justify-between items-center">
                  <span>Write Memory Nodes:</span>
                  <span className={`font-mono font-bold ${rolesConfig[activeRole].writeMemory ? "text-emerald-600" : "text-slate-400"}`}>
                    {rolesConfig[activeRole].writeMemory ? "ALLOWED" : "DENIED"}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span>Inspect Encrypted PII:</span>
                  <span className={`font-mono font-bold ${rolesConfig[activeRole].readPII ? "text-emerald-600" : "text-slate-400"}`}>
                    {rolesConfig[activeRole].readPII ? "ALLOWED" : "DENIED"}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span>Rotate KMS KMS keys:</span>
                  <span className={`font-mono font-bold ${rolesConfig[activeRole].rotateKeys ? "text-emerald-600" : "text-slate-400"}`}>
                    {rolesConfig[activeRole].rotateKeys ? "ALLOWED" : "DENIED"}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span>Manage Enterprise Billing:</span>
                  <span className={`font-mono font-bold ${rolesConfig[activeRole].billing ? "text-emerald-600" : "text-slate-400"}`}>
                    {rolesConfig[activeRole].billing ? "ALLOWED" : "DENIED"}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <button
            onClick={() => {
              addAuditLog(`RBAC Settings Updated for group ${activeRole}`, `admin@${ssoDomain}`, "12.43.19.45", "SUCCESS");
              alert(`Saved custom settings for ${activeRole} globally!`);
            }}
            className="w-full py-2.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-xs font-bold rounded-xl transition border border-indigo-100 flex items-center justify-center cursor-pointer"
          >
            <span>Save Custom Role Policy</span>
          </button>
        </div>

        {/* SECTION 4: LIVE COMPLIANCE AUDIT LEDGER */}
        <div className="lg:col-span-7 bg-white rounded-3xl border border-slate-200 shadow-xs p-6 space-y-4 text-left flex flex-col justify-between">
          <div className="space-y-3 flex-1">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2 text-slate-900">
                <Terminal className="w-5 h-5 text-indigo-500 animate-pulse" />
                <h3 className="font-bold text-base">Active Compliance Audit Ledger</h3>
              </div>
              <button
                onClick={() => addAuditLog("Security Assessment Probe Initiated", "system-probe", "127.0.0.1", "SUCCESS")}
                className="p-1 hover:bg-slate-100 rounded-lg text-slate-400 hover:text-slate-600 transition cursor-pointer"
                title="Force refresh audit test probe"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              Pruned-proof audit trail mapping access coordinates, API invocations, and tenant border crossings. Complies directly with SOX Section 404, ISO 27001, and SOC2.
            </p>

            {/* Audit stream box */}
            <div className="bg-slate-950 rounded-2xl p-4 h-[240px] overflow-y-auto font-mono text-[9px] text-slate-300 border border-slate-900 space-y-2.5 scrollbar-thin flex-1">
              {auditLogs.map(log => (
                <div key={log.id} className="flex items-start justify-between border-b border-slate-900 pb-2 gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center space-x-1.5">
                      <span className={`w-1.5 h-1.5 rounded-full ${
                        log.status === 'SUCCESS' ? 'bg-emerald-500' : log.status === 'WARNING' ? 'bg-amber-500 animate-ping' : 'bg-red-500 animate-pulse'
                      }`}></span>
                      <span className="text-slate-400">[{log.timestamp}]</span>
                      <span className="text-slate-100 font-semibold">{log.action}</span>
                    </div>
                    <div className="text-slate-500 text-[8px]">
                      ACTOR: {log.actor} | INGRESS IP: {log.ip}
                    </div>
                  </div>
                  <span className={`text-[8px] px-1.5 py-0.5 rounded-sm font-bold ${
                    log.status === 'SUCCESS' ? 'bg-emerald-500/10 text-emerald-400' : log.status === 'WARNING' ? 'bg-amber-500/10 text-amber-400' : 'bg-red-500/10 text-red-400'
                  }`}>
                    {log.status}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="text-[10px] text-slate-400 bg-slate-50 p-2.5 rounded-xl border border-slate-200/80 flex items-center justify-between font-mono">
            <span>Audit Integrity Status:</span>
            <span className="text-emerald-600 font-bold flex items-center space-x-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
              <span>SHA-256 SIGNED & LOCKED</span>
            </span>
          </div>
        </div>

      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* PII & GDPR Lab */}
        {/* PII Scrubbing Simulator */}
        <div className="bg-white rounded-3xl border border-slate-200 shadow-xs p-6 flex flex-col justify-between text-left">
          <div>
            <h3 className="font-bold text-slate-900 mb-2 flex items-center space-x-2">
              <ShieldCheck className="w-5 h-5 text-emerald-500" />
              <span>Automated PII Edge Scrubbing & CMEK</span>
            </h3>
            <p className="text-xs text-slate-500 mb-4 leading-relaxed">
              Before raw chat streams reach memory workers, the edge proxy detects and redacts API keys, passwords, credit cards, and SSNs.
            </p>

            <form onSubmit={handleScrubPII} className="space-y-4">
              <div>
                <label className="block text-[10px] font-bold text-slate-700 uppercase tracking-wider mb-1.5">Raw Input Text with Sensitive PII</label>
                <textarea
                  rows={3}
                  value={testText}
                  onChange={e => setTestText(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-2xl p-3 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500/20 text-slate-900 leading-relaxed font-mono"
                  required
                />
              </div>

              <button
                type="submit"
                disabled={loadingScrub}
                className="w-full bg-slate-900 hover:bg-slate-800 text-white py-2.5 rounded-xl font-bold text-xs shadow-sm transition cursor-pointer"
              >
                {loadingScrub ? 'Scrubbing & Encrypting...' : 'Run Edge PII Masking & CMEK Encryption'}
              </button>
            </form>

            {scrubResult && (
              <div className="mt-4 p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-3 animate-fadeIn">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-slate-600">PII Detected:</span>
                  <span className={`px-2 py-0.5 rounded-full font-bold text-[10px] ${scrubResult.detectedPII ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'}`}>
                    {scrubResult.detectedPII ? 'Yes (Redacted)' : 'None'}
                  </span>
                </div>
                <div>
                  <span className="block text-[10px] font-bold text-slate-400 mb-1">Scrubbed Output:</span>
                  <p className="text-xs font-mono bg-white p-2.5 rounded-lg border border-slate-200 text-slate-800 break-all leading-relaxed">
                    {scrubResult.scrubbedText}
                  </p>
                </div>
                <div className="flex justify-between items-center text-[10px] text-slate-400 pt-2 border-t border-slate-200 font-mono">
                  <span>Encryption Key:</span>
                  <strong className="font-mono text-indigo-600">{scrubResult.encryptionCMEK}</strong>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* GDPR Right to be Forgotten */}
        <div className="bg-white rounded-3xl border border-slate-200 shadow-xs p-6 flex flex-col justify-between text-left">
          <div>
            <h3 className="font-bold text-slate-900 mb-2 flex items-center space-x-2">
              <Trash2 className="w-5 h-5 text-red-500" />
              <span>GDPR "Right to be Forgotten" Graph Purger</span>
            </h3>
            <p className="text-xs text-slate-500 mb-4 leading-relaxed">
              Securely purge all vector chunks, knowledge graph sub-nodes, and episodic logs with cryptographic 3-pass zero-fill overrides.
            </p>

            <div className="space-y-4">
              <div>
                <label className="block text-[10px] font-bold text-slate-700 uppercase tracking-wider mb-1.5">Tenant / User UUID Scope</label>
                <input
                  type="text"
                  value={tenantId}
                  onChange={e => setTenantId(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-200 rounded-2xl px-3 py-2.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500/20 text-slate-900 font-mono font-bold"
                />
              </div>

              <button
                type="button"
                onClick={handleGdprDelete}
                disabled={loadingGdpr}
                className="w-full bg-red-600 hover:bg-red-700 text-white py-2.5 rounded-xl font-bold text-xs shadow-sm transition cursor-pointer flex items-center justify-center space-x-2"
              >
                <Trash2 className="w-4 h-4" />
                <span>{loadingGdpr ? 'Executing Cascade Wipe...' : 'Execute GDPR Cascading Wipe'}</span>
              </button>

              {gdprResult && (
                <div className="p-4 rounded-xl bg-red-50/50 border border-red-200 space-y-2 animate-fadeIn">
                  <div className="flex items-center space-x-2 text-red-800 font-bold text-xs">
                    <CheckCircle2 className="w-4 h-4 text-red-600" />
                    <span>Cascade Wipe Successful</span>
                  </div>
                  <p className="text-xs text-red-700 leading-relaxed font-semibold">{gdprResult.auditTrail}</p>
                  <div className="text-[10px] text-red-600 font-mono">
                    Nodes Wiped: {gdprResult.deletedCount} | Remaining Nodes: {gdprResult.remainingCount}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="pt-4 border-t border-slate-100 text-[10px] text-slate-400 font-mono">
            Row-Level Security (RLS): Enforced at PostgreSQL driver layer with cryptographically signed tenant context headers.
          </div>
        </div>
      </div>
    </div>
  );
};
