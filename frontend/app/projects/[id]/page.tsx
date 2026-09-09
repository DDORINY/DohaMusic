import { AppShell } from "@/components/app-shell";
import { ProjectDetail } from "@/features/projects/project-detail";
export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) { const { id } = await params; return <AppShell variant="daw"><ProjectDetail projectId={id} /></AppShell>; }
