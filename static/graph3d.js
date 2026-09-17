/**
 * graph3d.js — Visualización 3D del Grafo de Conocimiento
 * Usa Three.js y 3d-force-graph para renderizar entidades y relaciones.
 */

window.Graph3D = (() => {
  let graphInstance = null;
  let isLoaded = false;
  let container = null;
  let tooltip = null;

  function getOrCreateTooltip() {
    if (!tooltip) {
      tooltip = document.getElementById('graph-tooltip');
      if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'graph-tooltip';
        tooltip.style.cssText = `
          position: fixed;
          background: #1b1f23;
          border: 1px solid #3a4148;
          border-radius: 3px;
          padding: 0.625rem 0.75rem;
          font-size: 12px;
          color: #f5f6f4;
          font-family: "IBM Plex Sans", system-ui, sans-serif;
          pointer-events: none;
          z-index: 9999;
          display: none;
          max-width: 280px;
          line-height: 1.5;
          box-shadow: 0 0.5rem 1.5rem rgba(0,0,0,0.3);
        `;
        document.body.appendChild(tooltip);
      }
    }
    return tooltip;
  }

  // Los nombres y propiedades de nodos provienen de documentos subidos: escapar siempre
  function esc(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  let lastMouseX = 0;
  let lastMouseY = 0;

  // Solo mover el tooltip si la vista del grafo está activa y visible
  document.addEventListener('mousemove', (e) => {
    lastMouseX = e.clientX;
    lastMouseY = e.clientY;
    const viewGraph = document.getElementById('viewGraph');
    const isGraphActive = viewGraph && viewGraph.classList.contains('active');

    if (tooltip && tooltip.style.display !== 'none') {
      if (!isGraphActive) {
        hideTooltip();
      } else {
        moveTooltip();
      }
    }
  });

  function showTooltip(node) {
    const tip = getOrCreateTooltip();
    const props = node.properties || {};
    const lines = Object.entries(props)
      .filter(([k]) => k !== 'name' && k !== 'type')
      .slice(0, 4)
      .map(([k, v]) => `<span style="color:#9aa3ac">${esc(k)}:</span> ${esc(v)}`)
      .join('<br>');

    tip.innerHTML = `
      <div style="font-weight:600;margin-bottom:2px;">${esc(node.name)}</div>
      <div style="color:#9aa3ac;font-size:11px;margin-bottom:6px;">${esc((node.labels || [node.type || 'Entity']).join(', '))}</div>
      ${lines ? `<div style="margin-top:4px;border-top:1px solid rgba(255,255,255,0.1);padding-top:4px;">${lines}</div>` : ''}
      <div style="margin-top:8px;font-size:11px;color:#9aa3ac;">Pulsa para preguntar por esta entidad</div>
    `;
    tip.style.display = 'block';
    moveTooltip();
  }

  function moveTooltip() {
    if (!tooltip) return;
    const x = lastMouseX + 16;
    const y = lastMouseY - 20;
    tooltip.style.left = `${Math.min(x, window.innerWidth - 300)}px`;
    tooltip.style.top  = `${Math.min(y, window.innerHeight - 180)}px`;
  }

  function hideTooltip() {
    if (tooltip) {
      tooltip.style.display = 'none';
    }
  }

  async function init(containerId) {
    container = document.getElementById(containerId);
    if (!container) return;

    hideTooltip();

    // Obtener constructor ForceGraph3D de forma segura
    const GraphConstructor = window.ForceGraph3D || (typeof ForceGraph3D !== 'undefined' ? ForceGraph3D : null);

    container.innerHTML = `
      <div id="graph3d-loading" style="
        display:flex;flex-direction:column;align-items:center;justify-content:center;
        height:100%;gap:0.875rem;color:#5c6670;
      ">
        <div class="spinner" style="
          width:1.5rem;height:1.5rem;border:2px solid #dcdfdb;border-top-color:#1b1f23;
        "></div>
        <div style="font-size:0.875rem;">Cargando el grafo</div>
      </div>
    `;

    try {
      const resp = await fetch('/api/graph-data?max_nodes=500');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      if (!data.nodes || data.nodes.length === 0) {
        container.innerHTML = `
          <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
            height:100%;gap:0.5rem;color:#5c6670;text-align:center;padding:1.5rem;">
            <div style="font-size:1rem;font-weight:600;color:#1b1f23">Todavía no hay grafo</div>
            <div style="font-size:0.875rem;max-width:32ch">Sube documentos y el sistema extraerá de ellos las entidades y sus relaciones.</div>
          </div>
        `;
        return;
      }

      if (!GraphConstructor) {
        throw new Error('Librería 3D Force Graph no cargada.');
      }

      // Limpiar contenedor antes de inicializar canvas
      container.innerHTML = '';
      container.style.position = 'relative';

      // Dimensiones del canvas
      const w = container.clientWidth > 100 ? container.clientWidth : (window.innerWidth - 300);
      const h = container.clientHeight > 100 ? container.clientHeight : (window.innerHeight - 150);

      // Instanciar ThreeForceGraph
      graphInstance = GraphConstructor({ extraRenderers: [] })(container)
        .backgroundColor('#1b1f23')
        .showNavInfo(false)   // su texto de ayuda va en ingles; los controles se explican en la pagina
        .graphData({ nodes: data.nodes, links: data.links })
        .nodeLabel('')
        .nodeColor(n => n.color || '#e8eae6')
        .nodeVal(n => Math.max(3, n.size || 4))
        .nodeResolution(12)
        .linkColor(() => 'rgba(232,234,230,0.28)')
        .linkWidth(0.8)
        .linkDirectionalArrowLength(3.5)
        .linkDirectionalArrowRelPos(1)
        .linkDirectionalParticles(1)
        .linkDirectionalParticleWidth(1.2)
        .linkDirectionalParticleColor(() => '#4d9e84')
        .onNodeHover((node) => {
          container.style.cursor = node ? 'pointer' : 'default';
          if (node) showTooltip(node);
          else hideTooltip();
        })
        .onNodeClick((node) => {
          hideTooltip();
          const chatInput = document.getElementById('chatInput');
          if (chatInput && node.name) {
            chatInput.value = `Explica los detalles y relaciones de ${node.name}`;
            if (window.switchView) window.switchView('chat');
            setTimeout(() => {
              const btnSend = document.getElementById('btnSend');
              if (btnSend) btnSend.click();
            }, 150);
          }
        })
        .onBackgroundClick(() => hideTooltip())
        .width(w)
        .height(h);

      // Ocultar tooltip al salir del canvas
      container.addEventListener('mouseleave', () => {
        hideTooltip();
      });

      // Overlay de estadísticas
      const infoBar = document.createElement('div');
      infoBar.id = 'graph-info-overlay';
      infoBar.style.cssText = `
        position:absolute;top:16px;left:16px;z-index:10;
        background:rgba(27,31,35,0.9);border:1px solid #3a4148;
        border-radius:3px;padding:0.375rem 0.625rem;font-size:0.75rem;color:#c9cec9;
        display:flex;gap:0.875rem;align-items:center;pointer-events:none;
      `;
      infoBar.innerHTML = `
        <span>${data.node_count} entidades</span>
        <span>${data.link_count} relaciones</span>
      `;
      container.appendChild(infoBar);

      renderLegend(data.nodes);

      // Auto-centrar cámara
      setTimeout(() => {
        if (graphInstance && graphInstance.cameraPosition) {
          graphInstance.cameraPosition({ z: 350 });
        }
      }, 300);

      // Redimensionar responsivo
      window.addEventListener('resize', () => {
        if (graphInstance && container) {
          graphInstance.width(container.clientWidth || (window.innerWidth - 300));
          graphInstance.height(container.clientHeight || (window.innerHeight - 150));
        }
      });

      isLoaded = true;

    } catch (err) {
      container.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
          height:100%;gap:0.5rem;color:#5c6670;text-align:center;padding:1.5rem;">
          <div style="color:#8a4b2a;font-weight:600">No se pudo cargar el grafo</div>
          <div style="font-size:0.875rem">${esc(err.message)}</div>
          <button id="graphRetry" class="btn" style="margin-top:0.75rem;">Reintentar</button>
        </div>
      `;
      const reintentar = document.getElementById('graphRetry');
      if (reintentar) reintentar.addEventListener('click', reload);
    }
  }

  /**
   * Leyenda con los tipos presentes en el grafo actual. Sin ella los colores son
   * decoración: el lector no sabe si el verde significa empresa o certificación.
   */
  function renderLegend(nodes) {
    const cont = document.getElementById('graphLegend');
    if (!cont) return;

    const porTipo = new Map();
    (nodes || []).forEach(function (n) {
      const props = n.properties || {};
      const tipo = props.type || props.entity_type || 'Sin tipo';
      if (!porTipo.has(tipo)) porTipo.set(tipo, { color: n.color, total: 0 });
      porTipo.get(tipo).total += 1;
    });

    const ordenados = Array.from(porTipo.entries()).sort(function (a, b) {
      return b[1].total - a[1].total;
    });

    cont.innerHTML = '';
    ordenados.forEach(function (par) {
      const item = document.createElement('span');
      item.className = 'legend-item';

      const punto = document.createElement('i');
      punto.style.background = par[1].color;
      item.appendChild(punto);

      item.appendChild(document.createTextNode(par[0] + ' (' + par[1].total + ')'));
      cont.appendChild(item);
    });
  }

  function reload() {
    isLoaded = false;
    hideTooltip();
    init('graph3d-container');
  }

  return { init, reload, hideTooltip };
})();
