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

export interface MemFSFile {
  path: string;
  content: string;
  lastCommitHash: string;
  lastCommitMsg: string;
  updatedAt: string;
}

export interface DreamSession {
  sessionId: string;
  timestamp: string;
  episodicAnalyzedCount: number;
  consolidatedFactsGenerated: string[];
  tokensSaved: number;
}

export interface TemporalObservation {
  id: string;
  timestamp: string;
  attribute: string;
  oldValue: string;
  newValue: string;
  reason: string;
  confidence: number;
}

export interface DistributedJob {
  jobId: string;
  workerName: string;
  taskType: 'dream_consolidation' | 'hnsw_reindex' | 'zkp_generation' | 'pii_scrub';
  status: 'queued' | 'active' | 'completed' | 'failed';
  progress: number;
  payloadSize: number;
  createdAt: string;
}

export interface HNSWIndexMetric {
  m: number; // Max connections per node
  efConstruction: number; // Size of the dynamic candidate list for index construction
  recallRate: number;
  queryLatencyMs: number;
  totalIndexNodes: number;
}

export interface CommercialLicense {
  licenseId: string;
  licensedTo: string;
  purchaseDate: string;
  licenseKey: string;
  status: 'active' | 'pending';
  ipAssignmentSigned: boolean;
  slaTier: 'Enterprise 99.99%';
}





