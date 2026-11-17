(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const stamp = value => value ? new Date(value * 1000).toLocaleString() : '尚无记录';
  let managementToken = '';
  async function refresh() {
    try {
      const response = await fetch('/api/qq/status', {cache: 'no-store'});
      if (!response.ok) throw new Error('status');
      const data = await response.json();
      byId('state').textContent = data.configuration_error ? '配置有误：请检查 SAYA_QQ_* 环境变量，然后重启。' :
        data.running ? '接入服务运行中' : data.enabled ? '接入服务未运行' : '尚未启用 · 按下方说明配置后重启';
      const rows = [
        ['认证事件最近到达', stamp(data.last_event_at)], ['最近回复成功', stamp(data.last_sent_at)],
        ['主人私聊共通记忆', data.shared_memory ? '已开启，仅限主人好友私聊' : '关闭'],
        ['群聊触发', data.group_mention_only ? '@机器人时回复' : '白名单群所有文字'],
        ['主人身份映射', data.owner_identity_configured ? '已配置，独立于记忆开关' : '未配置'],
        ['主人邀请入群', data.auto_accept_owner_invites ? '自动同意并开启 @回复' : '等待审核'],
        ['直接入群衔接', data.owner_group_auto_enable ? '主人首次 @可开启新群' : '需手动开启回复'],
        ['群接入', `已允许 ${data.allowed_group_count || 0} 个群 / 待审核 ${data.pending_invite_count || 0} 个邀请`],
        ['消息处理计数', Object.entries(data.counts || {}).map(([key, value]) => `${key}: ${value}`).join(' / ') || '暂无'],
        ['最近错误代码', data.last_error || '无'],
        ['最近消息过滤', ({group_not_allowed: '群未授权', not_mentioned: '未 @机器人', stale_event: '消息已过期'})[data.last_ignored_reason] || data.last_ignored_reason || '无']
      ];
      const fragment = document.createDocumentFragment();
      rows.forEach(([name, value]) => {
        const term = document.createElement('dt'); const detail = document.createElement('dd');
        term.textContent = name; detail.textContent = value; fragment.append(term, detail);
      });
      byId('metrics').replaceChildren(fragment);
    } catch (_) { byId('state').textContent = '无法读取状态，请确认 Saya 后端已启动。'; }
  }
  byId('refresh').addEventListener('click', refresh);
  function lockManagement() {
    managementToken = '';
    byId('management').hidden = true; byId('lock-management').hidden = true;
    byId('group-list').replaceChildren(); byId('invite-list').replaceChildren();
    byId('management-summary').textContent = '管理已锁定。输入管理 Token 并检查连接后重新查看。';
  }
  async function adminRequest(path, method = 'GET', body) {
    if (!managementToken) throw new Error('管理已锁定，请重新验证。');
    const response = await fetch('/api/qq/' + path, {
      method, cache: 'no-store', headers: {Authorization: `Bearer ${managementToken}`, 'Content-Type': 'application/json'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401) lockManagement();
      throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请刷新重试。');
    }
    return data;
  }
  function actionButton(label, path, method, body) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
    button.addEventListener('click', async () => {
      button.disabled = true;
      try { await adminRequest(path, method, body); await loadManagement(); await refresh(); }
      catch (error) { byId('management-summary').textContent = error.message; }
      finally { button.disabled = false; }
    });
    return button;
  }
  async function loadManagement() {
    const data = await adminRequest('management');
    // A lock click during a network request must not repopulate private IDs.
    if (!managementToken) return;
    byId('management').hidden = false; byId('lock-management').hidden = false;
    byId('management-summary').textContent = `已配置主人：${data.owner_ids.join('、') || '无'}。主人邀请${data.auto_accept_owner_invites ? '自动同意' : '等待审核'}。`;
    byId('group-list').replaceChildren(); byId('invite-list').replaceChildren();
    data.groups.forEach(group => {
      const card = document.createElement('div'); card.className = 'qq-manage-card';
      const description = document.createElement('p');
      description.textContent = `${group.name || 'QQ 群'}（${group.group_id}） · ${group.joined ? '已加入' : '尚未加入'} · ${group.enabled ? '@回复开启' : '回复关闭'}`;
      card.append(description, actionButton(group.enabled ? '关闭回复' : '开启 @回复',
        'groups/' + encodeURIComponent(group.group_id), 'PUT', {enabled: !group.enabled}));
      byId('group-list').append(card);
    });
    if (!data.groups.length) byId('group-list').textContent = '尚未加入群。可用主人 QQ 邀请机器人入群。';
    const pending = data.invites.filter(item => item.state === 'pending');
    pending.forEach(invite => {
      const card = document.createElement('div'); card.className = 'qq-manage-card';
      const description = document.createElement('p');
      description.textContent = `群 ${invite.group_id} · 邀请人 ${invite.user_id} · ${stamp(invite.created)} · 编号 ${invite.id}`;
      card.append(description, actionButton('同意并开启回复', 'invites/' + invite.id, 'POST', {approve: true}),
        actionButton('拒绝', 'invites/' + invite.id, 'POST', {approve: false}));
      byId('invite-list').append(card);
    });
    if (!pending.length) byId('invite-list').textContent = '暂无待审核邀请。';
  }
  byId('lock-management').addEventListener('click', lockManagement);
  byId('reload-management').addEventListener('click', () => loadManagement().catch(error => { byId('management-summary').textContent = error.message; }));
  byId('diagnostics').addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button'); button.disabled = true;
    const token = byId('token').value; byId('token').value = '';
    byId('diagnostic-result').textContent = '正在检查…';
    try {
      const response = await fetch('/api/qq/diagnostics', {method: 'POST', headers: {Authorization: `Bearer ${token}`}});
      const data = await response.json();
      byId('diagnostic-result').textContent = response.ok ? data.message : response.status === 401 ?
        '管理 Token 不正确，或服务端尚未配置 SAYA_QQ_ADMIN_TOKEN。' : '接入服务未运行，请检查配置并重启。';
      if (response.ok) { managementToken = token; await loadManagement(); }
      else lockManagement();
    } catch (_) { byId('diagnostic-result').textContent = '连接检查失败，请确认后端状态。'; }
    finally { button.disabled = false; }
  });
  refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 10000);
})();
