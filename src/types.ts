export interface MemoryNode {
  id: string;
  category: 'preference' | 'project' | 'fact' | 'constraint' | 'episodic' | 'multimodal';
  content: string;
  confidence: number;
  source: string;
  timestamp: string;
  accessCount: number;
  status: 'active' | 'quarantined' | 'superseded';
  supersededBy?: string;
  tenantId?: string;
  modalType?: 'text' | 'image' | 'diagram' | 'git_diff';
  mediaUrl?: string;
}

export interface TelemetryLog {
  timestamp: string;
  query: string;
  provider: string;
  retrievedCount: number;
  latencyMs: number;
  tokenSavings: number;
}

export interface SystemStats {
  totalMemories: number;
  quarantinedCount: number;
  avgRetrievalLatencyMs: number;
  totalTokenSavings: number;
}

export interface GraphCommunity {
  communityId: string;
  theme: string;
  summary: string;
  nodeCount: number;
  cohesionScore: number;
}


