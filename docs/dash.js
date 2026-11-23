// A small stand-in for the dashboard runtime: loads each data file the page names, then draws every marked element.
window.dash = (() => {
  const store = {}, draws = {}, listeners = [];
  let params = Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
  const each = (name) => document.querySelectorAll(`[data-draw='${name}']`).forEach((el) => { try { draws[name](el); } catch (e) { console.error(name, e); } });
  const all = () => { listeners.forEach((f) => f()); Object.keys(draws).forEach(each); };
  const ids = () => [...new Set([...document.querySelectorAll('[data-source]')].map((e) => e.dataset.source).concat(['events']))];
  const load = () => Promise.all(ids().map((id) => fetch(`data/${id}.json`, { cache: 'no-cache' }).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((j) => { store[id] = { status: 'ok', data: j.rows }; }).catch(() => { store[id] = { status: 'error' }; }))).then(all);
  document.addEventListener('DOMContentLoaded', load);
  let t; window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(all, 150); });
  return {
    colors: ['#2f6fb0', '#c8642a', '#5a5a5a'],
    data: (id) => store[id] || { status: 'loading' },
    draw: (name, fn) => { if (fn) draws[name] = fn; else if (draws[name]) each(name); },
    onData: (fn) => listeners.push(fn),
    params: () => ({ ...params }),
    setParams: (p) => { params = { ...params, ...p }; history.replaceState(null, '', '#' + new URLSearchParams(params)); all(); },
  };
})();
