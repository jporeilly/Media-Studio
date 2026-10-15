import { Suspense, lazy, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import { Shell } from "./layout/Shell";
import { Spinner } from "./components/ui";
import LoginPage from "./pages/Login";
import ChangePasswordPage from "./pages/ChangePassword";
import DashboardPage from "./pages/Dashboard";

/* Login and the Dashboard ship in the main bundle; the placeholder pages are route-level chunks fetched on first visit. */
const ProjectsPage = lazy(() => import("./pages/Projects"));
const ProjectDetailPage = lazy(() => import("./pages/ProjectDetail"));
const DocsPage = lazy(() => import("./pages/Docs"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const AnnotatePage = lazy(() => import("./pages/Annotate"));
const WalkthroughsPage = lazy(() => import("./pages/Walkthroughs"));
const WalkthroughBuilderPage = lazy(() => import("./pages/WalkthroughBuilder"));
const WalkthroughPlayPage = lazy(() => import("./pages/WalkthroughPlay"));

function RequireAuth({ children }: { children: React.ReactElement }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="os-login"><Spinner label="Loading Media Studio..." /></div>;
  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  // A new or reset account sets its own password before it sees the studio.
  if (user.must_change_password) return <ChangePasswordPage />;
  return children;
}

/** Keeps the shell on screen and shows the shared spinner in the content area while a page chunk loads. */
function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Spinner />}>{children}</Suspense>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* The walkthrough player (W1): full window, OUTSIDE the Shell, signed in like everything else. */}
      <Route path="/walkthroughs/:id/play" element={<RequireAuth><Lazy><WalkthroughPlayPage /></Lazy></RequireAuth>} />
      <Route element={<RequireAuth><Shell /></RequireAuth>}>
        <Route index element={<DashboardPage />} />
        <Route path="/projects" element={<Lazy><ProjectsPage /></Lazy>} />
        <Route path="/projects/:id" element={<Lazy><ProjectDetailPage /></Lazy>} />
        {/* Annotate a still (T3b): pure web, so not behind the capture hide rule - the browser edition too. */}
        <Route path="/captures/:id/annotate" element={<Lazy><AnnotatePage /></Lazy>} />
        {/* Walkthroughs (W1): the list and the builder; pure web too, the browser edition has them. */}
        <Route path="/walkthroughs" element={<Lazy><WalkthroughsPage /></Lazy>} />
        <Route path="/walkthroughs/:id" element={<Lazy><WalkthroughBuilderPage /></Lazy>} />
        {/* The splat carries the document's own path (`docs/guides/timeline`),
            so every document is a link anyone can send. */}
        <Route path="/docs/*" element={<Lazy><DocsPage /></Lazy>} />
        <Route path="/settings" element={<Lazy><SettingsPage /></Lazy>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
