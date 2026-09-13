import { Suspense, lazy, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import { Shell } from "./layout/Shell";
import { Spinner } from "./components/ui";
import LoginPage from "./pages/Login";
import DashboardPage from "./pages/Dashboard";

/* Login and the Dashboard ship in the main bundle; the placeholder pages are route-level chunks fetched on first visit. */
const ProjectsPage = lazy(() => import("./pages/Projects"));
const DocsPage = lazy(() => import("./pages/Docs"));
const SettingsPage = lazy(() => import("./pages/Settings"));

function RequireAuth({ children }: { children: React.ReactElement }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="os-login"><Spinner label="Loading Media Studio..." /></div>;
  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />;
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
      <Route element={<RequireAuth><Shell /></RequireAuth>}>
        <Route index element={<DashboardPage />} />
        <Route path="/projects" element={<Lazy><ProjectsPage /></Lazy>} />
        <Route path="/docs" element={<Lazy><DocsPage /></Lazy>} />
        <Route path="/settings" element={<Lazy><SettingsPage /></Lazy>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
