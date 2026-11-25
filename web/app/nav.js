"use client";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { meta } from "../lib/api";

export default function Nav() {
  const p = usePathname();
  const [m, setM] = useState(null);
  useEffect(() => { meta().then(setM).catch(() => {}); }, []);
  return (
    <nav className="nav"><div className="wrap">
      <span className="brand">SetuHaul</span>
      <Link href="/" className={p === "/" ? "on" : ""}>Driver</Link>
      <Link href="/console" className={p === "/console" ? "on" : ""}>Coordinator</Link>
      <span className="sp">
        {m ? `clock ${m.now.slice(11, 16)} · ${m.slots} slots · ${m.audit_rows} audit rows`
           : "api offline"}
      </span>
    </div></nav>
  );
}
