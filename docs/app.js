const C = dash.colors, MUTED = 'var(--cds-chart-muted)';
const fmtN = d3.format(','), day = d3.utcFormat('%b %-d'), longDay = d3.utcFormat('%B %-d'), parseDay = d3.utcParse('%Y-%m-%d');
const FMT = { int: (v) => fmtN(v), min: (v) => v + ' min', pct: (v) => v + '%', hrs: (v) => v + ' h', chg: (v) => (v > 0 ? '+' : '') + v + '%', date: (v) => longDay(parseDay(v)), pts: (v) => (v > 0 ? '+' : '') + v + ' pts', raw: (v) => v };
const ok = (id) => { const s = dash.data(id); return s.status === 'ok' ? s.data : null; };
const where = (el) => { const [k, v] = (el.dataset.where || '').split('='); return (r) => !k || String(r[k]) === v; };

// every single number on the page: one marked element, one cell
dash.draw('v', (el) => {
  const rows = ok(el.dataset.source);
  const row = rows && rows.find(where(el));
  const v = row ? row[el.dataset.field] : null;
  el.textContent = v == null ? '—' : FMT[el.dataset.fmt || 'raw'](v);
});

function frame(el, height, m) {
  el.replaceChildren();
  const w = Math.max(el.clientWidth || 0, 280);
  const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${w} ${height}`).attr('role', 'img').attr('aria-label', el.dataset.label || '');
  const g = svg.append('g').attr('transform', `translate(${m.l},${m.t})`);
  const tip = d3.select(el).append('div').attr('class', 'pg-tip').attr('hidden', '');
  const show = (ev, lines) => {
    tip.attr('hidden', null).selectAll('div').data(lines).join('div').text((d) => d);
    const [x, y] = d3.pointer(ev, el), tw = tip.node().offsetWidth || 150;
    tip.style('left', Math.max(0, Math.min(x + 12, w - tw - 4)) + 'px').style('top', y + 12 + 'px');
  };
  return { g, w: w - m.l - m.r, h: height - m.t - m.b, show, hide: () => tip.attr('hidden', '') };
}
// a chart's rows, or a plain line in its place when there are none
const rowsOr = (el) => {
  const s = dash.data(el.dataset.source);
  if (s.status === 'ok' && s.data.length) return s.data;
  el.replaceChildren(); el.textContent = s.status === 'error' ? 'Failed to load' : s.status === 'ok' ? 'Nothing to show.' : '—';
  return null;
};
const axis = (g, ax) => g.call(ax).call((s) => s.select('.domain').style('stroke', 'var(--cds-chart-axis)')).call((s) => s.selectAll('line').style('stroke', 'var(--cds-chart-axis)'));
const grid = (g, y, w) => g.append('g').selectAll('line').data(y.ticks(4)).join('line').attr('x1', 0).attr('x2', w).attr('y1', y).attr('y2', y).style('stroke', 'var(--cds-chart-grid)');
const bandW = (x) => Math.min(x.bandwidth(), 70), bandX = (x, k) => x(k) + (x.bandwidth() - bandW(x)) / 2;

// an upright bar per row: data-x names the label column, data-y the value column, data-n an optional count shown when pointed at
dash.draw('bars', (el) => {
  const all = rowsOr(el); if (!all) return;
  if (!el.clientWidth) return;   // on a hidden page: drawn when that page is opened
  const X = el.dataset.x, Y = el.dataset.y, unit = el.dataset.unit || '', isWeek = X === 'week', nField = el.dataset.n, nUnit = el.dataset.nUnit || '';
  const rows = all.filter((r) => r[Y] != null), lab = (d) => (isWeek ? day(parseDay(d)) : String(d).split(' to ')[0]);
  const f = frame(el, 210, { l: 44, r: 8, t: 22, b: 30 });
  // a week with no value keeps its place on the axis and simply has no bar
  const dom = isWeek ? all : rows;
  const x = d3.scaleBand().domain(dom.map((r) => r[X])).range([0, f.w]).padding(0.2);
  const y = d3.scaleLinear().domain([0, d3.max(rows, (r) => r[Y]) || 1]).range([f.h, 0]).nice();
  grid(f.g, y, f.w);
  axis(f.g.append('g'), d3.axisLeft(y).ticks(4).tickFormat(d3.format('~s')));
  const step = Math.ceil(dom.length / Math.max(2, Math.floor(f.w / 52)));
  axis(f.g.append('g').attr('transform', `translate(0,${f.h})`), d3.axisBottom(x).tickValues(dom.filter((r, i) => i % step === 0).map((r) => r[X])).tickFormat(lab));
  f.g.append('g').selectAll('rect').data(rows).join('rect').attr('x', (r) => bandX(x, r[X])).attr('width', bandW(x)).attr('y', (r) => y(r[Y])).attr('height', (r) => f.h - y(r[Y]))
    .style('fill', (r) => (r.full_week === 0 ? MUTED : C[0]))
    .on('pointermove', (ev, r) => f.show(ev, [(isWeek ? 'Week of ' + lab(r[X]) : r[X]) + (r.full_week === 0 ? ' (part week)' : ''), fmtN(r[Y]) + ' ' + unit, nField ? fmtN(r[nField]) + ' ' + nUnit : ''].filter(Boolean))).on('pointerleave', f.hide);
  // numbered markers over the weeks a season or patch landed; the numbers are listed in the table under the Overview chart
  const events = isWeek && el.dataset.events ? (ok('events') || []).filter((e) => x(e.week) != null) : [];
  const mark = f.g.append('g').selectAll('g').data(events).join('g').attr('transform', (e) => `translate(${x(e.week) + x.bandwidth() / 2},-11)`)
    .on('pointermove', (ev, e) => f.show(ev, [e.when, e.event])).on('pointerleave', f.hide);
  mark.append('circle').attr('r', 7).style('fill', 'var(--color-bg)').style('stroke', 'var(--color-fg)');
  mark.append('text').attr('class', 'pg-lab').attr('text-anchor', 'middle').attr('dy', '0.35em').style('fill', 'var(--color-fg)').text((e) => e.n);
});

// one sideways bar per row with its value at the end: for a few named groups
dash.draw('hbars', (el) => {
  const all = rowsOr(el); if (!all) return;
  if (!el.clientWidth) return;
  const X = el.dataset.x, Y = el.dataset.y, unit = el.dataset.unit || '';
  const rows = all.filter((r) => r[Y] != null);
  // data-text names a column of words to print at the end of each bar in place of the number; data-left widens the label column
  const T = el.dataset.text, cw = el.clientWidth || 0, narrow = cw < 380, left = Math.min(+el.dataset.left || (narrow ? 128 : 160), Math.max(128, cw * 0.5));
  const f = frame(el, 16 + rows.length * 30, { l: left, r: T ? 104 : 48, t: 4, b: 8 });
  const y = d3.scaleBand().domain(rows.map((r) => r[X])).range([0, f.h]).padding(0.25);
  const x = d3.scaleLinear().domain([0, d3.max(rows, (r) => r[Y]) || 1]).range([0, f.w]);
  f.g.append('g').selectAll('text').data(rows).join('text').attr('class', 'pg-lab').attr('x', -8).attr('text-anchor', 'end').attr('y', (r) => y(r[X]) + y.bandwidth() / 2).attr('dy', '0.35em').text((r) => r[X]);
  f.g.append('g').selectAll('rect').data(rows).join('rect').attr('x', 0).attr('y', (r) => y(r[X])).attr('height', y.bandwidth()).attr('width', (r) => Math.max(1, x(r[Y]))).style('fill', C[0])
    .on('pointermove', (ev, r) => f.show(ev, [r.factor || r[X], T ? r[T] : '', fmtN(r[Y]) + ' ' + unit].filter(Boolean))).on('pointerleave', f.hide);
  f.g.append('g').selectAll('text').data(rows).join('text').attr('class', 'pg-lab').attr('x', (r) => x(r[Y]) + 6).attr('y', (r) => y(r[X]) + y.bandwidth() / 2).attr('dy', '0.35em').text((r) => (T ? r[T] : fmtN(r[Y])));
});

// a table whose columns are its own header cells: data-field names the column, data-fmt how to show it
dash.draw('rows', (el) => {
  const tb = el.closest('table'), all = ok(tb.dataset.source); el.replaceChildren();
  if (!all) return;
  const cols = [...tb.querySelectorAll('th')].map((th) => [th.dataset.field, FMT[th.dataset.fmt]]);
  for (const r of all) {
    const tr = el.appendChild(document.createElement('tr')); tr.dataset.key = r[tb.dataset.rowKey];
    for (const [k, f] of cols) { const td = tr.appendChild(document.createElement('td')); td.textContent = r[k] == null ? '—' : f ? f(r[k]) : r[k]; }
  }
});

// the pages: a button picks one and only that one shows; charts are drawn again once their page has a width
const redraw = () => ['bars', 'hbars'].forEach((n) => dash.draw(n));
document.querySelectorAll('.pg-tab').forEach((b) => b.addEventListener('click', () => { dash.setParams({ tab: b.value }); setTimeout(redraw, 60); }));
dash.onData(() => {
  const tab = dash.params().tab || 'overview';
  document.querySelectorAll('.pg-tab').forEach((b) => b.setAttribute('aria-pressed', String(b.value === tab)));
  document.querySelectorAll('.pg-pane').forEach((e) => { e.hidden = e.dataset.tab !== tab; });
});
// a chart inside a closed See the numbers block has no width yet, so charts are drawn again when one opens
document.querySelectorAll('.pg details').forEach((d) => d.addEventListener('toggle', () => { if (d.open) redraw(); }));
