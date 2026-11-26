"use client";
import { useMemo, useState } from "react";

/** Search, filter and sort for a list of records, kept in one place so every table
 *  behaves the same way and no page reinvents its own half of it. */
export function useTableControls(rows, { searchKeys = [], filters = [], sorts = [],
                                         initialSort = null } = {}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState({});
  const [sort, setSort] = useState(initialSort || sorts[0]?.key || "");
  const [dir, setDir] = useState("asc");

  const out = useMemo(() => {
    let r = rows || [];
    const needle = q.trim().toLowerCase();
    if (needle) {
      // Every word must match something, so "jaipur critical" narrows rather than widens.
      const words = needle.split(/\s+/);
      r = r.filter((row) => {
        const hay = searchKeys.map((k) => String(row[k] ?? "")).join(" ").toLowerCase();
        return words.every((w) => hay.includes(w));
      });
    }
    for (const f of filters) {
      const v = active[f.key];
      if (v && v !== "__all") r = r.filter((row) => f.test(row, v));
    }
    const s = sorts.find((x) => x.key === sort);
    if (s) {
      r = [...r].sort((a, b) => {
        const av = s.value(a), bv = s.value(b);
        const c = typeof av === "number" && typeof bv === "number"
          ? av - bv : String(av ?? "").localeCompare(String(bv ?? ""));
        return dir === "asc" ? c : -c;
      });
    }
    return r;
  }, [rows, q, active, sort, dir, searchKeys, filters, sorts]);

  // The toolbar needs the same filter and sort definitions the hook was given. Returning
  // them means a caller declares each one once, instead of passing the same array twice
  // and letting the two drift apart.
  return { rows: out, q, setQ, active, setActive, sort, setSort, dir, setDir,
           total: rows?.length || 0, filtersDef: filters, sortsDef: sorts };
}

export function Toolbar({ ctl, placeholder = "Search…", noun = "rows" }) {
  const filters = ctl.filtersDef || [];
  const sorts = ctl.sortsDef || [];
  const { q, setQ, active, setActive, sort, setSort, dir, setDir, rows, total } = ctl;
  const dirty = q || Object.values(active).some((v) => v && v !== "__all");
  return (
    <div className="toolbar">
      <div className="searchbox">
        <svg viewBox="0 0 16 16" aria-hidden="true" className="sicon">
          <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <line x1="10.5" y1="10.5" x2="14" y2="14" stroke="currentColor" strokeWidth="1.6"
                strokeLinecap="round" />
        </svg>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder}
               aria-label={placeholder} />
        {q && <button className="clearq" onClick={() => setQ("")} aria-label="Clear search">×</button>}
      </div>

      {filters.map((f) => (
        <label className="tfilter" key={f.key}>
          <span>{f.label}</span>
          <select value={active[f.key] || "__all"}
                  onChange={(e) => setActive({ ...active, [f.key]: e.target.value })}>
            <option value="__all">All</option>
            {f.options.map((o) => (
              <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>
            ))}
          </select>
        </label>
      ))}

      {sorts.length > 0 && (
        <label className="tfilter">
          <span>Sort</span>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            {sorts.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <button className="dirbtn" onClick={() => setDir(dir === "asc" ? "desc" : "asc")}
                  title={dir === "asc" ? "Ascending" : "Descending"}
                  aria-label={`Sort ${dir === "asc" ? "ascending" : "descending"}`}>
            {dir === "asc" ? "↑" : "↓"}
          </button>
        </label>
      )}

      <span className="tcount">
        {rows.length === total ? `${total} ${noun}` : `${rows.length} of ${total}`}
        {dirty && (
          <button className="clearall"
                  onClick={() => { setQ(""); setActive({}); }}>Clear</button>
        )}
      </span>
    </div>
  );
}

export const PRIORITIES = ["CRITICAL", "HIGH", "NORMAL", "LOW"];
export const PRANK = { CRITICAL: 0, HIGH: 1, NORMAL: 2, LOW: 3 };
