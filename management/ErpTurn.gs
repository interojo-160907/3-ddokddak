// G열은 참고 표시만 합니다. 실제 요청/진행 신호로 대기열을 관리합니다.
// H열 수집순서, 사용자명 이상호 최우선. 실행 중 요청을 선점하지 않습니다.
function erpTurn_(pcId, request, users) {
  const token = String(request.request_token || '');
  if (!/^[a-f0-9]{32}$/.test(token)) throw new Error('호출 토큰 형식 오류');
  const program = text_(request.program);
  const profiles = {};
  users.rows.forEach((row, index) => {
    if (text_(row[users.program]) !== program || !allowed_(row, users.use)) return;
    const pc = text_(row[users.pc]).toUpperCase();
    const name = text_(row[users.name]);
    const value = Number(text_(row[users.order]));
    profiles[pc] = {name:name, admin:name === '이상호',
      order:Number.isFinite(value) && value >= 1 ? value : 1000 + index};
  });
  if (!profiles[pcId]) throw new Error('수집 권한이 없는 PC입니다.');
  const lock = LockService.getScriptLock();
  lock.waitLock(5000);
  try {
    const props = PropertiesService.getScriptProperties();
    const key = 'production3:erp-queue:v2';
    const now = Date.now();
    const state = JSON.parse(props.getProperty(key) || '{}');
    state.queue = (state.queue || []).filter(q => profiles[q.pc] && now - q.seen < 180000);
    if (state.owner && now >= state.owner.until) {
      state.ready_at = Math.max(state.ready_at || 0, state.owner.until + 120000);
      state.owner = null;
    }
    const mine = q => q && q.pc === pcId && q.token === token;
    const owns = mine(state.owner);
    const result = {coordination_version:2, granted:false, retry_after:20,
      active_user:state.owner ? state.owner.name : '', status:'waiting'};
    if (request.action === 'erp_renew') {
      if (owns) { state.owner.until = now + 600000; result.granted = true; result.status = 'running'; }
    } else if (request.action === 'erp_release' || request.action === 'erp_cancel') {
      state.queue = state.queue.filter(q => !mine(q));
      if (owns) {
        state.last_order = profiles[pcId].order;
        state.owner = null;
        state.ready_at = now + (request.failed === true || request.action === 'erp_cancel' ? 600000 : 0);
        result.released = true;
      }
      result.status = 'finished';
    } else if (request.action === 'erp_acquire') {
      if (owns) {
        result.granted = true; result.status = 'running';
      } else {
        let queued = state.queue.find(mine);
        if (!queued) {
          if (state.queue.length >= 64) throw new Error('수집 대기열이 가득 찼습니다.');
          queued = {pc:pcId,token:token,first:now,seen:now};state.queue.push(queued);
        }
        queued.seen = now;
        const rank = q => {
          const p = profiles[q.pc];
          if (p.admin) return -1;
          return p.order + (p.order <= Number(state.last_order || 0) ? 1000000 : 0);
        };
        state.queue.sort((a,b) => rank(a)-rank(b) || a.first-b.first || a.pc.localeCompare(b.pc));
        const first = state.queue[0];
        const eligible = !state.owner && now >= Number(state.ready_at || 0);
        const settled = now - Math.min(...state.queue.map(q => q.first)) >= 5000;
        if (eligible && settled && mine(first)) {
          state.owner = {pc:pcId,token:token,name:profiles[pcId].name,until:now+600000};
          state.queue.shift();result.granted = true;result.status = 'running';result.active_user = profiles[pcId].name;
        } else {
          result.queue_position = state.queue.findIndex(mine) + 1;
          if (eligible && !settled) result.retry_after = 5;
          if (!eligible && !state.owner) result.status = 'cooldown';
        }
      }
    } else { throw new Error('지원하지 않는 수집 신호입니다.'); }
    props.setProperty(key, JSON.stringify(state));
    return result;
  } finally { lock.releaseLock(); }
}
