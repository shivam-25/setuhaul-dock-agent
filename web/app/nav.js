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
  const [open, setOpen] = useState(false);
  useEffect(() => { if (user) meta().then(setM).catch(() => {}); }, [user]);
  useEffect(() => { setOpen(false); }, [p]);

  if (!user) return (
    <nav className="nav"><div className="wrap"><span className="brand">SetuHaul</span></div></nav>
  );

  // A driver has no coordinator links at all. Hiding them is not the access control -
  // the API refuses either way - but showing a door that will not open is unkind.
  const links = user.role === "driver"
    ? [["/", "My trips"], ["/chat", "Dock desk"], ["/audit", "My decisions"]]
    : [["/console", "Overview"], ["/board", "Dock board"], ["/audit", "Decision log"],
       ["/", "Every truck"]];

  return (
    <nav className="nav">
      <div className="wrap">
        <Link href={user.role === "driver" ? "/" : "/console"} className="brand">SetuHaul</Link>
        <button className="burger" aria-label="Menu" aria-expanded={open}
                onClick={() => setOpen((o) => !o)}>☰</button>
        <div className={"links" + (open ? " open" : "")}>
          {links.map(([href, label]) => (
            <Link key={href} href={href} className={p === href ? "on" : ""}>{label}</Link>
          ))}
          <span className="sp" title="The dataset is a one-day snapshot with no current time, so the system runs at a fixed clock.">
            {m ? `demo clock ${m.now.slice(11, 16)} · ${m.slots} slots` : ""}
          </span>
          <Link href="/account" className={"who" + (p === "/account" ? " on" : "")}>
            {user.display_name} <em>{user.role}</em>
          </Link>
          <button className="ghost small" onClick={signOut}>Sign out</button>
        </div>
      </div>
    </nav>
  );
}
