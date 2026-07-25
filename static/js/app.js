// Error handler
    window.onerror = function(msg, url, line, col, error) {
        if (msg.includes("ResizeObserver")) return;
        console.error("Global Error:", msg, "Line:", line, error);
    };

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        })[char]);
    }

    function escapeJsString(value) {
        return String(value ?? '')
            .replace(/\\/g, '\\\\')
            .replace(/'/g, "\\'")
            .replace(/\r/g, '\\r')
            .replace(/\n/g, '\\n')
            .replace(/&/g, '\\x26')
            .replace(/"/g, '\\x22')
            .replace(/</g, '\\x3C')
            .replace(/>/g, '\\x3E');
    }

    function sanitizeHtml(html) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(`<div>${html}</div>`, 'text/html');
        const blocked = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META', 'FORM']);
        doc.body.querySelectorAll('*').forEach(element => {
            if (blocked.has(element.tagName)) {
                element.remove();
                return;
            }
            Array.from(element.attributes).forEach(attribute => {
                const name = attribute.name.toLowerCase();
                const value = attribute.value.trim();
                if (name.startsWith('on') || name === 'srcdoc') {
                    element.removeAttribute(attribute.name);
                } else if ((name === 'href' || name === 'src') &&
                           /^(?:javascript|vbscript|data):/i.test(value)) {
                    element.removeAttribute(attribute.name);
                }
            });
            if (element.tagName === 'A') {
                element.setAttribute('rel', 'noopener noreferrer');
            }
        });
        return doc.body.firstElementChild?.innerHTML || '';
    }

    function renderMarkdownSafe(text) {
        return sanitizeHtml(marked.parse(String(text ?? '')));
    }

    // --- API FIX FOR SEPARATE FRONTEND HOSTING ---
    // Redirects API calls to port 8000 if frontend is running on a different port (e.g. VSCode Live Server)
    const originalFetch = window.fetch;
    window.fetch = function(url, options) {
        if (typeof url === 'string' && url.startsWith('/')) {
            const API_BASE = window.location.port === '8000' ? '' : 'http://127.0.0.1:8000';
            url = API_BASE + url;
        }
        return originalFetch(url, options);
    };

    // --- SIDEBAR TOGGLE ---
    function updateRoleNavigationButton() {
        const button = document.getElementById('btn-toggle-sidebar');
        const panel = document.querySelector('.model-panel');
        if (!button || !panel) return;
        const assistantBack = currentRoleMode === 'assistant' && panel.classList.contains('collapsed');
        button.textContent = assistantBack ? '<' : '☰';
        button.title = assistantBack ? 'Back to Assistant' : 'Toggle Sidebar';
    }

    function setCurrentRoleMode(mode, options = {}) {
        const previousRoleMode = currentRoleMode || '';
        const nextRoleMode = mode || '';
        if (nextRoleMode === 'assistant' && previousRoleMode !== 'assistant') {
            const alreadyChanged = assistantMemoryChangedSinceEntry === true;
            assistantRoleEntryMemoryStamp = assistantRoleState?.last_read_at || '';
            assistantMemoryChangedSinceEntry = alreadyChanged;
            localStorage.setItem('saya_assistant_role_entry_memory_stamp', assistantRoleEntryMemoryStamp);
            localStorage.setItem('saya_assistant_role_memory_changed', alreadyChanged ? 'true' : 'false');
        }
        currentRoleMode = nextRoleMode;
        if (options.persist !== false) {
            localStorage.setItem('saya_current_role_mode', currentRoleMode);
        }
        if (previousRoleMode === 'assistant' && currentRoleMode !== 'assistant') {
            assistantRoleEntryMemoryStamp = '';
            assistantMemoryChangedSinceEntry = false;
            localStorage.removeItem('saya_assistant_role_entry_memory_stamp');
            localStorage.removeItem('saya_assistant_role_memory_changed');
        }
        document.body.classList.toggle('assistant-role-active', currentRoleMode === 'assistant');
        syncAssistantStrongLogicToggle();
        updateRoleNavigationButton();
    }

    function collapseSidebarForAssistantChat() {
        const panel = document.querySelector('.model-panel');
        const menu = document.getElementById('settings-menu');
        if (panel && !panel.classList.contains('collapsed')) {
            panel.classList.add('collapsed');
        }
        if (menu) menu.style.display = 'none';
        document.body.classList.add('sidebar-collapsed');
        updateRoleNavigationButton();
        setTimeout(() => {
            onWindowResize();
        }, 300);
    }

    function toggleSidebar() {
        const panel = document.querySelector('.model-panel');
        if (currentRoleMode === 'assistant' && panel?.classList.contains('collapsed')) {
            openAssistantRoleModal({fromChat: true});
            return;
        }
        panel.classList.toggle('collapsed');
        const menu = document.getElementById('settings-menu');
        if (panel.classList.contains('collapsed') && menu) {
            menu.style.display = 'none';
        }
        
        // Resize three.js renderer
        setTimeout(() => {
            onWindowResize();
            // Center chat content if collapsed
            const chatContent = document.getElementById('chat-content');
            if (panel.classList.contains('collapsed')) {
                 // We might want to add a class to chat content to limit width
                 // But chat content structure is simple divs.
                 // Let's just let it expand for now, or add a class to body
                 document.body.classList.add('sidebar-collapsed');
            } else {
                 document.body.classList.remove('sidebar-collapsed');
            }
            updateRoleNavigationButton();
        }, 300); // Wait for transition
    }

    // --- SETTINGS LOGIC ---
    const API_SETTINGS_STORAGE_KEY = 'saya_api_settings_v1';

    function getSavedApiSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(API_SETTINGS_STORAGE_KEY) || '{}');
            return saved && typeof saved === 'object' ? saved : {};
        } catch (e) {
            console.warn("Saved API settings are invalid; using defaults.", e);
            return {};
        }
    }

    function saveApiSettings(updates = {}) {
        const settings = {...getSavedApiSettings(), ...updates};
        localStorage.setItem(API_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
        return settings;
    }

    function saveCurrentApiSettings() {
        saveApiSettings({
            api_url: document.getElementById('api-url-input')?.value.trim() || '',
            api_key: document.getElementById('api-key-input')?.value.trim() || '',
            api_model_name: document.getElementById('api-model-input')?.value.trim() || '',
            multimodal_model_name: document.getElementById('api-multimodal-model-input')?.value.trim() || '',
            strong_logic_model_name: document.getElementById('api-strong-logic-model-input')?.value.trim() || '',
            memory_fast_model_name: document.getElementById('api-memory-fast-model-input')?.value.trim() || '',
            memory_strong_model_name: document.getElementById('api-memory-strong-model-input')?.value.trim() || ''
        });
    }

    function loadApiSettings() {
        const settings = getSavedApiSettings();

        const apiUrlInput = document.getElementById('api-url-input');
        const apiKeyInput = document.getElementById('api-key-input');
        const apiModelInput = document.getElementById('api-model-input');
        const multimodalModelInput = document.getElementById('api-multimodal-model-input');
        const strongLogicModelInput = document.getElementById('api-strong-logic-model-input');
        const memoryFastModelInput = document.getElementById('api-memory-fast-model-input');
        const memoryStrongModelInput = document.getElementById('api-memory-strong-model-input');

        if (apiUrlInput && settings.api_url) apiUrlInput.value = settings.api_url;
        if (apiKeyInput && settings.api_key) apiKeyInput.value = settings.api_key;
        if (apiModelInput && settings.api_model_name) apiModelInput.value = settings.api_model_name;
        if (multimodalModelInput && settings.multimodal_model_name) multimodalModelInput.value = settings.multimodal_model_name;
        if (strongLogicModelInput && settings.strong_logic_model_name) strongLogicModelInput.value = settings.strong_logic_model_name;
        if (memoryFastModelInput && settings.memory_fast_model_name) memoryFastModelInput.value = settings.memory_fast_model_name;
        if (memoryStrongModelInput && settings.memory_strong_model_name) memoryStrongModelInput.value = settings.memory_strong_model_name;

        [apiUrlInput, apiKeyInput, apiModelInput, multimodalModelInput, strongLogicModelInput, memoryFastModelInput, memoryStrongModelInput]
            .filter(Boolean)
            .forEach(input => { input.oninput = saveCurrentApiSettings; });
    }

    function clearSavedApiSettings() {
        if (!confirm("Forget all saved API URLs, model names, and API keys in this browser?")) return;
        localStorage.removeItem(API_SETTINGS_STORAGE_KEY);

        const apiUrlInput = document.getElementById('api-url-input');
        const apiKeyInput = document.getElementById('api-key-input');
        const apiModelInput = document.getElementById('api-model-input');
        const multimodalModelInput = document.getElementById('api-multimodal-model-input');
        const strongLogicModelInput = document.getElementById('api-strong-logic-model-input');
        const memoryFastModelInput = document.getElementById('api-memory-fast-model-input');
        const memoryStrongModelInput = document.getElementById('api-memory-strong-model-input');
        if (apiUrlInput) apiUrlInput.value = 'https://newapi.xiaoqianran.xyz/v1';
        if (apiKeyInput) apiKeyInput.value = '';
        if (apiModelInput) apiModelInput.value = 'openai/gpt-oss-120b';
        if (multimodalModelInput) multimodalModelInput.value = '';
        if (strongLogicModelInput) strongLogicModelInput.value = '';
        if (memoryFastModelInput) memoryFastModelInput.value = '';
        if (memoryStrongModelInput) memoryStrongModelInput.value = '';
        localStorage.setItem(ASSISTANT_STRONG_LOGIC_STORAGE_KEY, 'false');
        syncAssistantStrongLogicToggle();
        alert("Saved API information has been cleared.");
    }

    const OTHER_API_SETTINGS_STORAGE_KEY = 'saya_other_api_settings_v1';
    const DEFAULT_OTHER_API_SETTINGS = Object.freeze({
        translation_secret_id: '',
        translation_secret_key: '',
        translation_region: 'ap-guangzhou',
        translation_endpoint: 'tmt.tencentcloudapi.com',
        tts_api_url: 'https://api.elevenlabs.io/v1',
        tts_api_key: '',
        tts_voice_id: '',
        tts_model_id: 'eleven_multilingual_v2'
    });

    function getSavedOtherApiSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(OTHER_API_SETTINGS_STORAGE_KEY) || '{}');
            return {...DEFAULT_OTHER_API_SETTINGS, ...(saved && typeof saved === 'object' ? saved : {})};
        } catch (e) {
            console.warn('Saved Other API settings are invalid; using defaults.', e);
            return {...DEFAULT_OTHER_API_SETTINGS};
        }
    }

    function collectOtherApiSettings() {
        return {
            translation_secret_id: document.getElementById('translation-secret-id-input')?.value.trim() || '',
            translation_secret_key: document.getElementById('translation-secret-key-input')?.value.trim() || '',
            translation_region: document.getElementById('translation-region-input')?.value.trim() || DEFAULT_OTHER_API_SETTINGS.translation_region,
            translation_endpoint: document.getElementById('translation-endpoint-input')?.value.trim() || DEFAULT_OTHER_API_SETTINGS.translation_endpoint,
            tts_api_url: document.getElementById('tts-api-url-input')?.value.trim() || DEFAULT_OTHER_API_SETTINGS.tts_api_url,
            tts_api_key: document.getElementById('tts-api-key-input')?.value.trim() || '',
            tts_voice_id: document.getElementById('tts-voice-id-input')?.value.trim() || '',
            tts_model_id: document.getElementById('tts-model-id-input')?.value.trim() || DEFAULT_OTHER_API_SETTINGS.tts_model_id
        };
    }

    function saveOtherApiSettings(settings = collectOtherApiSettings()) {
        const clean = {...DEFAULT_OTHER_API_SETTINGS, ...(settings || {})};
        localStorage.setItem(OTHER_API_SETTINGS_STORAGE_KEY, JSON.stringify(clean));
        return clean;
    }

    function applyOtherApiSettings(settings) {
        const fieldMap = {
            'translation-secret-id-input': 'translation_secret_id',
            'translation-secret-key-input': 'translation_secret_key',
            'translation-region-input': 'translation_region',
            'translation-endpoint-input': 'translation_endpoint',
            'tts-api-url-input': 'tts_api_url',
            'tts-api-key-input': 'tts_api_key',
            'tts-voice-id-input': 'tts_voice_id',
            'tts-model-id-input': 'tts_model_id'
        };
        Object.entries(fieldMap).forEach(([id, key]) => {
            const input = document.getElementById(id);
            if (!input) return;
            input.value = settings[key] || '';
            input.oninput = () => saveOtherApiSettings(collectOtherApiSettings());
        });
    }

    function openOtherApiSettingsModal() {
        const menu = document.getElementById('settings-menu');
        if (menu && menu.style.display === 'flex') menu.style.display = 'none';
        applyOtherApiSettings(getSavedOtherApiSettings());
        document.getElementById('other-api-settings-modal').style.display = 'flex';
    }

    function closeOtherApiSettingsModal() {
        document.getElementById('other-api-settings-modal').style.display = 'none';
    }

    function saveOtherApiSettingsFromModal() {
        saveOtherApiSettings();
        alert('Other API settings saved.');
        closeOtherApiSettingsModal();
    }

    function clearOtherApiSettings() {
        if (!confirm('Reset saved Translation and TTS API settings?')) return;
        localStorage.removeItem(OTHER_API_SETTINGS_STORAGE_KEY);
        applyOtherApiSettings({...DEFAULT_OTHER_API_SETTINGS});
    }

    const OUTPUT_LIMIT_STORAGE_KEY = 'saya_output_limits_v1';
    const DEFAULT_OUTPUT_LIMITS = Object.freeze({
        api: 8192,
        qwen3_30b_q5: 4096,
        mistral_22b_q6: 4096,
        mistral_22b_q5: 4096,
        saya_r1_q5: 4096
    });

    function normalizeOutputLimit(value, model = 'api') {
        const parsed = Number.parseInt(value, 10);
        const fallback = DEFAULT_OUTPUT_LIMITS[model] || DEFAULT_OUTPUT_LIMITS.api;
        if (!Number.isFinite(parsed)) return fallback;
        const maximum = model === 'api' ? 65536 : 8192;
        return Math.min(maximum, Math.max(256, parsed));
    }

    function getSavedOutputLimits() {
        let saved = {};
        try {
            saved = JSON.parse(localStorage.getItem(OUTPUT_LIMIT_STORAGE_KEY) || '{}');
        } catch (e) {
            console.warn('Saved output limits are invalid; using defaults.', e);
        }
        return Object.fromEntries(Object.keys(DEFAULT_OUTPUT_LIMITS).map(model => [
            model,
            normalizeOutputLimit(saved?.[model], model)
        ]));
    }

    function loadOutputLimits() {
        const limits = getSavedOutputLimits();
        Object.entries(limits).forEach(([model, value]) => {
            const input = document.getElementById(`output-limit-${model}`);
            if (input) input.value = String(value);
        });
    }

    function saveOutputLimitsFromModal() {
        const limits = {};
        Object.keys(DEFAULT_OUTPUT_LIMITS).forEach(model => {
            limits[model] = normalizeOutputLimit(document.getElementById(`output-limit-${model}`)?.value, model);
        });
        localStorage.setItem(OUTPUT_LIMIT_STORAGE_KEY, JSON.stringify(limits));
        loadOutputLimits();
        closeOutputLimitsModal();
    }

    function resetOutputLimits() {
        localStorage.setItem(OUTPUT_LIMIT_STORAGE_KEY, JSON.stringify(DEFAULT_OUTPUT_LIMITS));
        loadOutputLimits();
    }

    function openOutputLimitsModal() {
        const menu = document.getElementById('settings-menu');
        if (menu) menu.style.display = 'none';
        loadOutputLimits();
        const modal = document.getElementById('output-limits-modal');
        if (modal) modal.style.display = 'flex';
    }

    function closeOutputLimitsModal() {
        const modal = document.getElementById('output-limits-modal');
        if (modal) modal.style.display = 'none';
    }

    function getOutputLimitForModel(model) {
        const key = Object.prototype.hasOwnProperty.call(DEFAULT_OUTPUT_LIMITS, model) ? model : 'api';
        return getSavedOutputLimits()[key];
    }

    const ASSISTANT_STRONG_LOGIC_STORAGE_KEY = 'saya_assistant_strong_logic_enabled_v3';

    function isAssistantStrongLogicEnabled() {
        return localStorage.getItem(ASSISTANT_STRONG_LOGIC_STORAGE_KEY) === 'true';
    }

    function syncAssistantStrongLogicToggle() {
        const toggle = document.getElementById('assistant-strong-logic-toggle');
        const enabled = isAssistantStrongLogicEnabled();
        if (toggle) {
            toggle.classList.toggle('on', enabled);
            toggle.setAttribute('aria-checked', enabled ? 'true' : 'false');
            toggle.setAttribute('aria-label', enabled ? 'Strong Logic on' : 'Strong Logic off');
            toggle.title = enabled ? 'Strong Logic: On' : 'Strong Logic: Off';
        }
        const stateLabel = document.getElementById('assistant-strong-logic-state');
        if (stateLabel) stateLabel.textContent = enabled ? 'ON' : 'OFF';
    }

    function toggleAssistantStrongLogic() {
        setAssistantStrongLogicEnabled(!isAssistantStrongLogicEnabled());
    }

    function setAssistantStrongLogicEnabled(enabled) {
        if (enabled) {
            const settings = getSavedApiSettings();
            const modelName = document.getElementById('api-strong-logic-model-input')?.value.trim() || settings.strong_logic_model_name || '';
            const apiKey = document.getElementById('api-key-input')?.value.trim() || settings.api_key || '';
            if (!modelName || !apiKey) {
                localStorage.setItem(ASSISTANT_STRONG_LOGIC_STORAGE_KEY, 'false');
                syncAssistantStrongLogicToggle();
                alert('Configure both API Key and Strong Logic Model in LLM API first.');
                closeAssistantRoleModal();
                openApiSettingsModal();
                return;
            }
        }
        localStorage.setItem(ASSISTANT_STRONG_LOGIC_STORAGE_KEY, enabled ? 'true' : 'false');
        syncAssistantStrongLogicToggle();
    }

    // --- USER PROFILE CARD ---
    const USER_PROFILE_STORAGE_KEY = 'saya_user_profile_v1';
    const DEFAULT_USER_PROFILE = {
        name: 'Doctor',
        avatar: ''
    };

    function getSavedUserProfile() {
        try {
            const saved = JSON.parse(localStorage.getItem(USER_PROFILE_STORAGE_KEY) || '{}');
            return {
                ...DEFAULT_USER_PROFILE,
                ...(saved && typeof saved === 'object' ? saved : {})
            };
        } catch (e) {
            console.warn("Saved user profile is invalid; using defaults.", e);
            return {...DEFAULT_USER_PROFILE};
        }
    }

    function saveUserProfile(profile) {
        const clean = {
            name: String(profile?.name || DEFAULT_USER_PROFILE.name).trim().slice(0, 40) || DEFAULT_USER_PROFILE.name,
            avatar: String(profile?.avatar || '').trim()
        };
        localStorage.setItem(USER_PROFILE_STORAGE_KEY, JSON.stringify(clean));
        return clean;
    }

    function profileInitials(name) {
        const clean = String(name || DEFAULT_USER_PROFILE.name).trim();
        const parts = clean.split(/\s+/).filter(Boolean);
        if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
        return clean.slice(0, 2).toUpperCase() || 'D';
    }

    function setProfileAvatar(imgEl, fallbackEl, avatar, name) {
        if (!imgEl || !fallbackEl) return;
        fallbackEl.textContent = profileInitials(name);
        imgEl.onload = () => {
            imgEl.style.display = 'block';
            fallbackEl.style.display = 'none';
        };
        imgEl.onerror = () => {
            imgEl.style.display = 'none';
            fallbackEl.style.display = 'inline';
            imgEl.removeAttribute('src');
        };
        if (avatar) {
            imgEl.src = avatar;
        } else {
            imgEl.style.display = 'none';
            fallbackEl.style.display = 'inline';
            imgEl.removeAttribute('src');
        }
    }

    function renderUserProfile() {
        const profile = getSavedUserProfile();
        const nameEl = document.getElementById('user-profile-name');
        if (nameEl) nameEl.textContent = profile.name;
        setProfileAvatar(
            document.getElementById('user-profile-avatar-img'),
            document.getElementById('user-profile-avatar-fallback'),
            profile.avatar,
            profile.name
        );
    }

    function loadUserProfileIntoModal() {
        const profile = getSavedUserProfile();
        const nameInput = document.getElementById('user-profile-name-input');
        const avatarInput = document.getElementById('user-profile-avatar-input');
        if (nameInput) nameInput.value = profile.name;
        if (avatarInput) avatarInput.value = profile.avatar;
        setProfileAvatar(
            document.getElementById('user-profile-preview-img'),
            document.getElementById('user-profile-preview-fallback'),
            profile.avatar,
            profile.name
        );
    }

    function openUserProfileModal(event) {
        if (event?.target?.closest('.user-settings-button')) return;
        const menu = document.getElementById('settings-menu');
        if (menu) menu.style.display = 'none';
        loadUserProfileIntoModal();
        document.getElementById('user-profile-modal').style.display = 'flex';
    }

    function closeUserProfileModal() {
        document.getElementById('user-profile-modal').style.display = 'none';
    }

    function previewUserAvatarUrl(value) {
        const name = document.getElementById('user-profile-name-input')?.value || DEFAULT_USER_PROFILE.name;
        setProfileAvatar(
            document.getElementById('user-profile-preview-img'),
            document.getElementById('user-profile-preview-fallback'),
            String(value || '').trim(),
            name
        );
    }

    function clearUserAvatarInput() {
        const input = document.getElementById('user-profile-avatar-input');
        const fileInput = document.getElementById('user-profile-avatar-file');
        if (input) input.value = '';
        if (fileInput) fileInput.value = '';
        previewUserAvatarUrl('');
    }

    function handleUserAvatarFile(input) {
        const file = input?.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => {
            const avatarInput = document.getElementById('user-profile-avatar-input');
            if (avatarInput) avatarInput.value = String(reader.result || '');
            previewUserAvatarUrl(reader.result);
        };
        reader.onerror = () => alert("Avatar image could not be loaded.");
        reader.readAsDataURL(file);
    }

    function saveUserProfileFromModal() {
        const profile = saveUserProfile({
            name: document.getElementById('user-profile-name-input')?.value,
            avatar: document.getElementById('user-profile-avatar-input')?.value
        });
        renderUserProfile();
        closeUserProfileModal();
        return profile;
    }

    // --- SAYA STYLE LOGIC ---
    const SAYA_STYLE_STORAGE_KEY = 'saya_style_settings_v1';
    const SAYA_STYLE_API_URL = '/api/saya-style/settings';
    const DEFAULT_SAYA_STYLE = {
        base_style: 'balanced',
        response_language: 'auto',
        attachment_level: 'medium',
        sociality: 'normal',
        memory_imagination: 'normal',
        tag_mode: 'normal',
        allow_emoji: false,
        readable: false
    };

    function getSayaStyleSettings() {
        try {
            const saved = JSON.parse(localStorage.getItem(SAYA_STYLE_STORAGE_KEY) || '{}');
            return {...DEFAULT_SAYA_STYLE, ...(saved && typeof saved === 'object' ? saved : {})};
        } catch (e) {
            console.warn("Saya style settings are invalid; using defaults.", e);
            return {...DEFAULT_SAYA_STYLE};
        }
    }

    function saveSayaStyleSettings(settings) {
        const clean = {
            base_style: String(settings?.base_style || DEFAULT_SAYA_STYLE.base_style),
            response_language: ['auto', 'zh-cn', 'zh-tw', 'en', 'ja', 'ko', 'es', 'fr', 'de', 'ru', 'pt', 'ar', 'hi', 'it'].includes(String(settings?.response_language || '').toLowerCase())
                ? String(settings.response_language).toLowerCase()
                : DEFAULT_SAYA_STYLE.response_language,
            attachment_level: ['low', 'medium', 'high', 'extreme'].includes(String(settings?.attachment_level || '').toLowerCase())
                ? String(settings.attachment_level).toLowerCase()
                : DEFAULT_SAYA_STYLE.attachment_level,
            sociality: ['expectant', 'normal', 'aloof', 'averse'].includes(String(settings?.sociality || '').toLowerCase())
                ? String(settings.sociality).toLowerCase()
                : DEFAULT_SAYA_STYLE.sociality,
            memory_imagination: ['off', 'normal', 'on'].includes(String(settings?.memory_imagination || '').toLowerCase())
                ? String(settings.memory_imagination).toLowerCase()
                : DEFAULT_SAYA_STYLE.memory_imagination,
            tag_mode: String(settings?.tag_mode || DEFAULT_SAYA_STYLE.tag_mode),
            allow_emoji: Boolean(settings?.allow_emoji),
            readable: Boolean(settings?.readable)
        };
        localStorage.setItem(SAYA_STYLE_STORAGE_KEY, JSON.stringify(clean));
        return clean;
    }

    function applySayaStyleToModal(settings) {
        const base = document.getElementById('saya-style-base');
        const language = document.getElementById('saya-style-language');
        const attachment = document.getElementById('saya-style-attachment');
        const sociality = document.getElementById('saya-style-sociality');
        const memoryImagination = document.getElementById('saya-style-memory-imagination');
        const tags = document.getElementById('saya-style-tags');
        const emoji = document.getElementById('saya-style-emoji');
        const readable = document.getElementById('saya-style-readable');
        if (base) base.value = settings.base_style;
        if (language) language.value = settings.response_language;
        if (attachment) attachment.value = settings.attachment_level;
        if (sociality) sociality.value = settings.sociality;
        if (memoryImagination) memoryImagination.value = settings.memory_imagination;
        if (tags) tags.value = settings.tag_mode;
        if (emoji) emoji.value = settings.allow_emoji ? 'on' : 'off';
        if (readable) readable.value = settings.readable ? 'on' : 'off';
    }

    async function persistSayaStyleSettings(settings) {
        const response = await fetch(SAYA_STYLE_API_URL, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({settings})
        });
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            throw new Error(data.detail || data.message || 'Unable to save Saya style settings');
        }
        return saveSayaStyleSettings(data.settings || settings);
    }

    async function loadSayaStyleIntoModal() {
        let settings = getSayaStyleSettings();
        try {
            const response = await fetch(SAYA_STYLE_API_URL);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Unable to load Saya style settings');
            if (data.exists) {
                settings = saveSayaStyleSettings(data.settings || settings);
            } else {
                settings = await persistSayaStyleSettings(settings);
            }
        } catch (e) {
            console.warn('Using local Saya style settings because server sync failed.', e);
        }
        applySayaStyleToModal(settings);
    }

    function collectSayaStyleFromModal() {
        return {
            base_style: document.getElementById('saya-style-base')?.value || DEFAULT_SAYA_STYLE.base_style,
            response_language: document.getElementById('saya-style-language')?.value || DEFAULT_SAYA_STYLE.response_language,
            attachment_level: document.getElementById('saya-style-attachment')?.value || DEFAULT_SAYA_STYLE.attachment_level,
            sociality: document.getElementById('saya-style-sociality')?.value || DEFAULT_SAYA_STYLE.sociality,
            memory_imagination: document.getElementById('saya-style-memory-imagination')?.value || DEFAULT_SAYA_STYLE.memory_imagination,
            tag_mode: document.getElementById('saya-style-tags')?.value || DEFAULT_SAYA_STYLE.tag_mode,
            allow_emoji: document.getElementById('saya-style-emoji')?.value === 'on',
            readable: document.getElementById('saya-style-readable')?.value === 'on'
        };
    }

    async function openSayaStyleModal() {
        const menu = document.getElementById('settings-menu');
        if (menu && menu.style.display === 'flex') menu.style.display = 'none';
        document.getElementById('saya-style-modal').style.display = 'flex';
        await loadSayaStyleIntoModal();
    }

    function closeSayaStyleModal() {
        document.getElementById('saya-style-modal').style.display = 'none';
    }

    async function saveSayaStyleSettingsFromModal() {
        const settings = saveSayaStyleSettings(collectSayaStyleFromModal());
        try {
            await persistSayaStyleSettings(settings);
            alert("Saya style saved.");
            closeSayaStyleModal();
        } catch (e) {
            alert("Saya style is saved in this browser but could not be synced: " + (e.message || e));
        }
    }

    async function resetSayaStyleSettings() {
        const settings = saveSayaStyleSettings(DEFAULT_SAYA_STYLE);
        try {
            await persistSayaStyleSettings(settings);
        } catch (e) {
            alert("Saya style reset locally but could not be synced: " + (e.message || e));
        }
        applySayaStyleToModal(settings);
    }

    function openApiSettingsModal() {
        const menu = document.getElementById('settings-menu');
        if (menu && menu.style.display === 'flex') menu.style.display = 'none';
        loadApiSettings();
        document.getElementById('api-settings-modal').style.display = 'flex';
    }

    function closeApiSettingsModal() {
        document.getElementById('api-settings-modal').style.display = 'none';
    }

    function saveApiSettingsFromModal() {
        saveCurrentApiSettings();
        alert("LLM API settings saved.");
        closeApiSettingsModal();
    }

    // Initialize on load
    document.addEventListener('DOMContentLoaded', () => {
        loadApiSettings();
        renderUserProfile();
        setCurrentRoleMode(currentRoleMode || '', {persist: false});
        const chatContent = document.getElementById('chat-content');
        if (chatContent) {
            chatContent.addEventListener('scroll', updateQuestionIndexActiveState, {passive: true});
            new MutationObserver(refreshQuestionIndex).observe(chatContent, {childList: true});
        }
        refreshQuestionIndex();
    });

    function positionSettingsMenu() {
        const menu = document.getElementById('settings-menu');
        if (!menu) return false;
        const panel = document.querySelector('.model-panel');
        if (!panel || panel.classList.contains('collapsed')) {
            menu.style.display = 'none';
            return false;
        }
        const profileCard = document.getElementById('user-profile-card');
        const panelRect = panel?.getBoundingClientRect();
        const profileRect = profileCard?.getBoundingClientRect();
        const computedMenuWidth = parseFloat(window.getComputedStyle(menu).width) || 200;
        const menuWidth = menu.getBoundingClientRect().width || computedMenuWidth;
        const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
        const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
        const anchorRight = panelRect && panelRect.width > 1 ? panelRect.right : 0;
        const maxLeft = Math.max(0, viewportWidth - menuWidth - 8);
        const left = Math.min(maxLeft, Math.max(0, anchorRight));
        const bottom = profileRect
            ? Math.max(8, viewportHeight - profileRect.bottom)
            : 18;
        menu.style.left = `${left}px`;
        menu.style.right = 'auto';
        menu.style.bottom = `${bottom}px`;
        return true;
    }

    function toggleSettings(event) {
        if (event) event.stopPropagation();
        const menu = document.getElementById('settings-menu');
        if (menu.style.display === 'flex') {
            menu.style.display = 'none';
        } else {
            if (positionSettingsMenu()) {
                menu.style.display = 'flex';
            }
        }
    }

    // Close settings when clicking outside
    document.addEventListener('click', function(event) {
        const menu = document.getElementById('settings-menu');
        const settingsTrigger = event.target.closest('.btn-settings');
        if (menu && menu.style.display === 'flex' && !menu.contains(event.target) && !settingsTrigger) {
            menu.style.display = 'none';
        }
    });

    window.addEventListener('resize', () => {
        const menu = document.getElementById('settings-menu');
        if (menu && menu.style.display === 'flex') {
            positionSettingsMenu();
        }
    });

    // --- PROMPT LOGIC ---
    function openPromptModal() {
        toggleSettings();
        document.getElementById('prompt-modal').style.display = 'flex';
        loadPrompt();
    }

    function closePromptModal() {
        document.getElementById('prompt-modal').style.display = 'none';
        // Reset state
        const editor = document.getElementById('system-prompt-editor');
        editor.disabled = true;
        editor.style.borderColor = '#444';
        document.getElementById('btn-unlock-prompt').style.display = 'block';
        document.getElementById('btn-save-prompt').style.display = 'none';
    }

    async function loadPrompt() {
        const editor = document.getElementById('system-prompt-editor');
        editor.value = "Loading...";
        try {
            const res = await fetch('/api/system_prompt');
            const data = await res.json();
            editor.value = data.prompt;
        } catch(e) {
            editor.value = "Error loading prompt.";
        }
    }

    function unlockPrompt() {
        if(confirm("⚠️ Warning: Changing the system prompt can fundamentally alter Saya's behavior and personality.\n\nAre you sure you want to unlock editing?")) {
            const editor = document.getElementById('system-prompt-editor');
            editor.disabled = false;
            editor.style.borderColor = '#ce4242';
            document.getElementById('btn-unlock-prompt').style.display = 'none';
            document.getElementById('btn-save-prompt').style.display = 'block';
        }
    }

    async function savePrompt() {
        if(!confirm("Apply changes? This will immediately affect all subsequent responses.")) return;
        
        const editor = document.getElementById('system-prompt-editor');
        const newPrompt = editor.value;
        
        try {
            const res = await fetch('/api/system_prompt/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({new_prompt: newPrompt})
            });
            const data = await res.json();
            if(data.status === 'success') {
                alert("Prompt updated successfully.");
                closePromptModal();
            } else {
                alert("Update failed: " + data.message);
            }
        } catch(e) {
            alert("Error saving prompt: " + e);
        }
    }

    // --- MEMORY SYSTEM UI ---
    let memoryAutoSave = true;
    let memoryAutoMergeEnabled = false;
    let memoryAutoMergeDays = 7;
    let memory2Enabled = true;
    let memoryRelevantCount = 8;
    let memoryIncludeMetadata = true;
    let memoryViewerIncludeArchived = false;
    let memoryViewerCategory = '';
    let checkinState = null;
    let checkinSelectedDate = null;
    let checkinViewYear = null;
    let checkinViewMonth = null;
    let pendingCheckinContext = "";
    let currentProjectId = "";
    let projectSidebarViewId = "";
    let projectsCache = [];
    let currentQuestTab = 'active';
    let currentRoleMode = localStorage.getItem('saya_current_role_mode') || "";
    let assistantRoleState = null;
    let assistantRoleEntryMemoryStamp = localStorage.getItem('saya_assistant_role_entry_memory_stamp') || "";
    let assistantMemoryChangedSinceEntry = localStorage.getItem('saya_assistant_role_memory_changed') === 'true';
    currentProjectId = localStorage.getItem('saya_current_project_id') || "";

    // --- DAILY CHECK-IN LOGIC ---
    function checkinStatusClass(status) {
        return ['completed', 'partial', 'missed', 'pending', 'future', 'no_tasks'].includes(status)
            ? status
            : 'pending';
    }

    function checkinDayStatusClass(day) {
        if (day?.date && checkinState?.today && day.date > checkinState.today) {
            return 'future';
        }
        return checkinStatusClass(day?.status);
    }

    function formatCheckinRate(value) {
        return `${Math.round(Number(value || 0) * 100)}%`;
    }

    function createCheckinLocalId(prefix = 'daily') {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return `${prefix}-${window.crypto.randomUUID()}`;
        }
        return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function checkinMonthKey(year, month) {
        return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
    }

    function isCheckinMonthAllowed(year, month) {
        const windowInfo = checkinState?.date_window;
        if (!windowInfo) return true;
        const key = checkinMonthKey(year, month);
        return key >= windowInfo.min_month && key <= windowInfo.max_month;
    }

    function isCheckinDateAllowed(date) {
        const windowInfo = checkinState?.date_window;
        if (!windowInfo || !date) return true;
        return date >= windowInfo.min_date && date <= windowInfo.max_date;
    }

    function checkinWindowText() {
        const windowInfo = checkinState?.date_window;
        if (!windowInfo) return '';
        return `${windowInfo.min_month} to ${windowInfo.max_month}`;
    }

    function heatColorForStatus(status) {
        const palette = {
            completed: '#0b3d78',
            active: '#0b3d78',
            partial: '#3d155f',
            no_tasks: '#3d155f',
            inactive: '#3d155f',
            missed: '#65151f',
            pending: '#111111',
            future: '#050505'
        };
        return palette[status] || '#111111';
    }

    function renderContinuousHeatStrip(container, statuses) {
        if (!container) return;
        const clean = (statuses || []).map(status => heatColorForStatus(status));
        if (!clean.length) {
            container.style.setProperty('--heat-strip', '#050505');
            return;
        }
        if (clean.length === 1) {
            container.style.setProperty('--heat-strip', clean[0]);
            return;
        }
        const stops = clean.map((color, index) => {
            const position = (index / (clean.length - 1)) * 100;
            return `${color} ${position.toFixed(2)}%`;
        });
        container.style.setProperty('--heat-strip', `linear-gradient(90deg, ${stops.join(', ')})`);
    }

    async function loadCheckinState(options = {}) {
        const now = new Date();
        if (!checkinViewYear) checkinViewYear = now.getFullYear();
        if (!checkinViewMonth) checkinViewMonth = now.getMonth() + 1;
        if (!checkinSelectedDate) {
            const yyyy = now.getFullYear();
            const mm = String(now.getMonth() + 1).padStart(2, '0');
            const dd = String(now.getDate()).padStart(2, '0');
            checkinSelectedDate = `${yyyy}-${mm}-${dd}`;
        }

        const params = new URLSearchParams({
            year: String(checkinViewYear),
            month: String(checkinViewMonth),
            selected_date: checkinSelectedDate
        });
        const response = await fetch(`/api/checkins/status?${params.toString()}`);
        if (!response.ok) throw new Error(`Check-in status failed: ${response.status}`);
        checkinState = await response.json();
        if (checkinState.year) checkinViewYear = checkinState.year;
        if (checkinState.month) checkinViewMonth = checkinState.month;
        if (checkinState.selected_date) checkinSelectedDate = checkinState.selected_date;
        if (checkinState.auto_report?.generated?.length) {
            console.info("Auto-saved check-in report:", checkinState.auto_report.generated);
        }
        renderCheckinPeek();
        if (options.renderModal) renderCheckinModal();
        return checkinState;
    }

    async function initializeCheckinPeek() {
        try {
            await loadCheckinState();
        } catch (e) {
            console.warn("Unable to load check-in state", e);
        }
    }

    async function loadSessionStats() {
        try {
            const response = await fetch('/api/sessions/stats');
            const stats = await response.json();
            renderSearchPeek(stats);
        } catch (e) {
            console.warn("Unable to load chat activity stats", e);
        }
    }

    function renderSearchPeek(stats) {
        const chatCountEl = document.getElementById('search-peek-chat-count');
        const dayCountEl = document.getElementById('search-peek-day-count');
        const dotsEl = document.getElementById('search-peek-dots');
        if (!stats || !chatCountEl || !dayCountEl || !dotsEl) return;

        chatCountEl.innerText = `Chats: ${Number(stats.total_sessions || 0)}`;
        dayCountEl.innerText = `Active days: ${Number(stats.active_days_total || 0)}`;

        dotsEl.innerHTML = '';
        const heatStatuses = [];
        (stats.month_days || []).forEach(day => {
            const dot = document.createElement('span');
            const status = ['active', 'inactive', 'future'].includes(day.status) ? day.status : 'inactive';
            heatStatuses.push(status);
            dot.className = `chat-dot ${status}`;
            dot.title = `${day.date}: ${status === 'active' ? 'chatted' : status === 'future' ? 'future' : 'no chat'}`;
            dotsEl.appendChild(dot);
        });
        renderContinuousHeatStrip(dotsEl, heatStatuses);
    }

    // --- INPUT SUGGESTIONS ---
    const suggestionLanguageAliases = {
        'zh': 'zh-cn',
        'zh-hans': 'zh-cn',
        'zh-cn': 'zh-cn',
        'zh-sg': 'zh-cn',
        'zh-hant': 'zh-tw',
        'zh-tw': 'zh-tw',
        'zh-hk': 'zh-tw',
        'zh-mo': 'zh-tw',
        'en': 'en',
        'ja': 'ja',
        'ko': 'ko',
        'es': 'es',
        'fr': 'fr',
        'de': 'de',
        'ru': 'ru',
        'pt': 'pt',
        'ar': 'ar',
        'hi': 'hi',
        'it': 'it'
    };

    const localizedSuggestionTexts = {
        tone_smiling: {
            'zh-cn': '笑着说：',
            'zh-tw': '笑著說：',
            en: 'says with a smile:',
            ja: '笑顔で言う：',
            ko: '웃으며 말한다:',
            es: 'dice sonriendo:',
            fr: 'dit avec un sourire :',
            de: 'sagt lächelnd:',
            ru: 'говорит с улыбкой:',
            pt: 'diz sorrindo:',
            ar: 'تقول بابتسامة:',
            hi: 'मुस्कुराकर कहती है:',
            it: 'dice sorridendo:'
        },
        tone_sad: {
            'zh-cn': '有点悲伤地说：',
            'zh-tw': '有點悲傷地說：',
            en: 'says a little sadly:',
            ja: '少し悲しそうに言う：',
            ko: '조금 슬프게 말한다:',
            es: 'dice con un poco de tristeza:',
            fr: 'dit avec un peu de tristesse :',
            de: 'sagt etwas traurig:',
            ru: 'говорит немного грустно:',
            pt: 'diz um pouco triste:',
            ar: 'تقول بحزن خفيف:',
            hi: 'थोड़ा उदास होकर कहती है:',
            it: "dice con un po' di tristezza:"
        },
        tone_annoyed: {
            'zh-cn': '有点懊恼，但还是想好好说：',
            'zh-tw': '有點懊惱，但還是想好好說：',
            en: 'says, a little annoyed but still trying to be kind:',
            ja: '少し苛立ちながらも、ちゃんと伝えようとして言う：',
            ko: '조금 짜증이 나지만 그래도 다정하게 말하려고 한다:',
            es: 'dice, un poco molesto pero intentando hablar con calma:',
            fr: 'dit, un peu agacé mais en essayant de rester doux :',
            de: 'sagt, etwas genervt, aber weiterhin freundlich:',
            ru: 'говорит немного раздраженно, но стараясь быть мягко:',
            pt: 'diz, um pouco irritado mas tentando falar com carinho:',
            ar: 'تقول بانزعاج خفيف لكنها تحاول الكلام بلطف:',
            hi: 'थोड़ी झुंझलाहट के साथ, फिर भी नरमी से कहती है:',
            it: "dice, un po' infastidita ma cercando di essere gentile:"
        },
        tone_soft: {
            'zh-cn': '轻声说：',
            'zh-tw': '輕聲說：',
            en: 'says softly:',
            ja: 'やさしく言う：',
            ko: '부드럽게 말한다:',
            es: 'dice suavemente:',
            fr: 'dit doucement :',
            de: 'sagt leise:',
            ru: 'говорит мягко:',
            pt: 'diz suavemente:',
            ar: 'تقول بلطف:',
            hi: 'धीरे से कहती है:',
            it: 'dice piano:'
        },
        tone_shy: {
            'zh-cn': '有点害羞地说：',
            'zh-tw': '有點害羞地說：',
            en: 'says a little shyly:',
            ja: '少し恥ずかしそうに言う：',
            ko: '조금 부끄러워하며 말한다:',
            es: 'dice con un poco de timidez:',
            fr: 'dit avec un peu de timidité :',
            de: 'sagt etwas schüchtern:',
            ru: 'говорит немного смущенно:',
            pt: 'diz um pouco timidamente:',
            ar: 'تقول بخجل قليل:',
            hi: 'थोड़ा शर्माकर कहती है:',
            it: "dice un po' timidamente:"
        },
        action_pat_head: {
            'zh-cn': '（轻轻摸摸 Saya 的头）',
            'zh-tw': '（輕輕摸摸 Saya 的頭）',
            en: " *gently pats Saya's head* ",
            ja: '（Sayaの頭をそっと撫でる）',
            ko: '（Saya의 머리를 살짝 쓰다듬는다）',
            es: '(acaricia suavemente la cabeza de Saya)',
            fr: '(caresse doucement la tête de Saya)',
            de: '(streichelt Saya sanft über den Kopf)',
            ru: '(нежно гладит Saya по голове)',
            pt: '(acaricia suavemente a cabeça de Saya)',
            ar: '(تربت برفق على رأس Saya)',
            hi: '(धीरे से Saya के सिर पर हाथ फेरता है)',
            it: '(accarezza dolcemente la testa di Saya)'
        },
        action_hold_hand: {
            'zh-cn': '（牵住 Saya 的手）',
            'zh-tw': '（牽住 Saya 的手）',
            en: " *holds Saya's hand for a moment* ",
            ja: '（Sayaの手をそっと握る）',
            ko: '（Saya의 손을 살며시 잡는다）',
            es: '(toma suavemente la mano de Saya)',
            fr: '(prend doucement la main de Saya)',
            de: '(hält sanft Sayas Hand)',
            ru: '(нежно держит Saya за руку)',
            pt: '(segura a mão de Saya com suavidade)',
            ar: '(تمسك يد Saya بلطف)',
            hi: '(धीरे से Saya का हाथ पकड़ता है)',
            it: '(prende dolcemente la mano di Saya)'
        },
        action_eye_contact: {
            'zh-cn': '（认真地看着 Saya）',
            'zh-tw': '（認真地看著 Saya）',
            en: " *looks into Saya's eyes* ",
            ja: '（Sayaの目をまっすぐ見る）',
            ko: '（Saya의 눈을 진지하게 바라본다）',
            es: '(mira a Saya a los ojos)',
            fr: '(regarde Saya dans les yeux)',
            de: '(sieht Saya in die Augen)',
            ru: '(внимательно смотрит Saya в глаза)',
            pt: '(olha nos olhos de Saya)',
            ar: '(تنظر إلى عيني Saya بتركيز)',
            hi: '(ध्यान से Saya की आंखों में देखता है)',
            it: '(guarda Saya negli occhi)'
        },
        action_hug: {
            'zh-cn': '（轻轻抱一下 Saya）',
            'zh-tw': '（輕輕抱一下 Saya）',
            en: ' *gently hugs Saya* ',
            ja: '（Sayaをそっと抱きしめる）',
            ko: '（Saya를 살짝 안아 준다）',
            es: '(abraza suavemente a Saya)',
            fr: '(serre doucement Saya dans ses bras)',
            de: '(umarmt Saya sanft)',
            ru: '(нежно обнимает Saya)',
            pt: '(abraca Saya suavemente)',
            ar: '(تعانق Saya بلطف)',
            hi: '(धीरे से Saya को गले लगाता है)',
            it: '(abbraccia dolcemente Saya)'
        },
        action_brush_hair: {
            'zh-cn': '（小心地帮 Saya 拨开脸侧的头发）',
            'zh-tw': '（小心地幫 Saya 撥開臉側的頭髮）',
            en: " *carefully brushes Saya's hair aside* ",
            ja: '（Sayaの顔にかかった髪をそっと払う）',
            ko: '（Saya의 얼굴 옆 머리카락을 조심히 넘겨 준다）',
            es: '(aparta con cuidado el cabello de Saya)',
            fr: '(écarte doucement les cheveux du visage de Saya)',
            de: '(streicht Saya vorsichtig die Haare aus dem Gesicht)',
            ru: '(осторожно убирает волосы с лица Saya)',
            pt: '(afasta com cuidado o cabelo do rosto de Saya)',
            ar: '(تزيح شعر Saya عن وجهها بحذر)',
            hi: '(सावधानी से Saya के चेहरे से बाल हटाता है)',
            it: '(scosta con cura i capelli dal viso di Saya)'
        }
    };

    const moodSuggestionChips = [
        {label: 'Tone: smiling', key: 'tone_smiling'},
        {label: 'Tone: sad', key: 'tone_sad'},
        {label: 'Tone: annoyed', key: 'tone_annoyed'},
        {label: 'Tone: soft', key: 'tone_soft'},
        {label: 'Tone: shy', key: 'tone_shy'}
    ];

    const actionSuggestionChips = [
        {label: 'Pat Saya’s head', text: ' *gently pats Saya’s head* '},
        {label: 'Hold Saya’s hand', text: ' *holds Saya’s hand for a moment* '},
        {label: 'Look at Saya', text: ' *looks into Saya’s eyes* '},
        {label: 'Give Saya a hug', text: ' *gently hugs Saya* '},
        {label: 'Brush her hair aside', text: ' *carefully brushes Saya’s hair aside* '}
    ];

    const safeActionSuggestionChips = [
        {label: 'Action: pat head', key: 'action_pat_head'},
        {label: 'Action: hold hand', key: 'action_hold_hand'},
        {label: 'Action: eye contact', key: 'action_eye_contact'},
        {label: 'Action: hug', key: 'action_hug'},
        {label: 'Action: brush hair', key: 'action_brush_hair'}
    ];

    function normalizeSuggestionLanguage(language) {
        const raw = String(language || '').trim().toLowerCase();
        if (!raw) return '';
        if (suggestionLanguageAliases[raw]) return suggestionLanguageAliases[raw];
        const primary = raw.split('-')[0];
        return suggestionLanguageAliases[primary] || '';
    }

    function detectSuggestionLanguageFromText(text) {
        const value = String(text || '');
        if (/[\u3040-\u30ff]/.test(value)) return 'ja';
        if (/[\uac00-\ud7af]/.test(value)) return 'ko';
        if (/[\u0600-\u06ff]/.test(value)) return 'ar';
        if (/[\u0900-\u097f]/.test(value)) return 'hi';
        if (/[\u0400-\u04ff]/.test(value)) return 'ru';
        if (/[\u4e00-\u9fff]/.test(value)) return 'zh-cn';
        if (/[A-Za-z]/.test(value)) return 'en';
        return '';
    }

    function getSuggestionLanguage() {
        const configured = String(getSayaStyleSettings().response_language || 'auto').toLowerCase();
        if (configured && configured !== 'auto') {
            return normalizeSuggestionLanguage(configured) || 'zh-cn';
        }
        const inputLanguage = detectSuggestionLanguageFromText(document.getElementById('user-input')?.value || '');
        if (inputLanguage) return inputLanguage;
        return normalizeSuggestionLanguage(navigator.language || '') || 'zh-cn';
    }

    function getSuggestionText(item) {
        if (!item) return '';
        if (typeof item.text === 'string') return item.text;
        const translations = localizedSuggestionTexts[item.key] || {};
        const language = getSuggestionLanguage();
        return translations[language] || translations.en || translations['zh-cn'] || '';
    }

    function insertSuggestionText(text) {
        const input = document.getElementById('user-input');
        if (!input) return;
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? input.value.length;
        const before = input.value.slice(0, start);
        const after = input.value.slice(end);
        const needsSpace = before && !/\s$/.test(before) && text && !/^\s/.test(text);
        const insertText = `${needsSpace ? ' ' : ''}${text}`;
        input.value = `${before}${insertText}${after}`;
        const nextPos = before.length + insertText.length;
        input.focus();
        input.setSelectionRange(nextPos, nextPos);
        updateInputSuggestions();
        autoResizeInput();
    }

    function updateInputSuggestions() {
        const strip = document.getElementById('input-suggestion-strip');
        const input = document.getElementById('user-input');
        if (!strip || !input) return;

        if (document.activeElement !== input && !strip.matches(':hover')) {
            strip.classList.remove('visible');
            return;
        }

        const source = input.value.trim() ? safeActionSuggestionChips : moodSuggestionChips;
        strip.innerHTML = '';
        source.forEach(item => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'suggestion-chip';
            chip.innerText = item.label;
            chip.onmousedown = event => event.preventDefault();
            chip.onclick = () => insertSuggestionText(getSuggestionText(item));
            strip.appendChild(chip);
        });
        strip.classList.add('visible');
    }

    function initInputSuggestions() {
        const input = document.getElementById('user-input');
        const strip = document.getElementById('input-suggestion-strip');
        if (!input || !strip) return;
        input.addEventListener('focus', updateInputSuggestions);
        input.addEventListener('input', updateInputSuggestions);
        input.addEventListener('blur', () => {
            setTimeout(() => {
                if (!strip.matches(':hover') && document.activeElement !== input) {
                    strip.classList.remove('visible');
                }
            }, 120);
        });
    }

    function renderCheckinPeek() {
        const timeEl = document.getElementById('checkin-peek-time');
        const statusEl = document.getElementById('checkin-peek-status');
        const dotsEl = document.getElementById('checkin-peek-dots');
        if (!checkinState || !timeEl || !statusEl || !dotsEl) return;

        timeEl.innerText = checkinState.current_time || '';
        const todayRecord = checkinState.records?.[checkinState.today] || checkinState.selected_record || {};
        statusEl.innerText = `Today: ${todayRecord.done || 0}/${todayRecord.total || 0}`;

        dotsEl.innerHTML = '';
        const monthDays = checkinState.month_days || [];
        const heatStatuses = [];
        monthDays.forEach(day => {
            const dot = document.createElement('span');
            const status = checkinDayStatusClass(day);
            heatStatuses.push(status);
            dot.className = `checkin-dot ${status}`;
            dot.title = `${day.date}: ${day.done}/${day.total}`;
            dotsEl.appendChild(dot);
        });
        renderContinuousHeatStrip(dotsEl, heatStatuses);
    }

    async function openCheckinModal() {
        document.getElementById('checkin-modal').style.display = 'flex';
        await loadCheckinState({renderModal: true});
    }

    function closeCheckinModal() {
        document.getElementById('checkin-modal').style.display = 'none';
    }

    async function changeCheckinMonth(delta) {
        const date = new Date(checkinViewYear, checkinViewMonth - 1 + delta, 1);
        if (!isCheckinMonthAllowed(date.getFullYear(), date.getMonth() + 1)) {
            alert(`Check-in browsing is limited to ${checkinWindowText()}.`);
            return;
        }
        await saveSelectedCheckinDay({silent: true, skipReload: true});
        checkinViewYear = date.getFullYear();
        checkinViewMonth = date.getMonth() + 1;
        await loadCheckinState({renderModal: true});
    }

    function renderCheckinModal() {
        if (!checkinState) return;
        const currentTime = document.getElementById('checkin-current-time');
        const monthLabel = document.getElementById('checkin-month-label');
        const monthSummary = document.getElementById('checkin-month-summary');
        const yearSummary = document.getElementById('checkin-year-summary');

        if (currentTime) currentTime.innerText = `Now: ${checkinState.current_time}`;
        if (monthLabel) monthLabel.innerText = `${checkinState.year}-${String(checkinState.month).padStart(2, '0')}`;

        const prevBtn = document.getElementById('checkin-prev-month');
        const nextBtn = document.getElementById('checkin-next-month');
        if (prevBtn) {
            const prevDate = new Date(checkinViewYear, checkinViewMonth - 2, 1);
            prevBtn.disabled = !isCheckinMonthAllowed(prevDate.getFullYear(), prevDate.getMonth() + 1);
            prevBtn.title = checkinWindowText();
        }
        if (nextBtn) {
            const nextDate = new Date(checkinViewYear, checkinViewMonth, 1);
            nextBtn.disabled = !isCheckinMonthAllowed(nextDate.getFullYear(), nextDate.getMonth() + 1);
            nextBtn.title = checkinWindowText();
        }

        const m = checkinState.month_summary || {};
        const y = checkinState.year_summary || {};
        if (monthSummary) {
            monthSummary.innerHTML = `
                <div class="checkin-summary-title">This Month</div>
                <div>Completion rate: ${formatCheckinRate(m.completion_rate)}</div>
                <div>Days counted: ${m.days_counted || 0} · Complete: ${m.completed_days || 0} · Partial: ${m.partial_days || 0} · Missed: ${m.missed_days || 0}</div>
                <div>Current streak: ${m.current_streak || 0} · One-day tasks: ${m.one_off_done || 0}/${m.one_off_total || 0}</div>
            `;
        }
        if (yearSummary) {
            yearSummary.innerHTML = `
                <div class="checkin-summary-title">This Year</div>
                <div>Completion rate: ${formatCheckinRate(y.completion_rate)}</div>
                <div>Days counted: ${y.days_counted || 0} · Complete: ${y.completed_days || 0} · Partial: ${y.partial_days || 0} · Missed: ${y.missed_days || 0}</div>
                <div>Best streak: ${y.longest_streak || 0} · One-day tasks: ${y.one_off_done || 0}/${y.one_off_total || 0}</div>
            `;
        }

        renderCheckinCalendar();
        renderSelectedCheckinDay();
    }

    function renderCheckinCalendar() {
        const grid = document.getElementById('checkin-calendar-grid');
        if (!grid || !checkinState) return;
        grid.innerHTML = '';

        const days = checkinState.month_days || [];
        if (days.length === 0) return;

        const first = new Date(`${days[0].date}T00:00:00`);
        const mondayOffset = (first.getDay() + 6) % 7;
        for (let i = 0; i < mondayOffset; i++) {
            const empty = document.createElement('div');
            empty.className = 'checkin-day empty';
            grid.appendChild(empty);
        }

        days.forEach(day => {
            const btn = document.createElement('button');
            btn.className = `checkin-day ${checkinDayStatusClass(day)}`;
            if (day.date === checkinSelectedDate) btn.classList.add('selected');
            if (day.date === checkinState.today) btn.classList.add('today');
            btn.innerText = day.day;
            btn.title = `${day.date}: ${day.done}/${day.total}`;
            btn.onclick = () => selectCheckinDate(day.date);
            grid.appendChild(btn);
        });
    }

    async function selectCheckinDate(date) {
        if (!isCheckinDateAllowed(date)) {
            alert(`Check-in editing is limited to ${checkinWindowText()}.`);
            return;
        }
        await saveSelectedCheckinDay({silent: true, skipReload: true});
        checkinSelectedDate = date;
        await loadCheckinState({renderModal: true});
    }

    function renderSelectedCheckinDay() {
        const title = document.getElementById('checkin-selected-title');
        const list = document.getElementById('checkin-task-list');
        const note = document.getElementById('checkin-note');
        if (!checkinState || !list || !note) return;

        const record = checkinState.selected_record || {};
        if (title) {
            title.innerText = `${checkinSelectedDate}${checkinSelectedDate === checkinState.today ? ' · Today' : ''} · ${record.done || 0}/${record.total || 0}`;
        }

        note.value = record.note || '';
        const mood = document.getElementById('checkin-mood');
        const energy = document.getElementById('checkin-energy');
        const difficulty = document.getElementById('checkin-difficulty');
        if (mood) mood.value = record.mood || '';
        if (energy) energy.value = record.energy || '';
        if (difficulty) difficulty.value = record.difficulty || '';
        list.innerHTML = '';

        const fixedTasks = checkinState.selected_tasks || checkinState.active_tasks || [];
        const dailyTasks = Array.isArray(record.daily_tasks) ? record.daily_tasks : [];

        if (fixedTasks.length === 0 && dailyTasks.length === 0) {
            list.innerHTML = '<div style="color:#888; font-size:0.85rem; line-height:1.5;">No tasks for this day yet. Add a fixed daily task or a one-day task below.</div>';
            return;
        }

        if (fixedTasks.length > 0) {
            const fixedTitle = document.createElement('div');
            fixedTitle.className = 'checkin-task-section-title';
            fixedTitle.innerText = 'Fixed daily tasks';
            list.appendChild(fixedTitle);
        }

        fixedTasks.forEach(task => {
            const row = document.createElement('label');
            row.className = 'checkin-task-row';
            const safeId = escapeJsString(task.id);
            const checked = record.completions && record.completions[task.id] ? 'checked' : '';
            row.innerHTML = `
                <input type="checkbox" data-task-id="${escapeHtml(task.id)}" ${checked} onchange="saveSelectedCheckinDay({silent:true})">
                <span>${escapeHtml(task.title)}</span>
                <span class="checkin-task-badge">${escapeHtml(task.frequency || 'daily')}</span>
                <button type="button" onclick="editCheckinTask('${safeId}')" title="Edit task">✎</button>
                <button type="button" onclick="moveCheckinTask('${safeId}', -1)" title="Move up">↑</button>
                <button type="button" onclick="moveCheckinTask('${safeId}', 1)" title="Move down">↓</button>
                <button type="button" onclick="deleteCheckinTask('${safeId}')" title="Delete task">×</button>
            `;
            list.appendChild(row);
        });

        if (dailyTasks.length > 0) {
            const dailyTitle = document.createElement('div');
            dailyTitle.className = 'checkin-task-section-title';
            dailyTitle.innerText = 'Tasks only for this day';
            list.appendChild(dailyTitle);
        }

        dailyTasks.forEach(task => {
            appendCheckinDailyTaskRow(task);
        });
    }

    function collectCheckinCompletions() {
        const completions = {};
        document.querySelectorAll('#checkin-task-list input[type="checkbox"][data-task-id]').forEach(box => {
            completions[box.dataset.taskId] = box.checked;
        });
        return completions;
    }

    function appendCheckinDailyTaskRow(task) {
        const list = document.getElementById('checkin-task-list');
        if (!list) return;
        const row = document.createElement('label');
        row.className = 'checkin-task-row daily';
        const id = task.id || createCheckinLocalId();
        const safeId = escapeJsString(id);
        const title = String(task.title || '').trim();
        row.dataset.dailyRow = 'true';
        row.dataset.dailyId = id;
        row.dataset.dailyTitle = title;
        row.innerHTML = `
            <input type="checkbox" data-daily-id="${escapeHtml(id)}" ${task.done ? 'checked' : ''} onchange="saveSelectedCheckinDay({silent:true})">
            <span>${escapeHtml(title)}</span>
            <span class="checkin-task-badge">today</span>
            <button type="button" onclick="deleteCheckinDailyTask('${safeId}')" title="Delete today's task">×</button>
        `;
        list.appendChild(row);
    }

    function collectCheckinDailyTasks() {
        return Array.from(document.querySelectorAll('#checkin-task-list [data-daily-row="true"]')).map(row => {
            const checkbox = row.querySelector('input[type="checkbox"][data-daily-id]');
            return {
                id: row.dataset.dailyId,
                title: row.dataset.dailyTitle || row.querySelector('span')?.innerText || '',
                done: Boolean(checkbox?.checked)
            };
        }).filter(task => task.title.trim());
    }

    async function saveSelectedCheckinDay(options = {}) {
        if (!checkinSelectedDate) return null;
        if (!isCheckinDateAllowed(checkinSelectedDate)) {
            if (!options.silent) alert(`Check-in editing is limited to ${checkinWindowText()}.`);
            return null;
        }
        const list = document.getElementById('checkin-task-list');
        if (!list) return null;
        const response = await fetch('/api/checkins/day', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                date: checkinSelectedDate,
                completions: collectCheckinCompletions(),
                daily_tasks: collectCheckinDailyTasks(),
                note: document.getElementById('checkin-note')?.value || '',
                mood: document.getElementById('checkin-mood')?.value || null,
                energy: document.getElementById('checkin-energy')?.value || null,
                difficulty: document.getElementById('checkin-difficulty')?.value || null
            })
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `Save failed: ${response.status}`);
        }
        const data = await response.json();
        if (!options.skipReload) await loadCheckinState({renderModal: true});
        if (!options.silent) alert("Check-in saved.");
        return data.record;
    }

    async function addCheckinTask() {
        const input = document.getElementById('new-checkin-task-input');
        const title = input?.value.trim();
        if (!title) return;
        const response = await fetch('/api/checkins/tasks/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title})
        });
        const data = await response.json();
        const frequency = document.getElementById('new-checkin-task-frequency')?.value || 'daily';
        if (data.task?.id) {
            await fetch('/api/checkins/tasks/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: data.task.id, updates: {frequency}})
            });
        }
        input.value = '';
        await loadCheckinState({renderModal: true});
    }

    async function editCheckinTask(id) {
        const task = (checkinState?.tasks || []).find(t => t.id === id);
        if (!task) return;
        const title = prompt("Edit fixed task title:", task.title);
        if (!title || !title.trim()) return;
        const frequency = prompt("Frequency: daily, weekdays, weekends, weekly, monthly", task.frequency || 'daily') || task.frequency || 'daily';
        await fetch('/api/checkins/tasks/update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id, updates: {title: title.trim(), frequency: frequency.trim().toLowerCase()}})
        });
        await loadCheckinState({renderModal: true});
    }

    async function moveCheckinTask(id, delta) {
        const tasks = [...(checkinState?.tasks || [])];
        const index = tasks.findIndex(t => t.id === id);
        const next = index + delta;
        if (index < 0 || next < 0 || next >= tasks.length) return;
        [tasks[index], tasks[next]] = [tasks[next], tasks[index]];
        await fetch('/api/checkins/tasks/reorder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ordered_ids: tasks.map(t => t.id)})
        });
        await loadCheckinState({renderModal: true});
    }

    async function addCheckinDailyTask() {
        const input = document.getElementById('new-checkin-daily-task-input');
        const title = input?.value.trim();
        if (!title) return;

        const list = document.getElementById('checkin-task-list');
        if (list && list.innerText.includes('No tasks for this day yet')) {
            list.innerHTML = '';
            const dailyTitle = document.createElement('div');
            dailyTitle.className = 'checkin-task-section-title';
            dailyTitle.innerText = 'Tasks only for this day';
            list.appendChild(dailyTitle);
        } else if (list && !list.querySelector('.checkin-task-row.daily')) {
            const dailyTitle = document.createElement('div');
            dailyTitle.className = 'checkin-task-section-title';
            dailyTitle.innerText = 'Tasks only for this day';
            list.appendChild(dailyTitle);
        }

        appendCheckinDailyTaskRow({
            id: createCheckinLocalId(),
            title,
            done: false
        });
        input.value = '';
        await saveSelectedCheckinDay({silent: true});
    }

    async function deleteCheckinDailyTask(id) {
        const row = Array.from(document.querySelectorAll('#checkin-task-list [data-daily-row="true"]'))
            .find(item => item.dataset.dailyId === id);
        if (row) row.remove();
        await saveSelectedCheckinDay({silent: true});
    }

    async function deleteCheckinTask(id) {
        if (!confirm("Delete this fixed check-in task? Past records will stay in the data file, but the task will no longer appear.")) return;
        await fetch('/api/checkins/tasks/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id})
        });
        await loadCheckinState({renderModal: true});
    }

    async function talkWithSayaAboutCheckin() {
        await saveSelectedCheckinDay({silent: true});
        const sessionResponse = await fetch('/api/checkins/chat_session', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({date: checkinSelectedDate})
        });
        const sessionData = await sessionResponse.json();
        const response = await fetch('/api/checkins/context', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({date: checkinSelectedDate})
        });
        const data = await response.json();
        pendingCheckinContext = data.context || '';
        closeCheckinModal();
        if (sessionData.session_id) {
            await loadSession(sessionData.session_id);
        }
        const input = document.getElementById('user-input');
        const defaultMessage = document.getElementById('checkin-talk-message')?.value.trim() ||
            "Saya, I want to talk with you about today's check-in progress.";
        input.value = defaultMessage;
        input.focus();
    }

    async function exportCheckinReport(scope) {
        await saveSelectedCheckinDay({silent: true});
        const response = await fetch('/api/checkins/export', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                scope,
                year: checkinViewYear,
                month: checkinViewMonth
            })
        });
        const data = await response.json();
        alert(`Exported ${scope} report:\nMarkdown: ${data.markdown_path}\nBackup JSON: ${data.json_path}`);
    }

    // --- QUEST LOGIC ---
    function getQuestStatusDate(goal) {
        if (goal.status === 'active') return String(goal.created_at || '');
        if (goal.status === 'completed') return String(goal.achieved_at || goal.completed_at || '');
        if (goal.status === 'failed') return String(goal.failed_at || goal.completed_at || '');
        return '';
    }

    function getQuestDateLabel(status) {
        if (status === 'active') return 'Started';
        if (status === 'completed') return 'Achieved';
        return 'Failed';
    }

    function formatQuestDate(value) {
        return String(value || '').split(' ')[0];
    }

    function isValidQuestDate(value) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
        if (!match) return false;
        const date = new Date(`${match[1]}-${match[2]}-${match[3]}T00:00:00`);
        return date.getFullYear() === Number(match[1]) &&
            date.getMonth() + 1 === Number(match[2]) &&
            date.getDate() === Number(match[3]);
    }

    function openQuestModal() {
        const settingsMenu = document.getElementById('settings-menu');
        if (settingsMenu) settingsMenu.style.display = 'none';
        const memoryModal = document.getElementById('memory-modal');
        if (memoryModal) memoryModal.style.display = 'none';
        document.getElementById('quest-modal').style.display = 'flex';
        loadQuests();
    }
    function closeQuestModal() {
        document.getElementById('quest-modal').style.display = 'none';
    }
    function switchQuestTab(tab) {
        currentQuestTab = tab;
        document.getElementById('tab-active').style.opacity = tab === 'active' ? '1' : '0.5';
        document.getElementById('tab-completed').style.opacity = tab === 'completed' ? '1' : '0.5';
        document.getElementById('tab-failed').style.opacity = tab === 'failed' ? '1' : '0.5';
        document.getElementById('quest-input-area').style.display = tab === 'active' ? 'flex' : 'none';
        loadQuests();
    }

    async function loadQuests() {
        try {
            const res = await fetch('/api/goals');
            const goals = await res.json();
            const list = document.getElementById('quest-list');
            list.innerHTML = '';

            const filtered = goals.filter(g => {
                if (currentQuestTab === 'active') return g.status === 'active';
                if (currentQuestTab === 'completed') return g.status === 'completed';
                if (currentQuestTab === 'failed') return g.status === 'failed';
                return false;
            });
            
            // Sort: Active by created (newest first), Completed/Failed by completed_at/updated (newest first)
            filtered.sort((a, b) => {
                const da = currentQuestTab === 'active' ? a.created_at : (a.completed_at || a.created_at);
                const db = currentQuestTab === 'active' ? b.created_at : (b.completed_at || b.created_at);
                return da < db ? 1 : -1;
            });

            if (filtered.length === 0) {
                list.innerHTML = '<div style="text-align:center; color:#666; padding:20px;">No quests found.</div>';
                return;
            }

            filtered.forEach(g => {
                const div = document.createElement('div');
                div.className = `quest-item ${g.status}`;
                const safeId = escapeJsString(g.id);
                const safeTitleJs = escapeJsString(g.title);
                const safeTitle = escapeHtml(g.title);
                const dateValue = formatQuestDate(getQuestStatusDate(g));
                const safeDateJs = escapeJsString(dateValue);
                const dateAction = `<button class="btn-quest-action btn-edit" onclick="editQuestDate('${safeId}', '${g.status}', '${safeDateJs}')" title="Edit ${getQuestDateLabel(g.status)} date">📅</button>`;
                
                let actions = '';
                if (g.status === 'active') {
                    actions = dateAction + `
                        <button class="btn-quest-action btn-complete" onclick="completeQuest('${safeId}', '${safeTitleJs}')" title="Mark Complete">✅</button>
                        <button class="btn-quest-action btn-fail" onclick="failQuest('${safeId}', '${safeTitleJs}')" title="Mark Failed">😭</button>
                        <button class="btn-quest-action btn-edit" onclick="editQuest('${safeId}', '${safeTitleJs}')" title="Edit">✏️</button>
                        <button class="btn-quest-action btn-delete" onclick="deleteQuest('${safeId}')" title="Delete">🗑️</button>
                    `;
                } else if (g.status === 'completed') {
                    actions = dateAction + `
                        <button class="btn-quest-action btn-share" onclick="shareQuest('${safeTitleJs}')" title="Share with Saya">💬</button>
                        <button class="btn-quest-action btn-edit" onclick="editQuest('${safeId}', '${safeTitleJs}')" title="Edit">✏️</button>
                        <button class="btn-quest-action btn-delete" onclick="deleteQuest('${safeId}')" title="Delete">🗑️</button>
                    `;
                } else if (g.status === 'failed') {
                    actions = dateAction + `
                        <button class="btn-quest-action btn-edit" onclick="editQuest('${safeId}', '${safeTitleJs}')" title="Edit">✏️</button>
                        <button class="btn-quest-action btn-delete" onclick="deleteQuest('${safeId}')" title="Delete">🗑️</button>
                    `;
                }

                const dateDisplay = `${getQuestDateLabel(g.status)}: ${dateValue || 'Not set'}`;

                let failureReason = '';
                if (g.status === 'failed' && g.description) {
                    failureReason = `<div style="font-size:0.75rem; color:#e74c3c; margin-top:2px;">Reason: ${escapeHtml(g.description)}</div>`;
                }

                div.innerHTML = `
                    <div>
                        <div class="quest-title">${safeTitle}</div>
                        <div class="quest-date">${escapeHtml(dateDisplay)}</div>
                        ${failureReason}
                    </div>
                    <div style="display:flex; gap:5px;">${actions}</div>
                `;
                list.appendChild(div);
            });
        } catch(e) { console.error(e); }
    }

    async function failQuest(id, title) {
        const reason = prompt("Reason for failure or secondary achievement?", "");
        if (reason !== null) {
            try {
                await fetch('/api/goals/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        id: id, 
                        updates: {
                            status: 'failed', 
                            description: reason
                        }
                    })
                });
                loadQuests();
            } catch(e) { alert("Failed to update status"); }
        }
    }

    async function editQuest(id, currentTitle) {
        const newTitle = prompt("Edit goal title:", currentTitle);
        if (newTitle && newTitle.trim() !== "" && newTitle !== currentTitle) {
            try {
                await fetch('/api/goals/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: id, updates: {title: newTitle.trim()}})
                });
                loadQuests();
            } catch(e) { alert("Failed to update"); }
        }
    }

    async function editQuestDate(id, status, currentDate) {
        const label = getQuestDateLabel(status);
        const nextDate = prompt(`Set ${label} date (YYYY-MM-DD):`, currentDate || '');
        if (nextDate === null) return;
        const date = nextDate.trim();
        if (!isValidQuestDate(date)) {
            alert('Please enter a valid date in YYYY-MM-DD format.');
            return;
        }
        const dateField = status === 'active'
            ? 'created_at'
            : (status === 'completed' ? 'achieved_at' : 'failed_at');
        try {
            const response = await fetch('/api/goals/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id, updates: {[dateField]: date}})
            });
            if (!response.ok) throw new Error(`Update failed: ${response.status}`);
            await loadQuests();
        } catch (e) {
            alert('Failed to update quest date: ' + (e.message || e));
        }
    }

    async function addQuest() {
        const input = document.getElementById('new-quest-input');
        const title = input.value.trim();
        if (!title) return;
        
        try {
            await fetch('/api/goals/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({title: title})
            });
            input.value = '';
            loadQuests();
        } catch(e) { alert("Failed to add quest"); }
    }

    async function completeQuest(id, title) {
        if(!confirm("Mark this quest as completed?")) return;
        try {
            await fetch('/api/goals/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {status: 'completed'}})
            });
            loadQuests();
            
            // Prompt to share
            if(confirm("Great job! Tell Saya about this achievement now?")) {
                shareQuest(title);
            }
        } catch(e) { alert("Failed to update"); }
    }

    async function deleteQuest(id) {
        if(!confirm("Delete this quest?")) return;
        try {
            await fetch('/api/goals/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id})
            });
            loadQuests();
        } catch(e) { alert("Failed to delete"); }
    }

    function shareQuest(title) {
        closeQuestModal();
        const input = document.getElementById('user-input');
        input.value = `I just completed my goal: "${title}"!`;
        input.focus();
        // Optional: Auto-send? Let's let user edit/send.
    }

    function closeModal() {
        document.getElementById('memory-modal').style.display = 'none';
    }

    function setMemoryModalHeader(title, backHandler = "") {
        const titleEl = document.getElementById('modal-title');
        const backEl = document.getElementById('memory-modal-back');
        if (titleEl) titleEl.innerText = title || "";
        if (!backEl) return;
        if (backHandler) {
            backEl.style.display = 'inline-flex';
            backEl.setAttribute('onclick', backHandler);
        } else {
            backEl.style.display = 'none';
            backEl.setAttribute('onclick', '');
        }
    }

    async function openMemoryViewer() {
        const settingsMenu = document.getElementById('settings-menu');
        if (settingsMenu) settingsMenu.style.display = 'none';
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("Memories", "openAboutYouModal()");
        body.innerHTML = `
            <div class="memory-toolbar">
                <input id="memory-search-input" type="text" placeholder="Search memories..." oninput="loadMemoryList()" />
                <select id="memory-category-filter" onchange="memoryViewerCategory = this.value; loadMemoryList()">
                    <option value="">All categories</option>
                    <option value="identity">Identity</option>
                    <option value="preference">Preference</option>
                    <option value="relationship">Relationship</option>
                    <option value="project">Project</option>
                    <option value="goal">Goal</option>
                    <option value="schedule">Schedule</option>
                    <option value="health">Health</option>
                    <option value="emotion">Emotion</option>
                    <option value="conversation_summary">Summary</option>
                    <option value="general">General</option>
                </select>
                <label class="memory-toolbar-check">
                    <input id="memory-archived-filter" type="checkbox" ${memoryViewerIncludeArchived ? 'checked' : ''} onchange="memoryViewerIncludeArchived = this.checked; loadMemoryList()">
                    Archived
                </label>
                <button onclick="toggleAddMemoryPanel()" title="Add a manual memory">Add memory</button>
                <button onclick="rebuildMemoryMetadata()" title="Rebuild Memory 2.0 metadata">Rebuild metadata</button>
            </div>
            <div id="memory-add-panel" class="memory-add-panel" style="display:none;">
                <div class="memory-add-grid">
                    <input id="memory-add-title" type="text" placeholder="Title (optional)" />
                    <select id="memory-add-category">
                        <option value="">Auto category</option>
                        <option value="identity">Identity</option>
                        <option value="preference">Preference</option>
                        <option value="relationship">Relationship</option>
                        <option value="project">Project</option>
                        <option value="goal">Goal</option>
                        <option value="schedule">Schedule</option>
                        <option value="health">Health</option>
                        <option value="emotion">Emotion</option>
                        <option value="general">General</option>
                    </select>
                </div>
                <textarea id="memory-add-content" rows="4" placeholder="Write the memory to save..."></textarea>
                <div class="memory-add-actions">
                    <label><input id="memory-add-important" type="checkbox" checked> Star important</label>
                    <label><input id="memory-add-locked" type="checkbox"> Lock</label>
                    <label id="memory-add-project-label" style="display:none;"><input id="memory-add-project" type="checkbox"> Current project only</label>
                    <button onclick="saveManualMemory()">Save memory</button>
                </div>
            </div>
            <div id="memory-list">Loading...</div>
        `;
        const projectLabel = document.getElementById('memory-add-project-label');
        if (projectLabel && currentProjectId) projectLabel.style.display = 'inline-flex';
        const categoryFilter = document.getElementById('memory-category-filter');
        if (categoryFilter) categoryFilter.value = memoryViewerCategory;
        modal.style.display = 'flex';

        await loadMemoryList();
    }

    async function loadMemoryList() {
        const list = document.getElementById('memory-list');
        if (!list) return;
        const query = document.getElementById('memory-search-input')?.value.trim() || '';
        const category = document.getElementById('memory-category-filter')?.value || memoryViewerCategory || '';
        list.innerHTML = 'Loading...';
        try {
            const res = await fetch('/api/kb/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    query,
                    category,
                    include_archived: memoryViewerIncludeArchived,
                    limit: 100
                })
            });
            const data = await res.json();
            renderMemoryList(data.memories || []);
        } catch(e) {
            list.innerHTML = 'Error loading memories.';
            console.error(e);
        }
    }

    function renderMemoryList(memories) {
        const list = document.getElementById('memory-list');
        if (!list) return;
        list.innerHTML = '';

        if (memories.length === 0) {
            list.innerHTML = '<div style="text-align:center; color:#888; padding:20px;">No memories found.</div>';
            return;
        }

        memories.forEach(mem => {
            const div = document.createElement('div');
            div.className = `memory-item ${mem.is_archived ? 'archived' : ''}`;
            const safeId = escapeJsString(mem.id);
            const tags = Array.isArray(mem.tags) ? mem.tags : [];
            const tagHtml = tags.length
                ? `<div class="memory-tags">${tags.map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}</div>`
                : '';
            const confidence = Number(mem.confidence ?? 0.75);
            const score = mem.relevance_score !== undefined
                ? ` · relevance ${Number(mem.relevance_score).toFixed(2)}`
                : '';
            const priority = mem.priority_score !== undefined || mem.base_priority_score !== undefined
                ? ` · ${escapeHtml(mem.memory_tier || 'auto')} ${Number(mem.priority_score ?? mem.base_priority_score).toFixed(2)}`
                : '';
            const archivedBadge = mem.is_archived ? '<span class="memory-badge archived">archived</span>' : '';
            const conflictBadge = mem.conflicts_with && mem.conflicts_with.length
                ? '<span class="memory-badge conflict">conflict</span>'
                : '';
            const updateBadge = mem.supersedes && mem.supersedes.length
                ? '<span class="memory-badge update">updates old</span>'
                : '';
            const managedBadge = mem.managed_slot
                ? '<span class="memory-badge managed">managed</span>'
                : '';
            const restoreButton = mem.is_archived && !mem.managed_slot
                ? `<button class="btn-mem-action" onclick="restoreMemory('${safeId}', this)" title="Restore archived memory">↩</button>`
                : '';
            const editableAttr = mem.managed_slot ? 'false' : 'true';
            const blurHandler = mem.managed_slot ? '' : ` onblur="updateMemoryContent('${safeId}', this.innerText)"`;
            const deleteButton = mem.managed_slot
                ? ''
                : `<button class="btn-mem-action" onclick="deleteMemory('${safeId}', this)" title="Delete memory">🗑️</button>`;
            const lockButton = mem.managed_slot
                ? `<button class="btn-mem-action lock active" title="Managed memory is protected">🔒</button>`
                : `<button class="btn-mem-action lock ${mem.is_locked ? 'active' : ''}" onclick="toggleMemoryLock('${safeId}', this)" title="Lock memory">🔒</button>`;

            div.innerHTML = `
                <div style="flex:1">
                    <div class="memory-title-row">
                        <span>${escapeHtml(mem.title || 'Untitled')}</span>
                        ${archivedBadge}${conflictBadge}${updateBadge}${managedBadge}
                    </div>
                    <div class="memory-meta">
                        ${escapeHtml(mem.creation_time || mem.date || '')}
                        · ${escapeHtml(mem.source || 'manual')}
                        · ${escapeHtml(mem.category || 'general')}
                        · confidence ${confidence.toFixed(2)}
                        · accessed ${Number(mem.access_count || 0)}
                        ${priority}
                        ${score}
                    </div>
                    ${tagHtml}
                    <div class="memory-content" contenteditable="${editableAttr}"${blurHandler}>${escapeHtml(mem.content || '')}</div>
                </div>
                <div class="memory-actions">
                    <button class="btn-mem-action star ${mem.is_important ? 'active' : ''}" onclick="toggleMemoryStar('${safeId}', this)" title="Star important memory">★</button>
                    ${lockButton}
                    ${restoreButton}
                    ${deleteButton}
                </div>
            `;
            list.appendChild(div);
        });
    }

    function toggleAddMemoryPanel() {
        const panel = document.getElementById('memory-add-panel');
        if (!panel) return;
        panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    }

    async function saveManualMemory() {
        const contentEl = document.getElementById('memory-add-content');
        const content = contentEl?.value.trim() || '';
        if (!content) {
            alert("Memory content is required.");
            return;
        }

        const metadata = {};
        if (document.getElementById('memory-add-locked')?.checked) {
            metadata.is_locked = true;
        }
        if (currentProjectId && document.getElementById('memory-add-project')?.checked) {
            const project = projectsCache.find(item => item.id === currentProjectId);
            metadata.project_id = currentProjectId;
            metadata.project_name = project?.name || "Untitled Project";
            metadata.project_scoped = true;
        }

        try {
            const response = await fetch('/api/kb/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    title: document.getElementById('memory-add-title')?.value.trim() || null,
                    content,
                    source: 'manual',
                    is_important: document.getElementById('memory-add-important')?.checked !== false,
                    category: document.getElementById('memory-add-category')?.value || null,
                    metadata
                })
            });
            if (!response.ok) throw new Error("Add memory failed");
            document.getElementById('memory-add-title').value = '';
            contentEl.value = '';
            document.getElementById('memory-add-category').value = '';
            loadMemoryList();
        } catch(e) {
            alert("Failed to save memory: " + e);
        }
    }

    async function openMemorySettings() {
        const settingsMenu = document.getElementById('settings-menu');
        if (settingsMenu) settingsMenu.style.display = 'none';
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("Settings", "openAboutYouModal()");
        modal.style.display = 'flex';

        try {
            const response = await fetch('/api/kb/settings');
            const settings = await response.json();
            memoryAutoSave = settings.auto_save_memories !== false;
            memoryAutoMergeEnabled = settings.auto_merge_enabled === true;
            memoryAutoMergeDays = Number(settings.auto_merge_days || 7);
            memory2Enabled = settings.memory_2_enabled !== false;
            memoryRelevantCount = Number(settings.relevant_memory_count || 8);
            memoryIncludeMetadata = settings.include_memory_metadata !== false;
        } catch (e) {
            console.warn("Unable to load memory settings", e);
        }
        
        body.innerHTML = `
            <div style="padding:10px;">
                <div style="margin-bottom:15px; display:flex; align-items:center; justify-content:space-between;">
                    <span>Auto-Save Memories</span>
                    <input type="checkbox" id="auto-save-toggle" ${memoryAutoSave ? 'checked' : ''} onchange="memoryAutoSave = this.checked">
                </div>
                <div style="font-size:0.75rem; color:#888; margin-top:-10px; margin-bottom:15px;">Automatically saves explicit “remember this” requests; it does not guess personal facts.</div>
                <div style="margin-bottom:15px; display:flex; align-items:center; justify-content:space-between;">
                    <span>Memory 2.0 Retrieval</span>
                    <input type="checkbox" id="memory-2-toggle" ${memory2Enabled ? 'checked' : ''} onchange="memory2Enabled = this.checked">
                </div>
                <div style="font-size:0.75rem; color:#888; margin-top:-10px; margin-bottom:15px;">Uses category, confidence, tags, recency, and relevance instead of dumping raw memories into the prompt.</div>
                <div style="margin-bottom:15px;">
                    <div style="margin-bottom:5px;">Relevant Memories per Prompt</div>
                    <input type="number" value="${memoryRelevantCount}" min="1" max="30" style="width:100%; padding:5px; background:#333; color:white; border:1px solid #555;" onchange="memoryRelevantCount = this.value">
                </div>
                <div style="margin-bottom:15px; display:flex; align-items:center; justify-content:space-between;">
                    <span>Include Metadata in Prompt</span>
                    <input type="checkbox" id="memory-metadata-toggle" ${memoryIncludeMetadata ? 'checked' : ''} onchange="memoryIncludeMetadata = this.checked">
                </div>
                <div style="margin-bottom:15px; display:flex; align-items:center; justify-content:space-between;">
                    <span>Enable Auto-Merge</span>
                    <input type="checkbox" id="auto-merge-toggle" ${memoryAutoMergeEnabled ? 'checked' : ''} onchange="memoryAutoMergeEnabled = this.checked">
                </div>
                <div style="margin-bottom:15px;">
                    <div style="margin-bottom:5px;">Auto-Merge Interval (Days)</div>
                    <input type="number" value="${memoryAutoMergeDays}" min="1" max="365" style="width:100%; padding:5px; background:#333; color:white; border:1px solid #555;" onchange="memoryAutoMergeDays = this.value">
                    <div style="font-size:0.75rem; color:#888; margin-top:5px;">Unlocked, unstarred memories older than this are compacted. Originals remain archived in knowledge.json.</div>
                </div>
                <div style="margin-top:20px; text-align:right;">
                    <button onclick="mergeMemoriesNow()" style="padding:5px 15px; margin-right:8px; background:#444; color:white; border:none; border-radius:4px; cursor:pointer;">Merge Now</button>
                    <button onclick="saveMemorySettings()" style="padding:5px 15px; background:var(--accent-blue); color:#000; border:none; border-radius:4px; cursor:pointer;">Save & Close</button>
                </div>
            </div>
        `;
    }

    async function saveMemorySettings() {
        try {
            const response = await fetch('/api/kb/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    auto_save_memories: memoryAutoSave,
                    auto_merge_enabled: memoryAutoMergeEnabled,
                    auto_merge_days: Number(memoryAutoMergeDays),
                    memory_2_enabled: memory2Enabled,
                    relevant_memory_count: Number(memoryRelevantCount),
                    include_memory_metadata: memoryIncludeMetadata
                })
            });
            if (!response.ok) throw new Error("Settings request failed");
            closeModal();
        } catch (e) {
            alert("Failed to save memory settings: " + e);
        }
    }

    async function mergeMemoriesNow() {
        if (!confirm("Compact eligible old memories now? Originals will remain archived in knowledge.json.")) return;
        try {
            const response = await fetch('/api/kb/merge', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({days: Number(memoryAutoMergeDays)})
            });
            const data = await response.json();
            alert(data.status === 'success'
                ? `Merged ${data.merged_count} memories.`
                : "No eligible memories needed merging.");
        } catch (e) {
            alert("Memory merge failed: " + e);
        }
    }

    async function toggleMemoryStar(id, btn) {
        const isActive = btn.classList.contains('active');
        try {
            await fetch('/api/kb/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {is_important: !isActive}})
            });
            btn.classList.toggle('active');
        } catch(e) { alert("Failed to update"); }
    }

    async function toggleMemoryLock(id, btn) {
        const isActive = btn.classList.contains('active');
        try {
            await fetch('/api/kb/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {is_locked: !isActive}})
            });
            btn.classList.toggle('active');
        } catch(e) { alert("Failed to update"); }
    }

    async function updateMemoryContent(id, newContent) {
        try {
            await fetch('/api/kb/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {content: newContent}})
            });
        } catch(e) { console.error("Update failed", e); }
    }

    async function deleteMemory(id, btn) {
        if(!confirm("Delete this memory permanently?")) return;
        try {
            await fetch('/api/kb/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id})
            });
            btn.closest('.memory-item').remove();
        } catch(e) { alert("Failed to delete"); }
    }

    async function restoreMemory(id, btn) {
        try {
            await fetch('/api/kb/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: id, updates: {is_archived: false}})
            });
            loadMemoryList();
        } catch(e) { alert("Failed to restore"); }
    }

    async function rebuildMemoryMetadata() {
        if(!confirm("Rebuild Memory 2.0 metadata for existing memories? Existing content will be preserved.")) return;
        try {
            const response = await fetch('/api/kb/rebuild_metadata', {method: 'POST'});
            const data = await response.json();
            alert(`Metadata rebuilt for ${data.count || 0} memories.`);
            loadMemoryList();
        } catch(e) { alert("Failed to rebuild metadata: " + e); }
    }

    function collectArchiveGenerationSettings() {
        const apiModelName = document.getElementById('api-model-input')?.value.trim() || '';
        const memoryFastModelInput = document.getElementById('api-memory-fast-model-input')?.value.trim() || '';
        const memoryStrongModelInput = document.getElementById('api-memory-strong-model-input')?.value.trim() || '';
        return {
            model: document.getElementById('model-select')?.value || '',
            api_key: document.getElementById('api-key-input')?.value || '',
            api_url: document.getElementById('api-url-input')?.value || '',
            api_model_name: apiModelName,
            memory_fast_model_name: memoryFastModelInput || apiModelName,
            memory_strong_model_name: memoryStrongModelInput || memoryFastModelInput || apiModelName
        };
    }

    async function archiveCurrentSession() {
        if(!confirm("Archive current session to memory? This will summarize recent messages.")) return;
        try {
            saveCurrentApiSettings();
            const res = await fetch('/api/history/archive', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(collectArchiveGenerationSettings())
            });
            const data = await res.json();
            if(data.status === 'success') {
                alert("Session archived:\n" + data.summary);
            } else {
                alert("Archive failed or empty history: " + (data.message || data.status || "Unknown error"));
            }
        } catch(e) { alert("Error: " + e); }
    }

    function triggerUpload() {
        const input = document.getElementById('file-upload');
        if (!input) return;
        input.value = '';
        input.click();
    }

    function clearAttachment() {
        pendingAttachment = null;
        document.getElementById('attachment-preview').style.display = 'none';
        document.getElementById('preview-img').src = '';
        document.getElementById('preview-text').innerText = '';
        document.getElementById('file-upload').value = '';
    }

    async function uploadFile(input) {
        const file = input instanceof File ? input : input?.files?.[0];
        if (file) {
            const formData = new FormData();
            formData.append("file", file);
            formData.append("model", document.getElementById('model-select')?.value || '');

            // const chatContent = document.getElementById('chat-content');
            // const loadingMsg = document.createElement('div');
            // loadingMsg.className = 'message user-message';
            // loadingMsg.innerText = "[Uploading...]";
            // chatContent.appendChild(loadingMsg);
            // chatContent.scrollTop = chatContent.scrollHeight;
            
            // Show uploading state in preview area
            document.getElementById('attachment-preview').style.display = 'flex';
            document.getElementById('preview-text').innerText = `Uploading ${file.name}...`;

            try {
                const response = await fetch('/api/upload_file', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                
                // loadingMsg.remove();
                
                if (data.status === 'success') {
                    pendingAttachment = data;
                    
                    // Show preview
                    const previewArea = document.getElementById('attachment-preview');
                    const previewImg = document.getElementById('preview-img');
                    const previewText = document.getElementById('preview-text');
                    
                    previewArea.style.display = 'flex';
                    
                    if (data.type === 'image') {
                        previewImg.src = data.url;
                        previewImg.style.display = 'block';
                        previewText.innerText = 'Image attached';
                    } else {
                        previewImg.style.display = 'none';
                        previewText.innerText = `File attached: ${data.filename}`;
                    }
                    
                    // Focus input for user to type text
                    document.getElementById('user-input').focus();
                    
                } else {
                    alert("Upload failed: " + (data.message || data.detail || "Unknown error"));
                    clearAttachment();
                }
            } catch (e) {
                // loadingMsg.innerText = "Error uploading file.";
                console.error(e);
                alert("Error uploading file");
                clearAttachment();
            }
        }
    }

    function initializeFileDropUpload() {
        const overlay = document.getElementById('file-drop-overlay');
        if (!overlay || document.body.dataset.fileDropReady === 'true') return;
        document.body.dataset.fileDropReady = 'true';
        let dragDepth = 0;

        const hasFiles = event => Array.from(event.dataTransfer?.types || []).includes('Files');
        const hideOverlay = () => {
            dragDepth = 0;
            overlay.classList.remove('visible');
            overlay.setAttribute('aria-hidden', 'true');
        };

        document.addEventListener('dragenter', event => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            dragDepth += 1;
            overlay.classList.add('visible');
            overlay.setAttribute('aria-hidden', 'false');
        });
        document.addEventListener('dragover', event => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
        });
        document.addEventListener('dragleave', event => {
            if (!hasFiles(event)) return;
            dragDepth = Math.max(0, dragDepth - 1);
            if (dragDepth === 0) hideOverlay();
        });
        document.addEventListener('drop', event => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            const files = Array.from(event.dataTransfer?.files || []);
            hideOverlay();
            if (!files.length) return;
            if (files.length > 1) {
                alert('One attachment can be sent per message. The first dropped file will be attached.');
            }
            uploadFile(files[0]);
        });
        window.addEventListener('blur', hideOverlay);
    }

    // Global variables
    let pendingAttachment = null;
    let motionFiles = [];
    let currentMotionIndex = 0;
    let isLooping = true;
    let currentMesh = null;
    let modelLoadGeneration = 0;
    let animationHelper = null;
    let scene, camera, renderer, controls, loader;
    let mixer = null;
    let initialPosition = null;
    let initialFootPosition = null;
    
    // Physics Reset Variables
    let lastPhysicsResetTime = Date.now();
    let physicsEnableTimer = null;
    const PHYSICS_RESET_INTERVAL = 30000; // Reset physics every 30 seconds to prevent vibration accumulation
    let lastPhysicsPanicTime = 0;
    let abnormalPhysicsFrames = 0;
    const PHYSICS_PANIC_COOLDOWN = 1200;
    const PHYSICS_MAX_ROOT_DRIFT = 8.0;
    const PHYSICS_MAX_FOOT_DRIFT = 14.0;
    const PHYSICS_MAX_FRAME_Y_DELTA = 3.5;
    
    const vmdCache = new Map();
    
    const FIXED_STEP = 1 / 60;
    let motionSpeed = 1.0;
    const clock = new THREE.Clock();
    let accumulator = 0;

    let motionCategories = {};
    let currentMotionState = 'normal_idle';
    let isWaitingForResponse = false;
    let lastInteractionTime = Date.now();
    let motionTimer = null;
    let currentHistoryLength = 0; // Track backend history length locally
    let currentSessionId = null;
    let videoRecommendationState = null;
    let videoRecommendationAutoOpened = false;
    // directTranslateEnabled removed

    // [ADDED] Lighting Global Variables for Dynamic Adjustment
    let mainAmbientLight, mainDirLight, mainTopLight;
    let initialCameraDistance = 0;
    const BASE_AMBIENT = 0.7;
    const BASE_DIR = 0.8;
    const BASE_TOP = 0.5;

    document.addEventListener('DOMContentLoaded', () => {
        // [ADDED] Initialize Draggable & Resizable Logic
        initDraggableModel();
        initializeFileDropUpload();
    });

    // --- DRAGGABLE & RESIZABLE LOGIC ---
    function initDraggableModel() {
        const container = document.getElementById('draggable-model');
        
        // [ADDED] Right-click to Reload Model
        container.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            if(confirm("Reload model?")) {
                reloadModel();
            }
        });

        const dragBar = container.querySelector('.drag-bar');
        const handles = container.querySelectorAll('.resize-handle');
        
        // --- DRAGGING ---
        let isDragging = false;
        let dragOffsetX = 0;
        let dragOffsetY = 0;

        dragBar.addEventListener('mousedown', (e) => {
            isDragging = true;
            dragOffsetX = e.clientX - container.offsetLeft;
            dragOffsetY = e.clientY - container.offsetTop;
            document.body.style.userSelect = 'none'; // Prevent selection
            document.addEventListener('mousemove', onDrag);
            document.addEventListener('mouseup', stopDrag);
        });

        function onDrag(e) {
            if (!isDragging) return;
            container.style.left = (e.clientX - dragOffsetX) + 'px';
            container.style.top = (e.clientY - dragOffsetY) + 'px';
        }

        function stopDrag() {
            isDragging = false;
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onDrag);
            document.removeEventListener('mouseup', stopDrag);
        }

        // --- RESIZING ---
        let isResizing = false;
        let startX, startY, startWidth, startHeight, startTop, startLeft;
        let aspectRatio = 300 / 350; // Initial aspect ratio
        let currentHandle = null;

        handles.forEach(handle => {
            handle.addEventListener('mousedown', (e) => {
                e.stopPropagation(); // Prevent drag
                isResizing = true;
                currentHandle = handle;
                startX = e.clientX;
                startY = e.clientY;
                startWidth = container.offsetWidth;
                startHeight = container.offsetHeight;
                startTop = container.offsetTop;
                startLeft = container.offsetLeft;
                aspectRatio = startWidth / startHeight; // Lock current ratio

                document.body.style.userSelect = 'none';
                document.addEventListener('mousemove', onResize);
                document.addEventListener('mouseup', stopResize);
            });
        });

        function onResize(e) {
            if (!isResizing) return;
            
            const dx = e.clientX - startX;
            // Proportional scaling: maintain aspect ratio
            // We'll base scaling on width change primarily, or largest delta
            
            let newWidth = startWidth;
            let newHeight = startHeight;
            let newTop = startTop;
            let newLeft = startLeft;

            // Simple corner logic with aspect ratio constraint
            // For simplicity, we assume dragging mostly changes width, height follows
            
            if (currentHandle.classList.contains('se')) {
                // Bottom-Right
                newWidth = startWidth + dx;
                newHeight = newWidth / aspectRatio;
            } else if (currentHandle.classList.contains('sw')) {
                // Bottom-Left
                newWidth = startWidth - dx;
                newHeight = newWidth / aspectRatio;
                newLeft = startLeft + (startWidth - newWidth);
            } else if (currentHandle.classList.contains('ne')) {
                // Top-Right
                newWidth = startWidth + dx;
                newHeight = newWidth / aspectRatio;
                newTop = startTop + (startHeight - newHeight);
            } else if (currentHandle.classList.contains('nw')) {
                // Top-Left
                newWidth = startWidth - dx;
                newHeight = newWidth / aspectRatio;
                newLeft = startLeft + (startWidth - newWidth);
                newTop = startTop + (startHeight - newHeight);
            }

            // Min size limit
            if (newWidth > 100) {
                container.style.width = newWidth + 'px';
                container.style.height = newHeight + 'px';
                if (newLeft !== startLeft) container.style.left = newLeft + 'px';
                if (newTop !== startTop) container.style.top = newTop + 'px';
                
                // Update 3D Renderer
                if (renderer && camera) {
                    camera.aspect = newWidth / newHeight;
                    camera.updateProjectionMatrix();
                    renderer.setSize(newWidth, newHeight);
                }
            }
        }

        function stopResize() {
            isResizing = false;
            currentHandle = null;
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onResize);
            document.removeEventListener('mouseup', stopResize);
        }
    }

    function isNightTime() {
        const hour = new Date().getHours();
        return hour >= 18 || hour <= 6;
    }

    async function fetchMotionConfig() {
        try {
            const response = await fetch('/get_motion_config');
            if (!response.ok) throw new Error("Failed to fetch motion config");
            motionCategories = await response.json();
            console.log("Motion categories loaded:", motionCategories);
        } catch (e) {
            console.error("Error loading motion config:", e);
        }
    }

    function selectMotionFromCategory(category) {
        const motions = motionCategories[category];
        if (!motions || motions.length === 0) return null;

        const totalWeight = motions.reduce((sum, motion) => sum + motion.weight, 0);
        let random = Math.random() * totalWeight;

        for (const motion of motions) {
            random -= motion.weight;
            if (random <= 0) {
                return motion.name;
            }
        }
        return motions[0].name;
    }

    function findMotionByName(motionName) {
        return motionFiles.findIndex(file => 
            file.includes(motionName) || 
            file.toLowerCase().includes(motionName.toLowerCase())
        );
    }

    function selectIntelligentMotion(context = {}) {
        let targetCategory = currentMotionState;

        if (context.isResponding) {
            targetCategory = 'giving_answer';
        } else if (context.isListening) {
            targetCategory = context.questionType === 'emotional' ? 'listening_emotional' : 'searching_task';
        } else if (context.emotion === 'happy') {
            targetCategory = 'happy_mood';
        } else if (context.emotion === 'sad') {
            targetCategory = 'sad_emotion';
        } else if (context.isPraised) {
            targetCategory = 'praised';
        } else if (context.isEasterEgg) {
            targetCategory = 'easter_egg';
        } else if (context.forceNight) {
            targetCategory = 'night_sleepy';
        } else if (context.forceNormal) {
            targetCategory = 'normal_idle';
        }

        const selectedMotionName = selectMotionFromCategory(targetCategory);
        if (!selectedMotionName) return null;

        const motionIndex = findMotionByName(selectedMotionName);
        if (motionIndex === -1) {
            console.warn(`Motion ${selectedMotionName} not found in files`);
            return null;
        }

        currentMotionState = targetCategory;
        return motionIndex;
    }

    function playIntelligentMotion(context = {}) {
        if (!currentMesh) return;

        const selectedIndex = selectIntelligentMotion(context);
        if (selectedIndex === null) return;

        forceResetPosition();
        currentMotionIndex = selectedIndex;
        loadAndPlayMotion(currentMesh);

        console.log(`Playing intelligent motion: ${motionFiles[selectedIndex]} (Category: ${currentMotionState})`);
    }

    let lastNightMotionTime = 0;

    function startAutoMotionSystem() {
        if (motionTimer) clearInterval(motionTimer);

        motionTimer = setInterval(() => {
            const timeSinceLastInteraction = Date.now() - lastInteractionTime;

            if (timeSinceLastInteraction > 10000 && !isWaitingForResponse) {
                const now = Date.now();
                if (isNightTime() && (now - lastNightMotionTime > 300000)) {
                    playIntelligentMotion({ forceNight: true });
                    lastNightMotionTime = now;
                } else {
                    playIntelligentMotion({ forceNormal: true });
                }
            }
        }, 8000);
    }

    function stopAutoMotionSystem() {
        if (motionTimer) {
            clearInterval(motionTimer);
            motionTimer = null;
        }
    }

    window.addEventListener('load', function() {
        fetchMotionConfig();
        fetchSessionList(); // Initial session list
        fetchCurrentModel(); // Sync model selection
        initializeCheckinPeek();
        loadSessionStats();
        initInputSuggestions();
        initializeVideoRecommendations();
        setTimeout(() => {
            if (isNightTime()) {
                console.log('Night mode detected - sleepy motions available');
            }
            startAutoMotionSystem();
        }, 3000);
    });

    // [ADDED] Model Switching Logic
    let previousModelValue = "";

    function initModelSelect() {
        const select = document.getElementById('model-select');
        if (select) {
            previousModelValue = select.value;
        }
    }
    window.addEventListener('load', initModelSelect);

    async function handleModelChange(selectElement) {
        const newValue = selectElement.value;
        if (newValue === 'api' && !getSavedApiSettings().api_key) {
            alert("Please configure LLM API first.");
            openApiSettingsModal();
            selectElement.value = previousModelValue;
            return;
        }

        // Confirm switch
        const confirmMsg = newValue === 'api'
            ? "Switch to API mode?"
            : "Switching local models requires reloading the backend. \n\n" +
                           "- Sending messages will be disabled during the switch.\n" + 
                           "- This may take a few moments.\n\n" +
                           "Continue?";
                           
        if (!confirm(confirmMsg)) {
            selectElement.value = previousModelValue;
            return;
        }
        
        // 1. Disable UI
        setChatDisabled(true);
        
        try {
            // 2. Call Backend
            const res = await fetch('/api/model/switch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({model: newValue})
            });
            const data = await res.json();
            
            if (data.status === 'success') {
                alert("Model switched to: " + (data.model || newValue));
                previousModelValue = newValue;
                saveApiSettings({selected_model: newValue});
                toggleApiInput(); 
            } else {
                throw new Error(data.detail || data.message || "Unknown error");
            }
        } catch (e) {
            alert("Model switch failed: " + e.message);
            selectElement.value = previousModelValue; // Revert
        } finally {
            // 3. Re-enable UI
            setChatDisabled(false);
        }
    }
    
    function setChatDisabled(disabled) {
        const sendBtn = document.querySelector('.btn-send'); 
        const input = document.getElementById('user-input');
        // Select all action buttons (Regenerate, Edit, Translate)
        const actionBtns = document.querySelectorAll('.btn-action');
        
        if (disabled) {
            if(sendBtn) {
                sendBtn.style.filter = "grayscale(100%)";
                sendBtn.style.pointerEvents = "none";
                sendBtn.style.opacity = "0.5";
            }
            if(input) {
                input.disabled = true;
                input.placeholder = "Switching model... please wait...";
            }
            // Disable message actions
            actionBtns.forEach(btn => {
                // Check if it is specifically a regenerate button if we want to be strict, 
                // but user said "options" (plural) and Edit/Regen both send requests.
                // Let's disable all for safety and consistency.
                btn.style.filter = "grayscale(100%)";
                btn.style.pointerEvents = "none";
                btn.style.opacity = "0.5";
                btn.disabled = true;
            });
        } else {
            if(sendBtn) {
                sendBtn.style.filter = "none";
                sendBtn.style.pointerEvents = "auto";
                sendBtn.style.opacity = "1";
            }
            if(input) {
                input.disabled = false;
                input.placeholder = "Type a message... (Shift+Enter for new line)";
                input.focus();
            }
            // Enable message actions
            actionBtns.forEach(btn => {
                btn.style.filter = "none";
                btn.style.pointerEvents = "auto";
                btn.style.opacity = "1";
                btn.disabled = false;
            });
        }
    }

    async function fetchCurrentModel() {
        try {
            const savedSettings = getSavedApiSettings();
            const savedApiMode =
                savedSettings.selected_model === 'api' &&
                Boolean(savedSettings.api_key);
            if (savedApiMode) {
                const select = document.getElementById('model-select');
                if (select) {
                    select.value = 'api';
                    previousModelValue = 'api';
                    toggleApiInput();
                    await fetch('/api/model/switch', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({model: 'api'})
                    });
                }
                return;
            }

            const res = await fetch('/api/current_model');
            const data = await res.json();
            if (data.model) {
                const select = document.getElementById('model-select');
                if (select) {
                    select.value = data.model;
                    previousModelValue = data.model;
                    toggleApiInput(); // Ensure UI state matches
                }
            }
        } catch (e) {
            console.error("Failed to fetch current model:", e);
        }
    }

    // --- SESSION MANAGEMENT ---
    async function fetchSessionList() {
        try {
            const res = await fetch('/api/sessions');
            const data = await res.json();
            const allSessions = data.sessions || [];
            const list = document.getElementById('session-list');
            list.innerHTML = '';

            list.appendChild(createSidebarActionButton('New Project', createProject));
            list.appendChild(await createProjectSection(allSessions));
            list.appendChild(createSidebarActionButton('New Chat', createNewSession));

            const globalSessions = allSessions.filter(s => !s.project_id && s.role_mode !== 'assistant');
            const pinnedSessions = globalSessions.filter(s => s.pinned);
            const historySessions = globalSessions.filter(s => !s.pinned);

            // PINNED Section
            if (pinnedSessions.length > 0) {
                const pinnedSection = createSessionSection('PINNED', pinnedSessions, true);
                list.appendChild(pinnedSection);
            }

            // HISTORY Section
            if (historySessions.length > 0) {
                const historySection = createSessionSection('HISTORY', historySessions, false);
                list.appendChild(historySection);
            }
            
            // Also fetch status
            fetchStatus();
            loadSessionStats();
        } catch(e) { console.error("Fetch sessions failed", e); }
    }

    function createSidebarActionButton(label, handler) {
        const button = document.createElement('div');
        button.className = 'btn-new-chat';
        button.innerHTML = `<span>+</span> ${escapeHtml(label)}`;
        button.onclick = handler;
        return button;
    }

    async function createProjectSection(allSessions = []) {
        await loadProjects();
        const validProjectIds = new Set(projectsCache.map(project => project.id));
        if (currentProjectId && !validProjectIds.has(currentProjectId)) {
            setCurrentProject("", {refresh: false});
        }
        if (projectSidebarViewId && !validProjectIds.has(projectSidebarViewId)) {
            projectSidebarViewId = "";
        }
        const section = document.createElement('div');
        section.className = 'session-section project-section';
        const title = 'PROJECT';
        const projectSessionsById = new Map();
        allSessions.forEach(session => {
            if (!session.project_id) return;
            if (!projectSessionsById.has(session.project_id)) {
                projectSessionsById.set(session.project_id, []);
            }
            projectSessionsById.get(session.project_id).push(session);
        });

        const header = document.createElement('div');
        header.className = 'session-section-header';
        header.innerHTML = '<span class="icon">鈻?/span> PROJECT';
        section.appendChild(header);

        header.innerHTML = '<span class="icon">▾</span> PROJECT';

        const container = document.createElement('div');
        container.className = 'session-list-container';
        const viewedProject = projectsCache.find(project => project.id === projectSidebarViewId);
        if (viewedProject) {
            container.appendChild(createProjectBackItem(viewedProject));
            const projectSessions = projectSessionsById.get(viewedProject.id) || [];
            if (projectSessions.length) {
                projectSessions.forEach(session => container.appendChild(createProjectSessionItem(session)));
            } else {
                container.appendChild(createDisabledProjectItem('No conversations yet'));
            }
        } else if (projectsCache.length) {
            projectsCache.forEach(project => container.appendChild(createProjectItem(project)));
        } else {
            container.appendChild(createDisabledProjectItem('No Project'));
        }
        section.appendChild(container);

        header.innerHTML = `<span class="icon">▾</span> ${title}`;
        header.innerHTML = '<span class="icon">▾</span> PROJECT';
        header.innerHTML = '<span class="icon">v</span> PROJECT';
        header.onclick = () => {
            header.classList.toggle('collapsed');
            container.classList.toggle('collapsed');
        };

        return section;
    }

    function createProjectItem(project) {
        const projectId = project.id || '';
        const div = document.createElement('div');
        div.className = `session-item project-session-item${projectId === currentProjectId ? ' active' : ''}`;
        div.onclick = () => openProjectView(projectId);

        const content = document.createElement('div');
        content.className = 'session-content';
        content.innerHTML = `
            <div class="session-preview">${escapeHtml(project.name || 'Untitled Project')}</div>
            <div class="session-date">${projectId === currentProjectId ? 'Current' : 'Project'}</div>
        `;
        div.appendChild(content);
        return div;
    }

    function createProjectBackItem(project) {
        const div = document.createElement('div');
        div.className = 'session-item project-back-item';
        div.onclick = () => closeProjectView();

        const back = document.createElement('span');
        back.className = 'project-back-symbol';
        back.textContent = '<';
        div.appendChild(back);

        const content = document.createElement('div');
        content.className = 'session-content';
        content.innerHTML = `
            <div class="session-preview">${escapeHtml(project.name || 'Untitled Project')}</div>
            <div class="session-date">Back to projects</div>
        `;
        div.appendChild(content);
        return div;
    }

    function createDisabledProjectItem(label) {
        const div = document.createElement('div');
        div.className = 'session-item project-session-item project-empty-item';
        div.setAttribute('aria-disabled', 'true');

        const content = document.createElement('div');
        content.className = 'session-content';
        content.innerHTML = `
            <div class="session-preview">${escapeHtml(label)}</div>
            <div class="session-date">Project</div>
        `;
        div.appendChild(content);
        return div;
    }

    function createProjectSessionItem(s) {
        const div = createSessionItem(s);
        div.classList.add('project-child-session');
        return div;
    }

    async function loadProjects() {
        try {
            const res = await fetch('/api/projects');
            const data = await res.json();
            projectsCache = data.projects || [];
        } catch (e) {
            projectsCache = [];
        }
    }

    function setCurrentProject(projectId, options = {}) {
        currentProjectId = projectId || "";
        localStorage.setItem('saya_current_project_id', currentProjectId);
        if (options.refresh) fetchSessionList();
    }

    function openProjectView(projectId) {
        if (!projectId) return;
        projectSidebarViewId = projectId;
        fetchSessionList();
    }

    function closeProjectView() {
        projectSidebarViewId = "";
        fetchSessionList();
    }

    async function createProject() {
        const name = prompt("Project name:");
        if (!name || !name.trim()) return;
        const response = await fetch('/api/projects/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name.trim()})
        });
        const data = await response.json();
        if (data.project?.id) {
            currentProjectId = data.project.id;
            projectSidebarViewId = data.project.id;
            localStorage.setItem('saya_current_project_id', currentProjectId);
            await startNewSessionSilently(data.project.id);
        }
        fetchSessionList();
    }

    function showUnderDevelopment(name) {
        alert(`${name} is still under development.`);
    }

    function openToolsModal() {
        const modal = document.getElementById('tools-modal');
        if (modal) modal.style.display = 'flex';
    }

    function closeToolsModal() {
        const modal = document.getElementById('tools-modal');
        if (modal) modal.style.display = 'none';
    }

    function openRolesModal() {
        const modal = document.getElementById('roles-modal');
        if (modal) modal.style.display = 'flex';
    }

    function closeRolesModal() {
        const modal = document.getElementById('roles-modal');
        if (modal) modal.style.display = 'none';
    }

    function closeAssistantRoleModal() {
        const modal = document.getElementById('assistant-role-modal');
        if (modal) modal.style.display = 'none';
    }

    async function backToRolesFromAssistant() {
        if (currentRoleMode === 'assistant') {
            await exitAssistantRoleMode({openRoles: true, clearChat: true});
            return;
        }
        closeAssistantRoleModal();
        openRolesModal();
    }

    async function fetchAssistantRoleState() {
        const response = await fetch('/api/kb/roles/assistant/state');
        if (!response.ok) throw new Error(`Assistant state failed: ${response.status}`);
        assistantRoleState = await response.json();
        return assistantRoleState;
    }

    function markAssistantMemoryChangedThisEntry() {
        assistantMemoryChangedSinceEntry = true;
        localStorage.setItem('saya_assistant_role_memory_changed', 'true');
    }

    async function assistantMemoryChangedThisEntry() {
        try {
            await fetchAssistantRoleState();
        } catch (e) {
            console.warn("Unable to check Assistant memory state before exit", e);
        }
        const latestStamp = assistantRoleState?.last_read_at || '';
        const stampChanged = Boolean(
            assistantRoleEntryMemoryStamp &&
            latestStamp &&
            latestStamp !== assistantRoleEntryMemoryStamp
        );
        return assistantMemoryChangedSinceEntry || stampChanged;
    }

    async function openAssistantRoleModal() {
        closeRolesModal();
        const modal = document.getElementById('assistant-role-modal');
        const body = document.getElementById('assistant-role-body');
        if (!modal || !body) return;
        modal.style.display = 'flex';
        body.innerHTML = '<div class="about-you-empty">Loading Assistant role...</div>';
        try {
            await fetchAssistantRoleState();
            renderAssistantRoleModal();
        } catch (e) {
            body.innerHTML = `<div class="about-you-empty">Unable to load Assistant role: ${escapeHtml(e.message || e)}</div>`;
        }
    }

    function renderAssistantRoleModal() {
        const body = document.getElementById('assistant-role-body');
        if (!body) return;
        const state = assistantRoleState || {};
        const sessions = state.sessions || [];
        const vaultPath = state.vault_path || state.default_vault_path || '';
        const lastRead = state.last_read_at ? `Last read: ${escapeHtml(state.last_read_at)}` : 'No Obsidian read yet.';
        const memoryHtml = `
            <div class="assistant-memory-item">
                <strong>Normal memory</strong>
                <small>${escapeHtml(vaultPath || 'No Obsidian library configured')}</small>
            </div>
        `;
        const chatHtml = sessions.length
            ? sessions.map(session => `
                <div class="assistant-chat-item">
                    <div>
                        <strong>${escapeHtml(session.preview || session.name || session.id)}</strong>
                        <small>${escapeHtml(session.time || session.id || '')}</small>
                    </div>
                    <button onclick="openAssistantRoleSession('${escapeJsString(session.id)}')">Open</button>
                </div>
            `).join('')
            : '<div class="about-you-empty">No Assistant chats yet.</div>';
        const activeHtml = currentRoleMode === 'assistant'
            ? '<div class="about-you-warning">Assistant role is active. Ordinary long-term memory is disabled for Assistant chats.</div>'
            : '<div class="assistant-role-hint">Start or open an Assistant chat to use this role. Assistant chats use only Assistant role memory plus the current conversation.</div>';

        body.innerHTML = `
            ${activeHtml}
            <section class="assistant-role-panel">
                <div class="assistant-role-section-title">Obsidian library</div>
                <div class="assistant-role-row">
                    <input id="assistant-vault-path" type="text" value="${escapeHtml(vaultPath)}" placeholder="Obsidian vault path">
                </div>
                <div class="assistant-role-hint">Assistant memory is created only when you batch read the vault here. Rough read focuses on note titles and major headings; detailed read also keeps deeper headings and short note signals.</div>
                <div class="assistant-role-actions">
                    <button onclick="readAssistantVault('rough')">Rough batch read</button>
                    <button onclick="readAssistantVault('detailed')">Detailed batch read</button>
                </div>
                <div id="assistant-role-status" class="assistant-role-hint">${lastRead}${state.file_count ? ` · ${Number(state.file_count)} notes` : ''}</div>
            </section>
            <section class="assistant-role-panel">
                <div class="assistant-dedicated-memory-header">
                    <div class="assistant-role-section-title">Dedicated memory</div>
                    <button onclick="openAssistantMemoryPicker()">Add memory</button>
                </div>
                <div class="assistant-role-list">${memoryHtml}</div>
            </section>
            <section class="assistant-role-panel">
                <div class="assistant-role-section-title">Dedicated chats</div>
                <div class="assistant-role-actions">
                    <button class="primary" onclick="startAssistantRoleSession()">New Assistant chat</button>
                </div>
                <div class="assistant-role-list">${chatHtml}</div>
            </section>
        `;
    }

    const assistantMemoryPickerState = {
        vaultPath: '',
        currentPath: '',
        parentPath: null,
        selectedPath: null,
        selectedType: null
    };

    function closeAssistantMemoryPicker() {
        const modal = document.getElementById('assistant-memory-picker-modal');
        if (modal) modal.style.display = 'none';
    }

    async function openAssistantMemoryPicker() {
        const vaultPath = document.getElementById('assistant-vault-path')?.value.trim()
            || assistantRoleState?.vault_path
            || assistantRoleState?.default_vault_path
            || '';
        assistantMemoryPickerState.vaultPath = vaultPath;
        assistantMemoryPickerState.currentPath = '';
        assistantMemoryPickerState.parentPath = null;
        assistantMemoryPickerState.selectedPath = null;
        assistantMemoryPickerState.selectedType = null;
        const description = document.getElementById('assistant-memory-description');
        if (description) description.value = '';
        const modal = document.getElementById('assistant-memory-picker-modal');
        if (modal) modal.style.display = 'flex';
        await browseAssistantMemoryFolder('');
    }

    async function browseAssistantMemoryFolder(relativePath) {
        const list = document.getElementById('assistant-memory-picker-list');
        const status = document.getElementById('assistant-memory-picker-status');
        if (list) list.innerHTML = '<div class="about-you-empty">Loading library...</div>';
        if (status) status.textContent = '';
        try {
            const response = await fetch('/api/kb/roles/assistant/browse', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    vault_path: assistantMemoryPickerState.vaultPath,
                    relative_path: relativePath || ''
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.message || 'Unable to browse library');
            assistantMemoryPickerState.vaultPath = data.vault_path || assistantMemoryPickerState.vaultPath;
            assistantMemoryPickerState.currentPath = data.current_path || '';
            assistantMemoryPickerState.parentPath = data.parent_path;
            renderAssistantMemoryPickerEntries(data.entries || []);
            const vault = document.getElementById('assistant-memory-picker-vault');
            if (vault) vault.textContent = assistantMemoryPickerState.vaultPath;
            const current = document.getElementById('assistant-memory-picker-current');
            if (current) current.textContent = assistantMemoryPickerState.currentPath || 'Vault root';
            const up = document.getElementById('assistant-memory-picker-up');
            if (up) up.disabled = data.parent_path === null || data.parent_path === undefined;
        } catch (e) {
            if (list) list.innerHTML = '<div class="about-you-empty">Unable to open library.</div>';
            if (status) status.textContent = e.message || String(e);
        }
    }

    function renderAssistantMemoryPickerEntries(entries) {
        const list = document.getElementById('assistant-memory-picker-list');
        if (!list) return;
        list.innerHTML = '';
        if (!entries.length) {
            list.innerHTML = '<div class="about-you-empty">No folders or Markdown files here.</div>';
            return;
        }
        entries.forEach(entry => {
            const row = document.createElement('div');
            row.className = 'assistant-memory-entry';
            const main = document.createElement('div');
            main.className = 'assistant-memory-entry-main';
            main.tabIndex = 0;
            main.setAttribute('role', 'button');
            main.innerHTML = `<span aria-hidden="true">${entry.type === 'folder' ? '▸' : '·'}</span>` +
                `<span class="assistant-memory-entry-name">${escapeHtml(entry.name || entry.path)}</span>`;
            main.onclick = () => selectAssistantMemoryPath(entry.path, entry.type);
            main.onkeydown = event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectAssistantMemoryPath(entry.path, entry.type);
                }
            };
            row.appendChild(main);
            if (entry.type === 'folder') {
                const open = document.createElement('button');
                open.type = 'button';
                open.textContent = 'Open';
                open.onclick = () => browseAssistantMemoryFolder(entry.path);
                row.appendChild(open);
            }
            list.appendChild(row);
        });
    }

    function browseAssistantMemoryParent() {
        if (assistantMemoryPickerState.parentPath === null || assistantMemoryPickerState.parentPath === undefined) return;
        browseAssistantMemoryFolder(assistantMemoryPickerState.parentPath);
    }

    function selectAssistantMemoryCurrentFolder() {
        selectAssistantMemoryPath(assistantMemoryPickerState.currentPath || '', 'folder');
    }

    function selectAssistantMemoryPath(path, type) {
        assistantMemoryPickerState.selectedPath = path || '';
        assistantMemoryPickerState.selectedType = type;
        const displayPath = path || 'Vault root';
        const selected = document.getElementById('assistant-memory-picker-selected');
        if (selected) selected.textContent = `${type}: ${displayPath}`;
        const existing = (assistantRoleState?.path_memories || []).find(item =>
            String(item.path || '').toLowerCase() === displayPath.toLowerCase()
        );
        const description = document.getElementById('assistant-memory-description');
        if (description) description.value = existing?.description || '';
    }

    async function saveAssistantPathMemory() {
        const status = document.getElementById('assistant-memory-picker-status');
        const description = document.getElementById('assistant-memory-description')?.value.trim() || '';
        if (!assistantMemoryPickerState.selectedType) {
            if (status) status.textContent = 'Select a Markdown file or folder first.';
            return;
        }
        if (!description) {
            if (status) status.textContent = 'Add a description first.';
            return;
        }
        if (status) status.textContent = 'Saving memory...';
        try {
            const response = await fetch('/api/kb/roles/assistant/path-memory', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    vault_path: assistantMemoryPickerState.vaultPath,
                    relative_path: assistantMemoryPickerState.selectedPath,
                    description
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.message || 'Unable to save memory');
            assistantRoleState = data;
            markAssistantMemoryChangedThisEntry();
            closeAssistantMemoryPicker();
            renderAssistantRoleModal();
        } catch (e) {
            if (status) status.textContent = e.message || String(e);
        }
    }

    async function readAssistantVault(detail) {
        const label = detail === 'detailed' ? 'Detailed batch read' : 'Rough batch read';
        if (!confirm(`${label} will overwrite the existing Assistant dedicated memory. Continue?`)) return;
        const status = document.getElementById('assistant-role-status');
        if (status) status.innerText = detail === 'detailed' ? 'Reading vault in detailed mode...' : 'Reading vault in rough mode...';
        try {
            const response = await fetch('/api/kb/roles/assistant/read', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    vault_path: document.getElementById('assistant-vault-path')?.value || '',
                    detail
                })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.message || "Read failed");
            assistantRoleState = data;
            markAssistantMemoryChangedThisEntry();
            renderAssistantRoleModal();
        } catch (e) {
            if (status) status.innerText = `Read failed: ${e.message || e}`;
        }
    }

    async function startAssistantRoleSession() {
        try {
            setCurrentRoleMode('assistant');
            await startNewSessionSilently("", {roleMode: 'assistant'});
            closeAssistantRoleModal();
            collapseSidebarForAssistantChat();
        } catch (e) {
            alert("Failed to start Assistant chat: " + (e.message || e));
        }
    }

    async function openAssistantRoleSession(sessionId) {
        if (!sessionId) return;
        setCurrentRoleMode('assistant');
        closeAssistantRoleModal();
        const loaded = await loadSession(sessionId, {skipConfirm: true, skipAlert: true, roleMode: 'assistant'});
        if (loaded) {
            collapseSidebarForAssistantChat();
        } else {
            await openAssistantRoleModal();
        }
    }

    async function exitAssistantRoleMode(options = {}) {
        try {
            const memoryChanged = await assistantMemoryChangedThisEntry();
            let updated = false;
            if (memoryChanged) {
                const shouldUpdate = options.autoUpdate === true || confirm("Assistant dedicated memory changed during this role session. Update the locked normal-mode learning memory now?");
                if (shouldUpdate) {
                    const response = await fetch('/api/kb/roles/assistant/exit', {method: 'POST'});
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || data.message || "Exit failed");
                    updated = Boolean(data.updated);
                }
            }
            setCurrentRoleMode('');
            if (options.clearChat !== false) {
                await startNewSessionSilently(projectSidebarViewId || currentProjectId || "", {skipRoleExit: true});
            } else {
                fetchSessionList();
            }
            if (!options.silent && memoryChanged && updated) {
                alert("Assistant role exited. Normal-mode learning memory updated.");
            }
            if (options.openRoles) {
                closeAssistantRoleModal();
                openRolesModal();
            } else if (!options.keepModalClosed) {
                await openAssistantRoleModal();
            }
            return true;
        } catch (e) {
            if (!options.silent) alert("Failed to exit Assistant role: " + (e.message || e));
            return false;
        }
    }

    function backToToolsFromAboutYou() {
        const modal = document.getElementById('memory-modal');
        if (modal) modal.style.display = 'none';
        openToolsModal();
    }

    function backToAboutYouFromQuest() {
        closeQuestModal();
        openAboutYouModal();
    }

    function backToToolsFromVideoRecommendations() {
        closeVideoRecommendationsModal();
        openToolsModal();
    }

    function backToToolsFromVideoDigest() {
        closeVideoDigestModal();
        openToolsModal();
    }

    const ABOUT_YOU_PERSONALITY_QUESTIONS = [
        {
            key: 'energy',
            label: 'Energy source',
            options: [
                'I recharge by being alone and thinking quietly.',
                'I recharge by talking, sharing, and being around people.',
                'It depends heavily on context.'
            ]
        },
        {
            key: 'work_style',
            label: 'Work style',
            options: [
                'I like focused execution once the goal is clear.',
                'I like exploring options before committing.',
                'I alternate between exploration and execution.'
            ]
        },
        {
            key: 'information',
            label: 'Information style',
            options: [
                'I trust concrete details and lived experience first.',
                'I like patterns, possibilities, and abstract models first.',
                'I use both depending on the problem.'
            ]
        },
        {
            key: 'decisions',
            label: 'Decision style',
            options: [
                'I prefer logical consistency and direct truth.',
                'I prefer emotional nuance and impact on people.',
                'I want both weighed explicitly.'
            ]
        },
        {
            key: 'decision_speed',
            label: 'Decision speed',
            options: [
                'Help me decide quickly with a clear recommendation.',
                'Give me tradeoffs and let me choose deliberately.',
                'Use quick recommendations for small things, tradeoffs for big things.'
            ]
        },
        {
            key: 'structure',
            label: 'Structure style',
            options: [
                'I like plans, closure, and clear next steps.',
                'I like flexibility, exploration, and options staying open.',
                'I need structure for hard work and freedom for creative work.'
            ]
        },
        {
            key: 'detail',
            label: 'Detail level',
            options: [
                'Start concise and only expand if needed.',
                'Give me rich detail and context.',
                'Give a concise answer first, then useful depth.'
            ]
        },
        {
            key: 'social',
            label: 'Social pace',
            options: [
                'Slow trust, deep bonds.',
                'Warm quickly, then refine trust over time.',
                'Highly situational.'
            ]
        },
        {
            key: 'emotion_tone',
            label: 'Emotional tone',
            options: [
                'Steady and calm.',
                'Warm and emotionally expressive.',
                'Match my mood without overdoing it.'
            ]
        },
        {
            key: 'stress',
            label: 'Stress response',
            options: [
                'I want calm analysis and practical steps.',
                'I want emotional reassurance before solving.',
                'I want both, in that order.'
            ]
        },
        {
            key: 'failure_support',
            label: 'Failure support',
            options: [
                'Analyze what happened and make a recovery plan.',
                'Acknowledge the emotional hit first.',
                'Reassure me briefly, then help me move.'
            ]
        },
        {
            key: 'support',
            label: 'Preferred support',
            options: [
                'Be direct and help me act.',
                'Be gentle and help me feel understood.',
                'Match the situation.'
            ]
        },
        {
            key: 'feedback',
            label: 'Feedback preference',
            options: [
                'Be direct even when it is uncomfortable.',
                'Be gentle and frame critique carefully.',
                'Be direct, but keep the tone kind.'
            ]
        },
        {
            key: 'communication',
            label: 'Communication',
            options: [
                'Concise and precise.',
                'Warm, expressive, and detailed.',
                'Start concise, expand when useful.'
            ]
        },
        {
            key: 'initiative',
            label: 'Agent initiative',
            options: [
                'Take initiative and implement reasonable next steps.',
                'Ask before changing direction or taking action.',
                'Take initiative for low-risk work, ask for high-risk choices.'
            ]
        },
        {
            key: 'memory',
            label: 'Memory preference',
            options: [
                'Only remember explicit important facts.',
                'Propose memory candidates for confirmation.',
                'Remember more proactively when confidence is high.'
            ]
        },
        {
            key: 'boundaries',
            label: 'Boundaries',
            options: [
                'Keep roleplay and practical work clearly separated.',
                'Blend warmth naturally into practical work.',
                'Follow the tone I set in the moment.'
            ]
        }
    ];
    const ABOUT_YOU_PERSONALITY_DETAILED_QUESTIONS = [
        {
            key: 'mbti_energy',
            label: 'Energy orientation',
            options: [
                'More introverted: depth, quiet recovery, selective sharing.',
                'More extraverted: interactive thinking, social energy, spoken processing.',
                'Ambiverted: changes with context and trust.'
            ]
        },
        {
            key: 'mbti_perception',
            label: 'Perception style',
            options: [
                'Sensing-leaning: concrete examples, details, evidence, proven steps.',
                'Intuition-leaning: patterns, meaning, possibilities, systems.',
                'Balanced: concrete grounding plus abstract synthesis.'
            ]
        },
        {
            key: 'mbti_judgment',
            label: 'Judgment style',
            options: [
                'Thinking-leaning: consistency, logic, precision, tradeoffs.',
                'Feeling-leaning: values, relationships, emotional impact, harmony.',
                'Balanced: logic and emotional consequence both matter.'
            ]
        },
        {
            key: 'mbti_lifestyle',
            label: 'Lifestyle style',
            options: [
                'Judging-leaning: plans, closure, deadlines, stable routines.',
                'Perceiving-leaning: flexibility, improvisation, options, discovery.',
                'Hybrid: structure for commitments, openness for creative work.'
            ]
        },
        {
            key: 'trust',
            label: 'Trust building',
            options: [
                'Reliability and factual accuracy build trust fastest.',
                'Emotional attunement and loyalty build trust fastest.',
                'Both matter; trust grows through repeated good judgment.'
            ]
        },
        {
            key: 'attachment',
            label: 'Reassurance needs',
            options: [
                'I need little reassurance unless something is wrong.',
                'I value explicit reassurance and emotional presence.',
                'I prefer reassurance only when stress or uncertainty is high.'
            ]
        },
        {
            key: 'focus',
            label: 'Focus support',
            options: [
                'Help me narrow scope and remove distractions.',
                'Help me stay emotionally engaged with the task.',
                'Help me alternate between clarity and momentum.'
            ]
        },
        {
            key: 'planning',
            label: 'Planning style',
            options: [
                'I like step-by-step plans with clear milestones.',
                'I like lightweight direction and room to adapt.',
                'I like a plan, but it should not feel rigid.'
            ]
        },
        {
            key: 'learning',
            label: 'Learning style',
            options: [
                'Teach through examples and concrete walkthroughs.',
                'Teach through concepts and first principles.',
                'Teach concept first, then examples.'
            ]
        },
        {
            key: 'creativity',
            label: 'Creative collaboration',
            options: [
                'Generate bold options and help me choose.',
                'Iterate gently around my existing taste.',
                'Offer a strong point of view with alternatives.'
            ]
        },
        {
            key: 'motivation',
            label: 'Motivation style',
            options: [
                'Progress, clarity, and competence motivate me.',
                'Meaning, connection, and emotional resonance motivate me.',
                'I need both achievement and meaning.'
            ]
        },
        {
            key: 'conflict',
            label: 'Conflict style',
            options: [
                'Address conflict directly and quickly.',
                'Reduce emotional tension before solving.',
                'Name the issue calmly and solve with care.'
            ]
        },
        {
            key: 'change',
            label: 'Change handling',
            options: [
                'I adapt well when the reason is clear.',
                'I need emotional orientation before changing direction.',
                'I can adapt if the next step is concrete.'
            ]
        },
        {
            key: 'privacy',
            label: 'Privacy preference',
            options: [
                'Be cautious and remember only explicitly approved personal details.',
                'Remember meaningful personal context when it helps continuity.',
                'Ask before saving sensitive or intimate details.'
            ]
        },
        {
            key: 'autonomy',
            label: 'Autonomy preference',
            options: [
                'Act independently when the goal is clear.',
                'Collaborate closely before acting.',
                'Use judgment: act on reversible work, ask on irreversible work.'
            ]
        },
        {
            key: 'humor',
            label: 'Humor preference',
            options: [
                'Keep humor light and rare.',
                'Use playful warmth when the moment allows.',
                'Match my humor level.'
            ]
        },
        {
            key: 'rituals',
            label: 'Continuity rituals',
            options: [
                'Prefer practical continuity without rituals.',
                'Like small recurring emotional references.',
                'Use continuity only when it feels natural.'
            ]
        },
        {
            key: 'communication',
            label: 'Response shape',
            options: [
                'Answer first, brief explanation after.',
                'Explain reasoning and emotional nuance.',
                'Start with the answer, then structured detail.'
            ]
        },
        {
            key: 'support',
            label: 'Support mode',
            options: [
                'Coach me firmly.',
                'Comfort me gently.',
                'Switch between coach and companion.'
            ]
        },
        {
            key: 'memory',
            label: 'Memory behavior',
            options: [
                'Conservative: only explicit saved memories.',
                'Balanced: suggest important memory candidates.',
                'Proactive: save high-confidence stable facts.'
            ]
        }
    ];
    let aboutYouPersonalityEntries = [];
    let aboutYouPersonalityMode = 'simple';
    let aboutYouPersonalityFullEntry = null;
    let aboutYouPossessionState = null;

    function todayIsoDate() {
        return new Date().toISOString().slice(0, 10);
    }

    function openAboutYouModal() {
        closeToolsModal();
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("About you", "backToToolsFromAboutYou()");
        body.innerHTML = `
            <div class="about-you-grid">
                <button class="about-you-card" onclick="openMemoryViewer()">
                    <span>Memories</span>
                    <small>View, search, edit, star, and lock memory notes</small>
                </button>
                <button class="about-you-card" onclick="openAboutYouPersonality()">
                    <span>User personality</span>
                    <small>Dated self-assessment bound to protected memory</small>
                </button>
                <button class="about-you-card" onclick="openAboutYouPossessions()">
                    <span>Important possessions</span>
                    <small>Protected slots for meaningful belongings or objects</small>
                </button>
                <button class="about-you-card" onclick="openQuestModal()">
                    <span>Life quests</span>
                    <small>Personal goals and completed quest memory</small>
                </button>
                <button class="about-you-card" onclick="openMemorySettings()">
                    <span>Settings</span>
                    <small>Retrieval, metadata, and merge behavior</small>
                </button>
            </div>
        `;
        modal.style.display = 'flex';
    }

    async function openAboutYouPersonality() {
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("About you · User personality", "openAboutYouModal()");
        body.innerHTML = 'Loading...';
        modal.style.display = 'flex';
        try {
            const response = await fetch('/api/kb/about_you/personality');
            const data = await response.json();
            aboutYouPersonalityMode = data.mode || 'simple';
            aboutYouPersonalityEntries = data.entries || [];
            aboutYouPersonalityFullEntry = data.full_entry || null;
            renderAboutYouPersonality();
        } catch (e) {
            body.innerHTML = 'Unable to load personality profile.';
        }
    }

    function renderAboutYouPersonality(view = null) {
        setMemoryModalHeader("About you · User personality", "openAboutYouModal()");
        if (!view && aboutYouPersonalityMode === 'full' && aboutYouPersonalityFullEntry) {
            renderAboutYouPersonalityFullSummary();
            return;
        }
        const body = document.getElementById('modal-body');
        const questionHtml = ABOUT_YOU_PERSONALITY_QUESTIONS.map(question => `
            <label class="about-you-field">
                <span>${escapeHtml(question.label)}</span>
                <select id="about-personality-${escapeHtml(question.key)}">
                    ${question.options.map(option => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`).join('')}
                </select>
            </label>
        `).join('');
        const entryHtml = aboutYouPersonalityEntries.length
            ? aboutYouPersonalityEntries.slice().reverse().map(entry => `
                <div class="about-you-entry">
                    <div>
                        <strong>${escapeHtml(entry.date || '')}</strong>
                        <div>${escapeHtml(Object.values(entry.answers || {}).filter(Boolean).slice(0, 2).join(' · ') || 'Saved assessment')}</div>
                    </div>
                    <div class="about-you-entry-actions">
                        <button onclick="editAboutYouPersonality('${escapeJsString(entry.id)}')">Edit</button>
                        <button onclick="deleteAboutYouPersonality('${escapeJsString(entry.id)}')">Delete</button>
                    </div>
                </div>
            `).join('')
            : '<div class="about-you-empty">No personality assessment yet.</div>';
        const fullWarning = aboutYouPersonalityMode === 'full'
            ? '<div class="about-you-warning">Saving this simple questionnaire will replace the existing detailed profile in the protected memory slot.</div>'
            : '';

        body.innerHTML = `
            <input id="about-personality-id" type="hidden" value="">
            <div class="about-you-form">
                ${fullWarning}
                <label class="about-you-field about-you-date-field">
                    <span>Date</span>
                    <input id="about-personality-date" type="date" value="${todayIsoDate()}">
                </label>
                <div class="about-you-survey-grid">${questionHtml}</div>
                <label class="about-you-field">
                    <span>Optional note</span>
                    <textarea id="about-personality-note" rows="3" placeholder="Anything Saya should understand about how you think or feel..."></textarea>
                </label>
                <div class="about-you-hint">Simple personality memory keeps at most two dated versions in one protected memory slot.</div>
                <div class="about-you-actions">
                    <button onclick="renderAboutYouDetailedPersonality()">Detailed MBTI-like questionnaire</button>
                    <button onclick="resetAboutYouPersonalityForm()">New dated entry</button>
                    <button onclick="saveAboutYouPersonality()">Save personality memory</button>
                </div>
            </div>
            <div class="about-you-section-title">Saved assessments</div>
            <div>${entryHtml}</div>
        `;
    }

    function renderAboutYouPersonalityFullSummary() {
        setMemoryModalHeader("About you · User personality", "openAboutYouModal()");
        const body = document.getElementById('modal-body');
        const entry = aboutYouPersonalityFullEntry || {};
        const answers = entry.answers || {};
        const answerRows = ABOUT_YOU_PERSONALITY_DETAILED_QUESTIONS
            .filter(question => answers[question.key])
            .map(question => `
                <div class="about-you-answer-row">
                    <strong>${escapeHtml(question.label)}</strong>
                    <span>${escapeHtml(answers[question.key])}</span>
                </div>
            `).join('');
        body.innerHTML = `
            <div class="about-you-form">
                <div class="about-you-warning">Detailed personality profile is active. It occupies the single protected personality memory slot and replaces dated simple versions.</div>
                <div class="about-you-section-title">Detailed profile · ${escapeHtml(entry.date || '')}</div>
                <div class="about-you-answer-list">${answerRows || '<div class="about-you-empty">No detailed answers saved.</div>'}</div>
                ${entry.note ? `<div class="about-you-note">${escapeHtml(entry.note)}</div>` : ''}
                <div class="about-you-actions">
                    <button onclick="renderAboutYouPersonality('simple')">Replace with simple dated questionnaire</button>
                    <button onclick="renderAboutYouDetailedPersonality()">Retake detailed questionnaire</button>
                    <button onclick="deleteAboutYouPersonality('${escapeJsString(entry.id || '')}')">Clear profile</button>
                </div>
            </div>
        `;
    }

    function renderAboutYouDetailedPersonality() {
        setMemoryModalHeader("About you · Detailed personality", "openAboutYouPersonality()");
        const body = document.getElementById('modal-body');
        const entry = aboutYouPersonalityMode === 'full' ? (aboutYouPersonalityFullEntry || {}) : {};
        const answers = entry.answers || {};
        const questionHtml = ABOUT_YOU_PERSONALITY_DETAILED_QUESTIONS.map(question => `
            <label class="about-you-field">
                <span>${escapeHtml(question.label)}</span>
                <select id="about-personality-full-${escapeHtml(question.key)}">
                    ${question.options.map(option => `<option value="${escapeHtml(option)}" ${answers[question.key] === option ? 'selected' : ''}>${escapeHtml(option)}</option>`).join('')}
                </select>
            </label>
        `).join('');
        const replaceWarning = aboutYouPersonalityMode === 'full'
            ? 'Saving will overwrite the current detailed profile.'
            : 'Saving this detailed questionnaire will overwrite all dated simple versions and become the only personality profile.';
        body.innerHTML = `
            <input id="about-personality-full-id" type="hidden" value="${escapeHtml(entry.id || '')}">
            <div class="about-you-form">
                <div class="about-you-warning">${replaceWarning}</div>
                <label class="about-you-field about-you-date-field">
                    <span>Date</span>
                    <input id="about-personality-full-date" type="date" value="${escapeHtml(entry.date || todayIsoDate())}">
                </label>
                <div class="about-you-survey-grid about-you-detailed-grid">${questionHtml}</div>
                <label class="about-you-field">
                    <span>Optional note</span>
                    <textarea id="about-personality-full-note" rows="4" placeholder="Anything especially important for Saya's long-term understanding...">${escapeHtml(entry.note || '')}</textarea>
                </label>
                <div class="about-you-actions">
                    <button onclick="openAboutYouPersonality()">Cancel</button>
                    <button onclick="saveAboutYouDetailedPersonality()">Save detailed profile</button>
                </div>
            </div>
        `;
    }

    async function saveAboutYouDetailedPersonality() {
        const hasExisting = aboutYouPersonalityMode === 'full' || aboutYouPersonalityEntries.length > 0;
        if (hasExisting && !confirm("This will rewrite the protected personality memory slot and replace previous personality versions. Continue?")) return;
        const answers = {};
        ABOUT_YOU_PERSONALITY_DETAILED_QUESTIONS.forEach(question => {
            answers[question.key] = document.getElementById(`about-personality-full-${question.key}`)?.value || '';
        });
        try {
            const response = await fetch('/api/kb/about_you/personality', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    mode: 'full',
                    id: document.getElementById('about-personality-full-id')?.value || null,
                    date: document.getElementById('about-personality-full-date')?.value || todayIsoDate(),
                    answers,
                    note: document.getElementById('about-personality-full-note')?.value || ''
                })
            });
            if (!response.ok) throw new Error("Save failed");
            const data = await response.json();
            aboutYouPersonalityMode = data.mode || 'full';
            aboutYouPersonalityEntries = data.entries || [];
            aboutYouPersonalityFullEntry = data.full_entry || null;
            renderAboutYouPersonalityFullSummary();
        } catch (e) {
            alert("Failed to save detailed personality memory: " + e);
        }
    }

    function resetAboutYouPersonalityForm() {
        const id = document.getElementById('about-personality-id');
        const date = document.getElementById('about-personality-date');
        const note = document.getElementById('about-personality-note');
        if (id) id.value = '';
        if (date) date.value = todayIsoDate();
        if (note) note.value = '';
    }

    function editAboutYouPersonality(id) {
        const entry = aboutYouPersonalityEntries.find(item => String(item.id) === String(id));
        if (!entry) return;
        document.getElementById('about-personality-id').value = entry.id || '';
        document.getElementById('about-personality-date').value = entry.date || todayIsoDate();
        document.getElementById('about-personality-note').value = entry.note || '';
        ABOUT_YOU_PERSONALITY_QUESTIONS.forEach(question => {
            const select = document.getElementById(`about-personality-${question.key}`);
            if (select && entry.answers && entry.answers[question.key]) {
                select.value = entry.answers[question.key];
            }
        });
    }

    async function saveAboutYouPersonality() {
        const activeId = document.getElementById('about-personality-id')?.value || '';
        if (aboutYouPersonalityMode === 'full') {
            if (!confirm("This will replace the existing detailed personality profile with a simple dated version. Continue?")) return;
        } else if (!activeId && aboutYouPersonalityEntries.length >= 2) {
            if (!confirm("Only two dated simple personality versions are kept. Saving this will overwrite the oldest version. Continue?")) return;
        }
        const answers = {};
        ABOUT_YOU_PERSONALITY_QUESTIONS.forEach(question => {
            answers[question.key] = document.getElementById(`about-personality-${question.key}`)?.value || '';
        });
        try {
            const response = await fetch('/api/kb/about_you/personality', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    mode: 'simple',
                    id: activeId || null,
                    date: document.getElementById('about-personality-date')?.value || todayIsoDate(),
                    answers,
                    note: document.getElementById('about-personality-note')?.value || ''
                })
            });
            if (!response.ok) throw new Error("Save failed");
            const data = await response.json();
            aboutYouPersonalityMode = data.mode || 'simple';
            aboutYouPersonalityEntries = data.entries || [];
            aboutYouPersonalityFullEntry = data.full_entry || null;
            renderAboutYouPersonality();
        } catch (e) {
            alert("Failed to save personality memory: " + e);
        }
    }

    async function deleteAboutYouPersonality(id) {
        if (!confirm("Delete this dated assessment? The protected memory slot will be rewritten.")) return;
        try {
            const response = await fetch('/api/kb/about_you/personality/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id})
            });
            if (!response.ok) throw new Error("Delete failed");
            const data = await response.json();
            aboutYouPersonalityMode = data.mode || 'simple';
            aboutYouPersonalityEntries = data.entries || [];
            aboutYouPersonalityFullEntry = data.full_entry || null;
            renderAboutYouPersonality();
        } catch (e) {
            alert("Failed to delete personality entry: " + e);
        }
    }

    async function openAboutYouPossessions() {
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("About you · Important possessions", "openAboutYouModal()");
        body.innerHTML = 'Loading...';
        modal.style.display = 'flex';
        try {
            const response = await fetch('/api/kb/about_you/possessions');
            aboutYouPossessionState = await response.json();
            renderAboutYouPossessions();
        } catch (e) {
            body.innerHTML = 'Unable to load important possessions.';
        }
    }

    function renderAboutYouPossessions() {
        const body = document.getElementById('modal-body');
        const slots = aboutYouPossessionState?.slots || [];
        const suggested = Number(aboutYouPossessionState?.suggested_length || 600);
        const activeSlots = slots.filter(slot => slot.active);
        const listHtml = activeSlots.length
            ? activeSlots.map(slot => `
                <div class="about-you-entry">
                    <div>
                        <strong>Slot ${escapeHtml(slot.slot)} · ${escapeHtml(slot.title || 'Untitled')}</strong>
                        <div>${escapeHtml((slot.content || '').slice(0, 140))}${(slot.content || '').length > 140 ? '...' : ''}</div>
                        <small>${Number(slot.parts || 1)} protected memory slot${Number(slot.parts || 1) > 1 ? 's' : ''}</small>
                    </div>
                    <div class="about-you-entry-actions">
                        <button onclick="loadAboutYouPossessionSlot('${escapeJsString(slot.slot)}')">Edit</button>
                        <button onclick="deleteAboutYouPossession('${escapeJsString(slot.slot)}')">Delete</button>
                    </div>
                </div>
            `).join('')
            : '<div class="about-you-empty">No important possessions saved yet.</div>';

        body.innerHTML = `
            <div class="about-you-form">
                <div class="about-you-possession-row">
                    <label class="about-you-field">
                        <span>Slot</span>
                        <select id="about-possession-slot" onchange="loadAboutYouPossessionSlot(this.value)">
                            ${Array.from({length: 8}, (_, index) => {
                                const slot = String(index + 1);
                                return `<option value="${slot}">Slot ${slot}</option>`;
                            }).join('')}
                        </select>
                    </label>
                    <label class="about-you-field">
                        <span>Title</span>
                        <input id="about-possession-title" type="text" placeholder="A concise title">
                    </label>
                </div>
                <label class="about-you-field">
                    <span>Details</span>
                    <textarea id="about-possession-content" rows="6" maxlength="2400" placeholder="Keep it concise when possible." oninput="updateAboutYouPossessionCount()"></textarea>
                </label>
                <div class="about-you-hint">
                    Suggested length: ${suggested} chars. Longer notes are split into additional protected memory slots.
                    <span id="about-possession-count">0</span> chars.
                </div>
                <div class="about-you-actions">
                    <button onclick="clearAboutYouPossessionForm()">Clear form</button>
                    <button onclick="saveAboutYouPossession()">Save possession memory</button>
                </div>
            </div>
            <div class="about-you-section-title">Saved possessions</div>
            <div>${listHtml}</div>
        `;
        updateAboutYouPossessionCount();
    }

    function getAboutYouPossession(slotId) {
        return (aboutYouPossessionState?.slots || []).find(slot => String(slot.slot) === String(slotId));
    }

    function loadAboutYouPossessionSlot(slotId) {
        const slot = getAboutYouPossession(slotId);
        const slotSelect = document.getElementById('about-possession-slot');
        if (slotSelect) slotSelect.value = String(slotId || '1');
        document.getElementById('about-possession-title').value = slot?.title || '';
        document.getElementById('about-possession-content').value = slot?.content || '';
        updateAboutYouPossessionCount();
    }

    function clearAboutYouPossessionForm() {
        document.getElementById('about-possession-title').value = '';
        document.getElementById('about-possession-content').value = '';
        updateAboutYouPossessionCount();
    }

    function updateAboutYouPossessionCount() {
        const count = document.getElementById('about-possession-content')?.value.length || 0;
        const target = document.getElementById('about-possession-count');
        if (target) target.innerText = String(count);
    }

    async function saveAboutYouPossession() {
        const slot = document.getElementById('about-possession-slot')?.value || '1';
        const content = document.getElementById('about-possession-content')?.value.trim() || '';
        if (!content) {
            alert("Details are required. Use Delete to clear an existing slot.");
            return;
        }
        try {
            const response = await fetch('/api/kb/about_you/possessions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    slot,
                    title: document.getElementById('about-possession-title')?.value || '',
                    content
                })
            });
            if (!response.ok) throw new Error("Save failed");
            aboutYouPossessionState = await response.json();
            renderAboutYouPossessions();
            loadAboutYouPossessionSlot(slot);
        } catch (e) {
            alert("Failed to save possession memory: " + e);
        }
    }

    async function deleteAboutYouPossession(slot) {
        if (!confirm("Clear this possession slot? Its protected memory content will be rewritten and archived.")) return;
        try {
            const response = await fetch('/api/kb/about_you/possessions/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({slot})
            });
            if (!response.ok) throw new Error("Delete failed");
            aboutYouPossessionState = await response.json();
            renderAboutYouPossessions();
        } catch (e) {
            alert("Failed to delete possession slot: " + e);
        }
    }

    async function initializeVideoRecommendations() {
        try {
            const state = await fetchVideoRecommendationState(false);
            if (state?.settings?.auto_open && !videoRecommendationAutoOpened) {
                videoRecommendationAutoOpened = true;
                await openVideoDigestModal({refresh: false});
            }
        } catch (e) {
            console.warn("Video recommendations init failed", e);
        }
    }

    async function fetchVideoRecommendationState(refresh = false) {
        const endpoint = `/api/video_recommendations/recommendations?refresh=${refresh ? 'true' : 'false'}`;
        const response = await fetch(endpoint);
        if (!response.ok) throw new Error(`Video recommendations failed: ${response.status}`);
        videoRecommendationState = await response.json();
        return videoRecommendationState;
    }

    async function openVideoRecommendationsModal(options = {}) {
        closeToolsModal();
        closeVideoDigestModal();
        const modal = document.getElementById('video-recommendations-modal');
        if (modal) modal.style.display = 'flex';
        setVideoRecommendationStatus(options.refresh ? 'Refreshing...' : 'Loading...');
        try {
            await fetchVideoRecommendationState(Boolean(options.refresh));
            renderVideoRecommendations();
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus("Unable to load recommendations.");
            renderVideoRecommendations();
        }
    }

    function closeVideoRecommendationsModal() {
        const modal = document.getElementById('video-recommendations-modal');
        if (modal) modal.style.display = 'none';
    }

    async function openVideoDigestModal(options = {}) {
        closeToolsModal();
        const modal = document.getElementById('video-digest-modal');
        if (modal) modal.style.display = 'flex';
        setVideoDigestStatus(options.refresh ? 'Refreshing...' : 'Loading...');
        try {
            await fetchVideoRecommendationState(Boolean(options.refresh));
            renderVideoDigest();
        } catch (e) {
            console.error(e);
            setVideoDigestStatus("Unable to load recommendations.");
            renderVideoDigest();
        }
    }

    function closeVideoDigestModal() {
        const modal = document.getElementById('video-digest-modal');
        if (modal) modal.style.display = 'none';
    }

    function setVideoRecommendationStatus(text) {
        const status = document.getElementById('video-recommendation-status');
        if (status) status.innerText = text || '';
    }

    function setVideoDigestStatus(text) {
        const status = document.getElementById('video-digest-status');
        if (status) status.innerText = text || '';
    }

    function videoCredentialStatus(state) {
        const parts = [];
        parts.push(state.bilibili_cookie_configured
            ? `Bilibili cookie: ${state.bilibili_cookie_source || 'configured'}`
            : 'Bilibili cookie: not configured');
        if (state.youtube_api_key_configured) {
            parts.push(`YouTube API key: ${state.youtube_api_key_source || 'configured'}`);
        } else if (state.youtube_cookie_configured) {
            parts.push(`YouTube cookie: ${state.youtube_cookie_source || 'configured'}`);
        } else {
            parts.push('YouTube API key: not configured');
        }
        return parts.join(' / ');
    }

    function renderVideoRecommendations() {
        const state = videoRecommendationState || {};
        const autoOpen = document.getElementById('video-auto-open-checkbox');
        if (autoOpen) autoOpen.checked = Boolean(state.settings?.auto_open);
        renderVideoCreatorList(state.creators || []);
        renderVideoRecommendationList(state.management_videos || []);
        const checked = state.last_checked ? formatVideoDate(state.last_checked) : 'Not checked';
        const windowDays = state.window_days || state.settings?.recent_days || 3;
        setVideoRecommendationStatus(`Last check: ${checked} / Managing all updates from ${windowDays} days / ${videoCredentialStatus(state)}`);
    }

    function renderVideoDigest() {
        const state = videoRecommendationState || {};
        const list = document.getElementById('video-digest-list');
        if (!list) return;
        const creators = state.creators || [];
        const videos = (state.recommendations || []).slice(0, 8);
        const activeCreators = creators.filter(creator => creator.enabled !== false);
        const checked = state.last_checked ? formatVideoDate(state.last_checked) : 'Not checked';
        const windowDays = state.window_days || state.settings?.recent_days || 3;
        setVideoDigestStatus(`${activeCreators.length} creators / Last check: ${checked} / ${windowDays}-day unwatched updates / ${videoCredentialStatus(state)}`);

        list.innerHTML = '';
        if (!activeCreators.length) {
            list.innerHTML = '<div class="video-empty">No creators configured.</div>';
            return;
        }
        if (!videos.length) {
            list.innerHTML = '<div class="video-empty">No unwatched videos from the last 3 days.</div>';
            return;
        }
        videos.forEach(video => {
            const div = document.createElement('article');
            div.className = 'video-digest-card';
            const safeId = escapeJsString(video.id || '');
            const reasons = Array.isArray(video.reasons) ? video.reasons.slice(0, 3).join(' / ') : '';
            const score = Number(video.score || 0).toFixed(1);
            const url = /^(?:https?:\/\/)/i.test(video.url || '') ? video.url : '#';
            const thumbnail = /^(?:https?:\/\/)/i.test(video.thumbnail || '')
                ? `<img src="${escapeHtml(video.thumbnail)}" alt="">`
                : '<div class="video-digest-thumb-fallback"></div>';
            div.innerHTML = `
                <a class="video-digest-thumb" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">
                    ${thumbnail}
                </a>
                <div class="video-digest-card-body">
                    <div class="video-digest-meta">
                        <span class="video-platform-badge">${escapeHtml(video.platform || '')}</span>
                        ${escapeHtml(video.creator_name || '')}
                    </div>
                    <a class="video-digest-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">
                        ${escapeHtml(video.title || 'Untitled')}
                    </a>
                    <div class="video-digest-subline">
                        ${escapeHtml(formatVideoDate(video.published_at))} / Score ${escapeHtml(score)}
                    </div>
                    <div class="video-digest-reason">${escapeHtml(reasons)}</div>
                    <div class="video-card-actions">
                        <button class="video-discuss-btn" onclick="discussVideo('${safeId}', 'digest')">Discuss</button>
                        <button class="video-watch-btn" onclick="markVideoWatched('${safeId}', true, 'digest')">Mark watched</button>
                    </div>
                </div>
            `;
            list.appendChild(div);
        });
    }

    function renderVideoCreatorList(creators) {
        const list = document.getElementById('video-creator-list');
        if (!list) return;
        list.innerHTML = '';
        if (!creators.length) {
            list.innerHTML = '<div class="video-empty">No creators yet.</div>';
            return;
        }
        creators.forEach(creator => {
            const div = document.createElement('div');
            div.className = 'video-creator-item';
            const safeId = escapeJsString(creator.id);
            const status = creator.status || 'pending';
            const errorLine = creator.last_error ? `<div class="video-meta">${escapeHtml(creator.last_error)}</div>` : '';
            div.innerHTML = `
                <div>
                    <div class="video-creator-title">
                        <span class="video-platform-badge">${escapeHtml(creator.platform || '')}</span>
                        ${escapeHtml(creator.display_name || creator.name || '')}
                    </div>
                    <div class="video-meta">${escapeHtml(status)}${creator.source_id ? ' · ' + escapeHtml(creator.source_id) : ''}</div>
                    ${errorLine}
                </div>
                <button class="video-delete-btn" onclick="deleteVideoCreator('${safeId}')">Delete</button>
            `;
            list.appendChild(div);
        });
    }

    function renderVideoRecommendationList(videos) {
        const list = document.getElementById('video-recommendation-list');
        if (!list) return;
        list.innerHTML = '';
        if (!videos.length) {
            list.innerHTML = '<div class="video-empty">No videos from the last 3 days.</div>';
            return;
        }
        videos.forEach(video => {
            const div = document.createElement('div');
            div.className = 'video-rec-card';
            const watchedBadge = video.watched ? '<span class="video-watched-badge">Watched</span>' : '';
            const watchLabel = video.watched ? 'Unwatch' : 'Mark watched';
            const watchTarget = video.watched ? 'false' : 'true';
            const safeId = escapeJsString(video.id || '');
            const reasons = Array.isArray(video.reasons) ? video.reasons.join(' · ') : '';
            const score = Number(video.score || 0).toFixed(1);
            const url = /^(?:https?:\/\/)/i.test(video.url || '') ? video.url : '#';
            div.innerHTML = `
                <div class="video-rec-title">
                    <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(video.title || 'Untitled')}</a>
                    ${watchedBadge}
                </div>
                <div class="video-meta">
                    <span class="video-platform-badge">${escapeHtml(video.platform || '')}</span>
                    ${escapeHtml(video.creator_name || '')} · ${escapeHtml(formatVideoDate(video.published_at))} · Score ${escapeHtml(score)}
                </div>
                <div class="video-reason">${escapeHtml(reasons)}</div>
                <div class="video-card-actions">
                    <button class="video-discuss-btn" onclick="discussVideo('${safeId}', 'recommendations')">Discuss</button>
                    <button class="video-watch-btn ${video.watched ? 'is-watched' : ''}" onclick="markVideoWatched('${safeId}', ${watchTarget}, 'recommendations')">${watchLabel}</button>
                </div>
            `;
            list.appendChild(div);
        });
    }

    async function markVideoWatched(id, watched = true, source = 'recommendations') {
        if (!id) return;
        if (source === 'digest') {
            setVideoDigestStatus("Saving watched status...");
        } else {
            setVideoRecommendationStatus("Saving watched status...");
        }
        try {
            const response = await fetch('/api/video_recommendations/videos/watched', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id, watched})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Save failed: ${response.status}`);
            videoRecommendationState = data.state || videoRecommendationState;
            renderVideoRecommendations();
            renderVideoDigest();
            const savedMessage = watched ? "Marked watched." : "Marked unwatched.";
            if (source === 'digest') {
                setVideoDigestStatus(savedMessage);
            } else {
                setVideoRecommendationStatus(savedMessage);
            }
        } catch (e) {
            console.error(e);
            if (source === 'digest') {
                setVideoDigestStatus("Failed to save watched status.");
            } else {
                setVideoRecommendationStatus("Failed to save watched status.");
            }
        }
    }

    async function discussVideo(id, source = 'recommendations') {
        if (!id) return;
        const setStatus = source === 'digest' ? setVideoDigestStatus : setVideoRecommendationStatus;
        setStatus("Preparing discussion...");
        try {
            const response = await fetch('/api/video_recommendations/videos/discuss', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Discussion failed: ${response.status}`);
            videoRecommendationState = data.state || videoRecommendationState;
            renderVideoRecommendations();
            renderVideoDigest();
            closeVideoDigestModal();
            closeVideoRecommendationsModal();

            const displayText = data.display_text || `Doctor给你分享了一个视频：${data.video?.title || 'Untitled'}`;
            setStatus("Starting new chat...");
            await startNewSessionSilently();
            setStatus("Starting discussion...");
            await sendMessage({
                userText: data.prompt || displayText,
                displayText
            });
        } catch (e) {
            console.error(e);
            setStatus(e.message || "Failed to prepare discussion.");
        }
    }

    async function saveVideoRecommendationSettings() {
        const checkbox = document.getElementById('video-auto-open-checkbox');
        try {
            const response = await fetch('/api/video_recommendations/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({auto_open: Boolean(checkbox?.checked)})
            });
            if (!response.ok) throw new Error(`Save failed: ${response.status}`);
            const data = await response.json();
            videoRecommendationState = {
                ...(videoRecommendationState || {}),
                settings: data.settings || {}
            };
            setVideoRecommendationStatus("Settings saved.");
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus("Settings save failed.");
        }
    }

    async function addVideoCreator() {
        const platform = document.getElementById('video-platform-select')?.value || 'bilibili';
        const input = document.getElementById('video-creator-input');
        const name = input?.value.trim();
        if (!name) return;
        setVideoRecommendationStatus("Adding creator...");
        try {
            const response = await fetch('/api/video_recommendations/creators/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({platform, name})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `Add failed: ${response.status}`);
            if (input) input.value = '';
            await fetchVideoRecommendationState(true);
            renderVideoRecommendations();
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus(e.message || "Add failed.");
        }
    }

    async function deleteVideoCreator(id) {
        if (!confirm("Delete this creator?")) return;
        setVideoRecommendationStatus("Deleting creator...");
        try {
            const response = await fetch('/api/video_recommendations/creators/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id})
            });
            if (!response.ok) throw new Error(`Delete failed: ${response.status}`);
            await fetchVideoRecommendationState(true);
            renderVideoRecommendations();
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus("Delete failed.");
        }
    }

    async function refreshVideoRecommendations() {
        setVideoRecommendationStatus("Refreshing...");
        try {
            await fetchVideoRecommendationState(true);
            renderVideoRecommendations();
        } catch (e) {
            console.error(e);
            setVideoRecommendationStatus("Refresh failed.");
        }
    }

    async function refreshVideoDigest() {
        setVideoDigestStatus("Refreshing...");
        try {
            await fetchVideoRecommendationState(true);
            renderVideoDigest();
        } catch (e) {
            console.error(e);
            setVideoDigestStatus("Refresh failed.");
        }
    }

    function formatVideoDate(value) {
        if (!value) return 'Unknown date';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
        return date.toLocaleString([], {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    async function openChatSearch() {
        const query = prompt("Search conversation history:");
        if (!query || !query.trim()) return;
        const response = await fetch('/api/sessions/search', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({query: query.trim(), limit: 50})
        });
        const data = await response.json();
        const modal = document.getElementById('memory-modal');
        const body = document.getElementById('modal-body');
        setMemoryModalHeader("Search Results");
        body.innerHTML = '';
        modal.style.display = 'flex';
        const results = data.results || [];
        if (!results.length) {
            body.innerHTML = '<div style="color:#888; padding:20px; text-align:center;">No matching conversations found.</div>';
            return;
        }
        results.forEach(result => {
            const div = document.createElement('div');
            div.className = 'memory-item';
            const safeId = escapeJsString(result.id);
            div.innerHTML = `
                <div style="flex:1">
                    <div class="memory-title-row">${escapeHtml(result.name || result.id)} · ${Number(result.match_count || 0)} matches</div>
                    <div class="memory-meta">${escapeHtml(result.time || '')}</div>
                    ${(result.matches || []).map(match => `<div class="memory-content">${escapeHtml(match.snippet)}</div>`).join('')}
                </div>
                <div class="memory-actions">
                    <button class="btn-mem-action" onclick="closeModal(); loadSession('${safeId}')">Open</button>
                </div>
            `;
            body.appendChild(div);
        });
    }

    function createSessionSection(title, sessions, isPinned) {
        const section = document.createElement('div');
        section.className = 'session-section';

        const header = document.createElement('div');
        header.className = 'session-section-header';
        header.innerHTML = `<span class="icon">▼</span> ${title}`;
        header.onclick = () => {
            header.classList.toggle('collapsed');
            container.classList.toggle('collapsed');
        };
        header.innerHTML = `<span class="icon">v</span> ${title}`;
        section.appendChild(header);

        const container = document.createElement('div');
        container.className = 'session-list-container';
        
        sessions.forEach(s => {
            container.appendChild(createSessionItem(s));
        });
        section.appendChild(container);

        return section;
    }

    function createSessionItem(s) {
        const div = document.createElement('div');
        div.className = 'session-item';
        div.onclick = (e) => {
            // Ignore if clicked on menu button or menu itself
            if(e.target.closest('.session-menu-btn') || e.target.closest('.session-menu-dropdown')) return;
            if (s.project_id) {
                setCurrentProject(s.project_id, {refresh: false});
                projectSidebarViewId = s.project_id;
            } else {
                setCurrentProject("", {refresh: false});
            }
            loadSession(s.id);
        };

        const content = document.createElement('div');
        content.className = 'session-content';
        content.innerHTML = `
            <div class="session-preview">${escapeHtml(s.preview)}</div>
            <div class="session-date">${escapeHtml(s.time || s.id)}${s.project_name ? ' · ' + escapeHtml(s.project_name) : ''}${s.legacy ? ' · Legacy (read-only)' : ''}</div>
        `;
        div.appendChild(content);

        // Menu Button (Vertical Ellipsis)
        const menuBtn = document.createElement('button');
        menuBtn.className = 'session-menu-btn';
        menuBtn.innerHTML = '⋮';
        menuBtn.onclick = (e) => {
            e.stopPropagation();
            toggleSessionMenu(e, s, div);
        };
        div.appendChild(menuBtn);

        return div;
    }

    function toggleSessionMenu(e, s, parentItem) {
        // Close any existing menus
        document.querySelectorAll('.session-menu-dropdown').forEach(el => el.remove());
        document.querySelectorAll('.session-menu-btn').forEach(el => el.classList.remove('active'));

        const btn = e.currentTarget;
        btn.classList.add('active');

        const menu = document.createElement('div');
        menu.className = 'session-menu-dropdown show';
        
        if (!s.legacy) {
            // Pin/Unpin Option
            const pinOption = document.createElement('div');
            pinOption.className = 'menu-option';
            pinOption.innerHTML = s.pinned ? '<span>Unpin</span>' : '<span>Pin</span>';
            pinOption.onclick = (ev) => {
                ev.stopPropagation();
                s.pinned ? unpinSession(s.id) : pinSession(s.id);
                menu.remove();
            };
            menu.appendChild(pinOption);
        }

        // Archive Option
        const archiveOption = document.createElement('div');
        archiveOption.className = 'menu-option';
        archiveOption.innerHTML = '<span>Archive</span>';
        archiveOption.onclick = (ev) => {
            ev.stopPropagation();
            archiveSession(s.id);
            menu.remove();
        };
        menu.appendChild(archiveOption);

        if (!s.legacy) {
            // Delete Option
            const delOption = document.createElement('div');
            delOption.className = 'menu-option delete';
            delOption.innerHTML = '<span>Delete</span>';
            delOption.onclick = (ev) => {
                ev.stopPropagation();
                deleteSession(s.id);
                menu.remove();
            };
            menu.appendChild(delOption);
        }

        document.body.appendChild(menu);

        // Positioning
        const rect = btn.getBoundingClientRect();
        menu.style.top = rect.bottom + 5 + 'px';
        menu.style.left = (rect.right - 120) + 'px'; // Align right edge

        // Close on click outside
        setTimeout(() => {
            const closeHandler = (ev) => {
                if (!menu.contains(ev.target) && ev.target !== btn) {
                    menu.remove();
                    btn.classList.remove('active');
                    document.removeEventListener('click', closeHandler);
                }
            };
            document.addEventListener('click', closeHandler);
        }, 0);
    }

    async function pinSession(id) {
        try {
            const res = await fetch('/api/sessions/pin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: id})
            });
            if (!res.ok) throw new Error("API request failed");
            const data = await res.json();
            if (!data.success) throw new Error("Server returned failure");
            fetchSessionList();
        } catch(e) { 
            console.error("Pin failed", e);
            alert("Failed to pin session. Please ensure server is restarted to apply new changes.");
        }
    }

    async function unpinSession(id) {
        try {
            const res = await fetch('/api/sessions/unpin', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: id})
            });
            if (!res.ok) throw new Error("API request failed");
            fetchSessionList();
        } catch(e) { 
            console.error("Unpin failed", e);
            alert("Failed to unpin session. Please ensure server is restarted.");
        }
    }

    async function fetchStatus() {
        // [MODIFIED] Emotion system is now autonomous via model tags. 
        // We no longer fetch numeric status from backend for UI bars.
        try {
            // Keep fetching status if there are other things (like server health), 
            // but for now, we just ignore the old emotion bars.
            // const res = await fetch('/api/status');
            // const data = await res.json();
            // ...
        } catch(e) {}
    }

    // [REMOVED] toggleEmotionLock (Emotion system is autonomous)
    /*
    async function toggleEmotionLock() {
       ...
    }
    */

    async function showOfflineReport() {
        try {
            const res = await fetch('/api/emotion/offline_report');
            const data = await res.json();
            let msg = data.message;
            if (data.details && data.details.length > 0) {
                msg += "\n\nChanges:\n" + data.details.join("\n");
            }
            alert(msg);
        } catch(e) { alert("Failed to fetch report"); }
    }

    async function loadSession(id, options = {}) {
        if(!options.skipConfirm && !confirm("Load this session? Current unsaved chat will be lost.")) return false;
        try {
            const res = await fetch('/api/sessions/load_full', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: id})
            });
            const data = await res.json();
            const loadedRoleMode = options.roleMode || data.role_mode || '';
            if (loadedRoleMode === 'assistant') {
                setCurrentRoleMode('assistant');
            } else if (currentRoleMode === 'assistant') {
                await exitAssistantRoleMode({silent: true, keepModalClosed: true, clearChat: false});
            } else {
                setCurrentRoleMode('');
            }
            const chatContent = document.getElementById('chat-content');
            if (chatContent) chatContent.innerHTML = '';
            
            (data.items || []).forEach((item, historyIndex) => {
                if (item.role === "user") {
                    appendMessage('user', item.content || '', {
                        historyIndex,
                        attachments: item.attachments || []
                    });
                } else if (item.role === "ai" || item.role === "assistant") {
                    appendMessage('ai', item.content || '', {
                        historyIndex,
                        translation: item.translation || '',
                        readableGenerated: item.readable_generated === true
                    });
                }
            });
            currentHistoryLength = (data.items || []).length;
            currentSessionId = data.session_id || id;
            if (data.migrated_from) fetchSessionList();
            if (!options.skipAlert) alert("Session loaded.");
            return true;
        } catch(e) {
            alert("Load failed: " + e);
            return false;
        }
    }

    async function archiveSession(sessionId) {
        if(!confirm("Archive this session to memory? It will be summarized and saved.")) return;
        try {
            saveCurrentApiSettings();
            const res = await fetch('/api/sessions/archive', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: sessionId, ...collectArchiveGenerationSettings()})
            });
            const data = await res.json();
            if(data.status === 'success') {
                alert(`Session archived successfully!\nTitle: ${data.title}`);
            } else {
                alert("Archive failed: " + (data.message || "Unknown error"));
            }
        } catch(e) { alert("Error: " + e); }
    }

    async function startNewSessionSilently(projectIdOverride = undefined, options = {}) {
        const roleMode = options.roleMode || '';
        if (currentRoleMode === 'assistant' && roleMode !== 'assistant' && !options.skipRoleExit) {
            await exitAssistantRoleMode({silent: true, keepModalClosed: true, clearChat: false});
        }
        const sessionProjectId = projectIdOverride !== undefined
            ? (projectIdOverride || "")
            : (projectSidebarViewId || currentProjectId || "");
        const response = await fetch('/api/sessions/new', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                project_id: roleMode === 'assistant' ? null : (sessionProjectId || null),
                role_mode: roleMode || null
            })
        });
        if (!response.ok) throw new Error(`New session failed: ${response.status}`);
        const data = await response.json();
        const chatContent = document.getElementById('chat-content');
        if (chatContent) chatContent.innerHTML = '';
        // [MODIFIED] Removed trans-content clear
        currentHistoryLength = 0; // Reset
        currentSessionId = data.session_id || null;
        if (roleMode === 'assistant') {
            setCurrentRoleMode('assistant');
        } else {
            setCurrentRoleMode('');
            if (data.project_id) {
                setCurrentProject(data.project_id, {refresh: false});
                projectSidebarViewId = data.project_id;
            } else {
                setCurrentProject("", {refresh: false});
            }
        }
        fetchSessionList();
        return data;
    }

    async function createNewSession() {
        if(!confirm("Start new session?")) return;
        await startNewSessionSilently(projectSidebarViewId || "");
    }

    async function deleteSession(id) {
        if(!confirm("Delete this session permanently?")) return;
        try {
            const deletingCurrentSession = currentSessionId && String(currentSessionId) === String(id);
            const previewResponse = await fetch('/api/sessions/delete-preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: id})
            });
            if (!previewResponse.ok) throw new Error(`Delete preview failed: ${previewResponse.status}`);
            const preview = await previewResponse.json();
            const autoMemories = Array.isArray(preview.auto_memories) ? preview.auto_memories : [];
            let deleteAutoMemories = false;

            if (autoMemories.length) {
                const memoryList = autoMemories
                    .map((memory, index) => {
                        const title = String(memory.title || 'Untitled automatic memory').trim();
                        const content = String(memory.content || '').trim();
                        return `${index + 1}. ${title}${content ? `\n   ${content}` : ''}`;
                    })
                    .join('\n\n');
                const message = [
                    `This session created ${autoMemories.length} automatic memor${autoMemories.length === 1 ? 'y' : 'ies'}.`,
                    'Deleting the session will also permanently delete the following automatic memories:',
                    '',
                    memoryList,
                    '',
                    'Continue and delete both the session and these memories?'
                ].join('\n');
                if (!confirm(message)) return;
                deleteAutoMemories = true;
            }

            const response = await fetch('/api/sessions/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    session_id: id,
                    delete_auto_memories: deleteAutoMemories
                })
            });
            if (!response.ok) throw new Error(`Delete failed: ${response.status}`);
            const data = await response.json();
            if (data.requires_memory_confirmation) {
                throw new Error("Automatic memories changed before this session was deleted. Please try again.");
            }
            if (!data.success) throw new Error("Server returned failure");

            const deletingDisplayedNormalSession = currentRoleMode !== 'assistant' &&
                (deletingCurrentSession || data.was_active_session === true);
            if (deletingDisplayedNormalSession) {
                const chatContent = document.getElementById('chat-content');
                if (chatContent) chatContent.innerHTML = '';
                currentHistoryLength = 0;
                currentSessionId = null;
                await startNewSessionSilently(projectSidebarViewId || currentProjectId || "");
            } else if (deletingCurrentSession) {
                await startNewSessionSilently(projectSidebarViewId || currentProjectId || "");
            } else {
                fetchSessionList();
            }
        } catch (e) {
            console.error("Delete session failed", e);
            alert("Failed to delete session: " + e.message);
        }
    }

    function toggleApiInput() {
        const select = document.getElementById('model-select');
        if (select && select.value === 'api' && !getSavedApiSettings().api_key) {
            console.info("API mode selected. Configure API details in Settings → LLM API.");
        }
    }

    let questionAnchorSequence = 0;

    function getQuestionIndexText(rawText) {
        const text = String(rawText || '')
            .replace(/\[FILE_CONTEXT\][\s\S]*?\[\/FILE_CONTEXT\]/gi, ' ')
            .replace(/\[(?:IMAGE|FILE):[^\]]*\]/gi, ' ')
            .replace(/\(System Note:[\s\S]*?\)\s*/gi, ' ')
            .replace(/^Doctor:\s*/i, '')
            .replace(/\s+/g, ' ')
            .trim();
        return text || 'Attachment';
    }

    function refreshQuestionIndex() {
        const chatContent = document.getElementById('chat-content');
        const index = document.getElementById('question-index');
        const list = document.getElementById('question-index-list');
        if (!chatContent || !index || !list) return;

        const userRows = Array.from(chatContent.querySelectorAll(':scope > .message-row.user-row'));
        list.innerHTML = '';
        index.hidden = userRows.length === 0;

        userRows.forEach(row => {
            if (!row.id) row.id = `chat-question-${++questionAnchorSequence}`;
            const fullLabel = row.dataset.questionText || getQuestionIndexText(row.dataset.rawContent);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'question-index-item';
            button.dataset.anchorId = row.id;
            button.setAttribute('aria-label', `Go to question: ${fullLabel}`);
            button.innerHTML = `<span class="question-index-label">${escapeHtml(fullLabel)}</span>`;
            button.addEventListener('click', () => {
                row.scrollIntoView({behavior: 'smooth', block: 'center'});
            });
            list.appendChild(button);
        });

        updateQuestionIndexActiveState();
    }

    function updateQuestionIndexActiveState() {
        const chatContent = document.getElementById('chat-content');
        const list = document.getElementById('question-index-list');
        if (!chatContent || !list) return;
        const rows = Array.from(chatContent.querySelectorAll(':scope > .message-row.user-row'));
        if (!rows.length) return;

        const focusLine = chatContent.scrollTop + (chatContent.clientHeight * 0.42);
        let nearest = rows[0];
        let nearestDistance = Infinity;
        rows.forEach(row => {
            const distance = Math.abs(row.offsetTop - focusLine);
            if (distance < nearestDistance) {
                nearest = row;
                nearestDistance = distance;
            }
        });
        list.querySelectorAll('.question-index-item').forEach(button => {
            button.classList.toggle('active', button.dataset.anchorId === nearest.id);
        });
    }

    function appendMessage(type, text, options = {}) {
        const chatContent = document.getElementById('chat-content');
        
        // Create Row Container
        const rowDiv = document.createElement('div');
        rowDiv.className = `message-row ${type}-row`;
        rowDiv.dataset.rawContent = text; // Store raw content for edit/regenerate
        rowDiv._attachments = cloneAttachmentMetadata(options.attachments);
        if (type === 'user') rowDiv.dataset.questionText = getQuestionIndexText(text);
        if (Number.isInteger(options.historyIndex)) {
            rowDiv.dataset.historyIndex = String(options.historyIndex);
        }
        if (type === 'ai' && options.readableGenerated) {
            rowDiv.dataset.readableGenerated = 'true';
        }
        
        // Create Bubble
        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = `message-bubble ${type}`; // 'user' or 'ai' class for legacy selector compatibility
        
        // Content
        const contentDiv = document.createElement('div');
        if (type === 'ai') {
            contentDiv.innerHTML = `Saya: ` + renderMarkdownSafe(text);
        } else {
            // Check for image tag [IMAGE:url]
            const imgMatch = text.match(/\[IMAGE:(.*?)\]/);
            const fileMatch = text.match(/\[FILE:(.*?)\]/);
            
            // Extract System Note if present (User side)
            let displayNote = "";
            let remainingText = text;
            
            // Logic: The message structure is [IMAGE:...] (System Note: ...)\n\nUser Text
            // We first extract/remove the image tag
            if (imgMatch) {
                const imgUrl = imgMatch[1];
                let textAfterImage = text.replace(imgMatch[0], "").trim();
                
                // Check if it starts with System Note
                if (textAfterImage.startsWith("(System Note:")) {
                    // We look for the separator we added in sendMessage (\n\n)
                    // If not found, maybe user text is empty, so the whole thing is note
                    const splitIndex = textAfterImage.indexOf("\n\n");
                    
                    let notePart = "";
                    if (splitIndex !== -1) {
                        notePart = textAfterImage.substring(0, splitIndex);
                        remainingText = textAfterImage.substring(splitIndex).trim();
                    } else {
                        // No user text following
                        notePart = textAfterImage;
                        remainingText = "";
                    }
                    
                    // Extract content from notePart: "(System Note: ... Content: ...)"
                    const contentMarker = "Content:";
                    const cIdx = notePart.indexOf(contentMarker);
                    if (cIdx !== -1) {
                        let rawContent = notePart.substring(cIdx + contentMarker.length).trim();
                        // Remove trailing ')'
                        if (rawContent.endsWith(')')) {
                            rawContent = rawContent.substring(0, rawContent.length - 1);
                        }
                        displayNote = rawContent;
                    }
                } else {
                    remainingText = textAfterImage;
                }

                const img = document.createElement('img');
                img.src = /^(?:\/|https?:\/\/)/i.test(imgUrl) ? imgUrl : '';
                img.style.maxWidth = '100%';
                img.style.borderRadius = '8px';
                img.style.display = 'block';
                img.style.marginBottom = '8px';
                img.onclick = () => window.open(imgUrl, '_blank');
                img.style.cursor = 'pointer';
                
                contentDiv.appendChild(img);
                
                // Show Description Toggle
                if (displayNote) {
                    const details = document.createElement('details');
                    details.style.marginBottom = '10px';
                    details.style.fontSize = '0.75rem';
                    details.style.color = '#888';
                    
                    const summary = document.createElement('summary');
                    summary.innerText = "Show Description";
                    summary.style.cursor = 'pointer';
                    summary.style.outline = 'none';
                    
                    const p = document.createElement('p');
                    p.innerText = displayNote;
                    p.style.marginTop = '5px';
                    p.style.background = 'rgba(0,0,0,0.2)';
                    p.style.padding = '5px';
                    p.style.borderRadius = '4px';
                    
                    details.appendChild(summary);
                    details.appendChild(p);
                    contentDiv.appendChild(details);
                }

                if (remainingText.length > 0) {
                    contentDiv.appendChild(document.createTextNode(`Doctor: ${remainingText}`));
                }
            } else if (fileMatch) {
                const filename = fileMatch[1];
                const fileContextPattern = /\[FILE_CONTEXT\][\s\S]*?\[\/FILE_CONTEXT\]/;
                const cleanText = text
                    .replace(fileMatch[0], "")
                    .replace(fileContextPattern, "")
                    .trim();
                
                const fileDiv = document.createElement('div');
                fileDiv.style.background = 'rgba(255,255,255,0.1)';
                fileDiv.style.padding = '8px 12px';
                fileDiv.style.borderRadius = '6px';
                fileDiv.style.display = 'flex';
                fileDiv.style.alignItems = 'center';
                fileDiv.style.gap = '10px';
                fileDiv.style.marginBottom = '5px';
                fileDiv.style.border = '1px solid rgba(255,255,255,0.2)';
                
                fileDiv.innerHTML = `
                    <span style="font-size:1.5rem;">📄</span>
                    <div style="display:flex; flex-direction:column;">
                        <span style="font-weight:bold; font-size:0.9rem;">${escapeHtml(filename)}</span>
                        <span style="font-size:0.75rem; opacity:0.7;">File Content Uploaded</span>
                    </div>
                `;
                
                contentDiv.appendChild(fileDiv);
                if (cleanText.length > 0) {
                    contentDiv.appendChild(document.createTextNode(`Doctor: ${cleanText}`));
                }
            } else {
                contentDiv.textContent = `Doctor: ${text}`;
            }
        }
        bubbleDiv.appendChild(contentDiv);

        // Actions
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'msg-actions';
        
        if (type === 'user') {
            const editBtn = document.createElement('button');
            editBtn.className = 'btn-action';
            editBtn.innerText = 'Edit';
            editBtn.onclick = () => editMessage(editBtn);
            actionsDiv.appendChild(editBtn);
        } else {
            const regenBtn = document.createElement('button');
            regenBtn.className = 'btn-action';
            regenBtn.innerText = 'Regenerate';
            regenBtn.onclick = () => regenerateMessage(regenBtn);
            actionsDiv.appendChild(regenBtn);

            // [MODIFIED] New Collapsible Translation
            const transBtn = document.createElement('button');
            transBtn.className = 'btn-action';
            transBtn.innerText = 'Translate';
            transBtn.onclick = () => {
                const allRows = Array.from(document.getElementById('chat-content').children);
                const index = rowDiv.dataset.historyIndex !== undefined
                    ? Number(rowDiv.dataset.historyIndex)
                    : allRows.indexOf(rowDiv);
                const raw = contentDiv.innerText.replace(/^Saya:\s*/, '').trim();
                toggleTranslation(bubbleDiv, raw, index);
            };
            actionsDiv.appendChild(transBtn);

            if (options.readableGenerated) {
                const readBtn = document.createElement('button');
                readBtn.className = 'btn-action read-aloud-inline';
                readBtn.innerText = '🔊 Read';
                readBtn.onclick = () => speakWithElevenLabs(getAiMessageText(rowDiv));
                actionsDiv.appendChild(readBtn);
            }
        }
        bubbleDiv.appendChild(actionsDiv);
        rowDiv.appendChild(bubbleDiv);

        chatContent.appendChild(rowDiv);
        chatContent.scrollTop = chatContent.scrollHeight;

        if (type === 'ai' && options.translation) {
            showStoredTranslation(bubbleDiv, options.translation);
        }
        
        // Update status after message
        if(type === 'ai') fetchStatus();
        
        return rowDiv; 
    }

    function getAiMessageText(rowDiv) {
        const bubble = rowDiv?.querySelector('.message-bubble.ai');
        if (!bubble) return "";
        const clone = bubble.cloneNode(true);
        clone.querySelectorAll('.msg-actions, .read-aloud-inline, .trans-box').forEach(node => node.remove());
        return clone.innerText.replace(/^Saya:\s*/, '').trim();
    }

    function showStoredTranslation(bubbleDiv, translatedText) {
        let transBox = bubbleDiv.querySelector('.trans-box');
        if (!transBox) {
            transBox = document.createElement('div');
            transBox.className = 'trans-box';
            transBox.style.marginTop = '12px';
            transBox.style.padding = '12px';
            transBox.style.background = 'rgba(0, 0, 0, 0.3)';
            transBox.style.borderTop = '1px solid #333';
            transBox.style.borderLeft = '2px solid var(--accent-blue)';
            transBox.style.fontFamily = "'Courier New', monospace";
            transBox.style.fontSize = '0.9rem';
            transBox.style.color = '#ccc';
            bubbleDiv.appendChild(transBox);
        }
        transBox.textContent = translatedText;
    }

    // [ADDED] Collapsible Translation Logic
    async function toggleTranslation(bubbleDiv, text, index) {
        let transBox = bubbleDiv.querySelector('.trans-box');
        
        if (transBox) {
            // Toggle
            transBox.style.display = transBox.style.display === 'none' ? 'block' : 'none';
            return;
        }

        // Create Container
        transBox = document.createElement('div');
        transBox.className = 'trans-box';
        transBox.style.marginTop = '12px';
        transBox.style.padding = '12px';
        transBox.style.background = 'rgba(0, 0, 0, 0.3)';
        transBox.style.borderTop = '1px solid #333';
        transBox.style.borderLeft = '2px solid var(--accent-blue)';
        transBox.style.fontFamily = "'Courier New', monospace";
        transBox.style.fontSize = '0.9rem';
        transBox.style.color = '#ccc';
        transBox.style.display = 'block';
        transBox.style.animation = 'fadeIn 0.3s ease';
        
        // Loading State
        transBox.innerHTML = '<span class="blink">_Analyzing...</span>';
        bubbleDiv.appendChild(transBox);

        try {
            const translationSettings = getSavedOtherApiSettings();
            const trRes = await fetch('/translate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    source: 'en',
                    target: 'zh',
                    session_id: currentSessionId,
                    message_index: index,
                    secret_id: translationSettings.translation_secret_id,
                    secret_key: translationSettings.translation_secret_key,
                    region: translationSettings.translation_region,
                    endpoint: translationSettings.translation_endpoint
                })
            });
            const trData = await trRes.json();
            if (!trRes.ok) {
                throw new Error(trData.detail || `Translation failed (${trRes.status})`);
            }
            if (trData && trData.translatedText) {
                transBox.innerHTML = '';
                const p = document.createElement('p');
                p.style.margin = '0';
                p.style.lineHeight = '1.5';
                p.innerText = trData.translatedText;
                transBox.appendChild(p);
            } else {
                transBox.innerText = "Translation failed.";
            }
        } catch (e) {
            transBox.innerText = "Error: " + e;
        }
    }

    function splitAttachmentContent(rawContent) {
        const fileContextMatch = rawContent.match(/\[FILE_CONTEXT\][\s\S]*?\[\/FILE_CONTEXT\]/);
        if (fileContextMatch) {
            const end = rawContent.indexOf(fileContextMatch[0]) + fileContextMatch[0].length;
            const prefix = rawContent.substring(0, end).trimEnd() + "\n\n";
            return {prefix, userPart: rawContent.substring(end).trim()};
        }

        const hasAttachment = rawContent.match(/\[IMAGE:(.*?)\]/) || rawContent.match(/\[FILE:(.*?)\]/);
        const splitIndex = rawContent.indexOf("\n\n");
        if (hasAttachment && splitIndex !== -1) {
            return {
                prefix: rawContent.substring(0, splitIndex + 2),
                userPart: rawContent.substring(splitIndex + 2)
            };
        }
        if (hasAttachment && rawContent.startsWith(hasAttachment[0])) {
            return {
                prefix: hasAttachment[0] + "\n",
                userPart: rawContent.substring(hasAttachment[0].length).trim()
            };
        }
        return {prefix: "", userPart: rawContent};
    }

    function cloneAttachmentMetadata(attachments) {
        if (!Array.isArray(attachments)) return [];
        return attachments
            .filter(item => item && typeof item === 'object' && ['image', 'file'].includes(String(item.type || '').toLowerCase()))
            .map(item => ({
                type: String(item.type || '').toLowerCase(),
                url: String(item.url || ''),
                data_url: String(item.data_url || ''),
                filename: String(item.filename || ''),
                description: String(item.description || '')
            }));
    }

    function getMessageAttachments(rowDiv) {
        const stored = cloneAttachmentMetadata(rowDiv?._attachments);
        if (stored.length) return stored;
        const rawContent = String(rowDiv?.dataset?.rawContent || '');
        const imageMatch = rawContent.match(/\[IMAGE:(.*?)\]/);
        if (imageMatch?.[1]) {
            return [{type: 'image', url: imageMatch[1], data_url: '', filename: '', description: ''}];
        }
        const fileMatch = rawContent.match(/\[FILE:(.*?)\]/);
        if (fileMatch?.[1]) {
            return [{type: 'file', url: '', data_url: '', filename: fileMatch[1], description: ''}];
        }
        return [];
    }

    async function editMessage(btn) {
        const rowDiv = btn.closest('.message-row');
        
        // Calculate index
        const allRows = Array.from(document.getElementById('chat-content').children);
        const domIndex = allRows.indexOf(rowDiv);
        const historyIndex = rowDiv.dataset.historyIndex !== undefined
            ? Number(rowDiv.dataset.historyIndex)
            : domIndex;
        
        if (domIndex === -1) {
             alert("Error identifying message.");
             return;
        }

        const rawContent = rowDiv.dataset.rawContent || "";
        const {prefix, userPart} = splitAttachmentContent(rawContent);
        const attachments = getMessageAttachments(rowDiv);

        // const bubbleDiv = rowDiv.querySelector('.message-bubble');
        // const contentDiv = bubbleDiv.firstElementChild;
        // const currentText = contentDiv.textContent.replace("Doctor:", "").trim();
        
        const newText = prompt("Edit your message:", userPart.trim());
        
        if (newText !== null && newText !== userPart.trim()) {
            if(!confirm("Resend edited message? This will remove all subsequent conversation history.")) return;

            // 1. Reset backend history to this index
            try {
                const res = await fetch('/api/history/reset_to', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({index: historyIndex})
                });
                const data = await res.json();
                if(data.status !== 'success') throw new Error(data.message);
                
                // 2. Update local state
                currentHistoryLength = data.remaining_count;

                // 3. Remove subsequent DOM elements
                const container = document.getElementById('chat-content');
                while (container.children.length > domIndex) {
                    container.removeChild(container.lastElementChild);
                }

                // 4. Send new message
                const editedContent = prefix + newText;
                sendMessage({
                    userText: editedContent,
                    displayText: editedContent,
                    attachments
                });

            } catch(e) {
                alert("Edit failed: " + e);
            }
        }
    }

    async function regenerateMessage(btn) {
        const rowDiv = btn.closest('.message-row');
        const allRows = Array.from(document.getElementById('chat-content').children);
        const domIndex = allRows.indexOf(rowDiv);

        if (domIndex === -1) return;
        
        // We need to find the preceding User message to re-send.
        // Usually index - 1.
        if (domIndex === 0) {
            alert("Cannot regenerate without context.");
            return;
        }
        
        const userRowDiv = allRows[domIndex - 1];
        if (!userRowDiv.classList.contains('user-row')) {
             alert("Preceding message is not from user. Cannot regenerate.");
             return;
        }
        
        // Retrieve raw content from dataset
        const rawContent = userRowDiv.dataset.rawContent || "";
        const attachments = getMessageAttachments(userRowDiv);
        
        if(!confirm("Regenerate response?")) return;

        try {
            // 1. Reset history to User message index (removing User + AI)
            // Wait, we want to re-send the User message.
            // So we need to remove the User message from backend history too, 
            // because sendMessage() will append it again.
            const resetIndex = userRowDiv.dataset.historyIndex !== undefined
                ? Number(userRowDiv.dataset.historyIndex)
                : domIndex - 1;
            
            const res = await fetch('/api/history/reset_to', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({index: resetIndex})
            });
            const data = await res.json();
            if(data.status !== 'success') throw new Error(data.message);
            
            currentHistoryLength = data.remaining_count;

            // 2. Remove DOM elements (remove all subsequent messages including the ones being regenerated)
            const container = document.getElementById('chat-content');
            while (container.children.length > domIndex - 1) {
                container.removeChild(container.lastElementChild);
            }

            // 3. Re-send User message using raw content
            sendMessage({
                userText: rawContent,
                displayText: rawContent,
                attachments
            });

        } catch(e) {
            alert("Regenerate failed: " + e);
        }
    }

    const physicsParams = {
        type: 1,
        friction: 0.2,
        restitution: 0.05,
        collisionMargin: 0.002,
        damping: 0.95,
        rigidBodyType: 0
    };

    function createAnimationHelper() {
        return new THREE.MMDAnimationHelper({
            afterglow: 2.0,
            physics: true,
            unitStep: FIXED_STEP,
            maxStepNum: 3,
            gravity: new THREE.Vector3(0, -9.8 * 0.8, 0),
            worldScale: 1.0
        });
    }

    function recreateAnimationHelper() {
        if (physicsEnableTimer) {
            clearTimeout(physicsEnableTimer);
            physicsEnableTimer = null;
        }
        if (animationHelper) {
            try {
                animationHelper.enable('physics', false);
            } catch (e) {
                console.warn("Physics disable before helper reset failed", e);
            }
        }
        animationHelper = createAnimationHelper();
        lastPhysicsResetTime = Date.now();
        abnormalPhysicsFrames = 0;
        accumulator = 0;
        clock.getDelta();
    }

    function applyMmdRigidBodyStability(mesh) {
        const rigidBodies = mesh?.geometry?.userData?.MMD?.rigidBodies;
        if (!Array.isArray(rigidBodies)) return;

        rigidBodies.forEach((rb) => {
            rb.friction = physicsParams.friction;
            rb.restitution = physicsParams.restitution;
            rb.damping = physicsParams.damping;
            rb.positionDamping = physicsParams.damping;
            rb.rotationDamping = physicsParams.damping;

            const rbName = String(rb.name || "").toLowerCase();
            const isHair = rbName.includes("hair") || /髪|前髪|後髪|横髪/i.test(rb.name || "");
            const isSkirt = rbName.includes("skirt") || /裙|服/i.test(rb.name || "");

            if (isHair) {
                rb.damping = 0.98;
                rb.rotationDamping = 0.98;
                rb.friction = 0.9;
                rb.mass = Math.max(0.01, Number(rb.mass || 0.01) * 2.0);
            } else if (isSkirt) {
                rb.damping = 0.9;
                rb.rotationDamping = 0.9;
            }
        });
        console.log("Optimized rigid body parameters");
    }

    function attachMeshToAnimationHelper(mesh) {
        if (!animationHelper || !mesh) return;
        applyMmdRigidBodyStability(mesh);
        animationHelper.add(mesh, {
            physics: true,
            ...physicsParams
        });
    }

    function getFootPosition() {
        if (!currentMesh) return null;
        currentMesh.updateMatrixWorld(true);
        
        const footBones = currentMesh.skeleton.bones.filter(bone => 
            bone.name.toLowerCase().includes('leg') || 
            bone.name.toLowerCase().includes('foot') ||
            bone.name.toLowerCase().includes('足') ||
            bone.name.toLowerCase().includes('脚')
        );
        
        if (footBones.length === 0) {
            const box = new THREE.Box3().setFromObject(currentMesh);
            return new THREE.Vector3(
                currentMesh.position.x,
                box.min.y,
                currentMesh.position.z
            );
        }
        
        const footPosition = new THREE.Vector3();
        footBones.forEach(bone => {
            const worldPos = bone.getWorldPosition(new THREE.Vector3());
            footPosition.add(worldPos);
        });
        footPosition.divideScalar(footBones.length);
        
        return footPosition;
    }

    function forceResetPosition(options = {}) {
        if (currentMesh && initialPosition) {
            const hard = Boolean(options.hard);
            if (hard) {
                currentMesh.position.copy(initialPosition.position);
                currentMesh.rotation.copy(initialPosition.rotation);
                currentMesh.updateMatrixWorld(true);
                return;
            }

            const currentFootPos = getFootPosition();
            if (currentFootPos && initialFootPosition) {
                const offset = new THREE.Vector3().subVectors(initialFootPosition, currentFootPos);
                const yOffset = currentMesh.position.y - initialPosition.position.y;
                const maxYOffset = 2.0;
                if (Math.abs(yOffset) > maxYOffset) {
                    const correctionSpeed = 0.1;
                    const targetY = initialPosition.position.y + Math.sign(yOffset) * maxYOffset;
                    currentMesh.position.y += (targetY - currentMesh.position.y) * correctionSpeed;
                }
                currentMesh.position.x += offset.x;
                currentMesh.position.z += offset.z;
                currentMesh.rotation.copy(initialPosition.rotation);
            }
        }
    }

    function isFiniteVector3(vector) {
        return vector && Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z);
    }

    function detectPhysicsInstability(beforeY) {
        if (!currentMesh || !initialPosition) {
            abnormalPhysicsFrames = 0;
            return null;
        }

        const position = currentMesh.position;
        if (!isFiniteVector3(position) || !isFiniteVector3(initialPosition.position)) {
            abnormalPhysicsFrames += 1;
            return "non-finite model position";
        }

        const rootDrift = position.distanceTo(initialPosition.position);
        const frameYDelta = Math.abs(position.y - beforeY);
        let footDrift = 0;
        const footPosition = getFootPosition();
        if (footPosition && initialFootPosition && isFiniteVector3(footPosition) && isFiniteVector3(initialFootPosition)) {
            footDrift = footPosition.distanceTo(initialFootPosition);
        }

        const severe = rootDrift > PHYSICS_MAX_ROOT_DRIFT * 1.6
            || footDrift > PHYSICS_MAX_FOOT_DRIFT * 1.6
            || frameYDelta > PHYSICS_MAX_FRAME_Y_DELTA * 2;
        const abnormal = severe
            || rootDrift > PHYSICS_MAX_ROOT_DRIFT
            || footDrift > PHYSICS_MAX_FOOT_DRIFT
            || frameYDelta > PHYSICS_MAX_FRAME_Y_DELTA;

        if (!abnormal) {
            abnormalPhysicsFrames = 0;
            return null;
        }

        abnormalPhysicsFrames += 1;
        if (severe || abnormalPhysicsFrames >= 2) {
            return `root=${rootDrift.toFixed(2)}, foot=${footDrift.toFixed(2)}, yJump=${frameYDelta.toFixed(2)}`;
        }
        return null;
    }

    function resetMmdPhysics(reason, options = {}) {
        if (!currentMesh || !animationHelper) return false;
        const now = Date.now();
        if (!options.force && now - lastPhysicsPanicTime < PHYSICS_PANIC_COOLDOWN) {
            return false;
        }

        lastPhysicsPanicTime = now;
        abnormalPhysicsFrames = 0;
        console.warn(`Resetting MMD physics: ${reason}`);

        if (physicsEnableTimer) {
            clearTimeout(physicsEnableTimer);
            physicsEnableTimer = null;
        }

        try {
            animationHelper.enable('physics', false);
        } catch (e) {
            console.warn("Physics disable during reset failed", e);
        }
        forceResetPosition({ hard: true });

        const meshSnapshot = currentMesh;
        physicsEnableTimer = setTimeout(() => {
            if (!meshSnapshot || currentMesh !== meshSnapshot) return;
            try {
                recreateAnimationHelper();
                attachMeshToAnimationHelper(meshSnapshot);
                forceResetPosition({ hard: true });
                animationHelper.enable('physics', true);
                lastPhysicsResetTime = Date.now();
            } catch (e) {
                console.warn("Physics rebuild failed", e);
            } finally {
                physicsEnableTimer = null;
            }
        }, options.delayMs ?? 80);

        return true;
    }

    if (typeof Ammo === 'function') {
        Ammo().then(function() {
            console.log('Ammo.js initialized');
            init();
        }).catch(function(err) {
            console.error('Ammo.js initialization failed:', err);
            alert('Failed to load Ammo.js. The model cannot be loaded. Please check your internet connection.');
        });
    } else {
        alert('Failed to load Ammo.js. The model cannot be loaded. Please check your internet connection.');
    }

    function resetModelColdStartState() {
        if (physicsEnableTimer) {
            clearTimeout(physicsEnableTimer);
            physicsEnableTimer = null;
        }
        vmdCache.clear();
        motionFiles = [];
        currentMotionIndex = 0;
        currentMotionState = 'normal_idle';
        lastInteractionTime = Date.now();
        lastPhysicsPanicTime = 0;
        abnormalPhysicsFrames = 0;
        lastPhysicsResetTime = Date.now();
        accumulator = 0;
        clock.getDelta();

        if (camera && controls) {
            controls.target.set(0, 0, 0);
            camera.position.set(0, 10, 30);
            controls.update();
            initialCameraDistance = camera.position.distanceTo(new THREE.Vector3(0, 0, 0));
        }
    }

    function reloadModel() {
        if (!loader) return;
        console.log("Reloading model...");
        resetModelColdStartState();
        loadModel(false, { coldStart: true });
    }

    function loadModel(isFirstLoad, options = {}) {
        const coldStart = Boolean(options.coldStart);
        const loadGeneration = ++modelLoadGeneration;
        if (currentMesh) {
            scene.remove(currentMesh);
            if (animationHelper) {
                try {
                    animationHelper.remove(currentMesh);
                } catch (e) { console.warn("Helper remove error", e); }
            }
            if (mixer) {
                mixer.stopAllAction();
                mixer = null;
            }
            
            if (currentMesh.geometry) currentMesh.geometry.dispose();
            if (currentMesh.material) {
                if (Array.isArray(currentMesh.material)) {
                    currentMesh.material.forEach(m => m.dispose());
                } else {
                    currentMesh.material.dispose();
                }
            }
            currentMesh = null;
        }

        if (!isFirstLoad) {
            recreateAnimationHelper();
        }

        loader.load(
            '/MMD/TDA Default Miku (Xoriu Ver.)/TDA Default Miku (Xoriu Ver).pmx',
            function(mesh) {
                if (loadGeneration !== modelLoadGeneration) {
                    if (mesh.geometry) mesh.geometry.dispose();
                    if (mesh.material) {
                        if (Array.isArray(mesh.material)) {
                            mesh.material.forEach(m => m.dispose());
                        } else {
                            mesh.material.dispose();
                        }
                    }
                    return;
                }

                currentMesh = mesh;
                mesh.scale.set(1.2, 1.2, 1.2);
                mesh.position.y = -11;
                
                initialPosition = {
                    position: mesh.position.clone(),
                    rotation: mesh.rotation.clone()
                };
                
                if (mesh.material) {
                    if (Array.isArray(mesh.material)) {
                        mesh.material.forEach(setupMaterial);
                    } else {
                        setupMaterial(mesh.material);
                    }
                } else if (mesh.materials) {
                    mesh.materials.forEach(setupMaterial);
                }
                
                scene.add(mesh);
                mesh.updateMatrixWorld(true);
                initialFootPosition = getFootPosition();
                mixer = new THREE.AnimationMixer(mesh);
                attachMeshToAnimationHelper(mesh);
                
                if (isFirstLoad) {
                    fetchMotionFiles({ resetIndex: true });
                    animate();
                } else if (coldStart) {
                    forceResetPosition({ hard: true });
                    fetchMotionFiles({ resetIndex: true });
                } else {
                    if (animationHelper) {
                        animationHelper.enable('physics', false);
                    }
                    setTimeout(() => {
                        forceResetPosition();
                        playIntelligentMotion({ forceNormal: true });
                    }, 180);
                }
            },
            function(xhr) {
                console.log(`Model loading: ${(xhr.loaded / xhr.total * 100).toFixed(1)}%`);
            },
            function(err) {
                console.error('MMD Model load error:', err);
                alert("Failed to load model. Please try again.");
            }
        );
    }

    function init() {
        const container = document.querySelector('.canvas-container');
        if (!container) {
            console.error("Canvas container not found!");
            return;
        }
        const width = container.clientWidth;
        const height = container.clientHeight;

        scene = new THREE.Scene();
        camera = new THREE.PerspectiveCamera(45, width / height, 1, 2000);
        camera.position.set(0, 10, 30);

        renderer = new THREE.WebGLRenderer({
            canvas: document.getElementById('mmd-canvas'),
            alpha: true,
            antialias: true,
            logarithmicDepthBuffer: true,
            precision: 'highp'
        });
        renderer.setSize(width, height);
        renderer.setPixelRatio(window.devicePixelRatio);
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        
        // [MODIFIED] Store lights in global variables
        mainAmbientLight = new THREE.AmbientLight(0xaaaaaa, BASE_AMBIENT);
        scene.add(mainAmbientLight);
        
        mainDirLight = new THREE.DirectionalLight(0xffffff, BASE_DIR);
        mainDirLight.position.set(0, 20, 10);
        scene.add(mainDirLight);

        // [MODIFIED] Advanced Rendering: Fog only (Grid removed)
        scene.fog = new THREE.FogExp2(0x000000, 0.035); // Slightly denser fog for darker surroundings

        // Additional overhead light for realism
        mainTopLight = new THREE.DirectionalLight(0xffffff, BASE_TOP);
        mainTopLight.position.set(0, 50, 0);
        scene.add(mainTopLight);

        controls = new THREE.OrbitControls(camera, renderer.domElement);
        loader = new THREE.MMDLoader();

        // [ADDED] Calculate initial camera distance (approx to target 0,0,0 or just length)
        // Since controls.target defaults to 0,0,0 and camera is at 0,10,30
        initialCameraDistance = camera.position.distanceTo(new THREE.Vector3(0, 0, 0));
        
        animationHelper = createAnimationHelper();

        loadModel(true);
        
        // Handle window resize
        window.addEventListener('resize', onWindowResize, false);
    }

    function onWindowResize() {
        const container = document.querySelector('.canvas-container');
        if (!container || !camera || !renderer) return;
        
        const width = container.clientWidth;
        const height = container.clientHeight;
        
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height);
    }

    function setupMaterial(material) {
        material.side = THREE.FrontSide;
        material.transparent = true;
        material.opacity = 1.0;
        material.depthWrite = true;
        material.depthTest = true;
        material.flatShading = false;
        material.skinning = true;
        material.morphTargets = true;
        material.morphNormals = false; // Disable morph normals to prevent lighting issues/discoloration
        material.needsUpdate = true;
    }

    function animate() {
        requestAnimationFrame(animate);
        
        // [ADDED] Dynamic Lighting Adjustment based on Camera Distance
        if (controls && mainAmbientLight && mainDirLight && mainTopLight && initialCameraDistance > 0) {
            const currentDist = camera.position.distanceTo(controls.target);
            // Calculate ratio: if closer (ratio < 1), darker; if farther (ratio > 1), brighter
            // We use a power curve to make it less aggressive or more aggressive if needed.
            // Linear is a good start. 
            // Clamp ratio to avoid extreme values (e.g. 0.2 to 3.0)
            let ratio = currentDist / initialCameraDistance;
            ratio = Math.max(0.2, Math.min(ratio, 3.0));

            // Apply to lights
            mainAmbientLight.intensity = BASE_AMBIENT * ratio;
            mainDirLight.intensity = BASE_DIR * ratio;
            mainTopLight.intensity = BASE_TOP * ratio;
        }

        const delta = clock.getDelta() * motionSpeed;
        const beforeY = currentMesh ? currentMesh.position.y : 0;
        
        if (mixer && currentMesh) {
            mixer.update(delta);
        }
        
        forceResetPosition();
        const prePhysicsInstability = detectPhysicsInstability(beforeY);
        const physicsResetStarted = prePhysicsInstability
            ? resetMmdPhysics(`pre-update instability (${prePhysicsInstability})`)
            : false;
        
        // Periodic Physics Hard Reset to prevent vibration accumulation
        const now = Date.now();
        if (!physicsResetStarted && animationHelper && (now - lastPhysicsResetTime > PHYSICS_RESET_INTERVAL)) {
            // Disable and re-enable physics to reset accumulated forces
            animationHelper.enable('physics', false);
            forceResetPosition({ hard: true });
            const helperSnapshot = animationHelper;
            if (physicsEnableTimer) clearTimeout(physicsEnableTimer);
            physicsEnableTimer = setTimeout(() => {
                if (animationHelper && animationHelper === helperSnapshot) {
                    forceResetPosition({ hard: true });
                    animationHelper.enable('physics', true);
                }
                physicsEnableTimer = null;
            }, 50); // Short delay to let the mesh settle
            lastPhysicsResetTime = now;
        }
        
        if (animationHelper && !physicsResetStarted) {
            try {
                animationHelper.update(delta);
                forceResetPosition();
                const postPhysicsInstability = detectPhysicsInstability(beforeY);
                if (postPhysicsInstability) {
                    resetMmdPhysics(`post-update instability (${postPhysicsInstability})`);
                }
            } catch (error) {
                console.warn("Physics error", error);
                resetMmdPhysics(`physics exception: ${error.message || error}`, { force: true });
            }
        }
        
        controls.update();
        renderer.render(scene, camera);
    }

    async function fetchMotionFiles(options = {}) {
        const expectedGeneration = modelLoadGeneration;
        try {
            const response = await fetch('/get_vmd_files');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            if (expectedGeneration !== modelLoadGeneration) return;
            
            if (data.vmd_files && Array.isArray(data.vmd_files) && data.vmd_files.length > 0) {
                motionFiles = data.vmd_files;
                if (options.resetIndex) {
                    currentMotionIndex = 0;
                    currentMotionState = 'normal_idle';
                }
                if (currentMesh) {
                    loadAndPlayMotion(currentMesh);
                }
            }
        } catch (error) {
            console.error('Fetch motion files failed:', error);
        }
    }

    function loadAndPlayMotion(mesh) {
        if (!motionFiles || !Array.isArray(motionFiles) || motionFiles.length === 0) return;
        const expectedGeneration = modelLoadGeneration;
        
        const vmdFilePath = motionFiles[currentMotionIndex];
        console.log("Loading motion:", vmdFilePath);

        const processAndPlayVmd = (vmd) => {
             if (expectedGeneration !== modelLoadGeneration || mesh !== currentMesh) return;
             if (mixer && vmd) {
                 try {
                     mixer.stopAllAction();
                     
                     if (!currentMesh.geometry.animations) {
                         currentMesh.geometry.animations = [];
                     }
                     
                     if (vmd.tracks && vmd.tracks.length > 0) {
                         if (!vmd.isProcessed) {
                             vmd.tracks = vmd.tracks.filter(track => {
                                 if (track.name.includes('.bones[')) {
                                     if (track.name.includes('position')) {
                                         const values = track.values;
                                         for (let i = 1; i < values.length; i += 3) {
                                             const originalY = values[i];
                                             values[i] = Math.max(Math.min(originalY, 5), -5);
                                         }
                                     }
                                     return true;
                                 }
                                 return !track.name.includes('position') && !track.name.includes('quaternion') && !track.name.includes('.material');
                            });
                             
                             let maxTime = 0;
                             vmd.tracks.forEach(track => {
                                 if (track.times && track.times.length > 0) {
                                     maxTime = Math.max(maxTime, track.times[track.times.length - 1]);
                                 }
                             });
                             vmd.duration = maxTime > 0 ? maxTime : 5.0;
                             vmd.isProcessed = true;
                         }
                         
                         if (animationHelper) {
                             animationHelper.enable('physics', false);
                         }
                         
                         currentMesh.geometry.animations = [vmd];
                         const action = mixer.clipAction(vmd);
                         if (action) {
                             action.setLoop(THREE.LoopRepeat);
                             action.clampWhenFinished = false;
                             action.fadeIn(0.5);
                             forceResetPosition();
                             action.play();
                             
                             const helperSnapshot = animationHelper;
                             if (physicsEnableTimer) clearTimeout(physicsEnableTimer);
                             physicsEnableTimer = setTimeout(() => {
                                 if (animationHelper && animationHelper === helperSnapshot) {
                                    animationHelper.enable('physics', true);
                                 }
                                 physicsEnableTimer = null;
                           }, 100);
                         }
                     }
                 } catch (error) {
                     console.error("Error playing motion:", error);
                 }
             }
        };

        if (vmdCache.has(vmdFilePath)) {
            processAndPlayVmd(vmdCache.get(vmdFilePath));
            return;
        }

        try {
            loader.loadAnimation(
                vmdFilePath,
                mesh,
                function(vmd) {
                    if (expectedGeneration !== modelLoadGeneration || mesh !== currentMesh) return;
                    vmdCache.set(vmdFilePath, vmd);
                    processAndPlayVmd(vmd);
                },
                undefined,
                function(error) {
                    if (expectedGeneration !== modelLoadGeneration || mesh !== currentMesh) return;
                    console.error("VMD load failed:", error);
                    if (isLooping) {
                        currentMotionIndex = (currentMotionIndex + 1) % motionFiles.length;
                        setTimeout(() => loadAndPlayMotion(currentMesh), 1000);
                    }
                }
            );
        } catch (error) {
            console.error("Load animation error:", error);
        }
    }

    async function resetConversation() {
        if (!confirm("Are you sure you want to clear the conversation history?")) return;
        try {
            await fetch('/reset', { method: 'POST' });
            const chatContent = document.getElementById('chat-content');
            if (chatContent) chatContent.innerHTML = '';

            playIntelligentMotion({ context: { emotion: 'normal' } });
            alert("Conversation reset.");
        } catch (e) {
            console.error("Reset failed", e);
            alert("Failed to reset conversation.");
        }
    }

    async function sendMessage(options = {}) {
        const input = document.getElementById('user-input');
        const directUserText = options && Object.prototype.hasOwnProperty.call(options, 'userText');
        let userText = directUserText ? String(options.userText || '').trim() : input.value.trim();
        let displayText = options?.displayText ? String(options.displayText).trim() : '';
        const outgoingAttachments = cloneAttachmentMetadata(options?.attachments);
        
        // Combine attachment if present
        if (pendingAttachment && !directUserText) {
            if (pendingAttachment.type === 'image') {
                const imgTag = `[IMAGE:${pendingAttachment.url}]`;
                outgoingAttachments.push({
                    type: 'image',
                    url: pendingAttachment.url || '',
                    data_url: pendingAttachment.data_url || '',
                    filename: pendingAttachment.filename || '',
                    description: pendingAttachment.description || ''
                });
                const attachmentModel = document.getElementById('model-select')?.value || '';
                const apiImageAttached = attachmentModel === 'api' && pendingAttachment.data_url;
                const sysNote = apiImageAttached
                    ? `(System Note: Doctor sent a picture. The image is attached for vision-capable API models. Content note: ${pendingAttachment.description})`
                    : `(System Note: Doctor sent a picture. Content: ${pendingAttachment.description})`;
                
                // If user text is empty, provide a default
                if (!userText) userText = "Sent a picture.";
                
                // Combine: Image + System Note + User Text
                userText = `${imgTag}\n${sysNote}\n\n${userText}`;
                
            } else if (pendingAttachment.type === 'file') {
                outgoingAttachments.push({
                    type: 'file',
                    filename: pendingAttachment.filename || ''
                });
                const safeContent = String(pendingAttachment.content || '')
                    .replace(/\[FILE_CONTEXT\]/gi, '[FILE CONTEXT]')
                    .replace(/\[\/FILE_CONTEXT\]/gi, '[/FILE CONTEXT]');
                if (!userText) userText = "Please review this file.";
                userText = `[FILE:${pendingAttachment.filename}]\n` +
                    `[FILE_CONTEXT]\n${safeContent}\n[/FILE_CONTEXT]\n\n${userText}`;
            }
            clearAttachment();
        }

        if (!userText) return;
        if (!displayText) displayText = userText;

        const model = document.getElementById('model-select').value;
        const apiKey = document.getElementById('api-key-input').value;
        const apiUrl = document.getElementById('api-url-input').value;
        const apiModelName = document.getElementById('api-model-input').value.trim();
        const multimodalModelName = document.getElementById('api-multimodal-model-input')?.value.trim() || '';
        const strongLogicModelName = document.getElementById('api-strong-logic-model-input')?.value.trim() || '';
        const useAssistantStrongLogic = currentRoleMode === 'assistant' && isAssistantStrongLogicEnabled();
        const maxOutputTokens = getOutputLimitForModel(useAssistantStrongLogic ? 'api' : model);
        const memoryFastModelInput = document.getElementById('api-memory-fast-model-input')?.value.trim() || '';
        const memoryStrongModelInput = document.getElementById('api-memory-strong-model-input')?.value.trim() || '';
        const memoryFastModelName = memoryFastModelInput || apiModelName;
        const memoryStrongModelName = memoryStrongModelInput || memoryFastModelName;
        const chatMode = document.getElementById('chat-mode').value;
        const sayaStyleSnapshot = getSayaStyleSettings();
        saveCurrentApiSettings();

        lastInteractionTime = Date.now();
        
        // addLog(`User: ${userText}`, 'user'); // Removed
        
        // Use appendMessage for consistent UI
        appendMessage('user', displayText, {
            historyIndex: currentHistoryLength,
            attachments: outgoingAttachments
        });
        input.value = '';
        input.style.height = '48px'; // Reset height
        input.style.overflowY = 'hidden'; // Reset overflow
        updateInputSuggestions();

        let aiDiv = null;
        let fullResponseText = "";
        try {
            const requestContext = {};
            if (pendingCheckinContext) {
                requestContext.checkin_context = pendingCheckinContext;
                pendingCheckinContext = "";
            }
            if (currentRoleMode === 'assistant') {
                requestContext.role_mode = 'assistant';
            } else if (currentProjectId) {
                requestContext.project_id = currentProjectId;
            }
            const response = await fetch('/chat_stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    user_input: userText,
                    display_input: displayText,
                    context: requestContext,
                    model: model,
                    api_key: apiKey,
                    api_url: apiUrl,
                    api_model_name: apiModelName,
                    multimodal_model_name: multimodalModelName,
                    max_output_tokens: maxOutputTokens,
                    strong_logic_model_name: strongLogicModelName,
                    use_strong_logic_model: useAssistantStrongLogic,
                    memory_fast_model_name: memoryFastModelName,
                    memory_strong_model_name: memoryStrongModelName,
                    attachments: outgoingAttachments,
                    mode: chatMode,
                    style_settings: sayaStyleSnapshot
                })
            });

            if (!response.ok) throw new Error("Network response was not ok");

            aiDiv = appendMessage('ai', '...', {
                historyIndex: currentHistoryLength + 1,
                readableGenerated: sayaStyleSnapshot.readable === true
            });
            const textSpan = aiDiv.firstElementChild.firstElementChild; // The content div inside bubble
            textSpan.innerHTML = `Saya: `;

            if (!response.body) throw new Error('Streaming response body is unavailable.');
            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";
            let streamNotice = null;

            const handleStreamEvent = (eventType, eventData) => {
                if (!eventType || !eventData) return;
                if (eventType === 'motion_start' || eventType === 'motion_end') {
                    if (eventData.motion_name) {
                        const motionIndex = findMotionByName(eventData.motion_name);
                        if (motionIndex !== -1) {
                            currentMotionIndex = motionIndex;
                            loadAndPlayMotion(currentMesh);
                        }
                    }
                } else if (eventType === 'text') {
                    fullResponseText += String(eventData.text || '');
                    textSpan.innerHTML = `Saya: ` + renderMarkdownSafe(fullResponseText);
                    const chatContent = document.getElementById('chat-content');
                    chatContent.scrollTop = chatContent.scrollHeight;
                } else if (eventType === 'stream_status' && eventData.incomplete) {
                    streamNotice = {message: eventData.message || 'The response reached its output limit.', isError: false};
                } else if (eventType === 'stream_error') {
                    streamNotice = {message: eventData.message || 'The response stream was interrupted.', isError: true};
                }
            };

            const handleSseBlock = (part) => {
                if (!part.trim()) return;
                let eventType = 'message';
                const dataLines = [];
                part.split(/\r?\n/).forEach(line => {
                    if (line.startsWith('event:')) {
                        eventType = line.substring(6).trim();
                    } else if (line.startsWith('data:')) {
                        dataLines.push(line.substring(5).replace(/^ /, ''));
                    }
                });
                if (!dataLines.length) return;
                try {
                    handleStreamEvent(eventType, JSON.parse(dataLines.join('\n')));
                } catch (parseError) {
                    console.error('SSE JSON parse error:', parseError, dataLines.join('\n'));
                }
            };

            const drainSseBuffer = () => {
                const parts = buffer.split(/\r?\n\r?\n/);
                buffer = parts.pop() || '';
                parts.forEach(handleSseBlock);
            };

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                buffer += decoder.decode(value, { stream: true });
                drainSseBuffer();
            }

            // Flush both an incomplete UTF-8 code point and an SSE event that did
            // not happen to end on the same network chunk as its blank separator.
            buffer += decoder.decode();
            const finalParts = buffer.split(/\r?\n\r?\n/);
            buffer = '';
            finalParts.forEach(handleSseBlock);

            if (streamNotice) showStreamNotice(aiDiv, streamNotice.message, streamNotice.isError);
            
            fetchSessionList(); // Update session list preview
            
            // Sync length: Assuming one User msg and one AI msg added
            // BUT, if stream failed, it might be different.
            // Safe way: fetch status from backend?
            // Or just increment by 2
            currentHistoryLength += 2;

            finalizeResponse(aiDiv, fullResponseText);

        } catch (error) {
            console.error('Error:', error);
            if (aiDiv) {
                finalizeResponse(aiDiv, fullResponseText);
                showStreamNotice(aiDiv, `Response stream interrupted: ${error.message || error}`, true);
            } else {
                appendMessage('ai', 'Sorry, I encountered an error.');
            }
        }
    }

    async function finalizeResponse(aiDiv, fullText) {
        if (aiDiv) aiDiv.dataset.rawContent = fullText;
    }

    function showStreamNotice(rowDiv, message, isError = false) {
        const bubble = rowDiv?.querySelector('.message-bubble.ai');
        if (!bubble) return;
        let notice = bubble.querySelector('.stream-notice');
        if (!notice) {
            notice = document.createElement('div');
            const actions = bubble.querySelector('.msg-actions');
            bubble.insertBefore(notice, actions || null);
        }
        notice.className = `stream-notice${isError ? ' error' : ''}`;
        notice.textContent = message;
    }

    // [REMOVED] Old Translation Functions: renderTranslationItem, renderTranslations, performTranslation
    
    async function speakWithElevenLabs(text) {
        try {
            const ttsSettings = getSavedOtherApiSettings();
            const res = await fetch("/tts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text,
                    api_url: ttsSettings.tts_api_url,
                    api_key: ttsSettings.tts_api_key,
                    voice_id: ttsSettings.tts_voice_id,
                    model_id: ttsSettings.tts_model_id
                })
            });

            if (!res.ok) {
                let detail = `TTS failed (${res.status})`;
                try {
                    const data = await res.json();
                    detail = data.detail || detail;
                } catch (_) {}
                throw new Error(detail);
            }
            if (!res.headers.get("Content-Type")?.includes("audio")) throw new Error('TTS API did not return audio.');

            const arrayBuf = await res.arrayBuffer();
            const blob = new Blob([arrayBuf], { type: "audio/mpeg" });
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.play().catch(e => console.warn("Playback failed", e));
        } catch (e) {
            console.warn("TTS playback failed:", e);
            alert(e.message || 'TTS playback failed.');
        }
    }

    const inputField = document.getElementById('user-input');

    function autoResizeInput() {
        inputField.style.height = 'auto';
        inputField.style.height = (inputField.scrollHeight) + 'px';
        if (inputField.scrollHeight > 150) {
            inputField.style.overflowY = 'auto';
        } else {
            inputField.style.overflowY = 'hidden';
        }
    }
    
    inputField.addEventListener('input', autoResizeInput);

    inputField.addEventListener('keydown', function (e) {
        // Prevent event from bubbling to global listeners (like OrbitControls/Model Movement)
        e.stopPropagation();

        if (e.key === 'Enter') {
            if (!e.shiftKey) {
                e.preventDefault();
                sendMessage();
                return;
            }
            // Allow Shift+Enter for newline, then resize
            setTimeout(autoResizeInput, 0);
        }

        // Custom cursor navigation for < and > keys
        if (e.key === '<') {
            e.preventDefault();
            const start = this.selectionStart;
            if (start > 0) {
                this.setSelectionRange(start - 1, start - 1);
            }
        } else if (e.key === '>') {
            e.preventDefault();
            const start = this.selectionStart;
            if (start < this.value.length) {
                this.setSelectionRange(start + 1, start + 1);
            }
        }
    });

    // Start periodic status updates
    setInterval(fetchStatus, 5000);
    fetchStatus(); // Initial fetch
