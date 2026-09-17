/**
 * Enterprise Knowledge — interfaz.
 * Sube documentos, pregunta sobre ellos y muestra con qué evidencia se responde.
 */
(function () {
  'use strict';

  // ==========================================================================
  // 1. Estado
  // ==========================================================================
  const state = {
    currentView: 'chat',
    isSubmitting: false,
    activeTasks: new Map(),   // fileId -> { file, taskId }
    activeDocuments: [],
    lastQuery: '',
    brandName: 'Enterprise Knowledge'   // lo sobrescribe COMPANY_NAME vía /api/app-config
  };

  const RUTAS = {
    FAST_PATH: 'directa',
    HYBRID_PATH: 'híbrida',
    FULL_GRAPH_RAG: 'grafo completo'
  };

  // Segmentos encendidos del medidor y etiqueta, por nivel epistémico.
  const EVIDENCIA = {
    LEVEL_A_SUFFICIENT:           { on: 4, texto: 'evidencia suficiente' },
    LEVEL_B_PARTIAL:              { on: 3, texto: 'respaldo parcial' },
    LEVEL_C_RELATED_INSUFFICIENT: { on: 2, texto: 'evidencia débil' },
    LEVEL_D_NO_EVIDENCE:          { on: 1, texto: 'sin evidencia' }
  };

  // ==========================================================================
  // 2. Elementos
  // ==========================================================================
  const dom = {
    btnBrand: document.getElementById('btnBrand'),
    navItems: document.querySelectorAll('.nav-item'),
    viewPanels: document.querySelectorAll('.view-panel'),
    appBrandName: document.getElementById('appBrandName'),
    systemStatus: document.getElementById('systemStatus'),

    // Documentos
    uploadDropzone: document.getElementById('uploadDropzone'),
    btnSelectFiles: document.getElementById('btnSelectFiles'),
    docFileInput: document.getElementById('docFileInput'),
    fileQueueSection: document.getElementById('fileQueueSection'),
    queueCount: document.getElementById('queueCount'),
    queueStatusSummary: document.getElementById('queueStatusSummary'),
    btnClearQueue: document.getElementById('btnClearQueue'),
    fileQueueList: document.getElementById('fileQueueList'),
    btnFinishUpload: document.getElementById('btnFinishUpload'),
    activeDocsCount: document.getElementById('activeDocsCount'),
    filterDocsInput: document.getElementById('filterDocsInput'),
    btnRefreshActiveDocs: document.getElementById('btnRefreshActiveDocs'),
    btnDeleteAllActiveDocs: document.getElementById('btnDeleteAllActiveDocs'),
    activeDocsList: document.getElementById('activeDocsList'),
    btnReloadSamples: document.getElementById('btnReloadSamples'),

    // Consultar
    emptyState: document.getElementById('emptyState'),
    suggestionList: document.getElementById('suggestionList'),
    messageList: document.getElementById('messageList'),
    chatScrollArea: document.getElementById('chatScrollArea'),
    loadingState: document.getElementById('loadingState'),
    chatInput: document.getElementById('chatInput'),
    btnVoiceInput: document.getElementById('btnVoiceInput'),
    btnSend: document.getElementById('btnSend'),

    // Grafo y métricas
    btnRefreshGraph: document.getElementById('btnRefreshGraph'),
    valLatency: document.getElementById('valLatency'),
    valCacheHit: document.getElementById('valCacheHit'),
    valNodes: document.getElementById('valNodes'),
    valRels: document.getElementById('valRels'),

    // Hoja y aviso
    settingsModal: document.getElementById('settingsModal'),
    btnOpenSettings: document.getElementById('btnOpenSettings'),
    btnCloseSettings: document.getElementById('btnCloseSettings'),
    toast: document.getElementById('toast'),
    toastMessage: document.getElementById('toastMessage')
  };

  // ==========================================================================
  // 3. Arranque
  // ==========================================================================
  function init() {
    setupNavigation();
    setupUpload();
    setupDocuments();
    setupChat();
    setupVoiceInput();
    setupSettingsSheet();

    loadAppConfig();
    checkHealth();
    loadActiveDocuments();
  }

  function switchView(viewName) {
    state.currentView = viewName;

    dom.navItems.forEach(item => {
      item.classList.toggle('active', item.dataset.view === viewName);
    });
    dom.viewPanels.forEach(panel => {
      panel.classList.toggle('active', panel.id === 'view' + capitalize(viewName));
    });

    if (window.Graph3D && window.Graph3D.hideTooltip) window.Graph3D.hideTooltip();

    // El panel de métricas sondea cada 5 s: se detiene al salir de su vista.
    if (viewName !== 'metrics' && window.MetricsPanel) window.MetricsPanel.stop();

    if (viewName === 'graph') {
      setTimeout(() => { if (window.Graph3D) window.Graph3D.init('graph3d-container'); }, 100);
    } else if (viewName === 'metrics') {
      loadMetricsSnapshot();
      if (window.MetricsPanel) window.MetricsPanel.start('metrics-panel-mount');
    } else if (viewName === 'knowledge') {
      loadActiveDocuments();
    } else if (viewName === 'chat' && dom.chatInput) {
      dom.chatInput.focus();
    }
  }
  window.switchView = switchView;

  function setupNavigation() {
    dom.navItems.forEach(item => {
      item.addEventListener('click', () => switchView(item.dataset.view));
    });

    if (dom.btnBrand) dom.btnBrand.addEventListener('click', () => switchView('chat'));

    if (dom.btnFinishUpload) {
      dom.btnFinishUpload.addEventListener('click', () => switchView('chat'));
    }
  }

  // ==========================================================================
  // 4. Subida de documentos
  // ==========================================================================
  function setupUpload() {
    if (!dom.uploadDropzone || !dom.docFileInput) return;

    dom.btnSelectFiles.addEventListener('click', () => {
      dom.docFileInput.value = '';
      dom.docFileInput.click();
    });

    dom.docFileInput.addEventListener('change', e => {
      if (e.target.files && e.target.files.length) {
        handleFiles(Array.from(e.target.files));
      }
    });

    ['dragenter', 'dragover'].forEach(evt => {
      dom.uploadDropzone.addEventListener(evt, e => {
        e.preventDefault();
        dom.uploadDropzone.classList.add('over');
      });
    });
    ['dragleave', 'drop'].forEach(evt => {
      dom.uploadDropzone.addEventListener(evt, e => {
        e.preventDefault();
        dom.uploadDropzone.classList.remove('over');
      });
    });
    dom.uploadDropzone.addEventListener('drop', e => {
      if (e.dataTransfer && e.dataTransfer.files.length) {
        handleFiles(Array.from(e.dataTransfer.files));
      }
    });

    dom.btnClearQueue.addEventListener('click', () => {
      dom.fileQueueList.innerHTML = '';
      dom.fileQueueSection.hidden = true;
      state.activeTasks.clear();
    });

    // Reintento delegado: sin onclick en el marcado.
    dom.fileQueueList.addEventListener('click', e => {
      const btn = e.target.closest('[data-retry]');
      if (!btn) return;
      const fileId = btn.dataset.retry;
      const item = state.activeTasks.get(fileId);
      if (item && item.file) {
        const row = document.getElementById('q_' + fileId);
        if (row) row.className = 'row-item';
        uploadFile(fileId, item.file);
      }
    });
  }

  function handleFiles(files) {
    if (!files.length) return;
    dom.fileQueueSection.hidden = false;

    files.forEach(file => {
      const fileId = 'f' + Math.random().toString(36).slice(2, 10);
      state.activeTasks.set(fileId, { file: file, taskId: null });
      addQueueRow(fileId, file);
      uploadFile(fileId, file);
    });
    updateQueueSummary();
  }

  function addQueueRow(fileId, file) {
    const row = document.createElement('div');
    row.className = 'row-item';
    row.id = 'q_' + fileId;
    row.innerHTML =
      '<div class="row-main">' +
        '<div class="row-name"></div>' +
        '<div class="row-meta" id="qs_' + fileId + '">Subiendo</div>' +
        '<div class="progress"><i id="qp_' + fileId + '" style="width:5%"></i></div>' +
      '</div>';
    row.querySelector('.row-name').textContent = file.name + ' · ' + formatSize(file.size);
    dom.fileQueueList.prepend(row);
  }

  async function uploadFile(fileId, file) {
    setQueueStage(fileId, 'Subiendo', 10);

    const form = new FormData();
    form.append('file', file);

    try {
      const resp = await fetch('/api/ingest-document', { method: 'POST', body: form });
      if (!resp.ok) throw new Error('El servidor rechazó el archivo (' + resp.status + ')');

      const data = await resp.json();
      state.activeTasks.set(fileId, { file: file, taskId: data.task_id });
      pollTask(fileId, data.task_id);
    } catch (err) {
      setQueueError(fileId, err.message);
    }
  }

  function pollTask(fileId, taskId) {
    const timer = setInterval(async () => {
      try {
        const resp = await fetch('/api/ingest-status/' + taskId);
        if (!resp.ok) return;
        const task = await resp.json();

        setQueueStage(fileId, task.step_description || 'Procesando',
                      Math.max(10, Math.min(100, task.progress_pct || 0)));

        if (task.status === 'COMPLETED') {
          clearInterval(timer);
          finishQueueRow(fileId, task);
        } else if (task.status === 'FAILED') {
          clearInterval(timer);
          setQueueError(fileId, task.error_message || 'No se pudo procesar el documento');
        }
      } catch (e) {
        console.warn('No se pudo consultar el estado de la ingesta:', e);
      }
    }, 400);
  }

  function setQueueStage(fileId, texto, pct) {
    const s = document.getElementById('qs_' + fileId);
    const p = document.getElementById('qp_' + fileId);
    if (s) { s.textContent = texto; s.className = 'row-meta'; }
    if (p) p.style.width = pct + '%';
    updateQueueSummary();
  }

  function finishQueueRow(fileId, task) {
    const row = document.getElementById('q_' + fileId);
    const s = document.getElementById('qs_' + fileId);
    const p = document.getElementById('qp_' + fileId);

    if (row) row.classList.add(task.error_message ? 'failed' : 'done');
    if (p) p.style.width = '100%';
    if (s) {
      s.className = task.error_message ? 'row-meta warn' : 'row-meta';
      s.textContent = task.error_message
        ? task.error_message
        : (task.entities_ingested || 0) + ' entidades · ' + (task.chunks_indexed || 0) + ' fragmentos';
    }

    updateQueueSummary();
    loadActiveDocuments();
  }

  function setQueueError(fileId, mensaje) {
    const row = document.getElementById('q_' + fileId);
    const s = document.getElementById('qs_' + fileId);
    const p = document.getElementById('qp_' + fileId);

    if (row) row.classList.add('failed');
    if (p) p.style.width = '100%';
    if (s) {
      s.className = 'row-meta warn';
      s.textContent = '';
      s.appendChild(document.createTextNode(mensaje + ' '));
      const btn = document.createElement('button');
      btn.className = 'btn-quiet';
      btn.dataset.retry = fileId;
      btn.textContent = 'Reintentar';
      s.appendChild(btn);
    }
    updateQueueSummary();
  }

  function updateQueueSummary() {
    const total = dom.fileQueueList.querySelectorAll('.row-item').length;
    const hechos = dom.fileQueueList.querySelectorAll('.row-item.done').length;
    const fallos = dom.fileQueueList.querySelectorAll('.row-item.failed').length;

    dom.queueCount.textContent = total;

    if (!total) {
      dom.queueStatusSummary.textContent = '';
    } else if (hechos === total) {
      dom.queueStatusSummary.textContent = 'Todo indexado';
    } else if (fallos) {
      dom.queueStatusSummary.textContent = hechos + ' de ' + total + ', ' + fallos + ' con error';
    } else {
      dom.queueStatusSummary.textContent = hechos + ' de ' + total;
    }
  }

  // ==========================================================================
  // 5. Documentos en la base
  // ==========================================================================
  function setupDocuments() {
    dom.btnRefreshActiveDocs.addEventListener('click', () => {
      loadActiveDocuments();
      showToast('Lista actualizada');
    });

    dom.filterDocsInput.addEventListener('input', e => {
      renderDocs(e.target.value.trim().toLowerCase());
    });

    dom.btnDeleteAllActiveDocs.addEventListener('click', deleteAllDocs);
    dom.btnReloadSamples.addEventListener('click', loadSamples);

    dom.activeDocsList.addEventListener('click', async e => {
      const btn = e.target.closest('[data-delete]');
      if (!btn) return;
      await deleteDoc(btn.dataset.delete, btn.closest('.row-item'), btn);
    });
  }

  async function loadActiveDocuments() {
    try {
      const resp = await fetch('/api/documents');
      if (!resp.ok) return;

      const data = await resp.json();
      state.activeDocuments = data.documents || [];
      dom.activeDocsCount.textContent = data.total_documents || 0;

      renderDocs(dom.filterDocsInput.value.trim().toLowerCase());
      loadSuggestions();
    } catch (e) {
      console.warn('No se pudieron cargar los documentos:', e);
    }
  }

  function renderDocs(query) {
    const lista = state.activeDocuments.filter(d =>
      !query || d.filename.toLowerCase().includes(query));

    dom.activeDocsList.innerHTML = '';

    if (!lista.length) {
      const vacio = document.createElement('p');
      vacio.className = 'empty-note';
      vacio.textContent = query
        ? 'Ningún documento coincide con el filtro.'
        : 'Todavía no hay documentos. Sube el primero y podrás preguntar sobre él.';
      dom.activeDocsList.appendChild(vacio);
      return;
    }

    lista.forEach(doc => {
      const row = document.createElement('div');
      row.className = 'row-item';

      const main = document.createElement('div');
      main.className = 'row-main';

      const nombre = document.createElement('div');
      nombre.className = 'row-name';
      nombre.textContent = doc.filename;

      const meta = document.createElement('div');
      meta.className = 'row-meta';
      meta.textContent = (doc.chunks_count || 1) + ' fragmentos · ' +
                         formatNumber(doc.char_count || 0) + ' caracteres';

      main.appendChild(nombre);
      main.appendChild(meta);

      const btn = document.createElement('button');
      btn.className = 'btn-quiet danger';
      btn.dataset.delete = doc.filename;
      btn.textContent = 'Eliminar';

      row.appendChild(main);
      row.appendChild(btn);
      dom.activeDocsList.appendChild(row);
    });
  }

  async function deleteDoc(filename, row, btn) {
    btn.disabled = true;
    btn.textContent = 'Eliminando';

    try {
      const resp = await fetch('/api/documents/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: filename })
      });

      if (resp.ok) {
        if (row) row.remove();
        showToast('Se eliminó ' + filename);
        loadActiveDocuments();
      } else {
        btn.disabled = false;
        btn.textContent = 'Eliminar';
        showToast('No se pudo eliminar ' + filename);
      }
    } catch (e) {
      btn.disabled = false;
      btn.textContent = 'Eliminar';
      showToast('No hay conexión con el servidor');
    }
  }

  async function deleteAllDocs() {
    if (!state.activeDocuments.length) {
      showToast('La base ya está vacía');
      return;
    }
    if (!confirm('Se eliminarán los ' + state.activeDocuments.length +
                 ' documentos y el grafo construido con ellos. Esta acción no se puede deshacer.')) {
      return;
    }

    try {
      const resp = await fetch('/api/documents/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ all: true })
      });
      if (!resp.ok) throw new Error();

      showToast('Base vaciada');
      state.activeDocuments = [];
      dom.activeDocsCount.textContent = '0';
      renderDocs('');
    } catch (e) {
      showToast('No se pudo vaciar la base');
    }
  }

  async function loadSamples() {
    showToast('Cargando documentos de ejemplo');
    try {
      const resp = await fetch('/api/load-samples', { method: 'POST' });
      if (resp.ok) {
        showToast('Ejemplos cargados');
        loadActiveDocuments();
      } else {
        showToast('No se pudieron cargar los ejemplos');
      }
    } catch (e) {
      showToast('No hay conexión con el servidor');
    }
  }

  // ==========================================================================
  // 6. Consultar
  // ==========================================================================
  function setupChat() {
    if (!dom.chatInput) return;

    dom.chatInput.addEventListener('input', () => {
      dom.chatInput.style.height = 'auto';
      dom.chatInput.style.height = Math.min(dom.chatInput.scrollHeight, 144) + 'px';
    });

    dom.chatInput.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        askQuestion();
      }
    });

    dom.btnSend.addEventListener('click', askQuestion);

    dom.suggestionList.addEventListener('click', e => {
      const btn = e.target.closest('[data-query]');
      if (!btn) return;
      dom.chatInput.value = btn.dataset.query;
      askQuestion();
    });

    // Reintento delegado tras un fallo de red.
    dom.messageList.addEventListener('click', e => {
      if (e.target.closest('[data-retry-question]') && state.lastQuery) {
        dom.chatInput.value = state.lastQuery;
        askQuestion();
      }
      const verFuentes = e.target.closest('[data-sources]');
      if (verFuentes) {
        const panel = document.getElementById(verFuentes.dataset.sources);
        if (panel) {
          panel.hidden = !panel.hidden;
          verFuentes.textContent = panel.hidden
            ? 'Ver fuentes (' + panel.dataset.count + ')'
            : 'Ocultar fuentes';
        }
      }
    });
  }

  async function loadSuggestions() {
    try {
      const resp = await fetch('/api/suggested-questions');
      if (!resp.ok) return;
      const items = await resp.json();
      if (!Array.isArray(items) || !items.length) return;

      dom.suggestionList.innerHTML = '';
      items.forEach(item => {
        const btn = document.createElement('button');
        btn.className = 'suggestion';
        btn.dataset.query = item.query || item.text;
        btn.textContent = item.text || item.query;
        if (item.category) {
          const cat = document.createElement('span');
          cat.textContent = item.category;
          btn.appendChild(cat);
        }
        dom.suggestionList.appendChild(btn);
      });
    } catch (e) {
      console.warn('No se pudieron cargar las sugerencias:', e);
    }
  }

  async function askQuestion() {
    const query = dom.chatInput.value.trim();
    if (!query || state.isSubmitting) return;

    state.isSubmitting = true;
    state.lastQuery = query;
    dom.btnSend.disabled = true;
    dom.chatInput.value = '';
    dom.chatInput.style.height = 'auto';
    dom.emptyState.hidden = true;

    renderQuestion(query);
    dom.loadingState.hidden = false;
    scrollToBottom();

    const t0 = performance.now();

    try {
      const resp = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: query })
      });

      dom.loadingState.hidden = true;
      const ms = Math.round(performance.now() - t0);

      if (resp.status === 400) {
        renderFailure(
          'La consulta no se puede procesar',
          'Contiene instrucciones que el sistema no acepta. Reformúlala como una pregunta sobre tus documentos.',
          false
        );
      } else if (resp.ok) {
        renderAnswer(await resp.json(), ms);
      } else {
        const err = await resp.json().catch(() => ({}));
        renderFailure('No se pudo responder',
                      err.detail || 'El servicio no está disponible ahora mismo.', true);
      }
    } catch (e) {
      dom.loadingState.hidden = true;
      renderFailure('Sin conexión con el servidor',
                    'Comprueba que el servicio sigue en marcha e inténtalo otra vez.', true);
    } finally {
      state.isSubmitting = false;
      dom.btnSend.disabled = false;
      dom.chatInput.focus();
    }
  }

  function renderQuestion(text) {
    const el = document.createElement('div');
    el.className = 'ask';
    el.textContent = text;
    dom.messageList.appendChild(el);
    scrollToBottom();
  }

  function renderAnswer(data, ms) {
    const bloque = document.createElement('div');

    const cuerpo = document.createElement('div');
    cuerpo.className = 'answer';
    cuerpo.innerHTML = formatMarkdown(data.text);
    bloque.appendChild(cuerpo);

    const fuentes = data.sources || [];
    const panelId = fuentes.length ? 'src' + Math.random().toString(36).slice(2, 9) : null;

    bloque.appendChild(buildReadout(data, ms, fuentes.length, panelId));
    if (panelId) bloque.appendChild(buildSources(fuentes, data, panelId));

    dom.messageList.appendChild(bloque);
    scrollToBottom();
  }

  /** La franja de lectura: nivel de evidencia, ruta y tiempo. */
  function buildReadout(data, ms, numFuentes, panelId) {
    const nivel = normalizeLevel(data.uncertainty_level,
                                 data.confidence_score != null ? data.confidence_score : data.confidence);
    const escala = EVIDENCIA[nivel] || EVIDENCIA.LEVEL_D_NO_EVIDENCE;

    const franja = document.createElement('div');
    franja.className = 'readout-strip';

    const evid = document.createElement('span');
    evid.className = 'evidence' + (nivel === 'LEVEL_D_NO_EVIDENCE' ? ' none' : '');

    const medidor = document.createElement('span');
    medidor.className = 'meter';
    medidor.setAttribute('role', 'img');
    medidor.setAttribute('aria-label', escala.texto);
    for (let i = 0; i < 4; i++) {
      const seg = document.createElement('i');
      if (i < escala.on) seg.className = 'on';
      medidor.appendChild(seg);
    }
    evid.appendChild(medidor);
    evid.appendChild(document.createTextNode(escala.texto));
    franja.appendChild(evid);

    const traza = document.createElement('span');
    traza.className = 'trace';
    const ruta = RUTAS[data.route] || 'híbrida';
    traza.textContent = data.origin === 'cache'
      ? 'en caché · ' + formatSeconds(ms)
      : ruta + ' · ' + formatSeconds(ms);
    franja.appendChild(traza);

    if (numFuentes) {
      const btn = document.createElement('button');
      btn.className = 'btn-sources';
      btn.dataset.sources = panelId;
      btn.textContent = 'Ver fuentes (' + numFuentes + ')';
      franja.appendChild(btn);
    }

    return franja;
  }

  /**
   * El backend devuelve las fuentes codificadas para trazabilidad interna
   * ('DocRRF[vector+bm25]:informe.md#Sección', 'Neo4jPath:(A) -[:REL]-> (B)').
   * Aquí se traducen a algo que una persona pueda leer.
   */
  function parseSource(src) {
    const texto = String(src);

    let m = texto.match(/^DocRRF\[[^\]]*\]:(.+)$/);
    if (m) {
      const partes = m[1].split('#');
      return { tipo: 'documento', titulo: partes[0], detalle: partes[1] || '' };
    }

    m = texto.match(/^Neo4jPath:(.+)$/);
    if (m) {
      const legible = m[1]
        .replace(/-\[:([A-Z0-9_]+)\]->/g, (_, rel) => '→ ' + rel.toLowerCase().replace(/_/g, ' ') + ' →')
        .replace(/[()]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
      return { tipo: 'relación', titulo: legible, detalle: '' };
    }

    m = texto.match(/^Neo4j:([^:]+):(.+)$/);
    if (m) return { tipo: 'entidad', titulo: m[2], detalle: m[1] };

    return { tipo: 'documento', titulo: texto, detalle: '' };
  }

  function buildSources(fuentes, data, panelId) {
    const panel = document.createElement('div');
    panel.className = 'sources';
    panel.id = panelId;
    panel.hidden = true;
    panel.dataset.count = fuentes.length;

    const claims = (data.evidence_grounding && data.evidence_grounding.claims) || [];
    const grupos = { documento: [], entidad: [], relación: [] };

    fuentes.forEach((src, i) => {
      const parsed = parseSource(src);
      parsed.cita = claims[i] && claims[i].verbatim_quote;
      grupos[parsed.tipo].push(parsed);
    });

    const titulos = { documento: 'Documentos', entidad: 'Entidades del grafo', relación: 'Relaciones' };

    Object.keys(grupos).forEach(tipo => {
      if (!grupos[tipo].length) return;

      const h = document.createElement('p');
      h.className = 'sources-group';
      h.textContent = titulos[tipo];
      panel.appendChild(h);

      grupos[tipo].forEach(item => {
        const fila = document.createElement('div');
        fila.className = 'source';

        const nombre = document.createElement('div');
        nombre.className = 'source-name';
        nombre.textContent = item.titulo;
        fila.appendChild(nombre);

        if (item.detalle) {
          const d = document.createElement('div');
          d.className = 'source-detail';
          d.textContent = item.detalle;
          fila.appendChild(d);
        }

        if (item.cita) {
          const q = document.createElement('div');
          q.className = 'source-quote';
          q.textContent = item.cita;
          fila.appendChild(q);
        }

        panel.appendChild(fila);
      });
    });

    return panel;
  }

  /**
   * El nivel lo fija la evidencia recuperada; la confianza, la auditoría posterior.
   * Si la auditoría tumbó la respuesta, no puede anunciarse como suficiente.
   */
  function normalizeLevel(level, confidence) {
    const conf = confidence != null ? confidence : 0;
    if (level === 'LEVEL_A_SUFFICIENT' && conf < 0.6) return 'LEVEL_B_PARTIAL';
    return level || 'LEVEL_D_NO_EVIDENCE';
  }

  function renderFailure(titulo, detalle, permitirReintento) {
    const bloque = document.createElement('div');
    bloque.className = 'failure';

    const h = document.createElement('h3');
    h.textContent = titulo;
    bloque.appendChild(h);

    const p = document.createElement('p');
    p.textContent = detalle;
    bloque.appendChild(p);

    if (permitirReintento) {
      const btn = document.createElement('button');
      btn.className = 'btn-quiet';
      btn.setAttribute('data-retry-question', '');
      btn.textContent = 'Reintentar';
      bloque.appendChild(btn);
    }

    dom.messageList.appendChild(bloque);
    scrollToBottom();
  }

  // ==========================================================================
  // 7. Dictado por voz
  // ==========================================================================
  function setupVoiceInput() {
    if (!dom.btnVoiceInput) return;

    let recorder = null;
    let chunks = [];
    let grabando = false;

    dom.btnVoiceInput.addEventListener('click', async () => {
      if (grabando) {
        if (recorder && recorder.state !== 'inactive') recorder.stop();
        grabando = false;
        dom.btnVoiceInput.classList.remove('recording');
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recorder = new MediaRecorder(stream);
        chunks = [];

        recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
        recorder.onstop = async () => {
          stream.getTracks().forEach(t => t.stop());
          await transcribe(new Blob(chunks, { type: 'audio/webm' }));
        };

        recorder.start();
        grabando = true;
        dom.btnVoiceInput.classList.add('recording');
        showToast('Grabando. Pulsa otra vez para terminar.');
      } catch (e) {
        showToast('No se pudo usar el micrófono');
      }
    });
  }

  async function transcribe(blob) {
    showToast('Transcribiendo');
    const form = new FormData();
    form.append('file', blob, 'grabacion.webm');

    try {
      const resp = await fetch('/api/transcribe', { method: 'POST', body: form });
      if (!resp.ok) throw new Error();
      const data = await resp.json();
      if (data.text) {
        dom.chatInput.value = data.text;
        dom.chatInput.focus();
      }
    } catch (e) {
      showToast('No se pudo transcribir el audio');
    }
  }

  // ==========================================================================
  // 8. Hoja, configuración y utilidades
  // ==========================================================================
  function setupSettingsSheet() {
    dom.btnOpenSettings.addEventListener('click', () => { dom.settingsModal.hidden = false; });
    dom.btnCloseSettings.addEventListener('click', () => { dom.settingsModal.hidden = true; });
    dom.settingsModal.addEventListener('click', e => {
      if (e.target === dom.settingsModal) dom.settingsModal.hidden = true;
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') dom.settingsModal.hidden = true;
    });
  }

  async function loadAppConfig() {
    try {
      const resp = await fetch('/api/app-config');
      if (!resp.ok) return;

      const cfg = await resp.json();
      if (!cfg.company_name) return;

      state.brandName = cfg.company_name;
      dom.appBrandName.textContent = cfg.company_name;
      // Evita "Enterprise Knowledge — Enterprise Knowledge": el sufijo solo
      // aporta contexto a nombres como "Acme".
      document.title = /knowledge/i.test(cfg.company_name)
        ? cfg.company_name
        : cfg.company_name + ' — Enterprise Knowledge';

      const pie = document.getElementById('appDisclaimerBrand');
      if (pie) pie.textContent = cfg.company_name;
    } catch (e) {
      console.warn('No se pudo cargar la configuración:', e);
    }
  }

  async function checkHealth() {
    try {
      const resp = await fetch('/health');
      if (!resp.ok) throw new Error();
      dom.systemStatus.textContent = 'En línea';
      dom.systemStatus.classList.remove('offline');
    } catch (e) {
      dom.systemStatus.textContent = 'Sin conexión';
      dom.systemStatus.classList.add('offline');
    }
  }

  async function loadMetricsSnapshot() {
    try {
      const resp = await fetch('/api/metrics');
      if (!resp.ok) return;
      const data = await resp.json();

      // Los nombres vienen del backend tal cual: 'Cache Hit Rate', 'Nodos en Neo4j'...
      const destino = {
        'Latencia promedio': dom.valLatency,
        'Aciertos de caché': dom.valCacheHit,
        'Entidades del grafo': dom.valNodes,
        'Relaciones del grafo': dom.valRels
      };
      (data.metrics || []).forEach(m => {
        const el = destino[m.name];
        if (el) el.textContent = m.value;
      });
    } catch (e) {
      console.warn('No se pudieron cargar las métricas:', e);
    }
  }

  let toastTimer = null;
  function showToast(msg) {
    dom.toastMessage.textContent = msg;
    dom.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { dom.toast.hidden = true; }, 3000);
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      dom.chatScrollArea.scrollTop = dom.chatScrollArea.scrollHeight;
    });
  }

  /** Markdown mínimo: negrita, cursiva, código, listas y párrafos. */
  function formatMarkdown(text) {
    let html = escapeHtml(text || '');

    html = html
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|\s)\*([^*\n]+)\*/g, '$1<em>$2</em>');

    const bloques = html.split(/\n{2,}/).map(bloque => {
      const lineas = bloque.split('\n');
      const esLista = lineas.every(l => /^\s*[-*]\s+/.test(l) || !l.trim());
      if (esLista && lineas.some(l => l.trim())) {
        const items = lineas.filter(l => l.trim())
          .map(l => '<li>' + l.replace(/^\s*[-*]\s+/, '') + '</li>').join('');
        return '<ul>' + items + '</ul>';
      }
      return '<p>' + lineas.join('<br>') + '</p>';
    });

    return bloques.join('');
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return Math.round(bytes / 1024) + ' kB';
    return (bytes / 1048576).toFixed(1).replace('.', ',') + ' MB';
  }

  function formatNumber(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  }

  function formatSeconds(ms) {
    return ms < 1000 ? ms + ' ms' : (ms / 1000).toFixed(1).replace('.', ',') + ' s';
  }

  function capitalize(s) {
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
