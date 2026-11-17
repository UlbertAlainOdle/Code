/* Explicit, local owner management; private records are never loaded on boot. */
(() => {
    'use strict';
    const $ = key => document.getElementById('qq-memory-' + key);
    if (!$('panel')) return;
    let listing = null;
    let detail = null;
    let selectedId = '';
    let busy = false;
    const message = text => { $('feedback').textContent = text; };
    async function api(path, options = {}) {
        const init = {...options, credentials:'same-origin', cache:'no-store', headers:{'Content-Type':'application/json'}};
        let response = await fetch('/api/qq/' + path, init);
        if (response.status === 401) {
            const auth = await fetch('/api/qq/ui-session', {method:'POST', credentials:'same-origin', headers:{'X-Saya-QQ-UI':'1'}});
            if (!auth.ok) throw new Error('请从本机 Saya 网页打开记忆管理。');
            response = await fetch('/api/qq/' + path, init);
        }
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '记忆操作失败，请重新读取。');
        return data;
    }
    function action(task) {
        return async () => {
            if (busy) return;
            busy = true; $('panel').disabled = true;
            try { await task(); }
            catch (error) { message(error.message); }
            finally { busy = false; $('panel').disabled = false; }
        };
    }
    function clearDetail() {
        detail = null; selectedId = ''; $('editor').hidden = true; $('confirm').checked = false;
    }
    async function read(page = 1, useSnapshot = false) {
        if (!$('owner').value) {
            const settings = await api('settings');
            $('owner').replaceChildren();
            for (const id of settings.identity.owner_ids) {
                const option = document.createElement('option'); option.value = id; option.textContent = id;
                $('owner').appendChild(option);
            }
        }
        const params = {owner_id:$('owner').value, kind:$('kind').value, query:$('query').value, page:String(page)};
        if (useSnapshot && listing) params.snapshot = listing.snapshot;
        const data = await api('memory-management?' + new URLSearchParams(params));
        listing = data; clearDetail(); $('items').replaceChildren();
        for (const item of data.items) {
            const row = document.createElement('div');
            const text = document.createElement('span');
            text.textContent = `${item.title || item.id} [${item.status}] · ${item.preview}`;
            row.appendChild(text);
            if (data.kind === 'saved') {
                const button = document.createElement('button'); button.type = 'button'; button.textContent = '查看及授权';
                button.addEventListener('click', action(async () => {
                    if (listing !== data || $('owner').value !== data.owner_id) throw new Error('列表已变化，请重新读取。');
                    const next = await api('memory-management/item?' + new URLSearchParams({owner_id:data.owner_id, memory_id:item.id}));
                    selectedId = item.id; detail = next;
                    const policy = next.policy;
                    $('detail').textContent = `${next.memory.title || ''}\n${next.memory.content || ''}\n来源：${next.memory.source || '未知'}\n` +
                        (policy ? next.policy_current ? '权限与当前内容匹配。' : '内容已变化，原授权失效；请重新核对。' : '这条记忆尚未逐条授权。') +
                        (next.use_allowed===false ? '\n这条记忆当前不参与自动回答：已停用或来源失效，正文仍保留。' : '');
                    $('subject').value = policy?.subject_id || data.owner_id;
                    $('visibility').value = policy?.visibility || 'private';
                    for (const key of ['groups','requesters','writers']) $(key).value = (policy?.[key] || []).join(',');
                    $('audience').textContent = `当前已授权群：${next.authorized_groups.join('、') || '无'}。` + (next.group_recall_available ? '逐群开关开启且本条授权匹配后，群内才可读取。' : '群记忆读取尚未启用。');
                    $('confirm').checked = false; $('editor').hidden = false;
                }));
                row.appendChild(button);
            }
            $('items').appendChild(row);
        }
        $('prev').disabled = data.page <= 1; $('next').disabled = !data.has_next;
        message(`${data.kind === 'saved' ? '已保存记忆' : '待确认候选'}：第${data.page}页，共${data.total}条。候选可在原记忆管理中确认或忽略。`);
    }
    function principals(key) { return $(key).value.split(/[,，\s]+/).filter(Boolean); }
    async function save(revoke) {
        if (!detail || !listing || $('owner').value !== listing.owner_id) throw new Error('请先读取条目。');
        if (!$('confirm').checked) throw new Error('请先核对并勾选这条记忆的权限范围。');
        const policy = {subject_id:$('subject').value.trim(), visibility:$('visibility').value,
            groups:principals('groups'), requesters:principals('requesters'), writers:principals('writers')};
        if (revoke) Object.assign(policy, {visibility:'private', groups:[], requesters:[], writers:[]});
        const saved = await api('memory-management/access', {method:'PUT', body:JSON.stringify({...policy,
            owner_id:listing.owner_id, memory_id:selectedId, content_version:detail.content_version,
            expected_revision:detail.policy?.revision || 0, revision:listing.revision, confirmed:true})});
        detail = saved; $('confirm').checked = false;
        for (const key of ['groups','requesters','writers']) $(key).value = saved.policy[key].join(',');
        $('visibility').value = saved.policy.visibility;
        message('逐条权限已保存。记忆正文保持原样；群记忆读取尚未启用。');
    }
    $('refresh').addEventListener('click', action(() => read()));
    $('prev').addEventListener('click', action(() => read(Math.max(1, (listing?.page || 1) - 1), true)));
    $('next').addEventListener('click', action(() => read((listing?.page || 1) + 1, true)));
    $('save').addEventListener('click', action(() => save(false)));
    $('revoke').addEventListener('click', action(() => save(true)));
    for (const key of ['owner','kind','query']) $(key).addEventListener('input', () => { listing = null; clearDetail(); });
    for (const key of ['subject','visibility','groups','requesters','writers']) $(key).addEventListener('input', () => { $('confirm').checked = false; });
})();
