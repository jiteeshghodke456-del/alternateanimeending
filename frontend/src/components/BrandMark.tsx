import { withToken, type BrandOut } from "@/lib/api";
import { bestText, normalizeHex } from "@/lib/color";
import { cn } from "@/lib/utils";

/** The workspace brand's logo, or its initials on its first palette colour. Never hard-coded to one brand. */
export function BrandMark({ brand, className }: { brand: BrandOut | null; className?: string }) {
  const logo = brand?.logo_url ?? brand?.logos?.find((l) => l.variant === "approved")?.url ?? null;
  const name = brand?.name?.trim() || "";
  if (logo) {
    return (
      <span className={cn("grid size-9 shrink-0 place-items-center overflow-hidden rounded-[10px] border border-border bg-white", className)}>
        <img src={withToken(logo)} alt="" className="size-full object-contain p-0.5" />
      </span>
    );
  }
  const bg = normalizeHex(brand?.palette?.[0]?.hex ?? "") ?? "#6e6e73";
  const initials = name ? name.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") : "·";
  return (
    <span
      aria-hidden
      className={cn("grid size-9 shrink-0 place-items-center rounded-[10px] text-footnote font-semibold tracking-tight", className)}
      style={{ background: bg, color: bestText(bg).color }}
    >
      {initials}
    </span>
  );
}
