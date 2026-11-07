import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";

const GRADS: Record<string, string> = {
  asha: "from-[#5E5CE6] to-[#8E7BFF]",
  rohan: "from-[#0A84FF] to-[#64B5FF]",
  meera: "from-[#BF5AF2] to-[#E58BFF]",
  kabir: "from-[#FF7A00] to-[#FFB340]",
};

/** Initial-letter avatar with a per-person gradient. White on these gradients is bold enough (large text). */
export function UserAvatar({ id, name, className }: { id: string; name: string; className?: string }) {
  return (
    <Avatar className={cn("size-9", className)}>
      <AvatarFallback className={cn("bg-gradient-to-br font-semibold text-white [text-shadow:0_1px_2px_rgba(0,0,0,0.35)]", GRADS[id] ?? "from-neutral-500 to-neutral-400")}>
        {name.charAt(0)}
      </AvatarFallback>
    </Avatar>
  );
}
