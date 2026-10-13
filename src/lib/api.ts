// Shared API client for the Synapse-Memory dashboard.
//
// Responsibilities:
//  1. Inject auth headers (X-Synapse-API-Key) + tenant (X-Tenant-ID) on every
//     request, read from localStorage so the dashboard works against a secured
//     engine with zero per-component wiring.
//  2. Transparently unwrap the canonical backend response envelope:
//       { success: boolean, data: <payload>, request_id }
//     while remaining backward compatible with legacy flat responses.
//
// Every component should call apiGet/apiPost instead of fetch() directly.

const API_KEY_STORAGE = 'synapse_api_key';
const TENANT_STORAGE = 'synapse_tenant_id';

export function getApiKey(): string {
  try {
    return localStorage.getItem(API_KEY_STORAGE) || '';
  } catch {
    return '';
  }
}

export function setApiKey(key: string): void {
  try {
    localStorage.setItem(API_KEY_STORAGE, key);
  } catch {
    /* ignore storage errors (SSR/private mode) */
  }
}

export function getTenant(): string {
  try {
    return localStorage.getItem(TENANT_STORAGE) || '';
  } catch {
    return '';
  }
}

export function setTenant(tenant: string): void {
  try {
    localStorage.setItem(TENANT_STORAGE, tenant);
  } catch {
    /* ignore */
  }
}

function buildHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...extra };
  const key = getApiKey();
  if (key) headers['X-Synapse-API-Key'] = key;
  const tenant = getTenant();
  if (tenant) headers['X-Tenant-ID'] = tenant;
  return headers;
}

/**
 * Unwrap the canonical envelope: if the body is { success, data, request_id },
 * return `.data` (or throw with the error detail). Otherwise return the body
 * as-is for legacy/flat responses and Node-gateway responses.
 */
export function unwrap(body: any): any {
  if (body && typeof body === 'object' && 'success' in body && 'data' in body) {
    if (body.success === false) {
      const detail = body.error ?? body.detail ?? 'Request failed';
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return body.data;
  }
  if (body && typeof body === 'object' && body.success === false) {
    const detail = body.error ?? body.detail ?? 'Request failed';
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return body;
}

export async function apiGet(path: string): Promise<any> {
  const res = await fetch(path, { headers: buildHeaders() });
  const body = await res.json().catch(() => ({}));
  return unwrap(body);
}

export async function apiPost(path: string, payload?: any): Promise<any> {
  const res = await fetch(path, {
    method: 'POST',
    headers: buildHeaders(),
    body: payload !== undefined ? JSON.stringify(payload) : undefined,
  });
  const body = await res.json().catch(() => ({}));
  return unwrap(body);
}

export async function apiDelete(path: string): Promise<any> {
  const res = await fetch(path, { method: 'DELETE', headers: buildHeaders() });
  const body = await res.json().catch(() => ({}));
  return unwrap(body);
}

/**
 * Fetch a DEMO-only endpoint. When the deployment gates demo routes off
 * (ENABLE_DEMO_ROUTES=false, the production default), these return HTTP 404
 * with {simulated: true}. Instead of throwing a cryptic error, this returns
 * { simulated: true } so components can render a clear "not available in this
 * deployment" notice rather than silently failing.
 */
export async function apiGetDemo(path: string): Promise<{ simulated: true } | any> {
  let res: Response;
  try {
    res = await fetch(path, { headers: buildHeaders() });
  } catch {
    return { simulated: true };
  }
  const body = await res.json().catch(() => ({}));
  if (res.status === 404 && body && body.simulated) {
    return { simulated: true };
  }
  return unwrap(body);
}

export async function apiPostDemo(path: string, payload?: any): Promise<{ simulated: true } | any> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: 'POST',
      headers: buildHeaders(),
      body: payload !== undefined ? JSON.stringify(payload) : undefined,
    });
  } catch {
    return { simulated: true };
  }
  const body = await res.json().catch(() => ({}));
  if (res.status === 404 && body && body.simulated) {
    return { simulated: true };
  }
  return unwrap(body);
}

/** True when a demo-endpoint response indicates the feature is disabled. */
export function isSimulated(result: any): boolean {
  return Boolean(result && typeof result === 'object' && result.simulated === true);
}
