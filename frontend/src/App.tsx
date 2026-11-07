import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/lib/theme";
import { SessionProvider } from "@/lib/session";
import { AppShell } from "@/components/AppShell";
import Landing from "@/pages/Landing";
import Dashboard from "@/pages/Dashboard";
import BrandKit from "@/pages/BrandKit";
import Content from "@/pages/Content";
import ContentNew from "@/pages/ContentNew";
import Item from "@/pages/Item";
import Review from "@/pages/Review";
import Eval from "@/pages/Eval";
import NotFound from "@/pages/NotFound";

// dev only: tree-shaken out of production builds
const Gallery = import.meta.env.DEV ? lazy(() => import("@/pages/Gallery")) : null;

export default function App() {
  return (
    <ThemeProvider>
      <SessionProvider>
        <TooltipProvider delayDuration={150}>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Landing />} />
              <Route element={<AppShell />}>
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/brand" element={<BrandKit />} />
                <Route path="/content" element={<Content />} />
                <Route path="/content/new" element={<ContentNew />} />
                <Route path="/items/:id" element={<Item />} />
                <Route path="/review" element={<Review />} />
                <Route path="/eval" element={<Eval />} />
                {Gallery && <Route path="/dev/gallery" element={<Suspense fallback={null}><Gallery /></Suspense>} />}
                <Route path="*" element={<NotFound />} />
              </Route>
            </Routes>
          </BrowserRouter>
          <Toaster position="top-center" />
        </TooltipProvider>
      </SessionProvider>
    </ThemeProvider>
  );
}
