import { NavLink, Outlet, useLocation } from "react-router-dom";
import { BarChart3, ClipboardCheck, FileText, LayoutDashboard, Palette, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/Logo";
import { MeshBackground } from "@/components/MeshBackground";
import { ThemeToggle } from "@/components/ThemeToggle";
import { UserAvatar } from "@/components/UserAvatar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { BlurFade } from "@/components/ui/blur-fade";
import { DEMO_USERS } from "@/lib/mock";
import { useSession } from "@/lib/session";

export const NAV: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/brand", label: "Brand Kit", icon: Palette },
  { to: "/content", label: "Content", icon: FileText },
  { to: "/review", label: "Review", icon: ClipboardCheck },
  { to: "/eval", label: "Evaluation", icon: BarChart3 },
];

function RoleSwitcher() {
  const { user, login } = useSession();
  return (
    <Select
      value={user?.id ?? ""}
      onValueChange={async (id) => {
        const u = await login(id);
        toast.success(`Now signed in as ${u.name}, ${u.role}`);
      }}
    >
      <SelectTrigger aria-label="Switch demo role" className="h-10 min-w-0 gap-2 rounded-full border-[var(--glass-border)] bg-[var(--glass-bg)] pl-1.5 pr-3 backdrop-blur-xl sm:min-w-[190px]">
        {user ? (
          <span className="flex min-w-0 items-center gap-2">
            <UserAvatar id={user.id} name={user.name} className="size-7 text-footnote" />
            <span className="truncate text-footnote font-medium"><SelectValue /></span>
          </span>
        ) : (
          <SelectValue placeholder="Choose a role" />
        )}
      </SelectTrigger>
      <SelectContent className="rounded-2xl border-[var(--glass-border)] bg-[var(--glass-strong-bg)] backdrop-blur-2xl">
        {DEMO_USERS.map((u) => (
          <SelectItem key={u.id} value={u.id} className="rounded-xl py-2">
            <span className="flex items-center gap-2">
              <UserAvatar id={u.id} name={u.name} className="size-6 text-caption" />
              {u.name} <span className="text-muted-foreground">· {u.role}</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function TopBar() {
  const { user, backendOnline } = useSession();
  return (
    <header className="sticky top-0 z-30 px-4 pt-3 md:px-8 md:pt-4">
      <div className="glass-strong mx-auto flex max-w-[1200px] items-center justify-between gap-3 !rounded-full px-3 py-2 md:px-4">
        <div className="flex items-center gap-3 md:hidden"><Logo /></div>
        <div className="hidden min-w-0 md:block">
          <p className="truncate text-footnote text-muted-foreground">
            {user ? <>Signed in as <span className="font-medium text-foreground">{user.name}</span>, {user.role}</> : "Not signed in"}
            {backendOnline === false && <span className="ml-2 rounded-full bg-human/15 px-2 py-0.5 text-caption text-human-text">Backend offline, showing sample data</span>}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <RoleSwitcher />
          <ThemeToggle />
        </div>
      </div>
    </header>
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
                "flex items-center gap-3 rounded-2xl px-3 py-2.5 text-body font-medium transition-colors",
                isActive ? "bg-[#0A84FF] text-white shadow-[0_6px_18px_rgba(10,132,255,0.35)]" : "text-foreground/80 hover:bg-foreground/6",
              )
            }
          >
            <Icon className="size-[18px]" strokeWidth={1.5} />
            {label}
          </NavLink>
        ))}
        <div className="mt-auto rounded-2xl bg-foreground/5 p-3">
          <p className="text-caption font-medium">Fictional demo brand</p>
          <p className="text-caption text-muted-foreground">Saffron &amp; Steam Coffee Co.</p>
        </div>
      </nav>
    </aside>
  );
}

function BottomTabs() {
  return (
    <nav aria-label="Main" className="fixed inset-x-3 bottom-3 z-30 md:hidden pb-[env(safe-area-inset-bottom)]">
      <div className="glass-strong flex items-stretch justify-around !rounded-[28px] p-1.5">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex min-w-0 flex-1 flex-col items-center gap-0.5 rounded-2xl px-1 py-1.5 text-[11px] font-medium leading-tight",
                isActive ? "bg-[#0A84FF] text-white" : "text-muted-foreground",
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

export function AppShell() {
  const { pathname } = useLocation();
  return (
    <div className="min-h-dvh">
      <MeshBackground />
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-[#0A84FF] focus:px-4 focus:py-2 focus:text-white">Skip to content</a>
      <Sidebar />
      <div className="md:pl-[248px]">
        <TopBar />
        <main id="main" className="mx-auto max-w-[1200px] px-4 pb-32 pt-6 md:px-8 md:pb-16 md:pt-8">
          <BlurFade key={pathname} duration={0.3} offset={8}>
            <Outlet />
          </BlurFade>
        </main>
      </div>
      <BottomTabs />
    </div>
  );
}
