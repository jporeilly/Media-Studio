import { Settings as SettingsIcon } from "lucide-react";
import { EmptyState, PageHeader } from "../components/ui";

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" subtitle="Studio and account settings." />
      <EmptyState
        icon={<SettingsIcon size={36} />}
        title="Coming soon"
        sub="Settings will appear here in a later milestone."
      />
    </>
  );
}
