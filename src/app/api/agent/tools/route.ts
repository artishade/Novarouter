/** GET /api/agent/tools — the agent's tool catalogue (per API contract section 3). */
import { NextResponse } from 'next/server';

const TOOLS = [
  { id: 'web_search', name: 'Web Search', description: 'Search the live web for up-to-date information', icon: 'Globe' },
  { id: 'read_url', name: 'Page Reader', description: 'Read and extract any web page', icon: 'BookOpen' },
  { id: 'terminal', name: 'Sandbox Terminal', description: 'Run safe diagnostics in the gateway sandbox', icon: 'TerminalSquare' },
  { id: 'gateway_stats', name: 'Gateway Telemetry', description: 'Inspect live gateway stats, models and routes', icon: 'Activity' },
  { id: 'storage_scan', name: 'Storage Scanner', description: 'Scan configured storage providers and files', icon: 'HardDrive' },
];

export async function GET() {
  return NextResponse.json(TOOLS);
}
