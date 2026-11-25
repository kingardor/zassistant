/* Z Assistant · browser UI
   Talks to server.py: GET /api/status, POST /api/chat (SSE), /api/reset, /api/quality. */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const el = {
    boot: $('boot'), bootTitle: $('boot-title'), bootSub: $('boot-sub'), bootRoute: $('boot-route'),
    routeHostLabel: $('route-host-label'), routeHostIcon: $('route-host-icon'),
    stages: $('stages'), bootElapsed: $('boot-elapsed'), bootHint: $('boot-hint'),
    app: $('app'), demoBadge: $('demo-badge'), brand: $('brand'),
    pill: $('pill'), pillIcon: $('pill-icon'), pillTitle: $('pill-title'), pillSub: $('pill-sub'),
    popover: $('popover'), popTitle: $('pop-title'), popBody: $('pop-body'), popRoute: $('pop-route'),
    popFacts: $('pop-facts'), popSession: $('pop-session'), popSessionLeft: $('pop-session-left'),
    popMeter: $('pop-meter'), popNote: $('pop-note'), qualityBtn: $('quality-btn'),
    themeBtn: $('theme-btn'), themeIcon: $('theme-icon'), newBtn: $('new-btn'),
    scroll: $('scroll'), hero: $('hero'), thread: $('thread'),
    composer: $('composer'), input: $('input'), send: $('send'), sendIcon: $('send-icon'),
    toasts: $('toasts'),
  };

  const state = {
    status: null,
    clockOffset: 0,      // server time - client time, in seconds
    ready: false,
    busy: false,
    controller: null,
  };

  const STAGE_COPY = {
    session: { pending: 'Request a Boost session', running: 'Requesting a Boost session', done: 'Boost session granted' },
    ollama: {
      boost: { pending: 'Start Ollama on the remote GPU', running: 'Starting Ollama on the remote GPU', done: 'Ollama running on the remote GPU' },
      local: { pending: 'Start Ollama', running: 'Starting Ollama', done: 'Ollama running on this GPU' },
    },
    model: { pending: 'Load the language model', running: 'Loading the language model', done: 'Language model loaded' },
    index: { pending: 'Index Z by HP documents', running: 'Indexing Z by HP documents', done: 'Z by HP documents indexed' },
  };

  /* ---------------- helpers ---------------- */
  const icon = (name, cls) => `<svg class="i${cls ? ' ' + cls : ''}" aria-hidden="true"><use href="icons.svg#${name}"/></svg>`;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const now = () => Date.now() / 1000 + state.clockOffset;
  const clock = (secs) => {
    secs = Math.max(0, Math.floor(secs));
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
  };
  const seconds = (ms) => (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + ' s';
  const shortId = (id) => (id && id.length > 14 ? id.slice(0, 8) + '…' + id.slice(-4) : id || '—');

  function toast(message, kind) {
    const t = document.createElement('div');
    t.className = 'toast' + (kind === 'error' ? ' toast--err' : '');
    t.innerHTML = icon(kind === 'error' ? 'alert' : 'check') + '<span></span>';
    t.querySelector('span').textContent = message;
    el.toasts.appendChild(t);
    setTimeout(() => t.remove(), 4200);
  }

  /* Tiny, safe markdown: paragraphs, bullet/numbered lists, **bold**, *italic*, `code`. */
  function inline(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
  }
  function markdown(src) {
    const out = [];
    for (const block of src.trim().split(/\n{2,}/)) {
      let list = null, para = [];
      const flushPara = () => { if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; } };
      const flushList = () => { if (list) { out.push(`<${list.tag}>` + list.items.map((i) => '<li>' + inline(i) + '</li>').join('') + `</${list.tag}>`); list = null; } };
      for (const line of block.split('\n')) {
        const ul = line.match(/^\s*[-*•]\s+(.*)$/), ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
        const m = ul || ol;
        if (m) {
          flushPara();
          const tag = ul ? 'ul' : 'ol';
          if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
          list.items.push(m[1]);
        } else if (line.trim()) {
          flushList();
          para.push(line.trim());
        }
      }
      flushPara(); flushList();
    }
    return out.join('') || '<p></p>';
  }

  /* ---------------- theme ---------------- */
  function applyThemeButton() {
    const dark = document.documentElement.dataset.theme === 'dark';
    el.themeIcon.setAttribute('href', 'icons.svg#' + (dark ? 'sun' : 'moon'));
    el.themeBtn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    el.themeBtn.title = dark ? 'Light theme' : 'Dark theme';
  }
  el.themeBtn.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('z-theme', next);
    applyThemeButton();
  });
  // Follow the OS theme until the user picks one explicitly.
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    if (localStorage.getItem('z-theme')) return;
    document.documentElement.dataset.theme = e.matches ? 'dark' : 'light';
    applyThemeButton();
  });
  applyThemeButton();

  /* ---------------- status ---------------- */
  async function poll() {
    const next = await refreshStatus();
    setTimeout(poll, next);
  }

  // Fetch status once and re-render; returns the delay before the next poll.
  async function refreshStatus() {
    let next = state.ready ? 5000 : 1000;
    try {
      const res = await fetch('/api/status', { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const s = await res.json();
      state.clockOffset = s.now - Date.now() / 1000;
      state.status = s;
    } catch (err) {
      state.status = Object.assign({}, state.status || {}, { state: 'offline' });
      next = 2000;
    }
    render();
    return next;
  }

  function sessionLeft(s) {
    if (!s || s.mode !== 'boost' || !s.session || !s.session.started || !s.session.timeout) return null;
    return s.session.started + s.session.timeout - now();
  }

  function render() {
    const s = state.status;
    if (!s) return;
    const mode = s.mode || 'local';
    el.demoBadge.hidden = !s.demo;
    renderBoot(s, mode);
    renderPill(s, mode);
    if (!el.popover.hidden) renderPopover(s, mode);

    if (s.state === 'ready' && !state.ready) {
      state.ready = true;
      el.boot.classList.add('is-done');
      el.boot.setAttribute('aria-hidden', 'true');
      el.app.inert = false;
      el.hero.querySelector('#hero-sub').textContent = mode === 'boost'
        ? 'Tell Z what you create. It answers from Z by HP product documents, running on a Boosted GPU.'
        : 'Tell Z what you create. It answers from Z by HP product documents.';
      updateComposer();
      el.input.focus();
    }
  }

  function renderBoot(s, mode) {
    if (state.ready) return;
    const boost = mode === 'boost';
    el.bootRoute.dataset.mode = mode;
    el.bootRoute.dataset.state = s.state;
    el.routeHostLabel.textContent = boost ? 'Z host GPU' : 'Local GPU';
    el.routeHostIcon.setAttribute('href', 'icons.svg#' + (boost ? 'server' : 'cpu'));

    if (s.state === 'offline') {
      el.bootTitle.textContent = "Can't reach Z Assistant";
      el.bootSub.textContent = 'The server is not responding. Retrying…';
    } else if (s.state === 'error') {
      el.bootTitle.textContent = "Z Assistant couldn't start";
      el.bootSub.textContent = s.error || 'Something went wrong during startup.';
    } else {
      el.bootTitle.textContent = boost ? 'Boosting Z Assistant' : 'Starting Z Assistant';
      el.bootSub.textContent = boost
        ? 'Connecting to a Z workstation GPU on your network. The first start can take a few minutes.'
        : "Loading the model on this machine's GPU. The first start can take a minute.";
    }

    if (s.stages) {
      const rows = s.stages.filter((st) => st.state !== 'skipped').map((st) => {
        const copy = st.id === 'ollama' ? STAGE_COPY.ollama[boost ? 'boost' : 'local'] : STAGE_COPY[st.id];
        const label = (copy && copy[st.state === 'error' ? 'running' : st.state]) || st.id;
        const ic = st.state === 'done' ? icon('check') : st.state === 'running' ? icon('loader') : st.state === 'error' ? icon('x') : '';
        // Running timers are filled in by tickStageTimers(), so this markup stays stable between ticks.
        let t = '—';
        if (st.state === 'done' && st.ms != null) t = seconds(st.ms);
        else if (st.state === 'running') t = st.started ? `<span data-started="${st.started}"></span>` : '…';
        else if (st.state === 'error') t = 'failed';
        const detail = st.state === 'error' ? (s.error || 'Failed') : st.detail;
        return `<li class="stage" data-state="${esc(st.state)}">
            <span class="stage-ic">${ic}</span>
            <div><b>${esc(label)}</b>${detail ? `<small>${esc(detail)}</small>` : ''}${st.state === 'running' ? '<div class="stage-bar"><i></i></div>' : ''}</div>
            <span class="stage-t">${t}</span>
          </li>`;
      });
      const allDone = s.state === 'ready';
      rows.push(`<li class="stage" data-state="${allDone ? 'done' : 'pending'}"><span class="stage-ic">${allDone ? icon('check') : ''}</span><div><b>Ready to chat</b></div><span class="stage-t">—</span></li>`);
      const html = rows.join('');
      if (html !== state.stagesHtml) { el.stages.innerHTML = html; state.stagesHtml = html; }
      tickStageTimers();
    }

    if (s.booted_at) el.bootElapsed.textContent = 'Elapsed ' + clock(now() - s.booted_at);
    el.bootHint.textContent = s.state === 'error' ? 'Check the container logs, then restart.' : '';
  }

  function tickStageTimers() {
    el.stages.querySelectorAll('[data-started]').forEach((n) => { n.textContent = clock(now() - Number(n.dataset.started)); });
  }

  function renderPill(s, mode) {
    const p = el.pill;
    p.dataset.mode = mode;
    p.dataset.state = s.state;
    let ic = mode === 'boost' ? 'zap' : 'cpu', title, sub = '';
    if (s.state === 'offline') { ic = 'alert'; title = 'Offline'; sub = 'Reconnecting…'; }
    else if (s.state === 'error') { ic = 'alert'; title = 'Startup failed'; }
    else if (s.state === 'booting') { ic = 'loader'; title = mode === 'boost' ? 'Boosting…' : 'Starting…'; }
    else if (mode === 'boost') {
      title = 'Boosted';
      const left = sessionLeft(s);
      sub = 'Remote Z GPU' + (left == null ? '' : left > 0 ? ' · ' + Math.ceil(left / 60) + ' min left' : ' · session ended');
    } else { title = 'Local GPU'; sub = s.model || ''; }
    el.pillIcon.setAttribute('href', 'icons.svg#' + ic);
    el.pillTitle.textContent = title;
    el.pillSub.textContent = sub;
    p.setAttribute('aria-label', 'Compute: ' + title + (sub ? ', ' + sub : '') + '. Show details');
  }

  function renderPopover(s, mode) {
    const boost = mode === 'boost';
    el.popTitle.textContent = boost ? 'Running on Z by HP Boost' : 'Running on the local GPU';
    el.popBody.textContent = boost
      ? "The language model runs on an idle GPU borrowed from a Z workstation on your network. This device runs the interface and the document search."
      : "The language model runs on this machine's GPU.";
    el.popRoute.dataset.mode = mode;
    el.popRoute.hidden = !boost;

    const docs = s.docs || [];
    const pages = docs.reduce((n, d) => n + (d.pages || 0), 0);
    const stats = s.stats || {};
    const facts = [];
    if (boost) facts.push(['GPU', `<span class="mono" title="${esc(s.gpu_id)}">${esc(shortId(s.gpu_id))}</span>`]);
    facts.push(['Model', esc(s.model || '—')]);
    facts.push(['Knowledge', docs.length ? `${docs.length} documents · ${pages} pages` : '—']);
    facts.push(['Answers', String(stats.answers || 0)]);
    facts.push(['Avg answer', stats.avg_ms != null ? seconds(stats.avg_ms) : '—']);
    el.popFacts.innerHTML = facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');

    const left = sessionLeft(s);
    el.popSession.hidden = left == null;
    if (left != null) {
      const pct = Math.max(0, Math.min(100, (left / s.session.timeout) * 100));
      el.popSessionLeft.textContent = left > 0 ? clock(left) + ' left' : 'Ended';
      el.popMeter.querySelector('i').style.width = pct + '%';
      el.popMeter.setAttribute('aria-valuenow', String(Math.round(pct)));
      el.popMeter.classList.toggle('low', pct < 15);
    }
    el.popNote.innerHTML = boost
      ? 'To use this machine\'s GPU instead, set <code>BOOST</code> to <code>0</code> in docker-compose.yml and restart.'
      : 'Have a Z workstation on your network? Set <code>BOOST</code> to <code>1</code> in docker-compose.yml to borrow its GPU.';
    el.qualityBtn.disabled = s.state !== 'ready';
  }

  // Keep countdowns and elapsed timers ticking between polls.
  setInterval(() => {
    if (!state.status) return;
    const mode = state.status.mode || 'local';
    renderPill(state.status, mode);
    if (!state.ready) renderBoot(state.status, mode);
    if (!el.popover.hidden) renderPopover(state.status, mode);
  }, 1000);

  /* ---------------- popover ---------------- */
  function setPopover(open) {
    el.popover.hidden = !open;
    el.pill.setAttribute('aria-expanded', String(open));
    if (open && state.status) renderPopover(state.status, state.status.mode || 'local');
  }
  el.pill.addEventListener('click', (e) => { e.stopPropagation(); setPopover(el.popover.hidden); });
  el.popover.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => setPopover(false));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !el.popover.hidden) { setPopover(false); el.pill.focus(); }
    if (e.key === '/' && document.activeElement !== el.input && !e.metaKey && !e.ctrlKey && state.ready) {
      e.preventDefault(); el.input.focus();
    }
  });

  el.qualityBtn.addEventListener('click', async () => {
    el.qualityBtn.disabled = true;
    const label = el.qualityBtn.innerHTML;
    el.qualityBtn.innerHTML = icon('loader') + 'Running…';
    try {
      const res = await fetch('/api/quality', { method: 'POST' });
      const out = await res.json();
      toast(out.message || 'Done', out.ok ? 'ok' : 'error');
    } catch (err) {
      toast('Quality check failed: ' + err.message, 'error');
    } finally {
      el.qualityBtn.innerHTML = label;
      el.qualityBtn.disabled = !state.ready;
    }
  });

  /* ---------------- conversation ---------------- */
  function nearBottom() {
    const s = el.scroll;
    return s.scrollHeight - s.scrollTop - s.clientHeight < 120;
  }
  function scrollToEnd(force) {
    if (force || nearBottom()) el.scroll.scrollTop = el.scroll.scrollHeight;
  }

  function updateComposer() {
    const empty = !el.input.value.trim();
    el.send.dataset.busy = String(state.busy);
    el.sendIcon.setAttribute('href', 'icons.svg#' + (state.busy ? 'stop' : 'send'));
    el.send.setAttribute('aria-label', state.busy ? 'Stop answer' : 'Send');
    el.send.disabled = !state.ready || (!state.busy && empty);
    document.querySelectorAll('.intent').forEach((b) => { b.disabled = state.busy || !state.ready; });
  }

  function autosize() {
    el.input.style.height = 'auto';
    el.input.style.height = Math.min(el.input.scrollHeight, 180) + 'px';
  }

  function addUser(text) {
    const div = document.createElement('div');
    div.className = 'msg-user';
    div.textContent = text;
    el.thread.appendChild(div);
  }

  function addAnswerCard() {
    const card = document.createElement('article');
    card.className = 'msg-z';
    card.setAttribute('aria-busy', 'true');
    card.innerHTML = `<div class="msg-head"><span class="logo-mark" aria-hidden="true">Z</span><b>Z</b></div>
      <div class="thinking"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Searching Z by HP documents…</span></div>
      <div class="answer" hidden></div>`;
    el.thread.appendChild(card);
    return card;
  }

  function finishCard(card, meta) {
    card.setAttribute('aria-busy', 'false');
    card.querySelector('.thinking')?.remove();
    const chips = [];
    if (meta.stopped) chips.push(`<span class="chip">${icon('stop')}Stopped</span>`);
    if (meta.ms != null) {
      const boost = meta.compute === 'boost';
      chips.push(`<span class="chip${boost ? ' chip--boost' : ''}">${icon(boost ? 'zap' : 'cpu')}${boost ? 'Boost GPU' : 'Local GPU'} · ${seconds(meta.ms)}</span>`);
    }
    for (const src of meta.sources || []) {
      chips.push(`<span class="chip chip--src" title="Source document">${icon('file')}${esc(src.doc)}${src.page ? ' · p.' + src.page : ''}</span>`);
    }
    if (chips.length) {
      const foot = document.createElement('div');
      foot.className = 'msg-foot';
      foot.innerHTML = chips.join('');
      card.appendChild(foot);
    }
  }

  function failCard(card, message, retryText) {
    card.setAttribute('aria-busy', 'false');
    card.querySelector('.thinking')?.remove();
    const err = document.createElement('div');
    err.className = 'msg-error';
    err.innerHTML = icon('alert') + '<span></span><button class="btn" type="button">' + icon('reset') + 'Retry</button>';
    err.querySelector('span').textContent = message;
    err.querySelector('button').addEventListener('click', () => {
      card.previousElementSibling?.classList.contains('msg-user') && card.previousElementSibling.remove();
      card.remove();
      ask(retryText);
    });
    card.appendChild(err);
  }

  async function ask(text) {
    text = text.trim();
    if (!text || state.busy || !state.ready) return;
    state.busy = true;
    updateComposer();
    addUser(text);
    const card = addAnswerCard();
    scrollToEnd(true);

    const answerEl = card.querySelector('.answer');
    const meta = { sources: [] };
    let answer = '';
    state.controller = new AbortController();

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
        signal: state.controller.signal,
      });
      if (!res.ok || !res.body) throw new Error('The server returned ' + res.status);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '', ended = false;
      while (!ended) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          const data = frame.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trim()).join('');
          if (!data) continue;
          const ev = JSON.parse(data);
          if (ev.type === 'sources') {
            meta.sources = ev.items || [];
            card.querySelector('.thinking span:last-child').textContent = 'Writing an answer…';
          } else if (ev.type === 'token') {
            if (!answer) { card.querySelector('.thinking')?.remove(); answerEl.hidden = false; }
            answer += ev.text;
            answerEl.innerHTML = markdown(answer);
            scrollToEnd();
          } else if (ev.type === 'done') {
            meta.ms = ev.ms; meta.compute = ev.compute;
            ended = true;
          } else if (ev.type === 'error') {
            throw new Error(ev.message || 'Something went wrong');
          }
        }
      }
      if (!answer) { answerEl.hidden = false; answerEl.innerHTML = '<p class="muted">No answer came back.</p>'; }
      finishCard(card, meta);
      refreshStatus(); // pick up the new answer count / average right away
    } catch (err) {
      if (err.name === 'AbortError') {
        if (!answer) { answerEl.hidden = false; answerEl.innerHTML = '<p class="muted">Stopped before Z answered.</p>'; }
        meta.stopped = true;
        finishCard(card, meta);
      } else {
        failCard(card, err.message, text);
      }
    } finally {
      state.busy = false;
      state.controller = null;
      updateComposer();
      scrollToEnd();
      if (document.activeElement === document.body) el.input.focus();
    }
  }

  el.composer.addEventListener('submit', (e) => {
    e.preventDefault();
    if (state.busy) { state.controller?.abort(); return; }
    const text = el.input.value;
    el.input.value = '';
    autosize();
    if (text.trim() === '/new') { newChat(); return; }
    if (text.trim() === '/quality') { setPopover(false); el.qualityBtn.click(); return; }
    ask(text);
  });
  el.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      el.composer.requestSubmit();
    }
  });
  el.input.addEventListener('input', () => { autosize(); updateComposer(); });

  document.querySelectorAll('.intent').forEach((b) => b.addEventListener('click', () => ask(b.dataset.prompt)));

  async function newChat() {
    if (!state.ready) return;
    state.controller?.abort();
    try {
      await fetch('/api/reset', { method: 'POST' });
    } catch (err) {
      toast("Couldn't reset the conversation: " + err.message, 'error');
      return;
    }
    el.thread.innerHTML = '';
    el.scroll.scrollTop = 0;
    toast('Started a new conversation');
    el.input.focus();
  }
  el.newBtn.addEventListener('click', newChat);
  el.brand.addEventListener('click', (e) => { e.preventDefault(); if (el.thread.children.length) newChat(); });

  updateComposer();

  // Demo server only: ?boot replays the startup screen, ?boot=local shows the local-GPU variant.
  const params = new URLSearchParams(location.search);
  if (params.has('boot')) {
    const mode = params.get('boot');
    fetch('/api/demo/reboot' + (mode === 'local' || mode === 'boost' ? '?mode=' + mode : ''), { method: 'POST' })
      .catch(() => {}).finally(poll);
  } else {
    poll();
  }
})();
