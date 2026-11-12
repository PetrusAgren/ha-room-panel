(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const app = $('#app'), id = decodeURIComponent(location.pathname.match(/\/panel\/([^/]+)/)?.[1] || '');
  const headers = {'X-Room-Panel': '1', 'Content-Type': 'application/json'};
  let state = null, source = null, pollTimer = null, isPaired = false;
  let previewToken = '', selectedPreset = null, refreshTimer = null;
  const activeSliders = new Set();
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const api = async (path, body) => {
    const res = await fetch(path, {method: body === undefined ? 'GET' : 'POST', credentials:'same-origin', headers: body === undefined ? {'X-Room-Panel':'1'} : headers, body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.error || `Request failed (${res.status})`), {status: res.status});
    return data;
  };
  const endpoint = suffix => `/api/panels/${encodeURIComponent(id)}${suffix}`;
  const notify = message => { const n=$('#notice'); n.textContent=message; n.hidden=!message; if(message) setTimeout(()=>{ if(n.textContent===message)n.hidden=true; },4500); };
  const connected = yes => { const el=$('#connection'); el.classList.toggle('online',!!yes); el.querySelector('span').textContent=yes?'Connected':'Offline'; $('#footer-room').textContent=yes?'Controls are up to date':'Waiting for connection'; };
  const escAttr = esc;
  function button(label, action, value, cls='') { return `<button class="icon-button ${cls}" data-action="${action}" data-value="${escAttr(value)}" aria-label="${escAttr(label)}" title="${escAttr(label)}">${label}</button>`; }
  function entityCard(e) {
    const attrs=e.attributes||{}, domain=e.domain||e.entity_id?.split('.')[0]||'', active=['on','playing'].includes(String(e.state).toLowerCase());
    const supported = ['light','media_player','script'].includes(domain);
    if(!supported) return '';
    const isMedia=domain==='media_player';
    const title=attrs.friendly_name||e.name||e.entity_id||'Device';
    const detail=isMedia ? (attrs.media_title||attrs.source||'Media player') : (e.state||'Unavailable');
    let controls='';
    const modes=Array.isArray(attrs.supported_color_modes)?attrs.supported_color_modes:[];
    const minK=Number(attrs.min_color_temp_kelvin||1000), maxK=Number(attrs.max_color_temp_kelvin||10000);
    if(domain==='light' && modes.some(m=>['brightness','white','color_temp','hs','rgb','rgbw','rgbww','xy'].includes(m))) controls+=`<div class="slider-row"><label>Brightness</label><input aria-label="Brightness" type="range" min="0" max="100" value="${Math.round((Number(attrs.brightness)||0)*100/255)}" data-action="brightness" data-entity="${escAttr(e.id)}"></div>`;
    if(domain==='light' && modes.includes('color_temp')) controls+=`<div class="slider-row"><label>Temperature</label><input aria-label="Color temperature" type="range" min="${minK}" max="${maxK}" value="${Number(attrs.color_temp_kelvin||attrs.color_temp)||minK}" data-action="color_temp" data-entity="${escAttr(e.id)}"></div>`;
    if(domain==='light' && modes.some(m=>['hs','rgb','rgbw','rgbww','xy'].includes(m))){const rgb=Array.isArray(attrs.rgb_color)?attrs.rgb_color:[255,255,255];const hex='#'+rgb.map(v=>Math.max(0,Math.min(255,Number(v)||0)).toString(16).padStart(2,'0')).join('');controls+=`<label class="color-control">Color <input aria-label="Color" type="color" value="${hex}" data-action="color" data-entity="${escAttr(e.id)}"></label>`;}
    if(isMedia) {
      const flags=Number(attrs.supported_features||0), can=bit=>(flags&bit)!==0, media=[];
      if(can(16384))media.push(button('▶','play',e.id)); if(can(1))media.push(button('Ⅱ','pause',e.id));
      if(can(8))media.push(button(attrs.is_volume_muted?'◉':'◖','mute',e.id,attrs.is_volume_muted?'muted':''));
      const volume=attrs.volume_level!==undefined&&can(4)?`<label class="volume"><span>Volume</span><input aria-label="Volume" type="range" min="0" max="100" value="${Math.round((Number(attrs.volume_level)||0)*100)}" data-action="volume" data-entity="${escAttr(e.id)}"></label>`:'';
      if(media.length||volume)controls+=`<div class="media-actions">${media.join('')}${volume}</div>`;
      if(can(2048)&&Array.isArray(attrs.source_list)&&attrs.source_list.length) controls+=`<select aria-label="Source" data-action="source" data-entity="${escAttr(e.id)}"><option value="">Choose source</option>${attrs.source_list.map(s=>`<option>${esc(s)}</option>`).join('')}</select>`;
    }
    if(domain==='script') controls=button('Run','run',e.id,'run-button');
    const toggle = domain!=='light' ? '' : `<button class="toggle ${active?'active':''}" data-action="power" data-entity="${escAttr(e.id)}" data-value="${active?'false':'true'}" aria-label="${active?'Turn off':'Turn on'} ${escAttr(title)}"><span></span></button>`;
    const glyph= domain==='light'?'☼':domain==='media_player'?'♫':domain==='script'?'↗':'⏻';
    return `<article data-state="${escAttr(e.state)}" class="device-card ${active?'is-active':''}"><div class="device-head"><div class="device-icon">${glyph}</div><div class="device-title"><h3>${esc(title)}</h3><p>${esc(detail)}</p></div>${toggle}</div>${controls?`<div class="device-controls">${controls}</div>`:''}</article>`;
  }
  function render(s) {
    state=s; document.title=`${s.room?.name||'Room'} · ${s.branding?.name||'Room Panel'}`;
    $('#room-name').textContent=s.room?.name||s.panel?.name||'Room'; $('#brand-name').textContent=s.branding?.name||'Home'; $('#room-label').textContent=(s.panel?.type||'control').toUpperCase()+' PANEL';
    if(s.branding?.primary) document.documentElement.style.setProperty('--accent',s.branding.primary);
    if(s.branding?.background) document.documentElement.style.setProperty('--page-bg',s.branding.background);
    const logo=$('#brand-logo'), mark=$('.brand-mark'); if(s.branding?.logo){logo.src=s.branding.logo;logo.hidden=false;mark.hidden=true;}else{logo.hidden=true;mark.hidden=false;}
    connected(!!s.connected); app.classList.toggle('disconnected',!s.connected);
    const door=s.panel?.type==='door'; $('#door-view').hidden=!door; $('#sections').hidden=door;
    if(door){
      const cal=s.calendar||{}, status=cal.status||'unknown', card=$('#door-view'); card.classList.toggle('busy',status==='busy');card.classList.toggle('free',status==='free');
      $('#door-status').textContent=status==='busy'?'In use':status==='free'?'Available':'Status unknown';
      $('#door-until').textContent=status==='busy'&&cal.until?`Busy until ${new Date(cal.until).toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZone:cal.timezone||undefined})}`:'';
      $('#availability').innerHTML=(cal.intervals||[]).slice(0,5).map(i=>`<div class="time-row"><span>${new Date(i.start).toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZone:cal.timezone||undefined})}</span><span class="dash">—</span><span>${new Date(i.end).toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZone:cal.timezone||undefined})}</span></div>`).join('')||'<p class="muted">No upcoming changes</p>';
    } else if(!activeSliders.size) renderSections(s);
    const presets=s.presets||[]; $('#presets-section').hidden=!(s.panel?.sections||[]).includes('presets')||!presets.length; $('#presets').innerHTML=presets.map(p=>`<div class="preset-wrap"><div class="preset"><span class="preset-icon">✦</span><span><strong>${esc(p.name)}</strong><small>Revision ${esc(p.revision??'—')}</small></span></div><div class="preset-actions"><button data-preset-apply="${escAttr(p.id)}" ${!s.connected||!p.can_apply?'disabled':''}>Activate</button>${s.pin_set?`<button data-preset-capture="${escAttr(p.id)}" ${!s.connected?'disabled':''}>Capture</button>`:''}</div></div>`).join('');
    const showCal=(s.panel?.sections||[]).includes('calendar')&&s.calendar; $('#calendar-section').hidden=!showCal;
    if(showCal){ const c=s.calendar; $('#calendar').innerHTML=c.status==='unknown'?'<p class="muted">Schedule unavailable</p>':c.status==='free'?'<div class="calendar-state"><span class="free-dot"></span><div><strong>Available</strong><p>No booking in progress</p></div></div>':`<div class="calendar-state"><span class="busy-dot"></span><div><strong>In use</strong><p>${c.until?`Available at ${new Date(c.until).toLocaleTimeString([], {hour:'numeric',minute:'2-digit',timeZone:c.timezone||undefined})}`:'Booking in progress'}</p></div></div>`; }
  }
  function renderSections(s) {
    const supported=(s.entities||[]).filter(e=>['light','media_player','script'].includes(e.domain||e.entity_id?.split('.')[0]));
    const groups=[['lights','Lights',e=>['light'].includes(e.domain||e.entity_id?.split('.')[0])],['music','Music',e=>(e.domain||e.entity_id?.split('.')[0])==='media_player'],['scripts','Scenes & scripts',e=>(e.domain||e.entity_id?.split('.')[0])==='script']];
    $('#sections').innerHTML=groups.filter(([key])=>(s.panel?.sections||[]).includes(key)).map(([key,title,filter])=>{const items=supported.filter(filter);return items.length?`<section class="section"><div class="section-heading"><div><p class="eyebrow">ROOM CONTROLS</p><h2>${title}</h2></div><span class="count">${items.length}</span></div><div class="device-grid">${items.map(entityCard).join('')}</div></section>`:'';}).join('')||'<div class="empty-state"><div>⌂</div><h2>Nothing to control yet</h2><p>This panel has no supported controls assigned.</p></div>';
    for(const control of $('#sections').querySelectorAll('button,input,select')){
      const device=control.closest('[data-state]');
      control.disabled=!s.connected||['unknown','unavailable',''].includes(device?.dataset.state||'');
    }
  }
  async function command(entity, action, value) {
    if (!state?.connected) return;
    try {
      await api(endpoint('/command'), {entity, action, value});
      scheduleRefresh();
    } catch (error) {
      notify(error.message);
    }
  }

  async function refresh() {
    if (!id) return;
    try {
      render(await api(endpoint('/state')));
      if (!isPaired) {
        isPaired = true;
        $('#pair-dialog').close();
      }
    } catch (error) {
      connected(false);
      if (state) state.connected = false;
      for (const control of app.querySelectorAll('#sections button, #sections input, #sections select, #presets button')) {
        control.disabled = true;
      }
      if (error.status === 401) {
        isPaired = false;
        source?.close();
        source = null;
        showPair();
      } else {
        if (state?.panel?.type === 'door') $('#door-status').textContent = 'Status unknown';
        notify(error.message);
      }
    }
  }

  function scheduleRefresh() {
    if (!refreshTimer) {
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        refresh();
      }, 120);
    }
  }

  function showPair() {
    if (!$('#pair-dialog').open) $('#pair-dialog').showModal();
  }

  function realtime() {
    source?.close();
    clearInterval(pollTimer);
    pollTimer = null;
    if (!isPaired) return;
    if (typeof EventSource !== 'function') {
      pollTimer = setInterval(refresh, 15000);
      return;
    }
    source = new EventSource(endpoint('/events'));
    source.addEventListener('refresh', scheduleRefresh);
    source.onopen = () => {
      clearInterval(pollTimer);
      pollTimer = null;
    };
    source.onerror = () => {
      if (!pollTimer) pollTimer = setInterval(refresh, 15000);
      scheduleRefresh();
    };
  }

  $('#pair-form').addEventListener('submit', async event => {
    event.preventDefault();
    const error = $('#pair-error');
    error.hidden = true;
    try {
      await api(endpoint('/pair'), {token: $('#pair-token').value.trim()});
      history.replaceState(null, '', location.pathname);
      await refresh();
      realtime();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    }
  });

  const controls = $('#sections');
  controls.addEventListener('click', event => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const {action, entity, value} = button.dataset;
    let input;
    if (action === 'power') input = value === 'true';
    if (action === 'mute') {
      const item = state.entities.find(row => row.id === value);
      input = !item?.attributes?.is_volume_muted;
    }
    command(entity || value, action, input);
  });

  controls.addEventListener('pointerdown', event => {
    if (event.target.matches('input[type=range]')) activeSliders.add(event.target);
  });
  window.addEventListener('pointerup', () => activeSliders.clear());
  window.addEventListener('pointercancel', () => activeSliders.clear());
  controls.addEventListener('change', event => {
    const element = event.target;
    const {entity, action} = element.dataset;
    if (!action) return;
    activeSliders.delete(element);
    let value = element.value;
    if (action === 'volume') value = Number(value) / 100;
    if (action === 'brightness' || action === 'color_temp') value = Number(value);
    if (action === 'color') value = value.slice(1).match(/../g).map(part => Number.parseInt(part, 16));
    if (action === 'source' && !value) return;
    command(entity, action, value);
  });

  $('#presets').addEventListener('click', async event => {
    const apply = event.target.closest('[data-preset-apply]');
    const capture = event.target.closest('[data-preset-capture]');
    if (apply) {
      try {
        await api(endpoint(`/presets/${encodeURIComponent(apply.dataset.presetApply)}/apply`), {});
        notify('Preset activated');
        scheduleRefresh();
      } catch (error) {
        notify(error.message);
      }
    } else if (capture) {
      selectedPreset = state.presets.find(preset => preset.id === capture.dataset.presetCapture);
      if (!selectedPreset) return;
      $('#pin-title').textContent = `Capture ${selectedPreset.name}`;
      $('#pin-eyebrow').textContent = 'SAVE CURRENT ROOM';
      $('#pin-description').textContent = 'Enter your PIN to preview the current room settings.';
      $('#pin-submit').textContent = 'Preview';
      $('#pin-input').value = '';
      $('#pin-error').hidden = true;
      $('#pin-dialog').showModal();
    }
  });

  $('#pin-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (event.submitter?.value === 'cancel') {
      $('#pin-dialog').close();
      return;
    }
    const error = $('#pin-error');
    const preset = encodeURIComponent(selectedPreset.id);
    try {
      const unlocked = await api(endpoint('/unlock'), {pin: $('#pin-input').value});
      const data = await api(endpoint(`/presets/${preset}/preview`), {token: unlocked.token});
      previewToken = data.token;
      $('#preview-title').textContent = `Review ${selectedPreset.name}`;
      $('#preview-values').innerHTML = data.values.map(item =>
        `<div><span>${esc(item.name)}</span><strong>${esc(item.value)}</strong></div>`).join('');
      $('#preview-fixed').hidden = !data.fixed.length;
      $('#preview-fixed').innerHTML = `<strong>Existing activation options</strong><ul>${data.fixed.map(item =>
        `<li>${esc(item)}</li>`).join('')}</ul>`;
      $('#pin-dialog').close();
      $('#preview-error').hidden = true;
      $('#preview-dialog').showModal();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    }
  });

  $('#preview-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (event.submitter?.value === 'cancel') {
      $('#preview-dialog').close();
      return;
    }
    try {
      await api(endpoint(`/presets/${encodeURIComponent(selectedPreset.id)}/save`), {token: previewToken});
      $('#preview-dialog').close();
      notify('Preset saved');
      scheduleRefresh();
    } catch (error) {
      $('#preview-error').textContent = error.message;
      $('#preview-error').hidden = false;
    }
  });
  $('#clock').textContent=new Intl.DateTimeFormat(undefined,{weekday:'short',month:'short',day:'numeric'}).format(new Date());
  if(location.hash.startsWith('#token=')){const token=decodeURIComponent(location.hash.slice(7));$('#pair-token').value=token;showPair();$('#pair-form').requestSubmit();}else {refresh().then(realtime);}
})();
