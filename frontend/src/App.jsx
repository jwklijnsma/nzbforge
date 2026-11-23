import { useCallback, useEffect, useMemo, useState } from "react";
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

function Admin({ api, apiKey }) {
  const [groups, setGroups] = useState([]);
  const [stats, setStats] = useState(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [pattern, setPattern] = useState("alt.binaries.*");
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      setStats(await api("/web/stats"));
      setGroups(await api("/web/groups"));
    } catch (e) { setError(e.message); }
  }, [api]);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    try {
      await api("/web/groups", { method: "POST", body: JSON.stringify({ name }) });
      setName(""); setError(""); load();
    } catch (e) { setError(e.message); }
  };

  const importAll = async () => {
    setImporting(true); setNotice(""); setError("");
    try {
      const r = await api("/web/groups/import", { method: "POST", body: JSON.stringify({ pattern }) });
      setNotice(`${r.matched} groups on your provider match ${pattern}; ${r.added} new added.`);
      load();
    } catch (e) { setError(e.message); }
    setImporting(false);
  };

  return (
    <section>
      {stats && (
        <p className="count">
          {stats.releases.toLocaleString()} releases across {stats.groups} groups
          {stats.newest && `, newest from ${new Date(stats.newest).toLocaleString()}`}
        </p>
      )}
      <div className="searchbar">
        <input value={name} placeholder="alt.binaries.example" onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()} />
        <button onClick={add}>Add group</button>
      </div>
      <div className="searchbar">
        <input value={pattern} placeholder="alt.binaries.*" onChange={(e) => setPattern(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && importAll()} />
        <button onClick={importAll} disabled={importing}>{importing ? "Importing…" : "Import all from provider"}</button>
      </div>
      {notice && <p className="count">{notice}</p>}
      {error && <p className="error">{error}</p>}
      <table>
        <thead><tr><th>Group</th><th>Last article scanned</th></tr></thead>
        <tbody>{groups.map((g) => <tr key={g.name}><td className="name">{g.name}</td><td>{g.last_article || "not scanned yet"}</td></tr>)}</tbody>
      </table>
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
        <nav>
          <button className={tab === "spots" ? "on" : ""} onClick={() => setTab("spots")}>Spots</button>
          {me.is_admin && <button className={tab === "admin" ? "on" : ""} onClick={() => setTab("admin")}>Admin</button>}
          <button onClick={() => { localStorage.removeItem("nzbforge-key"); setKey(""); setMe(null); }}>Sign out ({me.username})</button>
        </nav>
      </header>
      {tab === "spots" ? <Spots api={api} apiKey={key} me={me} q={q} /> : <div className="adminwrap"><Admin api={api} apiKey={key} /></div>}
    </div>
  );
}
