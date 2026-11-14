// Reader chrome only: leave document navigation and saved positions to app.js.
export class ReaderExperience {
  constructor({ root, stage, openSidebar, closeSidebar, toggleToolbar, showToolbar, openSearch, closeSettings, openSettings }) {
    Object.assign(this, { root, stage, openSidebar, closeSidebar, toggleToolbar, showToolbar, openSearch, closeSettings, openSettings });
    this.document = root.ownerDocument;
    this.sidebar = this.document.getElementById('reader-sidebar');
    this.settings = this.document.getElementById('reader-settings-panel');
    this.toolbar = this.document.getElementById('reader-toolbar');
    this.mobile = window.matchMedia('(max-width: 700px)');
    this.documents = new WeakSet();
    this.dialogOrigins = new WeakMap();
    this.bindDocument(this.document);
    root.querySelectorAll('[data-reader-help]').forEach((button) => button.addEventListener('click', () => this.showHelp(button)));
    root.querySelectorAll('[data-reader-fullscreen]').forEach((button) => button.addEventListener('click', () => void this.toggleFullscreen()));
    this.document.getElementById('reader-close-help')?.addEventListener('click', () => this.document.getElementById('reader-help-dialog')?.close());
    this.document.getElementById('reader-help-dialog')?.addEventListener('click', (event) => {
      if (event.target === event.currentTarget) event.currentTarget.close();
    });
    root.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('close', () => {
      const origin = this.dialogOrigins.get(dialog);
      this.dialogOrigins.delete(dialog);
      if (!origin?.isConnected) return this.stage?.focus({ preventScroll: true });
      const temporary = !origin.hasAttribute('tabindex') && !origin.matches('a[href],button,input,select,textarea');
      if (temporary) {
        origin.tabIndex = -1;
        origin.addEventListener('blur', () => origin.removeAttribute('tabindex'), { once: true });
      }
      origin.focus({ preventScroll: true });
    }));
    this.observer = new MutationObserver(() => this.syncPanels());
    this.observer.observe(this.sidebar, { attributes: true, attributeFilter: ['class'] });
    this.observer.observe(this.settings, { attributes: true, attributeFilter: ['hidden'] });
    this.observer.observe(this.root, { attributes: true, attributeFilter: ['class'] });
    this.mobile.addEventListener('change', () => this.syncPanels());
    this.document.addEventListener('fullscreenchange', () => this.syncFullscreen());
    this.syncPanels();
    this.syncFullscreen();
  }

  bindDocument(doc) {
    if (!doc || this.documents.has(doc)) return;
    this.documents.add(doc);
    doc.addEventListener('keydown', (event) => this.onKeydown(event), true);
  }

  showDialog(dialog, origin) {
    if (!dialog || dialog.open) return;
    this.dialogOrigins.set(dialog, origin || this.document.activeElement);
    dialog.showModal();
  }

  showHelp(origin) { this.showDialog(this.document.getElementById('reader-help-dialog'), origin); }

  syncPanels() {
    const settingsOpen = !this.settings.hidden;
    const sidebarOpen = !this.sidebar.classList.contains('is-hidden');
    const mobileSidebar = this.mobile.matches && sidebarOpen && !settingsOpen;
    const toolbarHidden = this.root.classList.contains('reader-toolbar-hidden');
    const active = this.document.activeElement;
    this.sidebar.inert = !sidebarOpen || settingsOpen;
    this.toolbar.inert = settingsOpen || mobileSidebar || toolbarHidden;
    this.root.querySelector('.reader-main').inert = settingsOpen || mobileSidebar;
    this.document.getElementById('reader-show-toolbar').inert = settingsOpen || mobileSidebar;
    if (!sidebarOpen && this.sidebar.contains(active)) {
      this.document.getElementById('reader-toggle-toc')?.focus({ preventScroll: true });
    }
    if (this.wasSettingsOpen && !settingsOpen) this.document.getElementById('reader-toggle-settings')?.focus({ preventScroll: true });
    this.sidebar.setAttribute('role', mobileSidebar ? 'dialog' : 'complementary');
    if (mobileSidebar) this.sidebar.setAttribute('aria-modal', 'true');
    else this.sidebar.removeAttribute('aria-modal');
    if (this.panelsInitialized && mobileSidebar && !this.wasMobileSidebar && !this.sidebar.contains(active)) {
      this.sidebar.querySelector('[role="tab"][aria-selected="true"]')?.focus({ preventScroll: true });
    }
    if (toolbarHidden && this.toolbar.contains(active)) this.document.getElementById('reader-show-toolbar')?.focus({ preventScroll: true });
    if (!toolbarHidden && this.wasToolbarHidden && active?.id === 'reader-show-toolbar') {
      this.document.getElementById('reader-toggle-toolbar')?.focus({ preventScroll: true });
    }
    this.wasMobileSidebar = mobileSidebar;
    this.wasToolbarHidden = toolbarHidden;
    this.wasSettingsOpen = settingsOpen;
    this.panelsInitialized = true;
  }

  trapFocus(event, panel) {
    const controls = [...panel.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')]
      .filter((node) => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
    const first = controls[0], last = controls.at(-1);
    const active = this.document.activeElement;
    if (!first) return;
    if (event.shiftKey && (active === first || !panel.contains(active))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (active === last || !panel.contains(active))) { event.preventDefault(); first.focus(); }
  }

  onKeydown(event) {
    if (event.defaultPrevented || event.isComposing) return;
    if (this.root.querySelector('dialog[open]')) {
      // Preserve the native dialog's Escape action without closing its parent panel.
      if (event.key === 'Escape') event.stopPropagation();
      return;
    }
    const panel = !this.settings.hidden ? this.settings
      : this.mobile.matches && !this.sidebar.classList.contains('is-hidden') ? this.sidebar : null;
    if (panel) {
      if (event.key === 'Tab') this.trapFocus(event, panel);
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (panel === this.settings) this.closeSettings();
        else this.closeSidebar();
      }
      return;
    }
    // Book content can contain native inputs or editable text too.
    if (event.target?.closest?.('input,textarea,select,button,a[href],[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="slider"]')) return;
    if (event.altKey) return;
    const key = event.key.toLowerCase();
    let action;
    if ((event.ctrlKey || event.metaKey) && key === 'f') action = this.openSearch;
    else if (event.ctrlKey || event.metaKey) return;
    else if (key === '/' && !event.shiftKey) action = this.openSearch;
    else if (key === '?') action = () => this.showHelp(event.target);
    else if (key === 'f' && !event.shiftKey) action = () => void this.toggleFullscreen();
    else if (key === 'h' && !event.shiftKey) action = this.toggleToolbar;
    else if (key === 't' && !event.shiftKey) action = () => {
      if (this.sidebar.classList.contains('is-hidden')) { this.showToolbar(); this.openSidebar(); }
      else this.closeSidebar();
    };
    if (action) { event.preventDefault(); action(); }
  }

  syncFullscreen() {
    const active = this.document.fullscreenElement === this.root;
    const supported = !!this.root.requestFullscreen && this.document.fullscreenEnabled !== false;
    this.root.querySelectorAll('[data-reader-fullscreen]').forEach((button) => {
      button.disabled = !supported;
      button.setAttribute('aria-pressed', String(active));
      const label = active ? '退出全屏' : '全屏阅读';
      button.setAttribute('aria-label', label);
      button.title = supported ? `${label}（F）` : '当前浏览器不支持全屏，可使用专注阅读';
      const text = button.querySelector('[data-fullscreen-label]');
      if (text) text.textContent = supported ? label : '当前浏览器不支持全屏';
    });
  }

  async toggleFullscreen() {
    const status = this.document.getElementById('reader-experience-status');
    try {
      if (this.document.fullscreenElement === this.root) await this.document.exitFullscreen();
      else if (this.root.requestFullscreen && this.document.fullscreenEnabled !== false) await this.root.requestFullscreen();
      else throw new Error('unsupported');
      if (status) status.textContent = '';
    } catch {
      this.showToolbar(); this.openSettings();
      if (status) status.textContent = '未能进入全屏。你仍可使用「专注」收起工具栏，或使用浏览器的全屏功能。';
    }
    this.syncFullscreen();
  }
}
