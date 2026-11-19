/* The graph explorer's drawing: lays out one batch around its focus, and moves, zooms and
   turns it. It reads and changes only the elements inside each [data-graph-interactive]:
   it makes no request, writes no markup and no inline style, and runs no text as code.
   Without it the page works as it is sent. Nothing moves by itself: every change is drawn
   at once, in answer to the person, so there is no motion for a reduced-motion setting to
   stop.

   Labels. Each label is cut to 190 px by its own frame in the markup, and every node's
   full title is a link in the "Graph content" list below the drawing, which the Tab key
   reaches. After every draw the labels are placed in turn: the focus's first, then the
   others nearest the viewer first (in 2D, in the order of the markup). A label whose box
   would cross one already placed is hidden; the next draw where it crosses none shows it
   again. Only labels are hidden: every mark, line and link stays. */
(() => {
  for (const root of document.querySelectorAll('[data-graph-interactive]')) {
    const svg = root.querySelector('svg');
    const viewport = svg && svg.parentElement;
    let box = svg && svg.viewBox.baseVal;
    const nodes = [...root.querySelectorAll('g[data-graph-node]')].map(element => ({
      element, id: element.dataset.graphNode, label: element.querySelector(':scope > svg'),
      x: Number(element.dataset.x), y: Number(element.dataset.y), z: Number(element.dataset.z),
    }));
    if (!svg || !box || !box.width || !box.height || !nodes.length ||
        nodes.some(n => ![n.x, n.y, n.z].every(Number.isFinite))) continue;
    const map = new Map(nodes.map(n => [n.id, n]));
    const edges = [...root.querySelectorAll('line[data-graph-source][data-graph-target]')]
      .map(element => ({element, source: map.get(element.dataset.graphSource), target: map.get(element.dataset.graphTarget)}))
      .filter(edge => edge.source && edge.target);
    const actions = Object.fromEntries([...root.querySelectorAll('[data-graph-action]')].map(el => [el.dataset.graphAction, el]));
    // Lay out this batch around its focus. The coordinates the server drew stay in the
    // markup for a page without the script.
    const focus = map.get(root.dataset.graphFocus) || nodes.find(n => n.element.dataset.graphKind === 'project') || nodes[0];
    const others = nodes.filter(n => n !== focus);
    let mode = '2d', yaw = 0.55, pitch = -0.35, scale = 1, panX = 0, panY = 0, drag = null, suppressClick = false;
    let laidOutWidth = 0;
    const sizeViewport = () => {
      const width = Math.max(1, viewport.clientWidth);
      laidOutWidth = width;
      // Each label takes up to 190 px to the right of its mark. A narrow screen gets one
      // column: more height, and every node still reachable.
      const columns = Math.min(3, Math.max(1, Math.floor((width - 20) / 210)));
      const minimumRows = Math.max(1, Math.ceil((others.length + 1) / columns));
      const rows = minimumRows % 2 ? minimumRows : minimumRows + 1;
      const rowGap = 40;
      const height = Math.max(480, rows * rowGap + 40);
      const slots = [];
      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < columns; col++) {
          slots.push({x: (col - (columns - 1) / 2) * 210, y: (row - (rows - 1) / 2) * rowGap});
        }
      }
      // The middle slot is the focus's, with one column too.
      slots.sort((a, b) => Math.hypot(a.x, a.y) - Math.hypot(b.x, b.y));
      slots.shift();
      slots.sort((a, b) => a.x - b.x || a.y - b.y);
      focus.x = focus.y = focus.z = 0;
      others.forEach((n, i) => { n.x = slots[i].x; n.y = slots[i].y; });
      svg.setAttribute('width', '100%');
      svg.setAttribute('height', String(height));
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      box = svg.viewBox.baseVal;
    };
    const fit = () => {
      scale = 1;
      panX = panY = 0;
    };
    const draw = () => {
      for (const n of nodes) {
        let x = n.x, y = n.y, z = n.z;
        n.depth = 0; // 2D: no node is nearer than another
        if (mode === '3d') {
          const rx = x * Math.cos(yaw) + z * Math.sin(yaw);
          const rz = -x * Math.sin(yaw) + z * Math.cos(yaw);
          const ry = y * Math.cos(pitch) - rz * Math.sin(pitch);
          z = y * Math.sin(pitch) + rz * Math.cos(pitch);
          const perspective = 1 / Math.max(0.25, 1 + z * scale / (Math.min(box.width, box.height) * 3));
          x = rx * perspective; y = ry * perspective;
          n.depth = z; // a larger z is farther from the viewer
        }
        n.px = box.x + box.width / 2 - 95 + panX + x * scale;
        n.py = box.y + box.height / 2 + panY + y * scale;
        n.element.setAttribute('transform', `translate(${n.px} ${n.py})`);
      }
      for (const {element, source, target} of edges) {
        element.setAttribute('x1', source.px); element.setAttribute('y1', source.py);
        element.setAttribute('x2', target.px); element.setAttribute('y2', target.py);
      }
      placeLabels();
    };
    // A label's box, as the markup places its frame: 9 right of the mark and 8 above it,
    // 22 high, as wide as its text up to the frame's 190.
    const labelBox = n => {
      if (n.labelWidth === undefined) {
        const text = n.label && n.label.querySelector('text');
        n.labelWidth = text ? Math.min(190, text.getComputedTextLength()) : 0;
      }
      return {left: n.px + 9, top: n.py - 8, right: n.px + 9 + n.labelWidth, bottom: n.py + 14};
    };
    const crosses = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const placeLabels = () => {
      const order = [focus, ...others.slice().sort((a, b) => a.depth - b.depth)];
      const placed = [];
      for (const n of order) {
        if (!n.label) continue;
        const own = labelBox(n);
        if (placed.some(other => crosses(own, other))) n.label.setAttribute('data-graph-label-hidden', '');
        else { n.label.removeAttribute('data-graph-label-hidden'); placed.push(own); }
      }
    };
    const zoom = factor => { scale = Math.max(0.01, Math.min(20, scale * factor)); draw(); };
    actions.mode?.addEventListener('click', () => {
      mode = mode === '2d' ? '3d' : '2d';
      actions.mode.setAttribute('aria-pressed', String(mode === '3d'));
      draw();
    });
    actions.in?.addEventListener('click', () => zoom(1.2));
    actions.out?.addEventListener('click', () => zoom(1 / 1.2));
    actions.fit?.addEventListener('click', () => { fit(); draw(); });
    svg.addEventListener('wheel', event => {
      const delta = event.deltaY || event.deltaX;
      if (!event.shiftKey || !delta) return; // a plain wheel scrolls the page
      event.preventDefault();
      zoom(delta < 0 ? 1.1 : 1 / 1.1);
    }, {passive: false});
    svg.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      drag = {id: event.pointerId, x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, moved: false};
    });
    svg.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 4) drag.moved = true;
      if (!drag.moved) return;
      drag.x = event.clientX; drag.y = event.clientY;
      if (svg.setPointerCapture && !svg.hasPointerCapture(event.pointerId)) {
        try { svg.setPointerCapture(event.pointerId); } catch (_) { /* a synthetic event */ }
      }
      if (mode === '3d') { yaw += dx * 0.01; pitch = Math.max(-1.5, Math.min(1.5, pitch + dy * 0.01)); }
      else { const bounds = svg.getBoundingClientRect(); panX += dx * box.width / bounds.width; panY += dy * box.height / bounds.height; }
      draw();
    });
    const endDrag = event => { if (drag && drag.id === event.pointerId) { suppressClick = drag.moved; drag = null; if (suppressClick) setTimeout(() => { suppressClick = false; }, 0); } };
    svg.addEventListener('pointerup', endDrag); svg.addEventListener('pointercancel', endDrag);
    // A drag that ends on a node's link does not follow it.
    svg.addEventListener('click', event => { if (suppressClick) { event.preventDefault(); event.stopPropagation(); suppressClick = false; } }, true);
    viewport.addEventListener('keydown', event => {
      if (event.target !== viewport) return; // never a key meant for a link inside it
      if (event.ctrlKey || event.metaKey || event.altKey) return; // the browser's own keys
      const step = 20;
      if (event.key === '+' || event.key === '=') zoom(1.2);
      else if (event.key === '-') zoom(1 / 1.2);
      else if (event.key.toLowerCase() === 'f') { fit(); draw(); }
      else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
        const dx = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
        const dy = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
        if (mode === '3d') { yaw += dx * 0.1; pitch = Math.max(-1.5, Math.min(1.5, pitch + dy * 0.1)); }
        else { panX += dx * step; panY += dy * step; }
        draw();
      } else return;
      event.preventDefault();
    });
    root.dataset.graphLive = ''; // the stylesheet's touch rule for the drawing
    sizeViewport();
    fit();
    draw();
    const controls = root.querySelector('[data-graph-controls]');
    if (controls) {
      controls.hidden = false;
      viewport.setAttribute('aria-describedby', 'graph-help');
    }
    // Lay out again when the drawing's own width changes, and only then.
    if (typeof ResizeObserver === 'function') {
      new ResizeObserver(() => {
        if (Math.max(1, viewport.clientWidth) === laidOutWidth) return;
        sizeViewport(); fit(); draw();
      }).observe(viewport);
    }
  }
})();
