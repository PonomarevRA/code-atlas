/** Один transport на страницу; закрытый UI не держит socket или polling. */
export class AgentActivityStream {
  /** Подключает bounded model и callbacks; socketFactory/baseUrl допускают изолированную проверку транспорта. */
  constructor({ model, onChange, onStatus, socketFactory = url => new WebSocket(url), baseUrl = location.href }) {
    Object.assign(this, { model, onChange, onStatus, socketFactory, baseUrl });
    this.active = false;
    this.projectId = '';
    this.retry = 0;
  }
  /** Меняет единственную подписку при переключении consumers или проекта. */
  configure(active, projectId) {
    const changed = this.projectId !== (projectId || '');
    if (this.active === active && !changed) return;
    this.stop();
    this.active = Boolean(active && projectId);
    this.projectId = projectId || '';
    if (changed) this.model.reset();
    if (this.active) this.connect();
  }
  /** Подключает replay с последнего применённого курсора; начальный снимок ограничен сервером. */
  connect() {
    if (!this.active || this.socket) return;
    const url = new URL('/api/agent-activity/stream', this.baseUrl);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('projectId', this.projectId);
    const socket = this.socketFactory(url.href);
    this.socket = socket;
    this.onStatus('Подключаем журнал Atlas…', 'loading');
    socket.onopen = () => {
      if (socket !== this.socket) return;
      socket.send(JSON.stringify({ type: 'subscribe', ...(this.model.cursor ? { cursor: this.model.cursor } : {}) }));
    };
    socket.onmessage = message => {
      if (socket !== this.socket) return;
      try { this.receive(socket, message.data); }
      catch { this.onStatus('Некорректный пакет активности', 'offline'); socket.close(1002); }
    };
    socket.onclose = () => {
      if (socket !== this.socket) return;
      this.socket = null;
      this.onStatus('Журнал Atlas отключён · показаны последние полученные записи', 'offline');
      if (!this.active) return;
      this.timer = setTimeout(() => this.connect(), Math.min(15000, 500 * 2 ** Math.min(this.retry++, 5)));
    };
    socket.onerror = () => { if (socket === this.socket) this.onStatus('Журнал Atlas недоступен', 'offline'); };
  }
  /** ACK отправляется только после синхронного применения bounded reducer. */
  receive(socket, data) {
    if (typeof data !== 'string' || new TextEncoder().encode(data).byteLength > 65536) throw new Error('packet-size');
    const packet = JSON.parse(data);
    if (packet.type === 'resetRequired') {
      this.model.reset();
      this.onChange({ reset: true });
      this.onStatus('История изменилась · повторное подключение', 'loading');
      return;
    }
    if (!['snapshot', 'events'].includes(packet.type)) return;
    if (!Array.isArray(packet.events) || packet.events.length > 128 || !packet.messageId || !packet.cursor) throw new Error('packet-shape');
    if (packet.cursor.journalEpoch !== packet.journalEpoch || typeof packet.cursor.sequence !== 'string' || !/^(0|[1-9][0-9]{0,19})$/.test(packet.cursor.sequence)) throw new Error('packet-cursor');
    for (const event of packet.events) {
      if (event.journalEpoch !== packet.journalEpoch || typeof event.sequence !== 'string' || !/^(0|[1-9][0-9]{0,19})$/.test(event.sequence)) throw new Error('packet-epoch');
      if (BigInt(event.sequence) > BigInt(packet.cursor.sequence)) throw new Error('packet-cursor-behind');
    }
    if (packet.type === 'events' && this.model.cursor && (this.model.cursor.journalEpoch !== packet.journalEpoch || BigInt(this.model.cursor.sequence) > BigInt(packet.cursor.sequence))) throw new Error('packet-cursor-regression');
    if (packet.type === 'snapshot') this.model.reset();
    this.model.apply(packet.events, packet.cursor);
    this.retry = 0;
    this.onChange(packet);
    const dropped = Object.values(packet.health?.dropped || {}).some(count => Number(count) > 0);
    this.onStatus(packet.health?.status === 'degraded' || dropped ? 'Журнал ограничен · история может быть неполной' : 'Журнал Atlas подключён · наблюдаем вызовы MCP', 'ready');
    socket.send(JSON.stringify({ type: 'ack', messageId: packet.messageId, cursor: this.model.cursor }));
  }
  /** Отменяет reconnect и обезвреживает обработчики старого socket. */
  stop() {
    clearTimeout(this.timer);
    this.active = false;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }
}
