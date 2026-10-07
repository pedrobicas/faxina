'use strict';
(() => {
  const TOKEN = document.querySelector('meta[name="faxina-token"]').content;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  // Cria elementos sem innerHTML: nomes de arquivo vêm do disco e podem ter qualquer caractere.
  function el(tag, attrs = {}, ...children) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'style') n.style.cssText = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (k === 'checked') n.checked = !!v;
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) n.append(c instanceof Node ? c : String(c));
    return n;
  }

  const fill = (node, ...kids) => node.replaceChildren(...kids.flat().filter(k => k != null && k !== false));

  // ---------- formatação ----------
  const nf0 = new Intl.NumberFormat('pt-BR');
  const nf1 = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });
  function bytes(n) {
    if (n == null) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = Math.abs(n), i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${n < 0 ? '−' : ''}${i === 0 ? nf0.format(v) : nf1.format(v)} ${units[i]}`;
  }
  const num = n => nf0.format(n);
  const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : many}`;
  const pct = (a, b) => (b > 0 ? `${nf1.format((a / b) * 100)}%` : '—');
  const dateStr = ts => new Date(ts * 1000).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' });
  const dateTime = ts => new Date(ts * 1000).toLocaleString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  function ago(days) {
    if (days == null) return 'sem data';
    if (days < 1) return 'hoje';
    if (days < 30) return `há ${plural(days, 'dia', 'dias')}`;
    const m = Math.round(days / 30.4);
    if (m < 12) return `há ${plural(m, 'mês', 'meses')}`;
    const y = days / 365;
    return `há ${nf1.format(y)} ${y >= 2 ? 'anos' : 'ano'}`;
  }
  const secs = s => (s < 1 ? 'menos de 1 s' : s < 60 ? `${nf1.format(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);
  const catOf = i => S.cats[i] || { label: 'Outros', color: '#858B90' };
  const daysSince = ts => Math.floor((Date.now() / 1000 - ts) / 86400);

  // ---------- API ----------
  async function api(path, body) {
    const opt = { method: body ? 'POST' : 'GET', headers: { 'X-Faxina-Token': TOKEN } };
    if (body) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(path, opt); } catch { throw new Error('O Faxina foi fechado. Rode o programa de novo.'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Erro ${res.status}`);
    return data;
  }

  // ---------- toasts e modal ----------
  function toast(msg, opts = {}) {
    const t = el('div', { class: `toast${opts.error ? ' error' : ''}`, role: opts.error ? 'alert' : 'status' }, el('span', {}, msg));
    if (opts.action) t.append(el('button', { type: 'button', onclick: async () => { t.remove(); await opts.action.fn(); } }, opts.action.label));
    $('#toasts').append(t);
    while ($('#toasts').children.length > 3) $('#toasts').firstElementChild.remove();
    setTimeout(() => t.remove(), opts.ms || 7000);
  }
  const fail = e => toast(e.message || String(e), { error: true });
  function modal({ title, body, actions }) {
    return new Promise(resolve => {
      const d = $('#modal');
      $('#modal-title').textContent = title;
      $('#modal-body').replaceChildren(...[].concat(body));
      $('#modal-actions').replaceChildren(...actions.map(a => el('button', { class: `btn ${a.class || 'ghost'}`, value: a.value, type: 'submit' }, a.label)));
      d.returnValue = '';
      d.onclose = () => resolve(d.returnValue);
      d.showModal();
    });
  }

  // ---------- estado ----------
  const S = { view: 'overview', cats: [], overview: null, treeId: 0, tree: null, files: { cat: '', older: 0 },
    sel: new Map(), dupsTimer: null, dups: null, keepRule: 'oldest', scanTimer: null };

  function show(screen) {
    for (const s of ['home', 'scan', 'app', 'bye']) $(`#screen-${s}`).hidden = s !== screen;
  }

  // ---------- tela inicial ----------
  async function renderHome() {
    show('home');
    clearInterval(S.dupsTimer);
    let data;
    try { data = await api('/api/places'); } catch (e) { return fail(e); }
    $('#drives').replaceChildren(...data.drives.map(d => {
      const used = d.total ? d.used / d.total : 0;
      return el('button', { class: 'drive', type: 'button', onclick: () => startScan(d.path) },
        el('b', {}, d.label),
        el('div', { class: `usage${used > 0.9 ? ' high' : ''}` }, el('i', { style: `width:${(used * 100).toFixed(1)}%` })),
        el('small', {}, `${bytes(d.free)} livres de ${bytes(d.total)}`));
    }));

    // A prévia da home mostra apenas dados reais fornecidos pelo sistema operacional.
    // Categorias de arquivos só aparecem depois que o usuário faz uma análise.
    const disk = data.drives[0];
    const preview = $('#space-preview');
    if (disk) {
      const usedRatio = disk.total ? Math.max(0, Math.min(1, disk.used / disk.total)) : 0;
      $('#preview-volume').textContent = disk.label.toUpperCase();
      $('#preview-capacity').textContent = bytes(disk.total);
      $('#preview-used').textContent = bytes(disk.used);
      $('#preview-used-list').textContent = bytes(disk.used);
      $('#preview-free').textContent = bytes(disk.free);
      $('#preview-path').textContent = disk.path;
      $('#preview-used-bar').style.width = `${usedRatio * 100}%`;
      $('#preview-free-bar').style.width = `${(1 - usedRatio) * 100}%`;
      const marks = [0, .25, .5, .75, 1].map(p => bytes(disk.total * p));
      $('#preview-axis').replaceChildren(...marks.map(m => el('span', {}, m)));
      $('#preview-stamp').textContent = `${nf1.format(usedRatio * 100)}% OCUPADO`;
      preview.classList.remove('is-empty');
    } else {
      $('#preview-volume').textContent = 'ARMAZENAMENTO LOCAL';
      $('#preview-capacity').textContent = 'INDISPONÍVEL';
      $('#preview-used').textContent = '—';
      $('#preview-used-list').textContent = '—';
      $('#preview-free').textContent = '—';
      $('#preview-path').textContent = 'Nenhum volume detectado';
      $('#preview-used-bar').style.width = '0%';
      $('#preview-free-bar').style.width = '100%';
      $('#preview-axis').replaceChildren(...['0', '—', '—', '—', '—'].map(m => el('span', {}, m)));
      $('#preview-stamp').textContent = 'SEM DADOS';
      preview.classList.add('is-empty');
    }
    $('#places').replaceChildren(...data.places.map(p => el('button', { class: 'place', type: 'button', title: p.path, onclick: () => startScan(p.path) }, p.label)));
    $('#recent-wrap').hidden = !data.recent.length;
    $('#recent').replaceChildren(...data.recent.map(r => el('button', { class: 'place', type: 'button', title: r.root, onclick: () => startScan(r.root) },
      r.root, el('small', {}, dateStr(r.last)))));
    if (data.places[0]) $('#path-input').placeholder = `Cole o caminho de uma pasta, por exemplo ${data.places[0].path}`;
  }

  async function startScan(path) {
    $('#path-error').hidden = true;
    try {
      await api('/api/scan', { path });
      watchScan();
    } catch (e) {
      if (!$('#screen-home').hidden) {
        $('#path-error').textContent = e.message;
        $('#path-error').hidden = false;
      } else fail(e);
    }
  }

  function openBrowser(start) {
    const body = el('div', {});
    let current = null;
    const load = async path => {
      try {
        const data = await api(`/api/browse?path=${encodeURIComponent(path || '')}`);
        current = data.path;
        const list = el('div', { class: 'browser-list' });
        if (data.parent) list.append(el('button', { type: 'button', class: 'up', onclick: () => load(data.parent) }, '↑ pasta de cima'));
        for (const d of data.dirs) list.append(el('button', { type: 'button', class: d.hidden ? 'hidden-dir' : '', onclick: () => load(d.path) }, d.name));
        if (!data.dirs.length) list.append(el('p', { class: 'muted', style: 'padding:12px;margin:0' }, 'Nenhuma subpasta aqui.'));
        body.replaceChildren(el('p', { class: 'browser-path' }, data.path), list);
      } catch (e) { fail(e); }
    };
    load(start);
    modal({ title: 'Escolha uma pasta', body, actions: [{ label: 'Cancelar', value: 'cancel' }, { label: 'Analisar esta pasta', value: 'ok', class: 'primary' }] })
      .then(v => { if (v === 'ok' && current) { $('#path-input').value = current; startScan(current); } });
  }

  async function quit() {
    const ok = await modal({ title: 'Encerrar o Faxina?', body: el('p', {}, 'O programa fecha. Os itens da quarentena continuam guardados para a próxima vez.'),
      actions: [{ label: 'Cancelar', value: 'cancel' }, { label: 'Encerrar', value: 'ok', class: 'primary' }] });
    if (ok !== 'ok') return;
    clearInterval(S.scanTimer);
    clearInterval(S.dupsTimer);
    try { await api('/api/shutdown', {}); } catch { /* já estava fechado */ }
    show('bye');
  }

  // ---------- varredura ----------
  function watchScan() {
    show('scan');
    clearInterval(S.scanTimer);
    const tick = async () => {
      let st;
      try { st = await api('/api/status'); } catch (e) { clearInterval(S.scanTimer); return fail(e); }
      const s = st.scan;
      if (!s) return;
      $('#scan-root').textContent = s.root;
      $('#scan-files').textContent = num(s.files);
      $('#scan-bytes').textContent = bytes(s.bytes);
      $('#scan-dirs').textContent = num(s.dirs);
      $('#scan-current').textContent = s.current || ' ';
      $('#scan-elapsed').textContent = `${secs(s.elapsed)}${s.errors ? ` · ${plural(s.errors, 'pasta sem permissão', 'pastas sem permissão')} (puladas)` : ''}`;
      if (s.state === 'done') {
        clearInterval(S.scanTimer);
        enterApp();
      } else if (s.state !== 'running') {
        clearInterval(S.scanTimer);
        if (s.state === 'error') toast(`A análise falhou: ${s.error}`, { error: true });
        renderHome();
      }
    };
    tick();
    S.scanTimer = setInterval(tick, 200);
  }

  // ---------- app ----------
  async function enterApp(view) {
    show('app');
    S.sel.clear();
    S.treeId = 0;
    S.tree = null;
    S.dups = null;
    try { await loadOverview(); } catch (e) { return fail(e); }
    go(view || 'overview');
  }

  async function loadOverview() {
    S.overview = await api('/api/overview');
    S.cats = S.overview.categories;
    const o = S.overview;
    $('#side-root').textContent = o.root;
    const safe = o.reclaim.dev.total + o.reclaim.installers.total + o.reclaim.temp.total;
    $('#badge-cleanup').textContent = safe ? bytes(safe) : '';
    $('#badge-dups').textContent = o.duplicates ? bytes(o.duplicates) : '';
    const st = await api('/api/status');
    $('#badge-quarantine').textContent = st.quarantine.count ? num(st.quarantine.count) : '';
    $('#freed').hidden = !o.freed;
    $('#freed').textContent = `${bytes(o.freed)} movidos para a quarentena nesta sessão`;
  }

  const RENDER = {};
  function go(view) {
    S.view = view;
    S.sel.clear();
    updateSel();
    clearInterval(S.dupsTimer);
    $$('.nav button').forEach(b => b.classList.toggle('is-active', b.dataset.view === view));
    $$('.view').forEach(v => { v.hidden = v.id !== `view-${view}`; });
    $('#tooltip').hidden = true;
    RENDER[view]();
    window.scrollTo(0, 0);
  }

  function head(view, kicker, title, sub) {
    const v = $(`#view-${view}`);
    fill(v, el('p', { class: 'kicker' }, kicker), el('h1', {}, title), sub ? el('p', { class: 'sub' }, sub) : null);
    return v;
  }

  // ---------- seleção ----------
  const key = (kind, id) => `${kind === 'dir' ? 'd' : 'f'}:${id}`;
  function setSel(kind, id, size, on) {
    const k = key(kind, id);
    if (on) S.sel.set(k, { kind, id, size });
    else S.sel.delete(k);
    updateSel();
  }
  function updateSel() {
    const n = S.sel.size;
    $('#selbar').hidden = !n;
    let total = 0;
    for (const x of S.sel.values()) total += x.size;
    $('#sel-info').replaceChildren(`${plural(n, 'selecionado', 'selecionados')} · `, el('b', {}, bytes(total)));
  }
  function checkbox(kind, id, size, label) {
    return el('input', { type: 'checkbox', 'aria-label': `Selecionar ${label}`, checked: S.sel.has(key(kind, id)),
      onchange: e => { setSel(kind, id, size, e.target.checked); e.target.closest('tr')?.classList.toggle('is-selected', e.target.checked); } });
  }

  async function moveSelected() {
    const items = [...S.sel.values()];
    if (!items.length) return;
    if (S.view === 'dups' && S.dups) {
      const wiped = S.dups.groups.filter(g => g.files.every(f => S.sel.has(key('file', f.id))));
      if (wiped.length) {
        await modal({ title: 'Mantenha pelo menos uma cópia',
          body: el('p', {}, `Você marcou todas as cópias de ${plural(wiped.length, 'grupo', 'grupos')}. Desmarque uma cópia em cada grupo para não perder o arquivo.`),
          actions: [{ label: 'Entendi', value: 'ok', class: 'primary' }] });
        return;
      }
    }
    const btn = $('#sel-move');
    btn.disabled = true;
    try {
      const res = await api('/api/quarantine/move', {
        files: items.filter(x => x.kind === 'file').map(x => x.id),
        dirs: items.filter(x => x.kind === 'dir').map(x => x.id),
      });
      S.sel.clear();
      updateSel();
      if (res.failed.length) toast(`${plural(res.failed.length, 'item não pôde', 'itens não puderam')} ser movido${res.failed.length > 1 ? 's' : ''}: ${res.failed[0].error}`, { error: true, ms: 9000 });
      if (res.moved.length) {
        const ids = res.moved.map(m => m.id);
        toast(`${plural(res.moved.length, 'item', 'itens')} na quarentena · ${bytes(res.bytes)}`, {
          action: { label: 'Desfazer', fn: async () => {
            try {
              await api('/api/quarantine/restore', { ids });
              toast('Restaurado. Analise a pasta de novo para os números voltarem.');
              refresh();
            } catch (e) { fail(e); }
          } },
        });
      }
      await refresh();
    } catch (e) { fail(e); } finally { btn.disabled = false; }
  }
  async function refresh() {
    try { await loadOverview(); } catch (e) { return fail(e); }
    RENDER[S.view]();
  }

  // ---------- visão geral ----------
  RENDER.overview = () => {
    const o = S.overview;
    const v = $('#view-overview');
    const safe = o.reclaim.dev.total + o.reclaim.installers.total + o.reclaim.temp.total;
    const used = o.disk ? o.disk.used / o.disk.total : 0;
    const hero = el('div', { class: 'hero' },
      el('div', {},
        el('p', { class: 'kicker' }, 'RESUMO DO ESPAÇO'),
        el('div', { class: 'big' }, bytes(o.size)),
        el('p', { class: 'meta' }, `${num(o.files)} arquivos em ${num(o.dirs)} pastas · analisado em ${secs(o.scanSeconds)}`,
          o.errors ? ` · ${num(o.errors)} sem permissão` : '')),
      o.disk ? el('div', { style: 'min-width:240px' },
        el('p', { class: 'label', style: 'margin:0 0 8px' }, 'disco'),
        el('div', { class: `usage${used > 0.9 ? ' high' : ''}` }, el('i', { style: `width:${(used * 100).toFixed(1)}%` })),
        el('p', { class: 'meta', style: 'margin:8px 0 0' }, `${bytes(o.disk.free)} livres de ${bytes(o.disk.total)}`)) : null);

    const reclaimRow = (label, total, view, hint) => el('div', { class: 'row clickable', onclick: () => go(view) },
      el('span', { class: 'name' }, label, hint ? el('span', { class: 'muted' }, ` · ${hint}`) : null),
      el('span', { class: 'val' }, total == null ? '—' : bytes(total)));
    const reclaim = el('div', { class: 'card reclaim' },
      el('h2', {}, 'Dá para liberar com segurança'),
      el('p', { class: 'sub' }, 'Coisas que se recriam sozinhas ou que normalmente só servem uma vez.'),
      el('div', { class: 'big' }, bytes(safe + (o.duplicates || 0))),
      el('div', { class: 'rows', style: 'margin-top:14px' },
        reclaimRow('Pastas de projetos regeneráveis', o.reclaim.dev.total, 'cleanup', plural(o.reclaim.dev.count, 'pasta', 'pastas')),
        reclaimRow('Instaladores antigos', o.reclaim.installers.total, 'cleanup', plural(o.reclaim.installers.count, 'arquivo', 'arquivos')),
        reclaimRow('Temporários e downloads incompletos', o.reclaim.temp.total, 'cleanup', plural(o.reclaim.temp.count, 'arquivo', 'arquivos')),
        o.duplicates != null
          ? reclaimRow('Cópias duplicadas', o.duplicates, 'dups')
          : el('div', { class: 'row' }, el('span', { class: 'name' }, 'Cópias duplicadas'), el('button', { class: 'link', type: 'button', onclick: () => go('dups') }, 'procurar')),
        o.reclaim.oldBig.total ? reclaimRow('Grandes e parados há mais de 1 ano', o.reclaim.oldBig.total, 'cleanup', 'revise antes') : null));

    const cats = o.categories.filter(c => c.size > 0).sort((a, b) => b.size - a.size);
    const catsCard = el('div', { class: 'card' },
      el('h2', {}, 'Por tipo de arquivo'),
      el('div', { class: 'stack', role: 'img', 'aria-label': 'Proporção do espaço por tipo de arquivo' },
        ...cats.map(c => el('i', { style: `width:${(c.size / o.size) * 100}%;background:${c.color}`, title: `${c.label}: ${bytes(c.size)}` }))),
      el('div', { class: 'rows' }, ...cats.map(c => el('div', { class: 'row clickable', onclick: () => { S.files.cat = c.id; go('files'); } },
        el('span', { class: 'name' }, el('i', { class: 'dot', style: `background:${c.color}` }), c.label, el('span', { class: 'muted' }, ` · ${num(c.count)}`)),
        el('span', { class: 'val' }, `${bytes(c.size)}  ${pct(c.size, o.size)}`)))));

    const maxTop = o.topDirs[0]?.size || 1;
    const topCard = el('div', { class: 'card' },
      el('h2', {}, 'Pastas que mais ocupam'),
      el('p', { class: 'sub' }, 'Clique para ver no mapa.'),
      el('div', { class: 'rows' }, ...o.topDirs.map(d => el('div', { class: 'row clickable', onclick: () => { S.treeId = d.id; go('map'); } },
        el('span', { class: 'name' }, d.name),
        el('span', { class: 'val' }, bytes(d.size)),
        el('div', { class: 'bar' }, el('i', { style: `width:${(d.size / maxTop) * 100}%;background:${catOf(d.cat).color}` }))))));

    const bigCard = el('div', { class: 'card' },
      el('h2', {}, 'Maiores arquivos'),
      el('p', { class: 'sub' }, el('button', { class: 'link', type: 'button', onclick: () => { S.files.cat = ''; go('files'); } }, 'ver a lista completa')),
      el('div', { class: 'rows' }, ...o.largest.map(f => el('div', { class: 'row' },
        el('span', { class: 'name', title: f.path }, el('i', { class: 'dot', style: `background:${catOf(f.cat).color}` }), f.name),
        el('span', { class: 'val' }, bytes(f.size))))));

    let deltaCard = null;
    if (o.delta) {
      const d = o.delta;
      deltaCard = el('div', { class: 'card', style: 'margin-top:14px' },
        el('h2', {}, `Desde a última análise (${dateStr(d.since)})`),
        d.diff === 0
          ? el('p', { class: 'sub' }, 'Nada mudou de tamanho.')
          : el('p', { class: 'sub' }, 'A pasta ', el('b', { class: d.diff > 0 ? 'delta-up' : 'delta-down' }, `${d.diff > 0 ? 'cresceu' : 'diminuiu'} ${bytes(Math.abs(d.diff))}`), '.'),
        el('div', { class: 'rows' }, ...d.changes.map(c => el('div', { class: 'row' },
          el('span', { class: 'name' }, c.name, c.before === 0 ? el('span', { class: 'muted' }, ' · nova') : null),
          el('span', { class: `val ${c.diff > 0 ? 'delta-up' : 'delta-down'}` }, `${c.diff > 0 ? '+' : ''}${bytes(c.diff)}`)))));
    }
    fill(v, hero, el('div', { class: 'grid two' }, reclaim, catsCard), el('div', { class: 'grid two', style: 'margin-top:14px' }, topCard, bigCard), deltaCard);
  };

  // ---------- mapa (treemap "squarified": blocos o mais quadrados possível) ----------
  function squarify(items, x, y, w, h) {
    const total = items.reduce((s, it) => s + it.size, 0);
    if (!total || w <= 0 || h <= 0) return [];
    const scale = (w * h) / total;
    const nodes = items.map(it => ({ it, area: it.size * scale }));
    const out = [];
    let row = [];
    const worst = (r, len) => {
      if (!r.length) return Infinity;
      let s = 0, max = 0, min = Infinity;
      for (const n of r) { s += n.area; max = Math.max(max, n.area); min = Math.min(min, n.area); }
      return Math.max((len * len * max) / (s * s), (s * s) / (len * len * min));
    };
    const layout = r => {
      const s = r.reduce((a, n) => a + n.area, 0);
      if (w >= h) {
        const cw = s / h;
        let cy = y;
        for (const n of r) { const nh = n.area / cw; out.push({ ...n, x, y: cy, w: cw, h: nh }); cy += nh; }
        x += cw; w -= cw;
      } else {
        const rh = s / w;
        let cx = x;
        for (const n of r) { const nw = n.area / rh; out.push({ ...n, x: cx, y, w: nw, h: rh }); cx += nw; }
        y += rh; h -= rh;
      }
    };
    let i = 0;
    while (i < nodes.length) {
      const len = Math.min(w, h);
      const candidate = [...row, nodes[i]];
      if (!row.length || worst(candidate, len) <= worst(row, len)) { row = candidate; i++; }
      else { layout(row); row = []; }
    }
    if (row.length) layout(row);
    return out;
  }

  function enterDir(id) {
    S.treeId = id;
    $('#tooltip').hidden = true;
    RENDER.map();
  }

  function drawTreemap() {
    const box = $('#treemap');
    if (!box || !S.tree) return;
    const rect = box.getBoundingClientRect();
    const tip = $('#tooltip');
    const tiles = squarify(S.tree.items, 0, 0, rect.width, rect.height).map(t => {
      const it = t.it, cat = catOf(it.cat);
      const canEnter = it.kind === 'dir' && it.hasChildren;
      const node = el('div', {
        class: `tile ${it.kind}${it.junk ? ' junk' : ''}`,
        style: `left:${t.x}px;top:${t.y}px;width:${Math.max(0, t.w)}px;height:${Math.max(0, t.h)}px;background:${it.kind === 'dir' ? cat.color : cat.color + 'CC'}`,
        role: canEnter ? 'button' : null, tabindex: canEnter ? '0' : null,
        'aria-label': `${it.name}, ${bytes(it.size)}`,
      });
      if (t.w > 56 && t.h > 30) node.append(el('b', {}, it.kind === 'dir' ? `${it.name}/` : it.name), el('small', {}, bytes(it.size)));
      if (canEnter) {
        node.addEventListener('click', () => enterDir(it.id));
        node.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); enterDir(it.id); } });
      }
      node.addEventListener('mousemove', e => {
        fill(tip, el('b', {}, it.kind === 'dir' ? `${it.name}/` : it.name),
          el('span', {}, `${bytes(it.size)} · ${pct(it.size, S.tree.size)} desta pasta${it.files ? ` · ${num(it.files)} arquivos` : ''}`),
          it.junk ? el('span', { style: 'display:block;color:#D8EF46' }, 'pasta regenerável de projeto') : null);
        tip.hidden = false;
        tip.style.left = `${Math.min(e.clientX + 16, innerWidth - tip.offsetWidth - 10)}px`;
        tip.style.top = `${Math.min(e.clientY + 16, innerHeight - tip.offsetHeight - 10)}px`;
      });
      node.addEventListener('mouseleave', () => { tip.hidden = true; });
      return node;
    });
    box.replaceChildren(...tiles);
  }

  RENDER.map = async () => {
    const v = head('map', 'DISTRIBUIÇÃO', 'Mapa do espaço', 'Cada bloco é proporcional ao espaço que ocupa. Clique numa pasta para entrar nela; ESC volta uma pasta.');
    const crumbs = el('div', { class: 'crumbs', 'aria-label': 'Caminho' });
    const box = el('div', { class: 'treemap', id: 'treemap' });
    const legend = el('div', { class: 'legend' }, ...S.cats.filter(c => c.size > 0).map(c => el('span', {}, el('i', { class: 'dot', style: `background:${c.color}` }), c.label)));
    const listWrap = el('div', { class: 'scroll', style: 'margin-top:22px' });
    v.append(crumbs, box, legend, listWrap);
    try { S.tree = await api(`/api/tree?id=${S.treeId}`); } catch (e) { return fail(e); }
    S.treeId = S.tree.id;
    const t = S.tree;
    t.crumbs.forEach((c, i) => {
      if (i) crumbs.append(el('span', {}, '/'));
      crumbs.append(el('button', { type: 'button', onclick: () => enterDir(c.id) }, c.name));
    });
    crumbs.append(el('span', { class: 'muted', style: 'margin-left:10px' }, `${bytes(t.size)} · ${num(t.files)} arquivos`));
    drawTreemap();
    const top = t.items[0]?.size || 1;
    const rows = t.items.filter(it => it.kind !== 'rest').map(it => {
      const cat = catOf(it.cat);
      return el('tr', { class: S.sel.has(key(it.kind, it.id)) ? 'is-selected' : '' },
        el('td', { class: 'check', 'data-label': 'Selecionar' }, checkbox(it.kind, it.id, it.size, it.name)),
        el('td', { 'data-label': 'Nome' }, el('div', { class: 'fname' }, it.kind === 'dir' ? `${it.name}/` : it.name),
          it.junk ? el('div', { class: 'fpath', style: 'color:#A76F00' }, 'regenerável') : null),
        el('td', { 'data-label': 'Tipo' }, el('span', { class: 'chip', style: `color:${cat.color}` }, it.kind === 'dir' ? 'pasta' : cat.label)),
        el('td', { class: 'num', 'data-label': 'Tamanho' }, bytes(it.size), el('div', { class: 'sizebar' }, el('i', { style: `width:${(it.size / top) * 100}%` }))),
        el('td', { class: 'num', 'data-label': 'Do total' }, pct(it.size, t.size)),
        el('td', { class: 'act', 'data-label': 'Ação' }, it.kind === 'dir' && it.hasChildren ? el('button', { class: 'iconbtn', type: 'button', onclick: () => enterDir(it.id) }, 'entrar') : null));
    });
    listWrap.append(el('table', { class: 'table' },
      el('thead', {}, el('tr', {}, el('th', {}, ''), el('th', {}, 'Nome'), el('th', {}, 'Tipo'), el('th', { class: 'num' }, 'Tamanho'), el('th', { class: 'num' }, '%'), el('th', {}, ''))),
      el('tbody', {}, ...rows)));
  };

  // ---------- maiores arquivos ----------
  const revealBtn = path => el('button', { class: 'iconbtn', type: 'button', title: 'Mostrar no explorador de arquivos',
    onclick: () => api('/api/reveal', { path }).catch(fail) }, 'abrir pasta');

  RENDER.files = async () => {
    const v = head('files', 'ARQUIVOS', 'Maiores arquivos', 'Os arquivos que mais ocupam espaço. Filtre por tipo ou por quanto tempo estão parados.');
    const catSel = el('select', { 'aria-label': 'Tipo de arquivo', onchange: e => { S.files.cat = e.target.value; RENDER.files(); } },
      el('option', { value: '' }, 'Todos os tipos'), ...S.cats.map(c => el('option', { value: c.id, selected: S.files.cat === c.id ? '' : null }, c.label)));
    const ageSel = el('select', { 'aria-label': 'Tempo sem alteração', onchange: e => { S.files.older = Number(e.target.value); RENDER.files(); } },
      ...[[0, 'Qualquer data'], [180, 'Parados há mais de 6 meses'], [365, 'Parados há mais de 1 ano'], [730, 'Parados há mais de 2 anos']]
        .map(([d, l]) => el('option', { value: d, selected: S.files.older === d ? '' : null }, l)));
    v.append(el('div', { class: 'toolbar' }, catSel, ageSel));
    let data;
    try { data = await api(`/api/files?cat=${S.files.cat}&older=${S.files.older}&limit=300`); } catch (e) { return fail(e); }
    if (!data.items.length) { v.append(el('p', { class: 'empty' }, 'Nenhum arquivo com esses filtros.')); return; }
    const max = data.items[0].size || 1;
    const rows = data.items.map(f => {
      const cat = catOf(f.cat);
      return el('tr', { class: S.sel.has(key('file', f.id)) ? 'is-selected' : '' },
        el('td', { class: 'check', 'data-label': 'Selecionar' }, checkbox('file', f.id, f.size, f.name)),
        el('td', { 'data-label': 'Arquivo' }, el('div', { class: 'fname' }, f.name), el('div', { class: 'fpath' }, f.path)),
        el('td', { 'data-label': 'Tipo' }, el('span', { class: 'chip', style: `color:${cat.color}` }, cat.label)),
        el('td', { class: 'num', 'data-label': 'Alterado' }, dateStr(f.mtime)),
        el('td', { class: 'num', 'data-label': 'Tamanho' }, bytes(f.size), el('div', { class: 'sizebar' }, el('i', { style: `width:${(f.size / max) * 100}%` }))),
        el('td', { class: 'act', 'data-label': 'Ação' }, revealBtn(f.path)));
    });
    v.append(el('div', { class: 'scroll' }, el('table', { class: 'table' },
      el('thead', {}, el('tr', {}, el('th', {}, ''), el('th', {}, 'Arquivo'), el('th', {}, 'Tipo'), el('th', { class: 'num' }, 'Alterado'), el('th', { class: 'num' }, 'Tamanho'), el('th', {}, ''))),
      el('tbody', {}, ...rows))));
  };

  // ---------- duplicados ----------
  const PHASES = { grouping: 'Agrupando por tamanho', links: 'Conferindo hard links', edges: 'Comparando o começo e o fim dos arquivos', full: 'Lendo os candidatos por inteiro', done: 'Finalizando' };
  const dupFraction = q => (q.phase === 'full' ? (q.totalBytes ? q.doneBytes / q.totalBytes : 0) : q.phase === 'edges' ? (q.totalFiles ? q.doneFiles / q.totalFiles : 0) : 0);
  const dupLine = q => (q.phase === 'full' ? `${bytes(q.doneBytes)} de ${bytes(q.totalBytes)} · ${q.current}` : q.current || ' ');

  function renderDupIntro(v, p) {
    let min = 1024 * 1024;
    const sizeSel = el('select', { 'aria-label': 'Tamanho mínimo', onchange: e => { min = Number(e.target.value); } },
      ...[[100 * 1024, 'a partir de 100 KB'], [1024 * 1024, 'a partir de 1 MB'], [10 * 1024 * 1024, 'a partir de 10 MB'], [100 * 1024 * 1024, 'a partir de 100 MB']]
        .map(([b, l]) => el('option', { value: b, selected: b === min ? '' : null }, l)));
    v.append(el('div', { class: 'card' },
      el('h2', {}, 'Como funciona'),
      el('p', { class: 'sub' }, 'Primeiro só os arquivos de mesmo tamanho viram candidatos. Depois o Faxina compara o começo e o fim de cada um, e só lê por inteiro quem continuou empatado. Por isso costuma ser rápido mesmo em pastas grandes.'),
      p && p.state === 'error' ? el('p', { class: 'form-error' }, p.error) : null,
      el('div', { class: 'toolbar', style: 'margin:0' }, sizeSel,
        el('button', { class: 'btn primary', type: 'button', onclick: async () => {
          try { await api('/api/duplicates', { minSize: min }); RENDER.dups(); } catch (e) { fail(e); }
        } }, 'Procurar duplicados'))));
  }

  function renderDupRunning(v, p) {
    v.append(el('div', { class: 'card' },
      el('h2', { id: 'dup-phase' }, PHASES[p.phase] || 'Procurando…'),
      el('div', { class: 'progress' }, el('i', { id: 'dup-bar', style: `width:${(dupFraction(p) * 100).toFixed(1)}%` })),
      el('p', { class: 'sub mono', id: 'dup-line', style: 'word-break:break-all' }, dupLine(p)),
      el('button', { class: 'btn ghost sm', type: 'button', onclick: async () => { await api('/api/duplicates/cancel', {}).catch(fail); RENDER.dups(); } }, 'Cancelar')));
    clearInterval(S.dupsTimer);
    S.dupsTimer = setInterval(async () => {
      if (S.view !== 'dups') return clearInterval(S.dupsTimer);
      const st = await api('/api/status').catch(() => null);
      if (!st || !st.dups || st.dups.state !== 'running') {
        clearInterval(S.dupsTimer);
        await loadOverview().catch(() => {});
        RENDER.dups();
        return;
      }
      const q = st.dups;
      const bar = $('#dup-bar');
      if (bar) bar.style.width = `${(dupFraction(q) * 100).toFixed(1)}%`;
      const phase = $('#dup-phase');
      if (phase) phase.textContent = PHASES[q.phase] || 'Procurando…';
      const line = $('#dup-line');
      if (line) line.textContent = dupLine(q);
    }, 400);
  }

  async function resetDups() {
    try { await api('/api/duplicates/reset', {}); } catch (e) { return fail(e); }
    S.dups = null;
    S.sel.clear();
    updateSel();
    await loadOverview().catch(() => {});
    RENDER.dups();
  }

  RENDER.dups = async () => {
    const v = head('dups', 'CÓPIAS', 'Arquivos duplicados', 'Arquivos com conteúdo idêntico, mesmo com nomes diferentes. A comparação é feita byte a byte (por hash), não pelo nome.');
    let d;
    try { d = await api('/api/duplicates'); } catch (e) { return fail(e); }
    const p = d.progress;
    if (!p || p.state === 'cancelled' || p.state === 'error') return renderDupIntro(v, p);
    if (p.state === 'running') return renderDupRunning(v, p);
    S.dups = d;
    if (!d.groups.length) {
      v.append(el('p', { class: 'empty' }, `Nenhum arquivo duplicado a partir de ${bytes(p.minSize)}. Boa!`),
        el('p', {}, el('button', { class: 'link', type: 'button', onclick: resetDups }, 'procurar de novo com outro tamanho')));
      return;
    }
    const keeper = g => {
      const files = g.files.slice();
      if (S.keepRule === 'newest') return files.sort((a, b) => b.mtime - a.mtime)[0].id;
      if (S.keepRule === 'shortest') return files.sort((a, b) => a.path.length - b.path.length)[0].id;
      return files.sort((a, b) => a.mtime - b.mtime)[0].id;
    };
    const ruleSel = el('select', { 'aria-label': 'Qual cópia manter', onchange: e => { S.keepRule = e.target.value; RENDER.dups(); } },
      ...[['oldest', 'Manter a cópia mais antiga'], ['newest', 'Manter a cópia mais recente'], ['shortest', 'Manter a de caminho mais curto']]
        .map(([k, l]) => el('option', { value: k, selected: S.keepRule === k ? '' : null }, l)));
    const economy = p.candidateBytes
      ? ` · leu ${bytes(p.readBytes)} dos ${bytes(p.candidateBytes)} candidatos`
      : '';
    v.append(el('div', { class: 'hero', style: 'margin-bottom:6px' },
      el('div', {}, el('div', { class: 'big', style: 'font-size:clamp(36px,5vw,64px)' }, bytes(d.total)),
        el('p', { class: 'meta' }, `desperdiçados em ${plural(d.count, 'grupo', 'grupos')} de cópias · ${secs(p.elapsed)}${economy}`))),
      el('div', { class: 'toolbar' }, ruleSel,
        el('button', { class: 'btn ghost sm', type: 'button', onclick: () => {
          for (const g of d.groups) {
            const keep = keeper(g);
            for (const f of g.files) setSel('file', f.id, f.size, f.id !== keep);
          }
          RENDER.dups();
        } }, 'Selecionar as outras cópias'),
        el('button', { class: 'link', type: 'button', onclick: resetDups }, 'nova busca')));
    for (const g of d.groups) {
      const keep = keeper(g);
      v.append(el('div', { class: 'group' },
        el('div', { class: 'group-head' }, el('b', {}, `${bytes(g.size)} × ${g.files.length} cópias`), el('span', {}, `${bytes(g.wasted)} a mais`)),
        ...g.files.map(f => el('label', { class: 'gfile' },
          checkbox('file', f.id, f.size, f.path),
          el('span', { class: 'fpath' }, f.path),
          f.id === keep ? el('span', { class: 'keep' }, 'manter') : el('span', { class: 'dup-status', 'aria-hidden': 'true' }),
          el('span', { class: 'when' }, dateStr(f.mtime))))));
    }
    if (d.count > d.groups.length) v.append(el('p', { class: 'muted' }, `Mostrando os ${num(d.groups.length)} maiores grupos de ${num(d.count)}.`));
  };

  // ---------- limpeza ----------
  RENDER.cleanup = async () => {
    const v = head('cleanup', 'REVISÃO SEGURA', 'Limpeza sugerida', 'Tudo aqui vai para a quarentena antes de ser apagado, então dá para desfazer.');
    let c;
    try { c = await api('/api/cleanup'); } catch (e) { return fail(e); }
    const section = (title, sub, block, rowFn, cols, extra) => {
      const s = el('div', { class: 'section' },
        el('div', { class: 'section-head' }, el('h2', {}, title), el('span', { class: 'total' }, `${bytes(block.total)} · ${num(block.count)}`)),
        el('p', { class: 'sub' }, sub), extra || null);
      if (!block.items.length) { s.append(el('p', { class: 'empty' }, 'Nada encontrado aqui.')); return s; }
      s.append(el('div', { class: 'scroll' }, el('table', { class: 'table' },
        el('thead', {}, el('tr', {}, el('th', {}, ''), ...cols.map(([l, cls]) => el('th', { class: cls || '' }, l)))),
        el('tbody', {}, ...block.items.map(rowFn)))));
      return s;
    };
    const devRow = d => el('tr', { class: S.sel.has(key('dir', d.id)) ? 'is-selected' : '' },
      el('td', { class: 'check', 'data-label': 'Selecionar' }, checkbox('dir', d.id, d.size, d.path)),
      el('td', { 'data-label': 'Pasta' }, el('div', { class: 'fname' }, d.label, ' ', el('span', { class: 'chip', style: 'color:#A76F00' }, d.type)),
        el('div', { class: 'fpath' }, d.path), el('div', { class: 'fpath' }, 'para recriar: ', el('code', {}, d.regen))),
      el('td', { class: 'num', 'data-label': 'Último uso' }, d.idleDays == null ? '—' : ago(d.idleDays)),
      el('td', { class: 'num', 'data-label': 'Tamanho' }, bytes(d.size)));
    const fileRow = f => el('tr', { class: S.sel.has(key('file', f.id)) ? 'is-selected' : '' },
      el('td', { class: 'check', 'data-label': 'Selecionar' }, checkbox('file', f.id, f.size, f.name)),
      el('td', { 'data-label': 'Arquivo' }, el('div', { class: 'fname' }, f.name), el('div', { class: 'fpath' }, f.path)),
      el('td', { class: 'num', 'data-label': 'Alterado' }, ago(daysSince(f.mtime))),
      el('td', { class: 'num', 'data-label': 'Tamanho' }, bytes(f.size)));
    const idle = c.dev.items.filter(d => d.idleDays >= 90);
    const idleBtn = idle.length
      ? el('p', {}, el('button', { class: 'btn ghost sm', type: 'button', onclick: () => {
        for (const d of idle) setSel('dir', d.id, d.size, true);
        RENDER.cleanup();
      } }, `Selecionar as de projetos parados há mais de 3 meses (${bytes(idle.reduce((s, d) => s + d.size, 0))})`)) : null;
    v.append(
      section('Pastas de projetos que dá para recriar', 'node_modules, ambientes virtuais do Python, builds do Maven, Gradle e Cargo e caches. Um comando recria tudo quando você voltar ao projeto.',
        c.dev, devRow, [['Pasta'], ['Último uso do projeto', 'num'], ['Tamanho', 'num']], idleBtn),
      section('Instaladores antigos', 'Instaladores e imagens de disco parados há mais de 30 dias. Depois de instalar, eles costumam não servir mais.',
        c.installers, fileRow, [['Arquivo'], ['Alterado', 'num'], ['Tamanho', 'num']]),
      section('Temporários e downloads incompletos', 'Arquivos .tmp, downloads que não terminaram (.crdownload, .part) e caches de miniaturas.',
        c.temp, fileRow, [['Arquivo'], ['Alterado', 'num'], ['Tamanho', 'num']]),
      section('Grandes e parados há mais de 1 ano', 'Não são lixo necessariamente. Vale olhar com calma: às vezes é um backup que dá para mover para um HD externo.',
        c.oldBig, fileRow, [['Arquivo'], ['Alterado', 'num'], ['Tamanho', 'num']]));
  };

  // ---------- quarentena ----------
  RENDER.quarantine = async () => {
    const v = head('quarantine', 'ÁREA DE ESPERA', 'Quarentena', null);
    let q;
    try { q = await api('/api/quarantine'); } catch (e) { return fail(e); }
    v.append(el('p', { class: 'sub' }, 'Itens movidos pelo Faxina. Eles ', el('b', {}, 'ainda ocupam espaço'),
      ' até você esvaziar a quarentena. Cada item fica guardado no mesmo disco de onde veio; os do disco do sistema ficam em ', el('code', {}, q.dir), '.'));
    v.append(el('div', { class: 'hero', style: 'margin-bottom:18px' },
      el('div', {}, el('div', { class: 'big', style: 'font-size:clamp(36px,5vw,64px)' }, bytes(q.total)), el('p', { class: 'meta' }, plural(q.count, 'item', 'itens'))),
      el('button', { class: 'btn danger', type: 'button', disabled: q.count ? null : '', onclick: async () => {
        const ok = await modal({ title: 'Esvaziar a quarentena?',
          body: el('p', {}, `Isso apaga de vez ${plural(q.count, 'item', 'itens')} e libera ${bytes(q.total)}. Não dá para desfazer.`),
          actions: [{ label: 'Cancelar', value: 'cancel' }, { label: `Apagar ${bytes(q.total)}`, value: 'ok', class: 'danger' }] });
        if (ok !== 'ok') return;
        try {
          const r = await api('/api/quarantine/purge', { all: true });
          toast(`${bytes(r.freed)} liberados de verdade.`);
          if (r.failed.length) toast(`${plural(r.failed.length, 'item não pôde', 'itens não puderam')} ser apagado${r.failed.length > 1 ? 's' : ''}: ${r.failed[0].error}`, { error: true });
          refresh();
        } catch (e) { fail(e); }
      } }, 'Esvaziar quarentena')));
    if (!q.items.length) { v.append(el('p', { class: 'empty' }, 'A quarentena está vazia.')); return; }
    const restore = async it => {
      try {
        const r = await api('/api/quarantine/restore', { ids: [it.id] });
        if (r.restored.length) toast(`Restaurado em ${r.restored[0].path}`);
        if (r.failed.length) toast(r.failed[0].error, { error: true });
        refresh();
      } catch (e) { fail(e); }
    };
    const purgeOne = async it => {
      const ok = await modal({ title: 'Apagar de vez?', body: el('p', {}, `${it.original} (${bytes(it.size)}) será apagado permanentemente.`),
        actions: [{ label: 'Cancelar', value: 'cancel' }, { label: 'Apagar', value: 'ok', class: 'danger' }] });
      if (ok !== 'ok') return;
      try { await api('/api/quarantine/purge', { ids: [it.id] }); refresh(); } catch (e) { fail(e); }
    };
    const rows = q.items.map(it => el('tr', {},
      el('td', { 'data-label': 'Item' }, el('div', { class: 'fname' }, it.original.split(/[\\/]/).pop(), it.kind === 'dir' ? '/' : ''),
        el('div', { class: 'fpath' }, it.original),
        it.exists ? null : el('div', { class: 'fpath', style: 'color:#C83D31' }, 'arquivo sumiu da quarentena')),
      el('td', { class: 'num', 'data-label': 'Movido em' }, dateTime(it.moved_at)),
      el('td', { class: 'num', 'data-label': 'Tamanho' }, bytes(it.size)),
      el('td', { class: 'act', 'data-label': 'Ações' },
        el('button', { class: 'iconbtn', type: 'button', onclick: () => restore(it) }, 'restaurar'), ' ',
        el('button', { class: 'iconbtn', type: 'button', onclick: () => purgeOne(it) }, 'apagar'))));
    v.append(el('div', { class: 'scroll' }, el('table', { class: 'table' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Item'), el('th', { class: 'num' }, 'Movido em'), el('th', { class: 'num' }, 'Tamanho'), el('th', {}, ''))),
      el('tbody', {}, ...rows))));
  };

  // ---------- histórico ----------
  RENDER.history = async () => {
    const v = head('history', 'EVOLUÇÃO DO ESPAÇO', 'Histórico desta pasta', 'Cada análise fica registrada. Assim dá para ver se a pasta está crescendo e onde.');
    let h;
    try { h = await api(`/api/history?root=${encodeURIComponent(S.overview.root)}`); } catch (e) { return fail(e); }
    if (h.items.length < 2) v.append(el('p', { class: 'note' }, 'Analise esta pasta de novo daqui a alguns dias para comparar o crescimento.'));
    const rows = h.items.map((it, i) => {
      const prev = h.items[i + 1];
      const diff = prev ? it.total_size - prev.total_size : null;
      return el('tr', {},
        el('td', { 'data-label': 'Data' }, dateTime(it.finished_at)),
        el('td', { class: 'num', 'data-label': 'Arquivos' }, num(it.files)),
        el('td', { class: 'num', 'data-label': 'Tamanho' }, bytes(it.total_size)),
        el('td', { class: `num ${diff > 0 ? 'delta-up' : diff < 0 ? 'delta-down' : ''}`, 'data-label': 'Variação' }, diff == null ? '—' : `${diff > 0 ? '+' : ''}${bytes(diff)}`));
    });
    v.append(el('div', { class: 'scroll' }, el('table', { class: 'table' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Data'), el('th', { class: 'num' }, 'Arquivos'), el('th', { class: 'num' }, 'Tamanho'), el('th', { class: 'num' }, 'Variação'))),
      el('tbody', {}, ...rows))));
  };

  // ---------- eventos ----------
  function bind() {
    $('#path-form').addEventListener('submit', e => { e.preventDefault(); const p = $('#path-input').value.trim(); if (p) startScan(p); });
    $('#browse-btn').addEventListener('click', () => openBrowser($('#path-input').value.trim()));
    $('#scan-cancel').addEventListener('click', () => api('/api/scan/cancel', {}).catch(fail));
    $$('.nav button').forEach(b => b.addEventListener('click', () => go(b.dataset.view)));
    $('#rescan-btn').addEventListener('click', () => startScan(S.overview.root));
    $('#other-btn').addEventListener('click', () => renderHome());
    $$('[data-quit]').forEach(b => b.addEventListener('click', quit));
    $('#sel-clear').addEventListener('click', () => { S.sel.clear(); updateSel(); RENDER[S.view](); });
    $('#sel-move').addEventListener('click', moveSelected);
    let resizeTimer;
    window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (S.view === 'map') drawTreemap(); }, 120); });
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape' || S.view !== 'map' || $('#screen-app').hidden || $('#modal').open) return;
      if (S.tree && S.tree.crumbs.length > 1) enterDir(S.tree.crumbs[S.tree.crumbs.length - 2].id);
    });
  }

  async function init() {
    bind();
    try {
      const st = await api('/api/status');
      if (st.scan && st.scan.state === 'running') watchScan();
      else if (st.scan && st.scan.state === 'done') enterApp();
      else renderHome();
    } catch (e) {
      show('home');
      fail(e);
    }
  }
  init();
})();
