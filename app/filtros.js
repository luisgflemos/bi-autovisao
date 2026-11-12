const searchableFilters = new Map();

function enableTimeRangeDrag(form) {
  let drag;
  let suppressClick = false;
  form.addEventListener('pointerdown', event => {
    suppressClick = false;
    if (!event.isPrimary || event.button !== 0) return;
    const start = event.target.closest('.time-option')?.querySelector('input');
    if (!start || !['ano', 'mes'].includes(start.name)) return;
    const group = start.closest('.time-options');
    const inputs = [...group.querySelectorAll('input')];
    drag = {pointer:event.pointerId, group, inputs, start:inputs.indexOf(start),
      original:inputs.map(input => input.checked), active:false};
  });
  document.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const input = document.elementFromPoint(event.clientX, event.clientY)
      ?.closest('.time-option')?.querySelector('input');
    const end = drag.inputs.indexOf(input);
    if (end < 0 || (!drag.active && end === drag.start)) return;
    drag.active = true;
    const low = Math.min(drag.start, end), high = Math.max(drag.start, end);
    drag.inputs.forEach((item, i) => {item.checked = drag.original[i] || (i >= low && i <= high);});
  });
  document.addEventListener('pointerup', event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const {active, inputs, start, group} = drag;
    drag = null;
    if (!active) return;
    suppressClick = group.contains(document.elementFromPoint(event.clientX, event.clientY));
    inputs[start].dispatchEvent(new Event('change', {bubbles:true}));
  });
  document.addEventListener('pointercancel', event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    drag.inputs.forEach((item, i) => {item.checked = drag.original[i];});
    drag = null;
  });
  form.addEventListener('click', event => {
    if (!suppressClick || !event.target.closest('.time-option')) return;
    suppressClick = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
}

function syncSearchFilters(form) {
  for (const [select, sync] of searchableFilters) {
    if (select.form === form) sync();
  }
}

function makeSearchableFilters(form, names) {
  const normalize = value => value.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLocaleLowerCase('pt-BR');
  for (const name of names) {
    const select = form.elements[name];
    const input = document.createElement('input');
    const list = document.createElement('datalist');
    list.id = 'busca-filtro-' + name;
    input.type = 'text';
    input.autocomplete = 'off';
    input.className = 'searchable-filter';
    input.setAttribute('list', list.id);
    input.placeholder = select.options[0]?.text || 'Todos';
    const label = select.closest('label');
    if (label) input.setAttribute('aria-label', label.firstChild.textContent.trim());
    select.hidden = true;
    select.after(input, list);

    const options = () => [...select.options].filter(option => option.value);
    function suggest() {
      const prefix = normalize(input.value.trim());
      const matches = options().filter(option => normalize(option.text).startsWith(prefix));
      list.replaceChildren(...matches.map(option => new Option(option.text)));
      return matches;
    }
    function sync() {
      input.value = select.value ? select.selectedOptions[0].text : '';
      suggest();
    }
    function commit(usePrefix = false) {
      const text = normalize(input.value.trim());
      const matches = suggest();
      const option = matches.find(item => normalize(item.text) === text)
        || (usePrefix && matches.length === 1 ? matches[0] : null);
      if (text && !option) {
        sync();
        return;
      }
      const value = option?.value || '';
      if (select.value !== value) {
        select.value = value;
        select.dispatchEvent(new Event('change', {bubbles:true}));
      }
      sync();
    }
    input.addEventListener('focus', () => input.select());
    input.addEventListener('input', suggest);
    input.addEventListener('change', event => {
      event.stopPropagation();
      commit();
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.isComposing) {
        event.preventDefault();
        commit(true);
      }
    });
    searchableFilters.set(select, sync);
    new MutationObserver(sync).observe(select, {childList:true,subtree:true});
    sync();
  }
}
