"""Local visual rendering; untrusted diagrams execute only in opaque sandbox frames."""


def render_chat_visuals_fragment() -> str:
    return r'''
<style>
.vool-visual { width:100%; min-width:0; min-height:160px; margin:8px 0 0; }
.vool-visual[data-state="error"] { min-height:0; }
.vool-visual[data-state="ready"] { min-height:0; }
.vool-visual iframe { display:block; width:100%; height:300px; border:0; border-radius:10px; background:transparent; }
.vool-visual-status { color:var(--muted); font-size:12px; line-height:1.5; }
.vool-visual[data-state="error"] .vool-visual-status { color:var(--warn,#fbbf24); }
.vool-visual-source { position:relative; max-width:100%; overflow:auto; margin:0 0 6px; }
.vool-visual-source[open] > .code-copy { top:30px; }
.vool-visual-source summary { cursor:pointer; color:var(--muted); font-size:12px; padding:3px 0; }
</style>
<script>
(function () {
  'use strict';
  const seen = new WeakSet(), frames = new Map(), watched = new Set(), queue = [];
  let active = 0;
  const palette = ['#3bbfa9', '#8b8cf5', '#e89a59', '#dd7393', '#74a6e8', '#a0ab72'];
  function keys(value, allowed) {
    if (!value || typeof value !== 'object' || Array.isArray(value) ||
        Object.keys(value).some(k => !allowed.includes(k))) throw Error('Unsupported chart fields');
  }
  function label(value) {
    if (typeof value !== 'string' || value.length > 240) throw Error('Invalid chart label');
    return value;
  }
  function color(value) {
    if (typeof value !== 'string' || !/^#[0-9a-f]{6}$/i.test(value)) throw Error('Invalid chart color');
    return value;
  }
  function chartSpec(text) {
    const spec = JSON.parse(text);
    keys(spec, ['type', 'data', 'title', 'background', 'axis']);
    if (!['bar','line','pie','doughnut'].includes(spec.type)) throw Error('Unsupported chart type');
    if (spec.axis !== undefined && (spec.type !== 'bar' || !['x','y'].includes(spec.axis))) throw Error('Invalid chart axis');
    keys(spec.data, ['labels','datasets']);
    const labels = spec.data.labels;
    if (!Array.isArray(labels) || !labels.length || labels.length > 200) throw Error('Invalid chart labels');
    labels.forEach(label);
    if (!Array.isArray(spec.data.datasets) || !spec.data.datasets.length || spec.data.datasets.length > 8)
      throw Error('Invalid chart datasets');
    const datasets = spec.data.datasets.map((d, i) => {
      keys(d, ['label','data','color']);
      if (!Array.isArray(d.data) || d.data.length !== labels.length ||
          d.data.some(v => typeof v !== 'number' || !Number.isFinite(v))) throw Error('Invalid chart values');
      if(d.data.some(v=>Math.abs(v)>Number.MAX_SAFE_INTEGER)) throw Error('Chart values exceed safe browser precision');
      if (['pie','doughnut'].includes(spec.type) && (d.data.some(v=>v<0) || !d.data.some(v=>v>0)))
        throw Error('Pie values must include a positive amount and no negatives');
      const c = d.color === undefined ? palette[i % palette.length] : color(d.color);
      return {label: d.label === undefined ? '' : label(d.label), data: d.data,
        backgroundColor: ['pie','doughnut'].includes(spec.type) ? labels.map((_,j)=>palette[j%palette.length]) : c,
        borderColor: c, borderWidth: spec.type === 'line' ? 2 : 0, borderRadius: spec.type === 'bar' ? 5 : 0, maxBarThickness:72};
    });
    return {type:spec.type, axis:spec.axis || 'x', data:{labels,datasets}, title:spec.title === undefined ? '' : label(spec.title),
      background: spec.background === undefined ? null : color(spec.background)};
  }
  // This trusted bootstrap is serialized into the sandbox, never evaluated in the app realm.
  async function draw(kind, input, theme) {
    const send = state => {
      // Measure rendered content, not the viewport's scrollHeight: the latter
      // retains a previous frame height in WKWebView and cannot shrink with it.
      const bottom = Math.max(0, ...Array.from(document.body.children)
        .filter(el => !['SCRIPT','STYLE'].includes(el.tagName))
        .map(el => el.getBoundingClientRect().bottom + window.scrollY));
      parent.postMessage({voolVisual:1, state,
        height:Math.min(1600, Math.max(32, Math.ceil(bottom + 8)))}, '*');
    };
    try {
      document.body.style.background = theme.background;
      document.body.style.color = theme.ink;
      if (kind === 'chart') {
        if (input.background) {
          document.body.style.background = input.background;
          const rgb = input.background.slice(1).match(/../g).map(v=>parseInt(v,16));
          theme.ink = rgb[0]*.299 + rgb[1]*.587 + rgb[2]*.114 > 150 ? '#242932' : '#e8eaf0';
          theme.grid = theme.ink + '26';
          document.body.style.color = theme.ink;
        }
        Chart.defaults.color = theme.ink;
        Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, system-ui, sans-serif';
        Chart.defaults.font.size = 13;
        const chart = document.createElement('div'); chart.style.height = 'clamp(220px, 42vw, 280px)';
        const canvas = document.createElement('canvas'); chart.append(canvas); document.body.append(chart);
        const {title, background, axis, ...config} = input;
        new Chart(canvas, {...config, options:{responsive:true, maintainAspectRatio:false, animation:false,
          indexAxis:axis,
          plugins:{title:{display:!!title,text:title,align:'start',font:{size:15},padding:{bottom:18}},
            legend:{position:'bottom',labels:{boxWidth:10,boxHeight:10,padding:16}}},
          scales:['bar','line'].includes(config.type) ? {
            x:{beginAtZero:axis==='y',grid:{color:theme.grid,display:axis==='y'},border:{display:false},ticks:{padding:8}},
            y:{beginAtZero:axis!=='y',grid:{color:theme.grid,display:axis!=='y'},border:{display:false},ticks:{padding:8}}} : {}}});
        const details = document.createElement('details'), summary = document.createElement('summary');
        summary.textContent = 'View data'; details.append(summary);
        const table = document.createElement('table');
        function row(values, heading) {
          const tr = document.createElement('tr');
          values.forEach(v => {const cell=document.createElement(heading?'th':'td'); cell.textContent=String(v); tr.append(cell);});
          table.append(tr);
        }
        row(['', ...input.data.datasets.map(d=>d.label)], true);
        input.data.labels.forEach((l,i)=>row([l,...input.data.datasets.map(d=>d.data[i])],false));
        details.append(table); document.body.append(details);
        details.addEventListener('toggle',()=>send('ready'));
      } else {
        // A wide flowchart squeezed into a narrow frame scales its text to ~8px. Below 520px
        // the diagram keeps its natural size and the frame scrolls sideways instead: labels
        // stay readable, nothing is clipped, and the source remains available verbatim.
        const naturalSize = window.innerWidth <= 520;
        mermaid.initialize({startOnLoad:false, securityLevel:'strict', suppressErrorRendering:true,
          maxTextSize:20000, maxEdges:200, flowchart:{htmlLabels:false,curve:'linear',padding:16,useMaxWidth:!naturalSize}, theme:'base',
          themeVariables:{background:theme.background,primaryColor:theme.panel,primaryTextColor:theme.ink,
            primaryBorderColor:theme.accent,lineColor:theme.muted,secondaryColor:theme.panel,tertiaryColor:theme.panel,
            textColor:theme.ink,fontFamily:'-apple-system, BlinkMacSystemFont, system-ui, sans-serif',fontSize:'15px'}});
        const result = await mermaid.render('diagram', input);
        const host = document.createElement('div'); host.innerHTML = result.svg; document.body.append(host);
        // Diagram links never become navigation or app commands, even inside this sandbox.
        host.querySelectorAll('a').forEach(a=>a.replaceWith(...a.childNodes));
      }
      send('ready');
      new ResizeObserver(()=>send('ready')).observe(document.body);
    } catch (_) { send('error'); }
  }
  function frameHTML(kind, input) {
    const file = kind === 'chart' ? 'chart-4.5.1.min.js' : 'mermaid-12.0.0.min.js';
    const url = new URL('/chat-assets/' + file, location.href).href;
    const nonce = Array.from(crypto.getRandomValues(new Uint32Array(4)), v=>v.toString(16)).join('');
    const encoded = JSON.stringify(input).replace(/</g,'\\u003c');
    const style = getComputedStyle(document.documentElement);
    const token = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    const theme = {background:token('--chat','#ffffff'),panel:token('--panel','#f4f6f8'),
      ink:token('--ink','#242932'),muted:token('--muted','#667085'),
      accent:token('--accent','#167d9a'),grid:token('--border','#e3e7ed')};
    const encodedTheme = JSON.stringify(theme).replace(/</g,'\\u003c');
    const csp = "default-src 'none'; script-src " + url + " 'nonce-"+nonce+"'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'";
    return '<!doctype html><meta http-equiv="Content-Security-Policy" content="'+csp+'">' +
      '<style>body{margin:0;padding:12px 8px 4px;font:13px/1.5 system-ui;color:#222}svg{max-width:100%;height:auto}@media (max-width:520px){body{overflow-x:auto}svg{max-width:none}}summary{cursor:pointer;opacity:.75;font-size:12px;padding:4px 0}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}td,th{padding:6px;text-align:left;border-bottom:1px solid currentColor}canvas{max-width:100%}</style>' +
      '<body><scr'+'ipt src="'+url+'"></scr'+'ipt><scr'+'ipt nonce="'+nonce+'">('+draw.toString()+')('+JSON.stringify(kind)+','+encoded+','+encodedTheme+');</scr'+'ipt>';
  }
  function pump() {
    while (active < 2 && queue.length) {
      const job = queue.shift();
      job.queued = false;
      if (!job.box.isConnected || !job.visible || job.frame) continue;
      active++;
      const frame = document.createElement('iframe');
      frame.title = job.kind === 'chart' ? 'Chart' : 'Diagram';
      frame.setAttribute('sandbox', 'allow-scripts');
      frame.setAttribute('referrerpolicy','no-referrer');
      job.box.append(frame);
      job.frame = frame;
      let released = false;
      job.release = () => {if (!released) {released=true; active--; clearTimeout(job.timer); pump();}};
      job.fail = () => {
        job.box.dataset.state = 'error'; job.status.textContent = 'Visual unavailable; source retained.';
        frames.delete(frame.contentWindow); frame.remove(); job.frame=null; job.release();
      };
      frames.set(frame.contentWindow,job);
      job.timer = setTimeout(job.fail,12000);
      frame.srcdoc = frameHTML(job.kind, job.input);
    }
  }
  function hydrate(root) {
    const blocks = root.matches && root.matches('.chat-code-wrap') ? [root] : [];
    if (root.querySelectorAll) blocks.push(...root.querySelectorAll('.chat-code-wrap'));
    blocks.forEach(block => {
      if (seen.has(block)) return;
      const code = block.querySelector('code[class~="lang-chart" i], code[class~="lang-mermaid" i]');
      if (!code) return;
      seen.add(block);
      const kind = Array.from(code.classList).some(c=>c.toLowerCase()==='lang-chart') ? 'chart' : 'mermaid';
      const box = document.createElement('div'); box.className='vool-visual'; box.dataset.state='loading';
      const status = document.createElement('div'); status.className='vool-visual-status'; status.textContent='Rendering visual...';
      box.append(status); block.prepend(box);
      try {
        const text = decodeURIComponent(block.dataset.rawCode);
        if (text.length > 20000) throw Error('Visual exceeds rendering limit');
        if (kind === 'mermaid' && (/%%\s*\{/.test(text) || /^\s*---/.test(text))) throw Error('Diagram configuration is not allowed');
        const input = kind === 'chart' ? chartSpec(text) : text;
        const job={box,status,kind,input,block}; watched.add(job); visibility.observe(box);
      } catch (e) {box.dataset.state='error'; status.textContent=e.message + '; source retained.';}
    });
  }
  window.addEventListener('message', event => {
    const job = frames.get(event.source), data = event.data;
    if (!job || !data || data.voolVisual !== 1) return;
    if (data.state === 'error') {job.fail(); return;}
    if (data.state !== 'ready' || !Number.isFinite(data.height)) return;
    job.frame.style.height = Math.min(1600,Math.max(32,data.height))+'px';
    job.box.style.minHeight = '';
    job.box.dataset.state='ready'; job.status.textContent='';
    if (!job.details) {
      job.details = document.createElement('details'); job.details.className='vool-visual-source';
      const summary=document.createElement('summary'); summary.textContent='View source'; job.details.append(summary);
      const copy=job.block.querySelector('.code-copy'); if(copy) job.details.append(copy);
      const pre=job.block.querySelector('pre.chat-code'); if(pre) job.details.append(pre);
      job.block.append(job.details);
    }
    job.release();
  });
  // Keep heavy library realms only near the viewport; long chat history must not retain
  // one Mermaid runtime per historical answer. The original source stays in the message.
  const visibility = new IntersectionObserver(entries => {
    for(const entry of entries) {
      const job=Array.from(watched).find(j=>j.box===entry.target);
      if(!job || job.box.dataset.state==='error') continue;
      job.visible=entry.isIntersecting;
      if(job.visible && !job.frame && !job.queued) {
        job.queued=true; queue.push(job);
      } else if(!job.visible && job.frame) {
        job.box.style.minHeight=job.frame.style.height || '360px';
        frames.delete(job.frame.contentWindow); job.frame.remove(); job.frame=null;
        job.box.dataset.state='waiting'; job.status.textContent=''; job.release();
      }
    }
    pump();
  }, {rootMargin:'200px'});
  const observer = new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) if(node.nodeType===1) hydrate(node);
    for (const job of watched) if(!job.box.isConnected) {
      visibility.unobserve(job.box); watched.delete(job);
      if(job.frame) {frames.delete(job.frame.contentWindow);job.frame.remove();job.frame=null;job.release();}
    }
  });
  observer.observe(document.body,{childList:true,subtree:true});
  window.VoolVisuals = Object.freeze({hydrate});
  hydrate(document.body);
})();
</script>
'''
