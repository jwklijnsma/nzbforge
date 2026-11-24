import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./styles.css";

// Spotweb-like colour per Newznab parent category
const CAT_STYLE = {
  1000: ["Console", "c-games"], 2000: ["Movie", "c-image"], 3000: ["Audio", "c-sound"], 4000: ["PC", "c-apps"],
  5000: ["TV", "c-image"], 6000: ["XXX", "c-xxx"], 7000: ["Other", "c-other"],
};
const RESOLUTIONS = ["2160p", "1080p", "720p", "576p", "480p"];
const SOURCES = ["Remux", "BluRay", "WEB-DL", "WEBRip", "HDTV", "DVD"];
const AGES = [["", "Any time"], ["1", "24 hours"], ["3", "3 days"], ["7", "Week"], ["30", "Month"]];
const SORTS = [["date", "Date"], ["title", "Title"], ["size", "Size"], ["rating", "Rating"], ["grabs", "Grabs"]];
const PAGE = 100;

const fmtSize = (b) => {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = b, i = 0;
  while (n >= 1024 && i < 4) { n /= 1024; i++; }
  return `${n.toFixed(i > 2 ? 2 : i ? 1 : 0)} ${units[i]}`;
};
const fmtDate = (iso) => {
  const d = new Date(iso), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const days = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (days === 0) return `Today ${hm}`;
  if (days === 1) return `Yesterday ${hm}`;
  return d.toLocaleDateString([], { day: "2-digit", month: "short", year: days > 300 ? "numeric" : undefined });
};
const epTag = (r) => r.season != null ? ` S${String(r.season).padStart(2, "0")}${r.episode != null ? `E${String(r.episode).padStart(2, "0")}` : ""}` : "";
const parent = (c) => c - (c % 1000);

