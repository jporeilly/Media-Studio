import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Clapperboard, FileText, FolderOpen, LayoutDashboard, LogOut, Moon, Settings as SettingsIcon, Sun } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { ACCENTS, useTheme } from "../context/ThemeContext";
import { Avatar, Select } from "../components/ui";

const BRAND_NAME = "Media Studio";

interface NavItem { to: string; label: string; icon: React.ReactNode }

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: <LayoutDashboard size={18} /> },
  { to: "/projects", label: "Projects", icon: <FolderOpen size={18} /> },
  { to: "/docs", label: "Docs", icon: <FileText size={18} /> },
  { to: "/settings", label: "Settings", icon: <SettingsIcon size={18} /> },
];

export function Shell() {
  const { user, logout } = useAuth();
  const { dark, setDark, accent, setAccent } = useTheme();
  const navigate = useNavigate();
  const version = useQuery({
    queryKey: ["system-health"],
    queryFn: () => api.get<{ status: string; version: string }>("/api/system/health"),
    staleTime: 5 * 60_000,
  });

  return (
    <div className="os-shell">
      <aside className="os-sidebar">
        <div className="os-logo" onClick={() => navigate("/")}>
          <Clapperboard size={24} /> {BRAND_NAME}
        </div>
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `os-nav-item ${isActive ? "active" : ""}`}>
            {item.icon}
            <span>{item.label}</span>
          </NavLink>
        ))}
        <div className="os-sidebar-user">
          <Avatar name={user?.display_name} size={32} />
          <div style={{ minWidth: 0 }}>
            <div className="os-truncate" style={{ fontWeight: 500 }}>{user?.display_name || "Signed in"}</div>
            <small>{user?.role || ""}</small>
          </div>
        </div>
        <div className="os-sidebar-foot">
          {/* From the API, never a literal in the source: an in-place update changes it,
              and a hard-coded one once printed 0.1.0 inside the 0.2.0 build. */}
          {version.data?.version && (
            <span title={`${BRAND_NAME} version ${version.data.version}`}>v{version.data.version}</span>
          )}
        </div>
      </aside>

      <div className="os-main">
        <header className="os-topbar">
          <div className="os-row" style={{ gap: 8 }}>
            <strong style={{ fontFamily: "var(--font-display)" }}>Media Studio Enterprise</strong>
          </div>
          <div className="os-topbar-right">
            <Select value={accent} onChange={(e) => setAccent(e.target.value)} title="Accent colour" style={{ padding: "6px 28px 6px 10px" }}>
              {Object.keys(ACCENTS).map((k) => <option key={k} value={k}>{k}</option>)}
            </Select>
            <button className="os-icon-btn" onClick={() => setDark(!dark)} title="Toggle dark / light">{dark ? <Sun size={18} /> : <Moon size={18} />}</button>
            <button className="os-icon-btn" onClick={async () => { await logout(); navigate("/login"); }} title="Log out"><LogOut size={18} /></button>
          </div>
        </header>
        <main className="os-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
