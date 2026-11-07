import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/lib/theme";
import { SessionProvider } from "@/lib/session";
import Landing from "@/pages/Landing";
import { StartingScreen } from "@/components/StartingScreen";

// The app shell (sidebar, menus, dialogs) loads after the landing page.
const AppShell = lazy(() => import("@/components/AppShell").then((m) => ({ default: m.AppShell })));
// Route-level code splitting: each page (and heavy deps like recharts) loads on first visit.
const Dashboard = lazy(() => import("@/pages/Dashboard"));
const BrandKit = lazy(() => import("@/pages/BrandKit"));
const Setup = lazy(() => import("@/pages/Setup"));
const Content = lazy(() => import("@/pages/Content"));
const ContentNew = lazy(() => import("@/pages/ContentNew"));
const Item = lazy(() => import("@/pages/Item"));
const Review = lazy(() => import("@/pages/Review"));
const Eval = lazy(() => import("@/pages/Eval"));
const Claims = lazy(() => import("@/pages/Claims")); // Round 3: claims ledger
const NotFound = lazy(() => import("@/pages/NotFound"));

export default function App() {
  return (
    <ThemeProvider>
      <SessionProvider>
        <TooltipProvider delayDuration={150}>
          <BrowserRouter>
            <Suspense fallback={null}>
              <Routes>
                <Route path="/" element={<Landing />} />
                <Route element={<AppShell />}>
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/brand" element={<BrandKit />} />
                  <Route path="/setup" element={<Setup />} />
                  <Route path="/content" element={<Content />} />
                  <Route path="/content/new" element={<ContentNew />} />
                  <Route path="/items/:id" element={<Item />} />
                  <Route path="/review" element={<Review />} />
                  <Route path="/eval" element={<Eval />} />
                  <Route path="/claims" element={<Claims />} />
                  <Route path="*" element={<NotFound />} />
                </Route>
              </Routes>
            </Suspense>
          </BrowserRouter>
          <StartingScreen />
          <Toaster position="top-center" />
        </TooltipProvider>
      </SessionProvider>
    </ThemeProvider>
  );
}
