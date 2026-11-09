/** Bounded local reader locations; map/browser history remains owned by workspace navigation. */
export class ReaderHistory {
  constructor(limit = 20) { this.limit = Math.max(1, Math.min(20, limit)); this.items = []; this.index = -1; this.scope = null; }
  /** A repository/snapshot change invalidates all previous reader locations. */
  reset(scope) { if (scope === this.scope) return; this.scope = scope; this.items = []; this.index = -1; }
  /** Store only plain reader settings, never source bodies or the graph. */
  copy(value) { return value == null ? null : JSON.parse(JSON.stringify(value)); }
  update(value) { if (this.index >= 0) this.items[this.index] = this.copy(value); }
  /** A newly chosen location drops the forward branch; repeated locations update in place. */
  push(value) {
    const previous = this.items[this.index]?.context || {}, context = value.context || {};
    const same = ['projectId','snapshotId','nodeId','line'].every(key => previous[key] === context[key]);
    if (this.index >= 0 && same) { this.items = this.items.slice(0, this.index + 1); this.update(value); return; }
    this.items = this.items.slice(0, this.index + 1); this.items.push(this.copy(value));
    if (this.items.length > this.limit) this.items.shift();
    this.index = this.items.length - 1;
  }
  move(delta) { const next = this.index + delta; if (!Number.isInteger(next) || next < 0 || next >= this.items.length) return null; this.index = next; return this.copy(this.items[next]); }
  get canBack() { return this.index > 0; }
  get canForward() { return this.index >= 0 && this.index + 1 < this.items.length; }
}
