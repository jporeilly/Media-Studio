import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";

export interface User {
  id: string; // shortuuid, not a number
  username: string;
  display_name: string;
  role: string;
  must_change_password?: number; // 1 = a new or reset account: the app shows the Set-a-new-password gate
  is_active?: number;
  last_login?: string | null;
}

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => { throw new Error("not ready"); },
  logout: async () => {},
  refresh: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.get<{ user: User }>("/api/auth/me");
      setUser(data.user);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    // The API client fires this event on any 401 (except the auth endpoints), which drops us back to /login.
    const onUnauthorized = () => setUser(null);
    window.addEventListener("mediastudio:unauthorized", onUnauthorized);
    return () => window.removeEventListener("mediastudio:unauthorized", onUnauthorized);
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const data = await api.post<{ user: User }>("/api/auth/login", { username, password });
    setUser(data.user);
    return data.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      setUser(null);
    }
  }, []);

  return <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
