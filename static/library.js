/* Optional bookshelf controls. All reading and account actions remain server-rendered. */
(() => {
  'use strict';
  const shelf = document.querySelector('[data-library]');
  if (!shelf) return;
  const list = shelf.querySelector('.account-bookshelf');
  const toolbar = shelf.querySelector('.library-toolbar');
  if (!list || !toolbar) return;
  const cards = Array.from(list.querySelectorAll('[data-book-title]'));
  const filter = shelf.querySelector('#library-filter');
  const count = shelf.querySelector('#library-count');
  const countUnit = shelf.dataset.libraryCountUnit || '本';
  const empty = shelf.querySelector('#library-no-results');
  const buttons = Array.from(toolbar.querySelectorAll('[data-library-view]'));
  let preferenceKey = null;
  try {
    const context = JSON.parse(document.getElementById('recoll-account-data')?.textContent || '{}');
    if (context.user?.id) {
      preferenceKey = `recoll-account:${encodeURIComponent(context.baseURL || '/')}:${context.user.id}:library-view`;
    }
  } catch (_) { /* The view remains usable when account context is unavailable. */ }
  const setView = view => {
    list.dataset.view = view === 'list' ? 'list' : 'grid';
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.libraryView === list.dataset.view)));
  };
  try { setView(preferenceKey ? localStorage.getItem(preferenceKey) : 'grid'); } catch (_) { setView('grid'); }
  buttons.forEach(button => button.addEventListener('click', () => {
    setView(button.dataset.libraryView);
    try { if (preferenceKey) localStorage.setItem(preferenceKey, list.dataset.view); } catch (_) { /* Storage is optional. */ }
  }));
  const applyFilter = () => {
    const query = filter.value.trim().normalize('NFKC').toLocaleLowerCase();
    let visible = 0;
    cards.forEach(card => {
      card.hidden = !card.dataset.bookTitle.normalize('NFKC').toLocaleLowerCase().includes(query);
      if (!card.hidden) visible += 1;
    });
    count.textContent = query ? `本页匹配 ${visible} / ${cards.length} ${countUnit}` : `本页 ${cards.length} ${countUnit}`;
    empty.hidden = visible !== 0;
  };
  filter.addEventListener('input', applyFilter);
  shelf.querySelector('[data-library-clear]').addEventListener('click', () => {
    filter.value = '';
    applyFilter();
    filter.focus();
  });
  toolbar.hidden = false;
})();
