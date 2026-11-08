import {notFound} from 'next/navigation';
import WorkspaceApp from '../../components/WorkspaceApp';
export const dynamic = 'force-dynamic';
export default async function StudioPage({params}:{params:Promise<{view:string}>}) {
  const {view} = await params;
  if (view !== 'bots' && view !== 'commands' && view !== 'files' && view !== 'hosting' && view !== 'templates' && view !== 'notifications' && view !== 'team' && view !== 'analytics' && view !== 'settings') notFound();
  return <WorkspaceApp backendConfigured={Boolean(process.env.VIDEL_BACKEND_ORIGIN)} initialView={view}/>;
}
