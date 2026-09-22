export interface MemoryNode {
  id: string;
  category: string;
  content: string;
  confidence: number;
  source: string;
  timestamp: string;
  accessCount: number;
  status: 'active' | 'quarantined' | 'archived';
  tenantId?: string;
  vector?: number[];
}

export const INITIAL_DEMO_MEMORIES: MemoryNode[] = [
  {
    id: "mem_101",
    category: "preference",
    content: "User prefers clean light mode UI with indigo (#4f46e5) accent buttons and high-contrast typography.",
    confidence: 0.98,
    source: "Explicit System Configuration",
    timestamp: new Date(Date.now() - 86400000 * 3).toISOString(),
    accessCount: 14,
    status: 'active',
    tenantId: 'default'
  },
  {
    id: "mem_102",
    category: "architecture",
    content: "Application architecture uses Python FastAPI backend with PostgreSQL + pgvector and Express gateway.",
    confidence: 0.95,
    source: "System Telemetry Scan",
    timestamp: new Date(Date.now() - 86400000 * 2).toISOString(),
    accessCount: 9,
    status: 'active',
    tenantId: 'default'
  },
  {
    id: "mem_103",
    category: "constraint",
    content: "Database authentication and scope RBAC must be enforced on all memory endpoints with tenant isolation.",
    confidence: 0.99,
    source: "Security Policy Enforcement",
    timestamp: new Date(Date.now() - 86400000 * 1).toISOString(),
    accessCount: 22,
    status: 'active',
    tenantId: 'default'
  }
];
