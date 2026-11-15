import WorkspaceApp from '../components/WorkspaceApp';
export const dynamic = 'force-dynamic';
export default function Home(){return <WorkspaceApp backendConfigured={Boolean(process.env.VIDEL_BACKEND_ORIGIN)}/>}
