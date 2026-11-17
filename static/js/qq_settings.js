/* QQ controls live in the main settings workspace; no credentials in storage. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const form = $('qq-settings-form');
    if (!form) return;
    const fields = ['enabled', 'shared_memory', 'group_mention_only', 'auto_accept_owner_invites',
        'owner_group_auto_enable', 'cooldown_seconds', 'history_turns', 'reply_timeout', 'thinking_effort',
        'agent_mode', 'recall_mode', 'recall_memory_count', 'memory_token_budget', 'graph_recall_mode',
        'memory_write_enabled', 'web_search_mode', 'max_output_tokens',
        'delivery_mode', 'bubble_strategy', 'reply_reference', 'bubble_chars', 'reply_max_messages', 'task_control_enabled', 'memory_management_enabled'];
    let dirty = false;
    let busy = false;
    let current = null;
    let syncing = false;
    let managementBusy = false;
    let groupSnapshot = null;
    let epoch = 0;
    let sharedDirty = false;
    let sharedRevision = null;
    let sharedBuilt = false;
    let webConfiguration = null;
    let serviceConfiguration = null;
    let serviceBusy = false;
    const inherited = new Map();
    const sharedNames = {thinking_effort:'思考档位',agent_mode:'自主工具',recall_mode:'记忆召回模式',recall_memory_count:'召回数量上限',
        memory_token_budget:'记忆预算',graph_recall_mode:'关联记忆',memory_write_enabled:'保存记忆',web_search_mode:'网络搜索',
        max_output_tokens:'输出及思考预算',history_turns:'近期上下文轮数'};

    function feedback(text, error = false) {
        $('qq-settings-feedback').textContent = text;
        $('qq-settings-feedback').dataset.error = String(error);
    }
    async function session() {
        const response = await fetch('/api/qq/ui-session', {method: 'POST', credentials: 'same-origin', headers: {'X-Saya-QQ-UI': '1'}});
        if (!response.ok) throw new Error('无法打开本机 QQ 设置，请使用当前 Saya 网页地址。');
    }
    async function api(path, options = {}) {
        const init = {...options, credentials: 'same-origin', cache: 'no-store',
            headers: {'Content-Type': 'application/json', ...(options.headers || {})}};
        let response = await fetch('/api/qq/' + path, init);
        if (response.status === 401) { await session(); response = await fetch('/api/qq/' + path, init); }
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'QQ 设置操作失败，请检查参数范围。');
        return data;
    }
    function render(payload) {
        rememberWebConfiguration(payload);
        current = payload;
        fields.forEach(key => {
            const field = $('qq-control-' + key);
            if (['task_control_enabled','memory_management_enabled'].includes(key)) field.disabled = !(key in payload.settings);
            if (field.type === 'checkbox') field.checked = payload.settings[key];
            else field.value = payload.settings[key];
        });
        renderShared(payload);
        const state = payload.status;
        $('qq-settings-status').textContent = state.configuration_error ? '配置有误' : state.draining ? '正在排空任务' : state.running ? '运行中' : payload.settings.enabled ? '未运行' : '已暂停';
        $('qq-settings-status').dataset.state = state.configuration_error ? 'error' : state.running ? 'running' : 'paused';
        const identity = payload.identity;
        $('qq-settings-identity').textContent = `机器人 QQ：${identity.bot_id || '未配置'}　·　主人 QQ：${identity.owner_ids.join('、') || '未配置'}　·　模型：${identity.model}`;
        $('qq-settings-capabilities').textContent = `${payload.capabilities?.thinking_supported ? '当前 API 支持 DeepSeek 思考档位。' : '当前接口未验证思考档位，请使用自动或关闭。'} 当前搜索：${payload.capabilities?.search_provider==='deepseek'?'DeepSeek':'Brave'}。${payload.capabilities?.web_search_configured ? '所选厂家密钥已配置。' : '所选厂家尚未配置搜索密钥，请到 LLM API → Search provider 设置并启用。'}`;
        dirty = false;
    }
    function rememberWebConfiguration(payload) {
        webConfiguration = {thinking_supported:payload.capabilities?.thinking_supported === true,
            follow_global:payload.shared?.web_follow_global === true,
            thinking_effort:payload.shared?.values?.thinking_effort || 'auto'};
        window.dispatchEvent(new CustomEvent('saya-web-configuration'));
    }
    function renderShared(payload) {
        const shared = payload.shared;
        if (!shared) return;
        if (!sharedBuilt) {
            shared.fields.forEach(key => {
                const original = $('qq-control-' + key);
                const label = document.createElement('label');
                label.textContent = sharedNames[key] || key;
                const clone = original.cloneNode(true);
                clone.id = 'qq-shared-value-' + key; clone.removeAttribute('name'); clone.disabled = false;
                label.append(clone); $('qq-shared-values').append(label);
                const inheritLabel = document.createElement('label');
                const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
                checkbox.dataset.inheritField = key;
                const text = document.createElement('span'); inheritLabel.append(checkbox, text);
                checkbox.addEventListener('change', () => { original.disabled = checkbox.checked; });
                inherited.set(key, {checkbox, text}); $('qq-inherit-fields').append(inheritLabel);
            });
            sharedBuilt = true;
        }
        inherited.forEach(({checkbox, text}, key) => {
            checkbox.checked = (payload.inherit_fields || []).includes(key);
            $('qq-control-' + key).disabled = checkbox.checked;
            const source = payload.value_sources?.[key];
            text.textContent = `${sharedNames[key]} · ${source === 'global' ? '跟随共享默认' : source === 'qq_fallback' ? '等待共享值，暂用 QQ 原值' : 'QQ 独立覆写'}`;
        });
        if (!sharedDirty) {
            shared.fields.forEach(key => {
                const field = $('qq-shared-value-' + key);
                const value = shared.values[key] ?? payload.settings[key];
                if (field.type === 'checkbox') field.checked = value;
                else field.value = value;
            });
            $('qq-shared-web-follow').checked = shared.web_follow_global;
            sharedRevision = shared.revision;
            $('qq-shared-feedback').textContent = Object.keys(shared.values).length ? '共享默认已读取；未选择跟随的 QQ 项不受影响。' : '尚未建立共享默认。下方显示 QQ 当前值，保存后才建立共享默认。';
        } else if (sharedRevision !== shared.revision) {
            $('qq-shared-feedback').textContent = '共享默认已由其他页面更新；未保存编辑仍保留，请重新读取后再修改。';
        }
    }
    function collect() {
        return Object.fromEntries(fields.filter(key => !['task_control_enabled','memory_management_enabled'].includes(key) || current?.settings && key in current.settings).map(key => {
            const field = $('qq-control-' + key);
            return [key, field.type === 'checkbox' ? field.checked : field.type === 'number' ? Number(field.value) : field.value];
        }));
    }
    async function action(task) {
        if (busy) return;
        epoch += 1;
        busy = true; $('qq-settings-fields').disabled = true;
        if ($('qq-shared-fields')) $('qq-shared-fields').disabled = true;
        try { await task(); }
        catch (error) { feedback(error.message, true); }
        finally { busy = false; $('qq-settings-fields').disabled = !current; if ($('qq-shared-fields')) $('qq-shared-fields').disabled = !current; }
    }
    async function refresh(force = false) {
        if (dirty && !force) return;
        if (force) sharedDirty = false;
        await action(async () => {
            render(await api('settings'));
            feedback('设置已从本机读取。保存并应用后立即生效。');
        });
        if (current) { await groups(); await refreshService(); }
    }
    function renderService(payload) {
        serviceConfiguration = payload;
        const napcat = payload.napcat;
        const input = $('qq-napcat-watch');
        input.checked = napcat.policy.enabled;
        input.disabled = serviceBusy || !napcat.policy.revision;
        const phases = {disabled:'未开启',manual_stop:'人工停止或维护中',installation_invalid:'专用安装配置有误',ownership_unknown:'无法确认进程归属，已停止尝试',foreign_profile:'安装被其他配置占用',multiple_instances:'检测到多个实例，需检查',running_owned:'专用进程运行中',exit_grace:'退出后等待恢复',root_reappeared:'专用进程已自行恢复',cleaning_owned_children:'清理已确认的残留子进程',startup_timeout:'启动等待超时，需检查',waiting_launcher:'等待启动器',blocked_foreign_port:'端口被其他程序占用，已保留',retry_exhausted:'重试次数耗尽，需检查后重新开启',backoff:'故障退避中',starting:'启动中',launch_failed:'启动失败，等待重试',unknown:'暂无记录'};
        $('qq-service-state').textContent = `Saya 守护：${payload.backend_watchdog === 'running' ? '运行中' : '未运行'}；NapCat：${phases[napcat.phase] || '未知'}${napcat.stale ? '（记录待刷新）' : ''}。${napcat.policy.enabled && !napcat.supervisor_active ? '请先使用本机启动脚本启动守护。' : ''}`;
        const observation = payload.observation;
        const hours = value => (Math.max(0, Number(value) || 0) / 3600).toFixed(2);
        $('qq-observation-state').textContent = `有效观察 ${hours(observation.measured_seconds)} 小时，其中健康 ${hours(observation.healthy_seconds)} 小时；未采样时段 ${hours(observation.unknown_seconds)} 小时。${observation.stale ? '采样已中断或尚未开始。' : '每分钟记录一次。'}${observation.ready_for_24h_review ? '已达到24小时采样，仍需人工核对故障与恢复记录。' : '24小时验收尚未完成。'}`;
    }
    async function refreshService() {
        if (serviceBusy || !$('qq-napcat-watch')) return;
        try { renderService(await api('service')); }
        catch (_) { $('qq-service-state').textContent = '暂时无法读取本机守护状态，请检查后端。'; }
    }
    $('qq-napcat-watch')?.addEventListener('change', async event => {
        if (!serviceConfiguration || serviceBusy) return;
        const enabled = event.target.checked;
        serviceBusy = true; event.target.disabled = true;
        try {
            await api('service/napcat-supervision', {method:'PUT', body:JSON.stringify({enabled, revision:serviceConfiguration.napcat.policy.revision})});
            $('qq-service-feedback').textContent = enabled ? 'NapCat 守护已开启；现有专用进程会被接管，不会重复登录。' : 'NapCat 自动恢复已关闭，当前进程及登录保持运行。';
        } catch (error) { event.target.checked = serviceConfiguration.napcat.policy.enabled; $('qq-service-feedback').textContent = error.message; }
        finally { serviceBusy = false; await refreshService(); }
    });
    function groupRow(title, detail) {
        const row = document.createElement('div'); row.className = 'qq-settings-group';
        const copy = document.createElement('div'); const name = document.createElement('p'); name.textContent = title;
        const note = document.createElement('small'); note.textContent = detail; copy.append(name, note); row.append(copy);
        return row;
    }
    async function groups() {
        if (!current?.status.running) {
            groupSnapshot = null;
            $('qq-settings-groups').textContent = 'QQ 已暂停。启用后可管理群与邀请。';
            $('qq-settings-invites').replaceChildren();
            return;
        }
        let data;
        const groupEpoch = epoch;
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 4000);
        try { data = await api('management', {signal:controller.signal}); }
        catch (_) { groupSnapshot = null; $('qq-settings-groups').textContent = '暂时无法读取群列表，请检查 NapCat 是否在线。'; $('qq-settings-invites').replaceChildren(); return; }
        finally { window.clearTimeout(timeout); }
        if (groupEpoch !== epoch) return;
        const snapshot = JSON.stringify(data);
        if (snapshot === groupSnapshot) return;
        groupSnapshot = snapshot;
        $('qq-settings-groups').replaceChildren(); $('qq-settings-invites').replaceChildren();
        data.groups.forEach(group => {
            const row = groupRow(`${group.name || 'QQ 群'} · ${group.group_id}`, `${group.joined ? '已加入' : '尚未加入'} · ${group.enabled ? '回复已开启' : '回复已关闭'}`);
            const label = document.createElement('label'); label.className = 'switch';
            const input = document.createElement('input'); input.type = 'checkbox'; input.checked = group.enabled;
            input.setAttribute('aria-label', `允许群 ${group.group_id} 回复`);
            const track = document.createElement('span'); track.className = 'switch-track'; label.append(input, track); row.append(label);
            input.addEventListener('change', async () => {
                managementBusy = true;
                input.disabled = true;
                try { await api('groups/' + group.group_id, {method: 'PUT', body: JSON.stringify({enabled: input.checked})}); feedback('群回复设置已保存。'); await groups(); }
                catch (error) { input.checked = group.enabled; feedback(error.message, true); }
                finally { input.disabled = false; managementBusy = false; }
            });
            $('qq-settings-groups').append(row);
        });
        if (!data.groups.length) $('qq-settings-groups').textContent = '尚未加入群。可用主人 QQ 邀请机器人入群。';
        const pending = data.invites.filter(item => item.state === 'pending');
        if (pending.length) {
            const heading = document.createElement('h4'); heading.textContent = '待审核邀请'; $('qq-settings-invites').append(heading);
        }
        pending.forEach(invite => {
            const row = groupRow(`群 ${invite.group_id}`, `邀请人 ${invite.user_id} · 编号 ${invite.id}`);
            const actions = document.createElement('div'); actions.className = 'qq-invite-actions';
            [true, false].forEach(approve => {
                const button = document.createElement('button'); button.type = 'button';
                button.className = 'workspace-secondary-action'; button.textContent = approve ? '同意' : '拒绝';
                button.addEventListener('click', async () => {
                    managementBusy = true;
                    actions.querySelectorAll('button').forEach(item => { item.disabled = true; });
                    try { await api('invites/' + invite.id, {method: 'POST', body: JSON.stringify({approve})}); feedback('邀请已处理。'); await groups(); }
                    catch (error) { feedback(error.message, true); }
                    finally { actions.querySelectorAll('button').forEach(item => { item.disabled = false; }); managementBusy = false; }
                });
                actions.append(button);
            });
            row.append(actions); $('qq-settings-invites').append(row);
        });
    }
    async function sync() {
        if (!current || busy || syncing || managementBusy || document.hidden || form.closest('.settings-pane').hidden
            || $('settings-workspace-modal').style.display !== 'flex') return;
        const started = epoch;
        syncing = true;
        try {
            const payload = await api('settings');
            if (busy || managementBusy || started !== epoch) return;
            rememberWebConfiguration(payload);
            if (payload.revision !== current.revision) {
                if (dirty) feedback('设置已由 QQ 或其他网页更新。你的修改尚未保存，请点击“重新读取”后再修改，避免覆盖。', true);
                else { render(payload); feedback('已同步 QQ 或其他网页修改的设置。'); }
            } else current.status = payload.status;
            await groups();
            await refreshService();
        } catch (_) { /* Keep unsaved edits and retry on focus or the next poll. */ }
        finally { syncing = false; }
    }
    form.addEventListener('input', event => { if(event.target?.id?.startsWith('qq-context-') || event.target?.id?.startsWith('qq-memory-') || event.target?.id?.startsWith('qq-group-') || event.target?.id?.startsWith('qq-contact-') || event.target?.id?.startsWith('qq-media-'))return; epoch += 1; dirty = true; feedback('有未保存的修改，点击“保存并应用”生效。'); });
    form.addEventListener('submit', event => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        const values = {...collect(), revision: current?.revision,
            inherit_fields: [...inherited.entries()].filter(([,item]) => item.checkbox.checked).map(([key]) => key)};
        void action(async () => { render(await api('settings', {method: 'PUT', body: JSON.stringify(values)})); await groups(); feedback('QQ 设置已保存并生效，无需重启。'); });
    });
    $('qq-settings-refresh').addEventListener('click', () => { void refresh(true); });
    $('qq-settings-groups-refresh').addEventListener('click', () => { void groups().catch(error => feedback(error.message, true)); });
    $('qq-settings-test').addEventListener('click', () => { void action(async () => {
        const result = await api('health');
        if (result.service) renderService(result.service);
        const labels = {saya:'Saya 后端',consumer:'QQ 消费者',configuration:'连接配置',napcat:'NapCat / OneBot',onebot_auth:'OneBot 鉴权',qq_login:'机器人登录',network:'外网连通性',model:'模型连接',search:'网络搜索'};
        const states = {ready:'在线',running:'运行中',paused:'已暂停',draining:'正在排空任务',valid:'有效',invalid:'有误',unknown:'未知',reachable:'可达',unreachable:'不可达',timeout:'超时',action_failed:'状态读取失败',accepted:'通过',rejected:'拒绝',http_error:'HTTP 错误',login_required:'需要重新扫码',matched:'已登录且账号匹配',wrong_account:'登录账号不匹配',configured_unverified:'已配置，未发起服务验证',not_configured:'未配置'};
        Object.assign(states,{offline:'QQ连接离线',login_initializing:'已确认，等待初始化',logged_in_unverified:'QQ已登录，账号待OneBot核对'});
        const container = $('qq-health-status'); container.replaceChildren();
        for (const [key, label] of Object.entries(labels)) {
            const item = document.createElement('div');
            const title = document.createElement('span'); title.textContent = label;
            const value = document.createElement('strong'); value.textContent = states[result[key]] || '未知';
            item.append(title, value); container.append(item);
        }
        $('qq-health-action').textContent = `检查时间：${new Date(result.checked_at * 1000).toLocaleTimeString()}。${result.requires_action.join('；') || '当前 QQ 连接正常。'}`;
        const connectionReady = result.qq_login === 'matched' && (!result.network || result.network === 'reachable');
        feedback(connectionReady ? 'QQ 账号与网络检查正常，组件状态见下方。' : '检测到需要处理的连接状态，请查看组件诊断。', !connectionReady);
    }); });
    $('qq-settings-search-sync').addEventListener('click', () => { void action(async () => {
        if (dirty) throw new Error('请先保存或重新读取设置，再同步搜索密钥；当前未保存的修改已保留。');
        await window.SayaLocalProfile?.pushSearch();
        render(await api('settings'));
        feedback('搜索配置状态已刷新；保存或冲突结果请查看 API 本机配置面板。');
    }); });
    let contextState = null;
    async function readContext() {
        const owner = $('qq-context-owner').value;
        const data = await api('context-links' + (owner ? '?owner_id='+encodeURIComponent(owner) : ''));
        contextState = data;
        for (const [id,items] of [['qq-context-owner',data.owners.map(x=>({id:x,preview:x}))],['qq-context-source',data.sessions]]) {
            const select=$(id); select.replaceChildren();
            items.forEach(item=>{const option=document.createElement('option');option.value=item.id;option.textContent=item.preview ? `${item.preview} · ${item.id}` : item.id;select.appendChild(option);});
        }
        $('qq-context-owner').value=data.owner_id;
        if(data.state?.enabled)$('qq-context-source').value=data.state.session_id;
        $('qq-context-feedback').textContent=data.state?.enabled && data.state.expires*1000>Date.now() ? `当前接续：${data.state.session_id}` : '当前QQ话题独立，未接续网页。';
    }
    function contextAction(operation) {
        return async()=>{
            try { await operation(); }
            catch(error){$('qq-context-feedback').textContent=error.message;}
        };
    }
    $('qq-context-refresh')?.addEventListener('click',contextAction(readContext));
    $('qq-context-owner')?.addEventListener('change',contextAction(readContext));
    for(const action of ['select','off'])$('qq-context-'+action)?.addEventListener('click',contextAction(async()=>{
        if(!contextState || contextState.owner_id!==$('qq-context-owner').value)throw new Error('请先读取可选话题');
        await api('context-links',{method:'PUT',body:JSON.stringify({action,owner_id:contextState.owner_id,
            generation:contextState.state.generation,revision:contextState.revision,session_id:$('qq-context-source').value})});
        await readContext();
    }));
    window.SayaQQSettings = {refresh, thinkingConfiguration:() => webConfiguration};
    const refreshWebConfiguration = () => { void api('settings').then(rememberWebConfiguration).catch(() => {}); };
    refreshWebConfiguration();
    $('api-use-shared-credentials')?.addEventListener('change', refreshWebConfiguration);
    window.addEventListener('focus', refreshWebConfiguration);
    $('qq-shared-form')?.addEventListener('input', () => { sharedDirty = true; $('qq-shared-feedback').textContent = '共享默认有未保存的修改。'; });
    $('qq-shared-form')?.addEventListener('submit', event => {
        event.preventDefault();
        if (dirty) { feedback('请先保存或重新读取 QQ 修改，再保存共享默认。', true); return; }
        if (!$('qq-shared-form').reportValidity()) return;
        const values = Object.fromEntries(current.shared.fields.map(key => {
            const field = $('qq-shared-value-' + key);
            return [key, field.type === 'checkbox' ? field.checked : field.type === 'number' ? Number(field.value) : field.value];
        }));
        void action(async () => {
            await api('shared-settings', {method:'PUT', body:JSON.stringify({values, web_follow_global:$('qq-shared-web-follow').checked, revision:sharedRevision})});
            sharedDirty = false; render(await api('settings')); feedback('共享默认已保存；已选择跟随的渠道将在新请求中使用。');
        });
    });
    $('qq-shared-refresh')?.addEventListener('click', () => { void refresh(true); });
    window.SayaQQSettings.syncCredentials = () => window.SayaLocalProfile?.push();
    window.setInterval(() => { void sync(); }, 5000);
    window.addEventListener('focus', () => { void sync(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void sync(); });
})();
