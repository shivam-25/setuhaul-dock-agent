// Small inline-SVG charts. No chart library: these are four shapes, and a dependency
// would cost more to load than the whole page. Everything uses currentColor or a token
// so both themes work without a second palette.

export function BarChart({ data, unit = "", max: forced, accent = "var(--code)" }) {
  if (!data?.length) return <p className="empty">Nothing to show yet.</p>;
  const max = forced ?? Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="bars">
      {data.map((d, i) => (
        <div className="bar" key={i}>
          <div className="track" title={`${d.label}: ${d.value}${unit}${d.sub ? ` · ${d.sub}` : ""}`}>
            <div className="fill" style={{ height: `${(d.value / max) * 100}%`, background: accent }} />
          </div>
          <span className="bv">{d.value}</span>
          <span className="bl">{d.label}</span>
          {d.sub ? <span className="bs">{d.sub}</span> : null}
        </div>
      ))}
    </div>
  );
}

/** A horizontal split of one total into named parts - docks busy/free/out, say. */
export function Split({ parts, total }) {
  const sum = total ?? (parts.reduce((a, p) => a + p.value, 0) || 1);
  return (
    <div className="split">
      <div className="splitbar">
        {parts.filter((p) => p.value > 0).map((p, i) => (
          <span key={i} style={{ width: `${(p.value / sum) * 100}%`, background: p.color }}
                title={`${p.label}: ${p.value}`} />
        ))}
      </div>
      <div className="splitkey">
        {parts.map((p, i) => (
          <span key={i}><i style={{ background: p.color }} /> {p.label} <b>{p.value}</b></span>
        ))}
      </div>
    </div>
  );
}

/** The brief's six stages, as a line a driver can read at a glance. */
export function Progress({ stages }) {
  const lastDone = stages.reduce((n, s, i) => (s.done ? i : n), -1);
  return (
    <ol className="prog">
      {stages.map((s, i) => (
        <li key={s.key} className={s.done ? "done" : i === lastDone + 1 ? "next" : ""}>
          <span className="dot" />
          <span className="pl">{s.label}</span>
          <span className="pt">{s.at || (i === lastDone + 1 ? "next" : "—")}</span>
        </li>
      ))}
    </ol>
  );
}

export function Stat({ n, label, tone }) {
  return (
    <div className={"stat" + (tone ? ` ${tone}` : "")}>
      <p className="n">{n}</p><p className="l">{label}</p>
    </div>
  );
}
