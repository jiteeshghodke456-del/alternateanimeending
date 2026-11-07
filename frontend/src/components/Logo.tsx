import { cn } from "@/lib/utils";

/** Rulemint logo. With the word: the full wordmark (a light- and a dark-background cut, swapped by theme in
 *  styles/index.css). Without it: the "R" mark alone, for tight spots like the mobile top bar. */
export function Logo({ className, showWord = true }: { className?: string; showWord?: boolean }) {
  if (!showWord) {
    return (
      <span className={cn("inline-flex items-center", className)}>
        <img src="/brand/rulemint-mark-64.png" alt="Rulemint" width={32} height={32} className="size-8 rounded-[10px]" />
      </span>
    );
  }
  return (
    <span className={cn("inline-flex items-center", className)} role="img" aria-label="Rulemint">
      <img src="/brand/rulemint-wordmark-on-light.png" alt="" width={112} height={28} className="logo-on-light h-7 w-auto" />
      <img src="/brand/rulemint-wordmark-on-dark.png" alt="" width={112} height={28} className="logo-on-dark h-7 w-auto" />
    </span>
  );
}