function makeApi(key) {
  return async (path, opts = {}) => {
    const res = await fetch(path, {
      ...opts,
      headers: { "X-Api-Key": key, "Content-Type": "application/json" },
    });
    if (!res.ok) {
      if (res.status === 401) throw new Error("That API key was not accepted.");
      const detail = await res.json().then((j) => j.detail).catch(() => null);
      throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status}).`);
    }
    return res.json();
  };
}

function SignIn({ onKey }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const submit = async () => {
    try {
      await makeApi(value.trim())("/web/me");
      onKey(value.trim());
    } catch (e) { setError(e.message); }
  };
  return (
    <main className="signin">
      <h1>nzbforge</h1>
      <p>Enter your API key to search the index.</p>
      <input type="password" value={value} placeholder="API key" autoFocus
        onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} />
      <button onClick={submit}>Sign in</button>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

function CatBadge({ cat }) {
  const [label, cls] = CAT_STYLE[parent(cat)] || CAT_STYLE[7000];
  return <span className={`cat ${cls}`}>{label}</span>;
}

function Tags({ r }) {
  const tags = [];
  if (r.resolution) tags.push([r.resolution, r.resolution === "2160p" ? "t-uhd" : r.resolution === "1080p" || r.resolution === "720p" ? "t-hd" : ""]);
  if (r.hdr) r.hdr.split(", ").forEach((h) => tags.push([h === "Dolby Vision" ? "DV" : h, "t-hdr"]));
  if (r.source) tags.push([r.source, ""]);
  if (r.video_codec) tags.push([r.video_codec, ""]);
  if (r.audio) tags.push([r.audio, ""]);
  if (r.languages?.includes("Dutch")) tags.push(["NL", "t-nl"]);
  if (r.subtitles?.includes("Dutch")) tags.push(["NL subs", "t-nl"]);
  if (r.edition) r.edition.split(", ").forEach((e) => tags.push([e, "t-ed"]));
  return tags.length ? <span className="tags">{tags.map(([t, c]) => <span key={t} className={`tag ${c}`}>{t}</span>)}</span> : null;
}

function Sidebar({ cats, f, set }) {
  const chip = (field, value, label) => (
    <button key={value} className={`chip ${f[field] === value ? "on" : ""}`} aria-pressed={f[field] === value}
      onClick={() => set({ [field]: f[field] === value ? "" : value })}>{label || value}</button>
  );
  return (
    <aside className="sidebar">
      <h4>Categories</h4>
      <ul className="tree">
        <li><button className={!f.cat ? "on" : ""} onClick={() => set({ cat: "" })}>
          <span>All spots</span><span className="n">{cats?.total?.toLocaleString() ?? ""}</span></button></li>
        {cats?.categories.filter((c) => c.count > 0 || [2000, 5000, 3000].includes(c.id)).map((c) => (
          <li key={c.id}>
            <button className={String(f.cat) === String(c.id) ? "on" : ""} onClick={() => set({ cat: String(c.id) })}>
              <span><span className={`dot ${(CAT_STYLE[c.id] || CAT_STYLE[7000])[1]}`} />{c.name}</span>
              <span className="n">{c.count.toLocaleString()}</span>
            </button>
            {c.subs.length > 0 && (String(f.cat).startsWith(String(c.id).slice(0, 1)) && f.cat !== "") && (
              <ul>
                {c.subs.map((s) => (
                  <li key={s.id}><button className={String(f.cat) === String(s.id) ? "on" : ""} onClick={() => set({ cat: String(s.id) })}>
                    <span>{s.name}</span><span className="n">{s.count.toLocaleString()}</span></button></li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
      <h4>Language</h4>
      <div className="chips">{chip("lang", "dutch", "Dutch (audio or subs)")}{chip("lang", "nlsubs", "NL subs only")}</div>
      <h4>Resolution</h4>
      <div className="chips">{RESOLUTIONS.map((r) => chip("resolution", r))}</div>
      <h4>Source</h4>
      <div className="chips">{SOURCES.map((s) => chip("source", s))}</div>
      <h4>Posted</h4>
      <div className="chips">{AGES.map(([v, l]) => chip("maxage", v, l))}</div>
    </aside>
  );
}

function Spots({ api, apiKey, me, q }) {
  const [f, setF] = useState({ cat: "", resolution: "", source: "", lang: "", maxage: "", sort: "date", order: "desc" });
  const [cats, setCats] = useState(null);
  const [data, setData] = useState({ total: 0, items: [] });
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);
  const set = (patch) => setF((old) => ({ ...old, ...patch }));

  const query = useMemo(() => new URLSearchParams({ q, ...f, limit: PAGE }).toString(), [q, f]);

  useEffect(() => { api("/web/categories").then(setCats).catch(() => {}); }, [api]);
  useEffect(() => {
    let live = true;
    setState("loading"); setError("");
    api(`/web/search?${query}&offset=0`)
      .then((d) => { if (live) { setData(d); setState("done"); } })
      .catch((e) => { if (live) { setError(e.message); setState("error"); } });
    return () => { live = false; };
  }, [api, query]);

  const more = async () => {
    setState("loading");
    try {
      const d = await api(`/web/search?${query}&offset=${data.items.length}`);
      setData((old) => ({ total: d.total, items: [...old.items, ...d.items] })); setState("done");
    } catch (e) { setError(e.message); setState("error"); }
  };
  const sortBy = (col) => set(f.sort === col ? { order: f.order === "desc" ? "asc" : "desc" } : { sort: col, order: col === "title" ? "asc" : "desc" });
  const th = (col, label) => (
    <th aria-sort={f.sort === col ? (f.order === "asc" ? "ascending" : "descending") : "none"}>
      <button className="sort" onClick={() => sortBy(col)}>{label}{f.sort === col ? (f.order === "asc" ? " ▲" : " ▼") : ""}</button>
    </th>
  );
  const catName = (() => {
    if (!f.cat || !cats) return "All spots";
    for (const c of cats.categories) {
      if (String(c.id) === f.cat) return c.name;
      const s = c.subs.find((x) => String(x.id) === f.cat);
      if (s) return `${c.name} › ${s.name}`;
    }
    return "Spots";
  })();
  const active = ["resolution", "source", "lang", "maxage"].filter((k) => f[k]);

  return (
    <div className="layout">
      <Sidebar cats={cats} f={f} set={set} />
      <main className="list">
        <div className="toolbar">
          <h2>{catName}</h2>
          <span className="count">{data.total.toLocaleString()} spots{q ? ` matching “${q}”` : ""}</span>
          {active.length > 0 && (
            <button className="link" onClick={() => set({ resolution: "", source: "", lang: "", maxage: "" })}>Clear filters</button>
          )}
          <label className="sortsel">Sort
            <select value={f.sort} onChange={(e) => set({ sort: e.target.value })}>
              {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        {state === "done" && data.items.length === 0 && (
          <p className="empty">No spots here. Add groups on the Admin tab and let the scanner run, or loosen the filters.</p>
        )}
        {data.items.length > 0 && (
          <table className="spots">
            <thead>
              <tr>
                <th className="c-cat">Cat</th>{th("title", "Title")}<th className="c-genre">Genre</th>
                <th className="c-poster">Poster</th>{th("date", "Date")}{th("size", "Size")}<th className="c-act"><span className="sr">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.guid} className={open === r.guid ? "sel" : ""} onClick={() => setOpen(r.guid)}>
                  <td className="c-cat"><CatBadge cat={r.category} /></td>
                  <td className="c-title" title={r.name}>
                    <div className="t1">
                      <button className="titlebtn" onClick={(e) => { e.stopPropagation(); setOpen(r.guid); }}>
                        {r.title || r.name}{epTag(r)}{r.year && parent(r.category) === 2000 ? ` (${r.year})` : ""}
                      </button>
                      {r.rating ? <span className="rating">★ {r.rating}</span> : null}
                      <Tags r={r} />
                    </div>
                    {r.title && <div className="t2">{r.name}</div>}
                  </td>
                  <td className="c-genre">{r.genres || ""}</td>
                  <td className="c-poster">{r.poster}</td>
                  <td className="c-date">{fmtDate(r.posted)}</td>
                  <td className="c-size">{fmtSize(r.size)}</td>
                  <td className="c-act" onClick={(e) => e.stopPropagation()}>
                    <a className="nzb" href={`/api?t=get&id=${r.guid}&apikey=${apiKey}`} title="Download NZB">NZB</a>
                    {me.downloader && <SendButton api={api} guid={r.guid} name={me.downloader} small />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data.items.length < data.total && (
          <button className="more" onClick={more} disabled={state === "loading"}>
            {state === "loading" ? "Loading…" : `Show more (${(data.total - data.items.length).toLocaleString()} left)`}
          </button>
        )}
      </main>
      {open && <SpotPanel api={api} apiKey={apiKey} me={me} guid={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function SendButton({ api, guid, name, small }) {
  const [state, setState] = useState("");
  const send = async () => {
    setState("sending");
    try { await api(`/web/release/${guid}/send`, { method: "POST" }); setState("sent"); }
    catch (e) { setState(e.message); }
  };
  const label = state === "sent" ? "✓ Sent" : state === "sending" ? "Sending…" : small ? "Send" : `Send to ${name}`;
  return (
    <button className={`send ${small ? "small" : ""} ${state && !["sent", "sending"].includes(state) ? "bad" : ""}`}
      onClick={send} disabled={state === "sending" || state === "sent"}
      title={state && !["sent", "sending"].includes(state) ? state : `Send to ${name}`}>{label}</button>
  );
}

function SpotPanel({ api, apiKey, me, guid, onClose }) {
  const [r, setR] = useState(null);
  const [error, setError] = useState("");
  const [showNfo, setShowNfo] = useState(false);

  useEffect(() => {
    setR(null); setError(""); setShowNfo(false);
    api(`/web/release/${guid}`).then(setR).catch((e) => setError(e.message));
  }, [api, guid]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rows = r ? [
    ["Category", r.category_name],
    ["Genre", r.genres],
    ["Year", r.year],
    ["Rating", r.rating ? `★ ${r.rating} / 10` : null],
    ["Resolution", r.resolution],
    ["Source", r.source],
    ["Video", [r.video_codec, r.hdr].filter(Boolean).join(", ")],
    ["Audio", r.audio],
    ["Language", r.languages],
    ["Subtitles", r.subtitles],
    ["Edition", r.edition],
    ["Size", `${fmtSize(r.size)} in ${r.files} file${r.files === 1 ? "" : "s"}`],
    ["Release group", r.release_group],
    ["Poster", r.poster],
    ["Newsgroup", r.group],
    ["Posted", new Date(r.posted).toLocaleString()],
    ["Grabs", r.grabs],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "") : [];
  const links = r ? [
    r.website && ["Website", r.website],
    r.imdbid && ["IMDb", `https://www.imdb.com/title/${r.imdbid}/`],
    r.tmdbid && !r.website?.includes("themoviedb") && ["TMDB", `https://www.themoviedb.org/movie/${r.tmdbid}`],
    r.tvdbid && ["TheTVDB", `https://thetvdb.com/dereferrer/series/${r.tvdbid}`],
  ].filter(Boolean) : [];

  return (
    <div className="scrim" onClick={onClose}>
      <aside className="panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Spot details">
        <div className={`panelbar ${r ? (CAT_STYLE[parent(r.category)] || CAT_STYLE[7000])[1] : ""}`}>
          <span>{r ? r.category_name : "Spot"}</span>
          <button className="close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="panelbody">
          {error && <p className="error">{error}</p>}
          {!r && !error && <p className="count">Loading spot…</p>}
          {r && (
            <>
              <h2>{r.title}{epTag(r)}{r.year && parent(r.category) === 2000 ? ` (${r.year})` : ""}</h2>
              <p className="rel">{r.name}</p>
              <div className="spot">
                {r.poster_url ? <img src={r.poster_url} alt={`Poster for ${r.title}`} /> : <div className="noposter"><CatBadge cat={r.category} /></div>}
                <table className="info"><tbody>
                  {rows.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}
                  {links.length > 0 && (
                    <tr><th>Links</th><td className="links">{links.map(([l, u]) => <a key={l} href={u} target="_blank" rel="noreferrer">{l}</a>)}</td></tr>
                  )}
                </tbody></table>
              </div>
              <div className="actions">
                <a className="btn" href={`/api?t=get&id=${r.guid}&apikey=${apiKey}`}>Download NZB</a>
                {me.downloader && <SendButton api={api} guid={r.guid} name={me.downloader} />}
              </div>
              <h3>Description</h3>
              {r.overview
                ? <p className="overview">{r.overview}</p>
                : <p className="empty">No description yet. TV and movie spots are looked up a few minutes after they're indexed{parent(r.category) === 2000 ? " (movies need TMDB_API_KEY)" : ""}.</p>}
              {r.nfo && (
                <>
                  <h3><button className="link" onClick={() => setShowNfo((v) => !v)} aria-expanded={showNfo}>
                    {showNfo ? "▾" : "▸"} NFO</button></h3>
                  {showNfo && <pre className="nfo">{r.nfo}</pre>}
                </>
              )}
              <h3>Files</h3>
              <table className="files">
                <thead><tr><th>Name</th><th>Parts</th><th>Size</th></tr></thead>
                <tbody>
                  {r.file_list.map((fl) => (
                    <tr key={fl.name} className={fl.parts < fl.total ? "incomplete" : ""}>
                      <td className="name">{fl.name}</td>
                      <td>{fl.parts}/{fl.total}</td>
                      <td>{fmtSize(fl.size)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

const fmtAgo = (iso) => {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
const fmtIn = (iso, now) => {
  const s = Math.max(0, Math.round((new Date(iso) - now) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const num = (n) => (n ?? 0).toLocaleString();
const BUSY = (st) => st && !["idle", "waiting"].includes(st);

function useJobs(api, fast, enabled) {
  const [jobs, setJobs] = useState(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true, timer;
    const tick = async () => {
      try { const j = await api("/web/jobs"); if (live) setJobs(j); } catch { /* keep last */ }
      if (live) timer = setTimeout(tick, fast ? 1500 : 5000);
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [api, fast, enabled]);
  return [jobs, setJobs];
}

function Bar({ value, label }) {
  const pct = value == null ? null : Math.max(0, Math.min(100, value * 100));
  return (
    <div className={`bar ${pct == null ? "indeterminate" : ""}`} role="progressbar" aria-label={label}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct == null ? undefined : Math.round(pct)}>
      <span style={pct == null ? undefined : { width: `${pct}%` }} />
    </div>
  );
}

function scanFraction(sc) {
  const span = sc.article_to - sc.article_from + 1;
  const inGroup = span > 0 && sc.article ? Math.min(1, (sc.article - sc.article_from + 1) / span) : 0;
  return { inGroup, overall: sc.group_count ? (sc.group_index - 1 + inGroup) / sc.group_count : 0 };
}

function JobPill({ jobs, onClick }) {
  if (!jobs) return null;
  const sc = jobs.scanner, im = jobs.import;
  let text = null;
  if (sc.state === "scanning") text = `Pulling ${sc.group_index}/${sc.group_count} · ${Math.round(scanFraction(sc).overall * 100)}%`;
  else if (BUSY(sc.state)) text = sc.state.charAt(0).toUpperCase() + sc.state.slice(1) + "…";
  else if (im.state === "running") text = `Importing groups · ${num(im.matched)}`;
  if (!text) return null;
  return <button className="jobpill" onClick={onClick} title="Show progress"><span className="spin" aria-hidden="true" />{text}</button>;
}

function ScannerCard({ api, jobs, setJobs, hasGroups }) {
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const [showErr, setShowErr] = useState(false);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  if (!jobs) return <div className="card"><p className="count">Loading scanner status…</p></div>;
  const sc = jobs.scanner, lp = sc.last_pass;
  const busy = BUSY(sc.state);
  const { inGroup, overall } = scanFraction(sc);
  const pullAll = async () => {
    setError("");
    try { const s = await api("/web/pull", { method: "POST", body: JSON.stringify({}) }); setJobs((j) => ({ ...j, scanner: { ...j.scanner, ...s, state: s.state === "idle" ? "connecting" : s.state } })); }
    catch (e) { setError(e.message); }
  };
  return (
    <div className="card">
      <div className="cardhead">
        <h3>Scanner</h3>
        <button onClick={pullAll} disabled={busy || !hasGroups} title={hasGroups ? "" : "Add groups first"}>
          {busy ? "Pulling…" : "Pull all groups now"}
        </button>
      </div>
      {sc.state === "scanning" && (
        <>
          <p><b>{sc.group}</b> <span className="count">group {num(sc.group_index)} of {num(sc.group_count)}</span></p>
          <Bar value={inGroup} label="Current group" />
          <p className="count small">
            {sc.article_to ? `${num(Math.max(0, sc.article - sc.article_from + 1))} of ${num(sc.article_to - sc.article_from + 1)} headers (${Math.round(inGroup * 100)}%)` : "Selecting group…"}
          </p>
          <p className="count small">All groups: {Math.round(overall * 100)}%</p>
          <Bar value={overall} label="All groups" />
          <p className="count small">
            {num(sc.headers)} headers this pull{sc.rate ? ` · ${num(sc.rate)} headers/s` : ""} · {sc.connections} connection{sc.connections === 1 ? "" : "s"}{sc.compressed ? " · compressed" : ""} · started {fmtAgo(sc.started)}
          </p>
        </>
      )}
      {busy && sc.state !== "scanning" && (<><p>{sc.state.charAt(0).toUpperCase() + sc.state.slice(1)}…</p><Bar value={null} label={sc.state} /></>)}
      {!busy && (
        <p className="count">
          {sc.enabled
            ? (sc.next_run ? <>Waiting · next pull in <b>{fmtIn(sc.next_run, now)}</b></> : "Idle")
            : "Automatic pulling is off (SCANNER=0 or NNTP_HOST not set). You can still pull by hand."}
        </p>
      )}
      {sc.queued?.length > 0 && <p className="count small">Queued: {sc.queued.join(", ")}</p>}
      {lp && (
        <p className="lastpass">
          Last pull {fmtAgo(lp.finished)}: {num(lp.groups)} group{lp.groups === 1 ? "" : "s"}{lp.only ? " (by hand)" : ""}, {num(lp.headers)} headers,
          {" "}<b>{num(lp.releases)} new releases</b>, took {lp.seconds < 120 ? `${lp.seconds}s` : `${Math.round(lp.seconds / 60)} min`}
          {lp.errors?.length > 0 && <> · <button className="link bad" onClick={() => setShowErr((v) => !v)}>{lp.errors.length} error{lp.errors.length > 1 ? "s" : ""}</button></>}
        </p>
      )}
      {showErr && lp?.errors?.length > 0 && <ul className="errs">{lp.errors.slice(0, 50).map((e) => <li key={e}>{e}</li>)}</ul>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function ImportCard({ api, jobs, setJobs, onDone }) {
  const [name, setName] = useState("");
  const [pattern, setPattern] = useState("alt.binaries.*");
  const [minArticles, setMinArticles] = useState(1);
  const [error, setError] = useState("");
  const im = jobs?.import;
  const running = im?.state === "running";
  const prev = useRef(im?.state);
  useEffect(() => { if (prev.current === "running" && im?.state !== "running") onDone(); prev.current = im?.state; }, [im?.state, onDone]);

  const add = async () => {
    setError("");
    try { await api("/web/groups", { method: "POST", body: JSON.stringify({ name }) }); setName(""); onDone(); }
    catch (e) { setError(e.message); }
  };
  const importAll = async () => {
    setError("");
    try {
      const r = await api("/web/groups/import", { method: "POST", body: JSON.stringify({ pattern, min_articles: Number(minArticles) || 0 }) });
      setJobs((j) => ({ ...j, import: r }));
    } catch (e) { setError(e.message); }
  };
  return (
    <div className="card">
      <h3>Add groups</h3>
      <div className="row">
        <input value={name} placeholder="alt.binaries.example" aria-label="Group name" onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && name && add()} />
        <button onClick={add} disabled={!name}>Add group</button>
      </div>
      <div className="row">
        <input value={pattern} placeholder="alt.binaries.*" aria-label="Group pattern" onChange={(e) => setPattern(e.target.value)} />
        <label className="minart">min. articles
          <input type="number" min="0" step="1000" value={minArticles} onChange={(e) => setMinArticles(e.target.value)} />
        </label>
        <button onClick={importAll} disabled={running}>{running ? "Importing…" : "Import all from provider"}</button>
      </div>
      {running && (<><Bar value={null} label="Group import" /><p className="count small">Reading the group list from your provider · {num(im.matched)} matching so far</p></>)}
      {im?.state === "done" && <p className="count small">Import of <code>{im.pattern}</code> {fmtAgo(im.finished)}: {num(im.matched)} groups match, <b>{num(im.added)} new</b> added.</p>}
      {im?.state === "failed" && <p className="error">Import failed: {im.error}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

const GROUP_PAGE = 200;

function Admin({ api, apiKey, jobs, setJobs }) {
  const [groups, setGroups] = useState([]);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [show, setShow] = useState("all");
  const [limit, setLimit] = useState(GROUP_PAGE);
  const [busyRow, setBusyRow] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, g] = await Promise.all([api("/web/stats"), api("/web/groups")]);
      setStats(s); setGroups(g); setError("");
    } catch (e) { setError(e.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);

  // refresh the table whenever a pull finishes or moves on to the next group
  const sc = jobs?.scanner;
  useEffect(() => { if (sc) load(); }, [sc?.last_pass?.finished, sc?.group]); // eslint-disable-line

  const act = async (name, fn) => {
    setBusyRow(name);
    try { await fn(); } catch (e) { setError(e.message); }
    setBusyRow("");
  };
  const toggle = (g) => act(g.name, async () => {
    const r = await api(`/web/groups/${encodeURIComponent(g.name)}`, { method: "PATCH", body: JSON.stringify({ active: !g.active }) });
    setGroups((gs) => gs.map((x) => (x.name === g.name ? r : x)));
  });
  const remove = (g) => {
    if (!window.confirm(`Remove ${g.name}? Releases already indexed stay; unfinished collections are dropped.`)) return;
    act(g.name, async () => {
      await api(`/web/groups/${encodeURIComponent(g.name)}`, { method: "DELETE" });
      setGroups((gs) => gs.filter((x) => x.name !== g.name));
    });
  };
  const pullOne = (g) => act(g.name, async () => {
    const s = await api("/web/pull", { method: "POST", body: JSON.stringify({ groups: [g.name] }) });
    setJobs((j) => ({ ...j, scanner: { ...j.scanner, ...s } }));
  });

  const f = filter.trim().toLowerCase();
  const shown = groups.filter((g) => (!f || g.name.includes(f)) &&
    (show === "all" || (show === "active" && g.active) || (show === "off" && !g.active) || (show === "errors" && g.error)));
  const queued = new Set(sc?.queued || []);
  const active = groups.filter((g) => g.active).length;
  const backlog = groups.reduce((n, g) => n + (g.active && g.backlog ? g.backlog : 0), 0);

  return (
    <section className="admin">
      {stats && (
        <p className="count">
          {num(stats.releases)} releases · {num(active)} of {num(groups.length)} groups active
          {backlog > 0 && ` · ${num(backlog)} headers still to pull`}
          {stats.newest && ` · newest from ${new Date(stats.newest).toLocaleString()}`}
        </p>
      )}
      <div className="cards">
        <ScannerCard api={api} jobs={jobs} setJobs={setJobs} hasGroups={active > 0} />
        <ImportCard api={api} jobs={jobs} setJobs={setJobs} onDone={load} />
      </div>
      {error && <p className="error">{error}</p>}

      <div className="grouphead">
        <h3>Groups</h3>
        <input value={filter} placeholder="Filter groups" aria-label="Filter groups" onChange={(e) => { setFilter(e.target.value); setLimit(GROUP_PAGE); }} />
        <div className="seg" role="group" aria-label="Show">
          {[["all", "All"], ["active", "Active"], ["off", "Off"], ["errors", "Errors"]].map(([v, l]) => (
            <button key={v} className={show === v ? "on" : ""} aria-pressed={show === v} onClick={() => { setShow(v); setLimit(GROUP_PAGE); }}>{l}</button>
          ))}
        </div>
        <span className="count">{num(shown.length)} shown</span>
      </div>
      {groups.length === 0 ? (
        <p className="empty">No groups yet. Add one above, or import all groups from your provider.</p>
      ) : (
        <table className="groups">
          <thead><tr><th>On</th><th>Group</th><th>Pulled up to</th><th>Newest on server</th><th>To pull</th><th>Last pulled</th><th><span className="sr">Actions</span></th></tr></thead>
          <tbody>
            {shown.slice(0, limit).map((g) => {
              const now = sc?.state === "scanning" && sc.group === g.name;
              const frac = now ? scanFraction(sc).inGroup : null;
              return (
                <tr key={g.name} className={`${g.active ? "" : "off"} ${now ? "pulling" : ""}`}>
                  <td><input type="checkbox" checked={g.active} disabled={busyRow === g.name} onChange={() => toggle(g)} aria-label={`Pull ${g.name} automatically`} /></td>
                  <td className="name">{g.name}{g.error && <div className="error small" title={g.error}>{g.error}</div>}</td>
                  <td className="num">{g.last_article ? num(g.last_article) : "–"}</td>
                  <td className="num">{g.server_last ? num(g.server_last) : "–"}</td>
                  <td className="num">
                    {now ? <><Bar value={frac} label={`Pulling ${g.name}`} /><span className="small">{Math.round(frac * 100)}%</span></>
                      : g.backlog == null ? "–" : g.backlog === 0 ? "✓" : num(g.backlog)}
                  </td>
                  <td>{now ? "pulling now" : queued.has(g.name) ? "queued" : fmtAgo(g.scanned_at)}</td>
                  <td className="acts">
                    <button className="small" onClick={() => pullOne(g)} disabled={now || queued.has(g.name) || busyRow === g.name}>Pull</button>
                    <button className="small ghost" onClick={() => remove(g)} disabled={busyRow === g.name}>Remove</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {shown.length > limit && <button className="more" onClick={() => setLimit((l) => l + GROUP_PAGE)}>Show more ({num(shown.length - limit)} left)</button>}
      <div className="indexer-url">
        <p>Add this to Prowlarr as a Generic Newznab indexer:</p>
        <code>URL: {location.origin} &nbsp; API path: /api &nbsp; API key: {apiKey}</code>
      </div>
    </section>
  );
}

export default function App() {
  const [key, setKey] = useState(localStorage.getItem("nzbforge-key") || "");
  const [me, setMe] = useState(null);
  const [tab, setTab] = useState("spots");
  const [input, setInput] = useState("");
  const [q, setQ] = useState("");
  const api = useMemo(() => makeApi(key), [key]);
  const [jobs, setJobs] = useJobs(api, tab === "admin", !!me);

  useEffect(() => {
    if (!key) return;
    api("/web/me").then(setMe).catch(() => { localStorage.removeItem("nzbforge-key"); setKey(""); });
  }, [api, key]);

  if (!key) return <SignIn onKey={(k) => { localStorage.setItem("nzbforge-key", k); setKey(k); }} />;
  if (!me) return null;

  const search = (e) => { e.preventDefault(); setQ(input.trim()); setTab("spots"); };

  return (
    <div className="app">
      <header className="topbar">
        <button className="logo" onClick={() => { setTab("spots"); setInput(""); setQ(""); }}>nzb<b>forge</b></button>
        <form className="topsearch" onSubmit={search} role="search">
          <input value={input} placeholder="Search spots" aria-label="Search spots" onChange={(e) => setInput(e.target.value)} />
          <button type="submit">Search</button>
        </form>
        <JobPill jobs={jobs} onClick={() => me.is_admin && setTab("admin")} />
        <nav>
          <button className={tab === "spots" ? "on" : ""} onClick={() => setTab("spots")}>Spots</button>
          {me.is_admin && <button className={tab === "admin" ? "on" : ""} onClick={() => setTab("admin")}>Admin</button>}
          <button onClick={() => { localStorage.removeItem("nzbforge-key"); setKey(""); setMe(null); }}>Sign out ({me.username})</button>
        </nav>
      </header>
      {tab === "spots" ? <Spots api={api} apiKey={key} me={me} q={q} /> : <div className="adminwrap"><Admin api={api} apiKey={key} jobs={jobs} setJobs={setJobs} /></div>}
    </div>
  );
}
