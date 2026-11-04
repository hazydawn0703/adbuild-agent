import {cookies} from 'next/headers';
import {redirect} from 'next/navigation';
import UsageConsole from '../UsageConsole';

export const dynamic = 'force-dynamic';

export default async function OpsPage() {
  const token = (await cookies()).get('adbuild-ops-session')?.value;
  if (!token) redirect('/ops/login');
  const response = await fetch(`http://127.0.0.1:${process.env.NODE_ENV === 'production' ? '18791' : '8000'}/ops/session`, {
    headers: {Cookie: `adbuild-ops-session=${token}`}, cache: 'no-store',
  });
  if (!response.ok) redirect('/ops/login');
  return <UsageConsole/>;
}
