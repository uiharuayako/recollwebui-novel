// Per-page, per-account state only. Legacy browser positions are deliberately
// never read here: an anonymous browser's data has no verified account owner.
export function sectionReadingProgress(index, ratio, count, atEnd = false) {
  const sections = Math.max(0, Math.floor(Number(count) || 0));
  if (!sections) return 0;
  const section = Math.max(0, Math.min(sections - 1, Math.floor(Number(index) || 0)));
  const within = Math.max(0, Math.min(1, Number(ratio) || 0));
  return atEnd && section === sections - 1 ? 1 : (section + within) / sections;
}

export class ReaderAccountSync {
  constructor({ account, apiBase, fetchImpl = (...args) => fetch(...args),
    onStatus = () => {}, onExpired = () => {}, onSettings = () => {},
    setTimer = (callback, delay) => setTimeout(callback, delay),
    clearTimer = (timer) => clearTimeout(timer), interval = 6000 } = {}) {
    this.account = account;
    this.apiBase = apiBase;
    this.fetchImpl = fetchImpl;
    this.onStatus = onStatus;
    this.onExpired = onExpired;
    this.onSettings = onSettings;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.interval = interval;
    this.contexts = new Map();
    this.controllers = new Set();
    this.active = null;
    this.stopped = false;
    this.writerId = globalThis.crypto?.randomUUID?.() || `reader-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  status(type, message, context = this.active) {
    if (!this.stopped && (!context || context === this.active)) this.onStatus({ type, message, context });
  }

  async request(url, { body, keepalive = false } = {}) {
    if (this.stopped) throw new Error('阅读同步已停止');
    const controller = new AbortController();
    this.controllers.add(controller);
    try {
      const response = await this.fetchImpl(url, {
        method: body === undefined ? 'GET' : 'POST',
        credentials: 'same-origin', signal: controller.signal, keepalive,
        headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': this.account?.csrf || '' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (response.status === 401) {
        this.stop();
        this.onExpired();
        throw new Error('登录已失效，请重新登录');
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(data.error || data.message || `保存失败 (${response.status})`);
        error.status = response.status;
        error.data = data;
        throw error;
      }
      if (this.stopped) throw new Error('阅读同步已停止');
      return data;
    } finally {
      this.controllers.delete(controller);
    }
  }

  applySettings(settings, context) {
    if (!settings) return;
    this.onSettings(settings);
    if (context) {
      context.settings = settings;
      // A handle binds the epoch at issue time. Never replace its epoch with a
      // newer value after a deletion or disabled recording setting.
      if (context.epoch == null) context.epoch = settings.progress_epoch ?? settings.progress_history_epoch ?? 0;
    }
  }

  async prepare(item) {
    if (this.stopped || !item?.documentId || !item.openHandle || !this.account) return null;
    const existing = this.contexts.get(item.documentId);
    if (existing?.loaded) {
      this.active = existing;
      existing.restored = false;
      existing.rendered = false;
      existing.opened = false;
      if (existing.blockedEpoch) return null;
      try {
        const data = await this.request(`${this.apiBase}/progress/${encodeURIComponent(item.documentId)}`);
        const epoch = data.settings?.progress_epoch ?? data.settings?.progress_history_epoch;
        if (epoch != null && epoch !== existing.epoch) {
          this.handleError({ status: 409, data: { code: 'stale_epoch' } }, existing);
          existing.position = null;
          return null;
        }
        this.applySettings(data.settings, existing);
        if (data.content_version && item.contentVersion && data.content_version !== item.contentVersion && data.position) {
          existing.blocked = true;
          existing.conflict = data;
          existing.position = null;
          this.status('version-changed', '文档内容已更新，原位置已保留。可选择从当前位置继续。', existing);
          return null;
        }
        if (Number(data.revision || 0) !== existing.revision && existing.dirty) {
          existing.blocked = true;
          existing.conflict = data;
          this.status('conflict', '其他设备已保存新的位置，请选择继续方式。', existing);
        } else if (!existing.blocked) {
          existing.revision = Number(data.revision) || 0;
          existing.position = existing.dirty && existing.latest ? existing.latest : data.position || null;
          if (!existing.dirty) {
            existing.saved = JSON.stringify(existing.position);
            existing.latest = null;
          }
        }
      } catch (error) {
        this.status('error', `暂时无法刷新账号进度：${error.message}`, existing);
      }
      if (!existing.blocked && existing.latest) existing.position = existing.latest;
      return existing.position;
    }
    const context = { item, loaded: false, restored: false, rendered: false, opened: false,
      revision: 0, epoch: null, position: null, latest: null, dirty: false,
      timer: null, inFlight: null, sequence: 0, pendingWrite: null, conflict: null, blocked: false };
    this.contexts.set(item.documentId, context);
    this.active = context;
    this.status('loading', '正在加载账号阅读进度…', context);
    try {
      const data = await this.request(`${this.apiBase}/progress/${encodeURIComponent(item.documentId)}`);
      if (this.stopped) return null;
      this.applySettings(data.settings || this.account.settings, context);
      context.loaded = true;
      context.revision = Number(data.revision) || 0;
      context.position = data.position || null;
      context.saved = JSON.stringify(context.position);
      if (data.content_version && item.contentVersion && data.content_version !== item.contentVersion && context.position) {
        context.conflict = data;
        context.blocked = true;
        context.position = null;
        this.status('version-changed', '文档内容已更新，原位置已保留。可选择从当前位置继续。', context);
        return null;
      }
      return context.position;
    } catch (error) {
      this.status('error', `原进度暂不可用，已暂停保存：${error.message}`, context);
      return null;
    }
  }

  position(item) {
    return this.contexts.get(item?.documentId)?.position || null;
  }

  async rendered(item, folderKey) {
    const context = this.contexts.get(item?.documentId);
    if (!context || this.stopped) return;
    context.rendered = true;
    context.restored = true;
    context.folderKey = folderKey;
    if (!context.opened) await this.recordOpen(context);
    if (!context.blocked && context.loaded) this.status('saved', '阅读进度已就绪', context);
  }

  async recordOpen(context) {
    if (!context.rendered || context.opened || context.opening || context.blockedEpoch || this.stopped) return;
    context.opening = true;
    try {
      const data = await this.request(`${this.apiBase}/open`, { body: {
        handle: context.item.openHandle,
        ...(context.folderKey ? { folderKey: context.folderKey } : {}),
      } });
      this.applySettings(data.settings, context);
      context.opened = true;
      if (context.loaded && data.progress && Number(data.progress.revision) !== context.revision) {
        context.conflict = data.progress;
        context.blocked = true;
        this.status('conflict', '其他设备已保存新的位置，请选择继续方式。', context);
      }
    } catch (error) {
      this.handleError(error, context);
    } finally {
      context.opening = false;
    }
  }

  capture(item, position) {
    const context = this.contexts.get(item?.documentId);
    if (this.stopped || this.paused || !context?.loaded || context.blockedEpoch || !context.restored || !context.rendered || !position || typeof position !== 'object') return;
    context.latest = JSON.parse(JSON.stringify(position));
    if (context.blocked || context.settings?.progress_sync_enabled === false || context.settings?.progress_sync_enabled === 0) return;
    context.dirty = JSON.stringify(context.latest) !== context.saved;
    if (context.dirty && !context.timer) {
      this.status('pending', '阅读位置尚未同步', context);
      context.timer = this.setTimer(() => {
        context.timer = null;
        void this.flushContext(context);
      }, this.interval);
    }
  }

  async flush(item, options) {
    const context = item ? this.contexts.get(item.documentId) : this.active;
    return context ? this.flushContext(context, options) : undefined;
  }

  async flushContext(context, { keepalive = false } = {}) {
    if (context.timer) this.clearTimer(context.timer);
    context.timer = null;
    if (this.stopped || this.paused || !context.loaded || !context.restored || !context.rendered || context.blocked
      || context.settings?.progress_sync_enabled === false || context.settings?.progress_sync_enabled === 0) return;
    if (context.inFlight) {
      await context.inFlight;
      if ((context.dirty || context.pendingWrite) && !this.stopped && !context.blocked) return this.flushContext(context, { keepalive });
      return;
    }
    if (!context.opened) await this.recordOpen(context);
    if (!context.opened || context.blocked || (!context.dirty && !context.pendingWrite)
      || (!context.latest && !context.pendingWrite) || this.stopped) return;
    const retrying = !!context.pendingWrite;
    if (!context.pendingWrite) {
      const position = context.latest;
      const percentage = this.progressPercentage(position);
      context.pendingWrite = {
        handle: context.item.openHandle, position, revision: context.revision,
        writer_id: this.writerId, writer_seq: context.sequence + 1,
        epoch: context.epoch, content_version: context.item.contentVersion,
        ...(percentage == null ? {} : { progress: percentage }),
      };
    }
    // A lost response does not prove that a write failed. Reuse the exact
    // request until acknowledged, so the server can recognize its committed
    // revision and writer sequence as an idempotent replay.
    const body = context.pendingWrite;
    const position = body.position;
    const signature = JSON.stringify(position);
    context.inFlight = (async () => {
      try {
        const data = await this.request(`${this.apiBase}/progress/${encodeURIComponent(context.item.documentId)}`, { keepalive, body });
        context.revision = Number(data.revision) || context.revision;
        context.sequence = body.writer_seq;
        context.pendingWrite = null;
        context.position = position;
        context.saved = signature;
        context.dirty = JSON.stringify(context.latest) !== signature;
        this.status(context.dirty ? 'pending' : 'saved', context.dirty ? '阅读位置尚未同步' : '阅读位置已同步', context);
      } catch (error) {
        this.handleError(error, context);
      }
    })();
    await context.inFlight;
    context.inFlight = null;
    if (retrying && !context.pendingWrite && context.dirty && !context.blocked && !this.stopped) {
      return this.flushContext(context, { keepalive });
    }
    if ((context.dirty || context.pendingWrite) && !context.blocked && !this.stopped && !context.timer) {
      context.timer = this.setTimer(() => { context.timer = null; void this.flushContext(context); }, this.interval);
    }
  }

  handleError(error, context) {
    if (this.stopped || error.name === 'AbortError') return;
    if (error.status === 409) {
      context.blocked = true;
      if (error.data?.code === 'stale_epoch' || error.data?.code === 'content_changed') {
        context.blockedEpoch = true;
        context.latest = null;
        context.dirty = false;
        this.status('stale', error.data.code === 'content_changed'
          ? '文档内容已变化，请重新打开后选择阅读位置。'
          : '记录设置已改变或记录已清除，请重新打开页面以恢复同步。', context);
      } else {
        context.conflict = error.data?.current || null;
        this.status('conflict', '其他设备已保存新的位置，请选择继续方式。', context);
      }
    } else if (error.status === 403) {
      context.blocked = true;
      this.status('stale', '会话或阅读授权已改变，请重新打开页面。', context);
    } else if (error.status >= 400 && error.status < 500) {
      context.blocked = true;
      this.status('error', error.status === 413
        ? '阅读位置数据过大，未保存当前进度。'
        : `当前阅读位置未保存：${error.message}`, context);
    } else {
      this.status('error', `阅读位置尚未同步：${error.message}`, context);
    }
  }

  progressPercentage(position) {
    const value = position?.progress?.percentage ?? position?.percentage;
    return value == null || !Number.isFinite(Number(value)) ? null : Math.max(0, Math.min(100, Number(value) * 100));
  }

  async resolve(useCurrent) {
    const context = this.active;
    if (!context || context.blockedEpoch || !context.blocked || this.stopped) return null;
    // An explicit user decision starts with the actual server revision, so a
    // second concurrent update still results in a conflict instead of a loss.
    let row;
    try {
      row = await this.request(`${this.apiBase}/progress/${encodeURIComponent(context.item.documentId)}`);
    } catch (error) { this.handleError(error, context); return null; }
    const epoch = row.settings?.progress_epoch ?? row.settings?.progress_history_epoch;
    if (epoch != null && epoch !== context.epoch) {
      this.handleError({ status: 409, data: { code: 'stale_epoch' } }, context);
      return null;
    }
    context.revision = Number(row.revision) || 0;
    if (row.writer_id === this.writerId) context.sequence = Math.max(context.sequence, Number(row.writer_seq) || 0);
    context.pendingWrite = null;
    context.position = row.position || null;
    context.saved = JSON.stringify(context.position);
    context.conflict = null;
    context.blocked = false;
    if (useCurrent) {
      context.dirty = context.latest && JSON.stringify(context.latest) !== context.saved;
      await this.flushContext(context);
      return null;
    }
    context.latest = null;
    context.dirty = false;
    context.restored = false;
    this.status('saved', '已采用保存的位置', context);
    return row.position || null;
  }

  stop() {
    this.stopped = true;
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
    for (const context of this.contexts.values()) if (context.timer) this.clearTimer(context.timer);
    this.contexts.clear();
    this.active = null;
  }
}
