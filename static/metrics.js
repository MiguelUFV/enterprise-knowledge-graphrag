/**
 * metrics.js — tabla de métricas del servicio.
 * Consulta GET /api/metrics cada 5 s mientras la vista está visible.
 */
window.MetricsPanel = (function () {
  'use strict';

  const INTERVALO = 5000;
  let timer = null;

  const ESTADO = {
    ok:    'correcto',
    warn:  'atención',
    error: 'fallo',
    info:  'informativo'
  };

  function render(containerId, data) {
    const cont = document.getElementById(containerId);
    if (!cont) return;

    const metrics = data.metrics || [];
    cont.innerHTML = '';

    if (!metrics.length) {
      const vacio = document.createElement('p');
      vacio.className = 'empty-note';
      vacio.textContent = 'No hay métricas disponibles.';
      cont.appendChild(vacio);
      return;
    }

    const tabla = document.createElement('table');
    tabla.className = 'table';

    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>Métrica</th><th>Valor</th><th>Estado</th></tr>';
    tabla.appendChild(thead);

    const tbody = document.createElement('tbody');
    metrics.forEach(function (m) {
      const tr = document.createElement('tr');

      const nombre = document.createElement('td');
      nombre.textContent = m.name;
      if (m.description) nombre.title = m.description;

      const valor = document.createElement('td');
      valor.className = 'num';
      valor.textContent = m.value;

      const estado = document.createElement('td');
      estado.className = 'state ' + (m.status || 'info');
      estado.textContent = ESTADO[m.status] || ESTADO.info;

      tr.appendChild(nombre);
      tr.appendChild(valor);
      tr.appendChild(estado);
      tbody.appendChild(tr);
    });
    tabla.appendChild(tbody);

    cont.appendChild(tabla);

    const pie = document.createElement('p');
    pie.className = 'muted';
    pie.style.marginTop = '0.75rem';
    pie.textContent = 'Actualizado a las ' + new Date().toLocaleTimeString('es-ES');
    cont.appendChild(pie);
  }

  async function refresh(containerId) {
    try {
      const resp = await fetch('/api/metrics');
      if (!resp.ok) throw new Error('el servicio respondió ' + resp.status);
      render(containerId, await resp.json());
    } catch (err) {
      const cont = document.getElementById(containerId);
      if (!cont) return;
      cont.innerHTML = '';
      const aviso = document.createElement('p');
      aviso.className = 'empty-note';
      aviso.textContent = 'No se pudieron leer las métricas: ' + err.message;
      cont.appendChild(aviso);
    }
  }

  function start(containerId) {
    stop();
    refresh(containerId);
    timer = setInterval(function () { refresh(containerId); }, INTERVALO);
  }

  function stop() {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  return { start: start, stop: stop, refresh: refresh };
})();
