import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { Role } from "@/lib/api";
import { cn } from "@/lib/utils";

const GRADS = [
  "from-[#5E5CE6] to-[#8E7BFF]",
  "from-[#0A84FF] to-[#64B5FF]",
  "from-[#BF5AF2] to-[#E58BFF]",
  "from-[#FF7A00] to-[#FFB340]",
];
const BY_ROLE: Record<Role, string> = { admin: GRADS[0], creator: GRADS[1], reviewer: GRADS[2], approver: GRADS[3] };

function hashed(id: string): string {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) | 0;
  return GRADS[Math.abs(h) % GRADS.length];
}

/** Initial-letter avatar; colour follows the role (or a hash of the id). White on these gradients is bold enough (large text). */
export function UserAvatar({ id, name, role, className }: { id: string; name: string; role?: Role; className?: string }) {
  return (
    <Avatar className={cn("size-9", className)}>
      <AvatarFallback className={cn("bg-gradient-to-br font-semibold text-white [text-shadow:0_1px_2px_rgba(0,0,0,0.35)]", role ? BY_ROLE[role] : hashed(id))}>
        {name.charAt(0)}
      </AvatarFallback>
    </Avatar>
  );
}
