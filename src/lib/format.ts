/** Formatting helpers shared across the dashboard. */

export function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (Math.abs(n) >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return String(Math.round(n));
}

export function fmtBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function fmtMb(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
}

export function fmtUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const hh = Math.floor((seconds % 86400) / 3600);
  const mm = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${hh}h ${mm}m`;
  if (hh > 0) return `${hh}h ${mm}m`;
  return `${mm}m`;
}

export function timeAgo(ts: number | null | undefined): string {
  if (!ts) return 'never';
  const diff = Date.now() - ts;
  if (diff < 0) return 'just now';
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const hh = Math.floor(m / 60);
  if (hh < 24) return `${hh}h ago`;
  const d = Math.floor(hh / 24);
  return `${d}d ago`;
}

export function fmtClock(ts: number | null | undefined): string {
  if (!ts) return '—';
  return new Date(ts).toLocaleTimeString('en-US', { hour12: false });
}

export function fmtDate(ts: number | null | undefined): string {
  if (!ts) return '—';
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function maskKey(key: string): string {
  if (key.length <= 12) return key.slice(0, 4) + '••••';
  return key.slice(0, 8) + '••••••••' + key.slice(-4);
}

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}
