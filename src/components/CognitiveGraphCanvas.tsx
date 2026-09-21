import React, { useEffect, useRef, useState } from 'react';

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  label: string;
  category: string;
}

export const CognitiveGraphCanvas: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [activeNodeCount, setActiveNodeCount] = useState(45);
  const [lastRecallLabel, setLastRecallLabel] = useState("Vector Index Bootstrapped");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrameId: number;
    let width = canvas.width = containerRef.current?.clientWidth || 600;
    let height = canvas.height = 340;

    // Handle Resize
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (canvas) {
          width = canvas.width = entry.contentRect.width || 600;
          height = canvas.height = 340;
        }
      }
    });

    if (containerRef.current) {
      resizeObserver.observe(containerRef.current);
    }

    // Static memory words to simulate cognitive nodes
    const memoryKeywords = [
      "pgvector", "HNSW", "Knapsack", "0/1 Budget", "GDPR", "PII Redact", 
      "Sovereign", "Multi-Tenant", "JWT Auth", "SLA 99.99%", "Redis Cache",
      "Embedding", "Sparse search", "Jaccard", "Dense Vector", "Recall Rate",
      "GraphRAG", "Bayesian Revision", "Semantic Map", "Cognitive Proxy"
    ];

    // Generate random nodes
    const nodes: Node[] = Array.from({ length: 45 }).map(() => {
      const label = memoryKeywords[Math.floor(Math.random() * memoryKeywords.length)];
      return {
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        radius: Math.random() * 2.5 + 1.5,
        label,
        category: Math.random() > 0.5 ? "Technical" : "Compliance"
      };
    });

    // Mouse interaction tracking
    const mouse = { x: -1000, y: -1000, active: false };

    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = e.clientX - rect.left;
      mouse.y = e.clientY - rect.top;
      mouse.active = true;
    };

    const handleMouseLeave = () => {
      mouse.x = -1000;
      mouse.y = -1000;
      mouse.active = false;
    };

    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseleave', handleMouseLeave);

    // Dynamic label changes to simulate active memory queries
    const interval = setInterval(() => {
      const randomNode = nodes[Math.floor(Math.random() * nodes.length)];
      setLastRecallLabel(`Injected memory: [${randomNode.label}]`);
    }, 4000);

    // Core Animation Loop
    const render = () => {
      ctx.clearRect(0, 0, width, height);

      // Draw faint structural grid background
      ctx.strokeStyle = 'rgba(241, 245, 249, 0.5)';
      ctx.lineWidth = 0.5;
      const gridSize = 40;
      for (let x = 0; x < width; x += gridSize) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      for (let y = 0; y < height; y += gridSize) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }

      // 1. Particle positions updating with smooth force-fields
      nodes.forEach(node => {
        // Drift movement
        node.x += node.vx;
        node.y += node.vy;

        // Bounce boundaries
        if (node.x < 0 || node.x > width) node.vx *= -1;
        if (node.y < 0 || node.y > height) node.vy *= -1;

        // Interaction with mouse cursor (Force Field)
        if (mouse.active) {
          const dx = mouse.x - node.x;
          const dy = mouse.y - node.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const maxDistance = 110;

          if (dist < maxDistance) {
            // Smoothly pull particles closer to symbolize active synapse focus
            const force = (maxDistance - dist) / maxDistance;
            node.x += (dx / dist) * force * 1.5;
            node.y += (dy / dist) * force * 1.5;
          }
        }
      });

      // 2. Draw cognitive links between adjacent nodes
      ctx.lineWidth = 0.6;
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const maxLinkDist = 75;

          if (dist < maxLinkDist) {
            const alpha = (maxLinkDist - dist) / maxLinkDist;
            ctx.strokeStyle = `rgba(245, 158, 11, ${alpha * 0.25})`;
            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.stroke();
          }
        }
      }

      // 3. Draw mouse synapse links (glowing golden focus lines)
      if (mouse.active) {
        nodes.forEach(node => {
          const dx = mouse.x - node.x;
          const dy = mouse.y - node.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const maxSynapseDist = 100;

          if (dist < maxSynapseDist) {
            const alpha = (maxSynapseDist - dist) / maxSynapseDist;
            ctx.strokeStyle = `rgba(99, 102, 241, ${alpha * 0.4})`;
            ctx.lineWidth = 0.85;
            ctx.beginPath();
            ctx.moveTo(mouse.x, mouse.y);
            ctx.lineTo(node.x, node.y);
            ctx.stroke();
          }
        });

        // Draw mouse outer glowing locus ring
        ctx.strokeStyle = 'rgba(99, 102, 241, 0.15)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(mouse.x, mouse.y, 40, 0, Math.PI * 2);
        ctx.stroke();

        ctx.strokeStyle = 'rgba(245, 158, 11, 0.08)';
        ctx.beginPath();
        ctx.arc(mouse.x, mouse.y, 80, 0, Math.PI * 2);
        ctx.stroke();
      }

      // 4. Draw individual nodes & labels
      nodes.forEach(node => {
        // Core node circle
        ctx.fillStyle = node.category === "Technical" ? '#6366f1' : '#f59e0b';
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
        ctx.fill();

        // Node hover label highlighting
        if (mouse.active) {
          const dx = mouse.x - node.x;
          const dy = mouse.y - node.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 45) {
            ctx.fillStyle = 'rgba(15, 23, 42, 0.95)';
            ctx.font = 'bold 9px sans-serif';
            const textWidth = ctx.measureText(node.label).width;

            // Draw a subtle background capsule for the highlighted word
            ctx.beginPath();
            ctx.roundRect(node.x - textWidth / 2 - 4, node.y - 14, textWidth + 8, 11, 4);
            ctx.fill();

            // Draw label text
            ctx.fillStyle = '#ffffff';
            ctx.fillText(node.label, node.x - textWidth / 2, node.y - 5);
          }
        }
      });

      animationFrameId = requestAnimationFrame(render);
    };

    render();

    return () => {
      cancelAnimationFrame(animationFrameId);
      resizeObserver.disconnect();
      canvas.removeEventListener('mousemove', handleMouseMove);
      canvas.removeEventListener('mouseleave', handleMouseLeave);
      clearInterval(interval);
    };
  }, []);

  return (
    <div ref={containerRef} className="relative w-full h-[340px] bg-slate-50 border border-slate-200 rounded-3xl overflow-hidden flex flex-col justify-between">
      <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_bottom,rgba(245,158,11,0.04),transparent_60%)]"></div>
      
      {/* HUD Bar */}
      <div className="px-4 py-3 border-b border-slate-200/80 bg-white/75 backdrop-blur-md flex items-center justify-between text-xs font-mono relative z-10 pointer-events-none">
        <div className="flex items-center space-x-2">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
          <span className="text-slate-600 font-bold uppercase tracking-wide">ACTIVE TOPOLOGY VECTOR MAP</span>
        </div>
        <span className="text-slate-400">Latency: 0.12ms</span>
      </div>

      {/* Main Canvas */}
      <canvas ref={canvasRef} className="w-full flex-1 block cursor-crosshair bg-transparent" />

      {/* Synapse Realtime Feed */}
      <div className="px-4 py-2.5 bg-slate-900 border-t border-slate-800 text-amber-400 font-mono text-[10px] flex items-center justify-between relative z-10 pointer-events-none">
        <span className="truncate flex items-center">
          <span className="w-2 h-2 rounded-full bg-amber-500 animate-ping mr-1.5"></span>
          <span>SYSTEM GATEWAY RECALL: {lastRecallLabel}</span>
        </span>
        <span className="text-slate-500 shrink-0 select-none">Hover Cursor Over Canvas</span>
      </div>
    </div>
  );
};
