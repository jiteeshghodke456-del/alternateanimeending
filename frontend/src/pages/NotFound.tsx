import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return <EmptyState icon={Compass} title="We could not find that page" description="The link may be old. Head back to the dashboard." action={<Button asChild className="rounded-full"><Link to="/dashboard">Go to dashboard</Link></Button>} />;
}
