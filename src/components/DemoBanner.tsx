import React from 'react';
import { Info } from 'lucide-react';

/**
 * Shown in place of a demonstration-only tab when the deployment has demo
 * routes disabled (ENABLE_DEMO_ROUTES=false, the production default). Keeps the
 * UI honest: these features are not backed by the real cognitive engine.
 */
export const DemoBanner: React.FC<{ feature?: string }> = ({ feature }) => (
  <div className="flex items-start space-x-3 p-4 rounded-xl bg-amber-50 border border-amber-200 text-amber-900">
    <Info className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
    <div className="text-sm">
      <p className="font-semibold">Demo feature unavailable in this deployment</p>
      <p className="text-amber-800 mt-0.5">
        {feature || 'This module'} is a demonstration surface not backed by the production cognitive
        engine. Enable it locally with <code className="bg-amber-100 px-1 rounded">ENABLE_DEMO_ROUTES=true</code>{' '}
        for development, or contact your administrator for the enterprise build.
      </p>
    </div>
  </div>
);
