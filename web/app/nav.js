"use client";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { meta } from "../lib/api";
import { useSession } from "../lib/session";

export default function Nav() {
  const p = usePathname();
  const { user, signOut } = useSession();
  const [m, setM] = useState(null);
  useEffect(() => { if (user) meta().then(setM).catch(() => {}); }, [user]);

  if (!user) return (
    <nav className="nav"><div className="wrap"><span className="brand">SetuHaul</span></div></nav>
  );

  const isDriver = user.role === "driver";
  return (
    <nav className="nav"><div className="wrap">
      <span className="brand">SetuHaul</span>
      {/* A driver has no coordinator link at all. Hiding it is not the control - the API
          refuses the call either way - but showing a door that will not open is unkind. */}
      {isDriver
        ? <Link href="/" className={p === "/" ? "on" : ""}>My trips</Link>
        : <>
            <Link href="/console" className={p === "/console" ? "on" : ""}>Coordinator</Link>
            <Link href="/" className={p === "/" ? "on" : ""}>Driver view</Link>
          </>}
      <span className="sp">
        {m ? `clock ${m.now.slice(11, 16)} · ${m.slots} slots` : ""}
      </span>
      <Link href="/account" className={"who" + (p === "/account" ? " on" : "")}>
        {user.display_name} <em>{user.role}</em>
      </Link>
      <button className="ghost small" onClick={signOut}>Sign out</button>
    </div></nav>
  );
}
