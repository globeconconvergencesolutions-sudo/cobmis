"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { login as apiLogin, logout as apiLogout, me, type User } from "./api";

type AuthState = {
  user: User | null;
  csrf: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [csrf, setCsrf] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const r = await me();
      setUser(r.user);
      setCsrf(r.csrf_token);
    } catch {
      setUser(null);
      setCsrf(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const r = await apiLogin(email, password);
    setUser(r.user);
    setCsrf(r.csrf_token);
  }, []);

  const logout = useCallback(async () => {
    await apiLogout(csrf);
    setUser(null);
    setCsrf(null);
  }, [csrf]);

  const value = useMemo(
    () => ({ user, csrf, loading, login, logout, refresh }),
    [user, csrf, loading, login, logout, refresh],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
