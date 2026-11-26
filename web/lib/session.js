"use client";
// One place that knows who is signed in. Every page asks this rather than each fetching
// /api/auth/me, so a signed-out user lands on the sign-in screen once, not per component.
import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import { me as fetchMe, logout as apiLogout } from "./api";

const Ctx = createContext({ user: null, loading: true });
export const useSession = () => useContext(Ctx);

export function SessionProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const path = usePathname();

  const refresh = useCallback(async () => {
    try { setUser(await fetchMe()); }
    catch { setUser(null); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh, path]);

  useEffect(() => {
    if (loading) return;
    if (!user && path !== "/login") router.replace("/login");
    // A first password is handed out by a coordinator, so it is shared knowledge until it
    // is changed. Force that before anything else is reachable.
    else if (user?.must_change_password && path !== "/account") router.replace("/account");
  }, [user, loading, path, router]);

  const signOut = async () => {
    try { await apiLogout(); } catch {}
    setUser(null); router.replace("/login");
  };

  return <Ctx.Provider value={{ user, loading, refresh, signOut }}>{children}</Ctx.Provider>;
}
