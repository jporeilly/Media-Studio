import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export const ACCENTS: Record<string, { primary: string; sidebar: string }> = {
  Teal: { primary: "#0d9488", sidebar: "#134e4a" },
  Slate: { primary: "#475569", sidebar: "#1e293b" },
  Blue: { primary: "#2563eb", sidebar: "#1e3a5f" },
  Indigo: { primary: "#4f46e5", sidebar: "#312e81" },
  Purple: { primary: "#7c3aed", sidebar: "#581c87" },
  Green: { primary: "#16a34a", sidebar: "#14532d" },
  Orange: { primary: "#ea580c", sidebar: "#7c2d12" },
  Rose: { primary: "#e11d48", sidebar: "#881337" },
};

interface ThemeState {
  dark: boolean;
  accent: string;
  setDark: (v: boolean) => void;
  setAccent: (name: string) => void;
}

const ThemeContext = createContext<ThemeState>({ dark: true, accent: "Teal", setDark: () => {}, setAccent: () => {} });

function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [dark, setDarkState] = useState<boolean>(() => readStored("ms.dark", true));
  const [accent, setAccentState] = useState<string>(() => readStored("ms.accent", "Teal"));

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", dark ? "dark" : "light");
    const a = ACCENTS[accent] || ACCENTS.Teal;
    root.style.setProperty("--brand", a.primary);
    root.style.setProperty("--brand-sidebar", a.sidebar);
    try {
      localStorage.setItem("ms.dark", JSON.stringify(dark));
      localStorage.setItem("ms.accent", JSON.stringify(accent));
    } catch {
      /* ignore */
    }
  }, [dark, accent]);

  return (
    <ThemeContext.Provider value={{ dark, accent, setDark: setDarkState, setAccent: setAccentState }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
