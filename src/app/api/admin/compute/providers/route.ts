import { NextResponse } from 'next/server';
import { getGpuProviders } from '@/lib/server/config';

export const dynamic = 'force-dynamic';

/** GET /api/admin/compute/providers → ComputeProvider[] */
export async function GET() {
  const providers = await getGpuProviders();
  return NextResponse.json(providers);
}
