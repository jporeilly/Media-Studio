import { FileText } from "lucide-react";
import { EmptyState, PageHeader } from "../components/ui";

export default function DocsPage() {
  return (
    <>
      <PageHeader title="Documents" subtitle="In-app documentation for Media Studio Enterprise." />
      <EmptyState
        icon={<FileText size={36} />}
        title="Coming soon"
        sub="Documentation will appear here once the backend docs API is wired up."
      />
    </>
  );
}
