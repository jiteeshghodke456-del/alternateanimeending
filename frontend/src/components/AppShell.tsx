import { Suspense } from "react";
import { Link, Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { ArrowRight, BarChart3, ClipboardCheck, FileText, LayoutDashboard, ListChecks, Palette, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/Logo";
import { MeshBackground } from "@/components/MeshBackground";
import { ThemeToggle } from "@/components/ThemeToggle";
import { UserAvatar } from "@/components/UserAvatar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { BlurFade } from "@/components/ui/blur-fade";
import { HealthBanner } from "@/components/HealthBanner";
import { AccountMenu, workspaceLabel } from "@/components/AccountMenu";
import { BrandMark } from "@/components/BrandMark";
import { Skeleton } from "@/components/ui/skeleton";
import { ROLE_LABELS } from "@/lib/api";
import { reportError } from "@/lib/hooks";
import { useSession } from "@/lib/session";

export const NAV: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/brand", label: "Brand Kit", icon: Palette },
  { to: "/content", label: "Content", icon: FileText },
  { to: "/review", label: "Review", icon: ClipboardCheck },
  { to: "/claims", label: "Claims", icon: ListChecks }, // Round 3: claims ledger
  { to: "/eval", label: "Evaluation", icon: BarChart3 },
];

function RoleSwitcher() {
  const { user, users, login } = useSession();
  return (
    <Select
      value={user?.id ?? ""}
      onValueChange={async (id) => {
        try {
          const u = await login(id);
          toast.success(`Now signed in as ${u.name}, ${ROLE_LABELS[u.role]}`);
        } catch (e) { reportError(e); }
      }}
    >
      <SelectTrigger aria-label="Switch demo role" className="h-10 min-w-0 gap-2 rounded-full [&>span]:!flex [&>span]:!items-center border-[var(--glass-border)] bg-[var(--glass-bg)] pl-1.5 pr-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.35)] transition-colors duration-200 hover:bg-foreground/5 sm:min-w-[190px]">
        {user ? (
          <span className="flex min-w-0 items-center gap-2">
            <UserAvatar id={user.id} name={user.name} role={user.role} className="size-7 text-footnote" />
            <span className="truncate text-footnote font-medium"><SelectValue>{user.name} <span className="hidden font-normal text-muted-foreground sm:inline">· {ROLE_LABELS[user.role]}</span></SelectValue></span>
          </span>
        ) : (
          <SelectValue placeholder="Choose a role" />
        )}
      </SelectTrigger>
      <SelectContent className="rounded-2xl border-[var(--glass-border)] bg-[var(--glass-strong-bg)] backdrop-blur-2xl">
        {users.map((u) => (
          <SelectItem key={u.id} value={u.id} className="rounded-xl py-2">
            <span className="flex items-center gap-2">
              <UserAvatar id={u.id} name={u.name} role={u.role} className="size-6 text-caption" />
              {u.name} <span className="text-muted-foreground">· {ROLE_LABELS[u.role]}</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function TopBar() {
  const { user, backendOnline, brand } = useSession();
  return (
    <header className="sticky top-0 z-30 px-4 pt-3 md:px-8 md:pt-4">
      <div className="glass-strong glass-chrome mx-auto flex max-w-[1200px] items-center justify-between gap-2 !rounded-full px-2 py-2 sm:gap-3 md:px-4">
        <Link to="/dashboard" className="flex min-w-0 items-center gap-2 rounded-full md:hidden" aria-label={`${brand?.name ?? "Rulemint"}, dashboard`}>
          <Logo showWord={false} />
          {brand?.name && <span className="truncate text-footnote font-semibold">{brand.name}</span>}
        </Link>
        <div className="hidden min-w-0 md:block">
          <p className="truncate text-footnote text-muted-foreground">
            {brand?.name && <><span className="font-semibold text-foreground">{brand.name}</span><span aria-hidden className="mx-2 text-border">|</span></>}
            {user ? <>Signed in as <span className="font-medium text-foreground">{user.name}</span>, {ROLE_LABELS[user.role]}</> : "Not signed in"}
            {backendOnline === false && <span className="ml-2 rounded-full bg-human/15 px-2 py-0.5 text-caption text-human-text">Can't reach the server</span>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1 sm:gap-1.5">
          <RoleSwitcher />
          <AccountMenu />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

function BrandCard() {
  const { brand, workspaceKind, user } = useSession();
  if (!brand) return <div className="mt-auto"><Skeleton className="h-16 rounded-2xl" /></div>;
  return (
    <div className="mt-auto rounded-2xl bg-foreground/5 p-3 shadow-[inset_0_0_0_1px_hsl(var(--foreground)/0.05)]">
      <div className="flex items-center gap-2.5">
        <BrandMark brand={brand} />
        <div className="min-w-0">
          <p className="truncate text-footnote font-semibold" title={brand.name}>{brand.name}</p>
          <p className="text-caption text-muted-foreground">{brand.demo ? "Demo brand · sample data" : workspaceLabel(workspaceKind)}</p>
        </div>
      </div>
      {brand.is_blank_setup && user?.role === "admin" && (
        <Link to="/setup" className="mt-2.5 flex items-center justify-between rounded-xl bg-[var(--accent-fill)] px-3 py-2 text-caption font-semibold text-white transition-colors hover:bg-[var(--accent-fill-hover)]">
          Finish setup <ArrowRight className="size-3.5" strokeWidth={2} aria-hidden />
        </Link>
      )}
    </div>
  );
}

function Sidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-20 hidden w-[248px] p-4 md:block">
      <nav className="glass-strong flex h-full flex-col gap-1 p-3" aria-label="Main">
        <NavLink to="/" className="mb-4 mt-2 px-3 py-1"><Logo /></NavLink>
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-2xl px-3 py-2.5 text-body font-medium transition-[background-color,color,transform] duration-200 ease-out active:scale-[0.98]",
                isActive ? "btn-gloss bg-[var(--accent-fill)] text-white" : "text-foreground/80 hover:bg-foreground/6",
              )
            }
          >
            <Icon className="size-[18px]" strokeWidth={1.5} />
            {label}
          </NavLink>
        ))}
        <BrandCard />
      </nav>
    </aside>
  );
}

function BottomTabs() {
  return (
    <nav aria-label="Main" className="fixed inset-x-3 bottom-3 z-30 md:hidden pb-[env(safe-area-inset-bottom)]">
      <div className="glass-strong glass-chrome flex items-stretch justify-around !rounded-[28px] p-1.5">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex min-w-0 flex-1 flex-col items-center gap-0.5 rounded-2xl px-1 py-1.5 text-[11px] font-medium leading-tight transition-[background-color,transform] duration-200 active:scale-[0.96]",
                isActive ? "btn-gloss bg-[var(--accent-fill)] text-white" : "text-muted-foreground",
              )
            }
          >
            <Icon className="size-5" strokeWidth={1.5} />
            <span className="truncate">{label === "Evaluation" ? "Eval" : label === "Brand Kit" ? "Brand" : label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}

function PageSkeleton() {
  return <div className="space-y-4" aria-busy="true" aria-label="Loading"><Skeleton className="h-10 w-64" /><Skeleton className="h-40" /></div>;
}

export function AppShell() {
  const { pathname } = useLocation();
  const { user, ready } = useSession();
  if (ready && !user) return <Navigate to="/" replace state={{ from: pathname }} />;
  return (
    <div className="min-h-dvh">
      <MeshBackground />
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-[var(--accent-fill)] focus:px-4 focus:py-2 focus:text-white">Skip to content</a>
      <Sidebar />
      <div className="md:pl-[248px]">
        <TopBar />
        <main id="main" className="mx-auto max-w-[1200px] px-4 pb-32 pt-6 md:px-8 md:pb-16 md:pt-8">
          <HealthBanner />
          {!ready ? (
            <PageSkeleton />
          ) : (
            <Suspense fallback={<PageSkeleton />}>
              <BlurFade key={`${pathname}:${user?.id}`} duration={0.3} offset={8}>
                <Outlet />
              </BlurFade>
            </Suspense>
          )}
        </main>
      </div>
      <BottomTabs />
    </div>
  );
}
