import React, { useState } from 'react';
import { Code, Copy, Check, Terminal, Globe, Key, ShieldCheck } from 'lucide-react';

export const DeveloperDocs: React.FC = () => {
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  const codeSnippets = [
    {
      title: "1. OpenAI-Compatible Proxy (Zero Code Migration)",
      language: "typescript",
      code: `import OpenAI from 'openai';

// Simply point the OpenAI client base URL to SynapseMemory Gateway
const openai = new OpenAI({
  apiKey: 'syn_live_9823471092',
  baseURL: 'https://api.synapsememory.com/v1' // Automatically injects RAG memory
});

const response = await openai.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'What styling setup do I prefer?' }]
});

console.log(response.choices[0].message.content);`
    },
    {
      title: "2. TypeScript SDK Integration (`@synapse/memory-sdk`)",
      language: "typescript",
      code: `import { SynapseMemory } from '@synapse/memory-sdk';

const memory = new SynapseMemory({ apiKey: process.env.SYNAPSE_API_KEY });

// Retrieve memory context before calling Claude or Gemini
const context = await memory.retrieve({
  userId: 'user_9921',
  query: 'How should I structure my backend API routes?'
});

// Pass context to your LLM of choice
const promptWithMemory = \`\${context}\\n\\nUser: How should I structure my backend API routes?\`;
const llmResponse = await callClaude(promptWithMemory);

// Asynchronously feed back interaction for continuous learning
await memory.recordInteraction({
  userId: 'user_9921',
  prompt: 'How should I structure my backend API routes?',
  response: llmResponse
});`
    },
    {
      title: "3. Python SDK Integration (`synapse-memory`)",
      language: "python",
      code: `from synapse_memory import SynapseClient

client = SynapseClient(api_key="syn_live_9823471092")

# Retrieve context for user
context = client.retrieve(
    user_id="user_9921",
    query="Remember my database configuration"
)

# Inject into prompt
enriched_prompt = f"{context}\\n\\nUser: Remember my database configuration"
response = gemini_client.generate_content(enriched_prompt)
print(response.text)`
    }
  ];

  const handleCopy = (code: string, idx: number) => {
    navigator.clipboard.writeText(code);
    setCopiedIndex(idx);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
        <div className="flex items-center space-x-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-indigo-50 text-indigo-600 flex items-center justify-center font-bold">
            <Globe className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-slate-900">Global Developer SDK & API Documentation</h2>
            <p className="text-xs text-slate-500">Integrate SynapseMemory into your apps worldwide with drop-in proxy endpoints or native SDKs.</p>
          </div>
        </div>
      </div>

      <div className="space-y-6">
        {codeSnippets.map((snippet, idx) => (
          <div key={idx} className="bg-slate-900 rounded-2xl border border-slate-800 overflow-hidden shadow-lg">
            <div className="px-6 py-4 bg-slate-800/80 border-b border-slate-700/60 flex items-center justify-between">
              <span className="text-sm font-semibold text-white flex items-center space-x-2">
                <Terminal className="w-4 h-4 text-indigo-400" />
                <span>{snippet.title}</span>
              </span>
              <button
                onClick={() => handleCopy(snippet.code, idx)}
                className="text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1.5 rounded-lg transition flex items-center space-x-1.5 cursor-pointer"
              >
                {copiedIndex === idx ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    <span className="text-emerald-400 font-medium">Copied!</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    <span>Copy Snippet</span>
                  </>
                )}
              </button>
            </div>
            <div className="p-6 overflow-x-auto">
              <pre className="text-xs font-mono text-indigo-200 leading-relaxed">
                <code>{snippet.code}</code>
              </pre>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
