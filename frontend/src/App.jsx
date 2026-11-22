import { useCallback, useEffect, useState } from "react";
import "./styles.css";

const CATS = [["", "Everything"], ["2000", "Movies"], ["5000", "TV"], ["3000", "Audio"], ["7000", "Other"]];

const fmtSize = (b) => {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = b, i = 0;
  while (n >= 1024 && i < 4) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
};
const fmtAge = (iso) => {
  const days = (Date.now() - new Date(iso)) / 86400000;
  return days < 1 ? `${Math.max(1, Math.round(days * 24))}h` : `${Math.round(days)}d`;
};

function makeApi(key) {
  return async (path, opts = {}) => {
    const res = await fetch(path, {
      ...opts,
      headers: { "X-Api-Key": key, "Content-Type": "application/json" },
    });
    if (!res.ok) throw new Error(res.status === 401 ? "That API key was not accepted." : `Request failed (${res.status}).`);
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

function Search({ api, apiKey }) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [data, setData] = useState({ total: 0, items: [] });
  const [state, setState] = useState("idle");
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");

  const run = useCallback(async () => {
    setState("loading"); setError("");
    try {
      setData(await api(`/web/search?q=${encodeURIComponent(q)}&cat=${cat}&limit=100`));
      setState("done");
    } catch (e) { setError(e.message); setState("error"); }
  }, [api, q, cat]);

  useEffect(() => { run(); }, [cat]); // eslint-disable-line

  return (
    <section>
      <div className="searchbar">
        <input value={q} placeholder="Search releases" onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()} />
        <select value={cat} onChange={(e) => setCat(e.target.value)}>
          {CATS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <button onClick={run}>Search</button>
      </div>
      {error && <p className="error">{error}</p>}
      {state === "done" && data.items.length === 0 && (
        <p className="empty">No releases match. Add groups on the Admin tab and let the scanner run, or loosen the search.</p>
      )}
      {data.items.length > 0 && (
        <>
          <p className="count">{data.total.toLocaleString()} releases</p>
          <table>
            <thead><tr><th>Name</th><th>Category</th><th>Size</th><th>Age</th><th>Grabs</th><th></th></tr></thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.guid} className="click" onClick={() => setOpen(r.guid)}>
                  <td className="name" title={r.group}>
                    <div className="namecell">
                      {r.poster_url ? <img src={r.poster_url} alt="" loading="lazy" /> : <span className="noimg" />}
                      <div>
                        {r.name}
                        {r.title && (
                          <div className="sub">
                            {r.title}{r.year ? ` (${r.year})` : ""}{r.rating ? `, rated ${r.rating}` : ""}
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td>{r.category_name}</td>
                  <td>{fmtSize(r.size)}</td>
                  <td>{fmtAge(r.posted)}</td>
                  <td>{r.grabs}</td>
                  <td><a href={`/api?t=get&id=${r.guid}&apikey=${apiKey}`} onClick={(e) => e.stopPropagation()}>Download NZB</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {open && <ReleasePanel api={api} apiKey={apiKey} guid={open} onClose={() => setOpen(null)} />}
    </section>
  );
}

function ReleasePanel({ api, apiKey, guid, onClose }) {
  const [r, setR] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setR(null); setError("");
    api(`/web/release/${guid}`).then(setR).catch((e) => setError(e.message));
  }, [guid]); // eslint-disable-line

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="scrim" onClick={onClose}>
      <aside className="panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Release details">
        <button className="close" onClick={onClose}>Close</button>
        {error && <p className="error">{error}</p>}
        {!r && !error && <p className="count">Loading details</p>}
        {r && (
          <>
            <div className="hero">
              {r.poster_url && <img src={r.poster_url} alt="" />}
              <div>
                <h2>{r.title}{r.year ? ` (${r.year})` : ""}</h2>
                <p className="count">
                  {r.category_name}, {fmtSize(r.size)}, posted {fmtAge(r.posted)} ago{r.rating ? `, rated ${r.rating}` : ""}
                </p>
                <p className="rel">{r.name}</p>
                <p className="links">
                  <a href={`/api?t=get&id=${r.guid}&apikey=${apiKey}`}>Download NZB</a>
                  {r.imdbid && <a href={`https://www.imdb.com/title/${r.imdbid}/`} target="_blank" rel="noreferrer">IMDb</a>}
                  {r.tmdbid && <a href={`https://www.themoviedb.org/movie/${r.tmdbid}`} target="_blank" rel="noreferrer">TMDB</a>}
                  {r.tvdbid && <a href={`https://thetvdb.com/dereferrer/series/${r.tvdbid}`} target="_blank" rel="noreferrer">TheTVDB</a>}
                </p>
              </div>
            </div>
            {r.overview
              ? <p className="overview">{r.overview}</p>
              : <p className="empty">No description yet. The scanner looks up TV and movie releases a few minutes after they are indexed.</p>}
            <h3>Files</h3>
            <table>
              <thead><tr><th>Name</th><th>Parts</th><th>Size</th></tr></thead>
              <tbody>
                {r.file_list.map((f) => (
                  <tr key={f.name}>
                    <td className="name">{f.name}</td>
                    <td>{f.parts}/{f.total}</td>
                    <td>{fmtSize(f.size)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {r.nfo && (<><h3>NFO</h3><pre className="nfo">{r.nfo}</pre></>)}
            <p className="count">Posted by {r.poster} in {r.group}</p>
          </>
        )}
      </aside>
    </div>
  );
}

function Admin({ api, apiKey }) {
  const [groups, setGroups] = useState([]);
  const [stats, setStats] = useState(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

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
  const [tab, setTab] = useState("search");
  const api = makeApi(key);

  useEffect(() => {
    if (!key) return;
    makeApi(key)("/web/me").then(setMe).catch(() => { localStorage.removeItem("nzbforge-key"); setKey(""); });
  }, [key]);

  if (!key) return <SignIn onKey={(k) => { localStorage.setItem("nzbforge-key", k); setKey(k); }} />;
  if (!me) return null;

  return (
    <div className="app">
      <header>
        <h1>nzbforge</h1>
        <nav>
          <button className={tab === "search" ? "on" : ""} onClick={() => setTab("search")}>Search</button>
          {me.is_admin && <button className={tab === "admin" ? "on" : ""} onClick={() => setTab("admin")}>Admin</button>}
          <button onClick={() => { localStorage.removeItem("nzbforge-key"); setKey(""); setMe(null); }}>Sign out</button>
        </nav>
      </header>
      {tab === "search" ? <Search api={api} apiKey={key} /> : <Admin api={api} apiKey={key} />}
    </div>
  );
}
