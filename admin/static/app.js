/**
 * QQ Bot 配置管理器 - 前端逻辑
 */

// ========== 全局变量 ==========
let currentSessionId = null;
let templates = [];
let authToken = '';

// ========== 鉴权 ==========
function getAuthToken() {
    // 优先从 URL 参数读取
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get('token');
    if (urlToken) {
        localStorage.setItem('admin_token', urlToken);
        // 清除 URL 中的 token 参数
        window.history.replaceState({}, '', window.location.pathname);
        return urlToken;
    }
    // 其次从 localStorage 读取
    return localStorage.getItem('admin_token') || '';
}

function promptForToken() {
    const token = prompt('请输入管理员 Token:');
    if (token) {
        localStorage.setItem('admin_token', token);
        authToken = token;
        // 重新加载数据
        loadPrompt();
        loadTemplates();
        loadSessions();
    }
}

function authFetch(url, options = {}) {
    if (!options.headers) {
        options.headers = {};
    }
    if (authToken) {
        options.headers['Authorization'] = `Bearer ${authToken}`;
    }
    return fetch(url, options);
}

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
    authToken = getAuthToken();
    loadProviders();
    loadPrompt();
    loadTemplates();
    loadSessions();

    // 监听输入更新字符计数
    document.getElementById('promptEditor').addEventListener('input', updateCharCount);
});

// ========== Toast 通知 ==========
function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
    <span>${type === 'success' ? '✅' : '❌'}</span>
    <span>${message}</span>
  `;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100px)';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ========== 字符计数 ==========
function updateCharCount() {
    const text = document.getElementById('promptEditor').value;
    document.getElementById('charCount').textContent = `${text.length} 字符`;
}

// ========== 通用响应处理 ==========
async function handleResponse(res) {
    if (res.status === 401) {
        showToast('Token 无效或未设置，请重新输入', 'error');
        promptForToken();
        return null;
    }
    return res;
}

// ========== 模型切换 ==========
async function loadProviders() {
    try {
        const res = await authFetch('/api/providers');
        const checked = await handleResponse(res);
        if (!checked) return;
        const data = await checked.json();

        const container = document.getElementById('providerSelector');
        const currentLabel = document.getElementById('currentModel');

        container.innerHTML = data.providers.map(p => `
      <button class="template-btn ${p.id === data.active ? 'active' : ''}"
              data-id="${p.id}" onclick="switchProvider('${p.id}')">
        ${p.name}
      </button>
    `).join('');

        const active = data.providers.find(p => p.id === data.active);
        if (active) {
            currentLabel.textContent = `当前: ${active.name} (${active.model})`;
        }
    } catch (e) {
        console.error('加载模型列表失败', e);
    }
}

async function switchProvider(providerId) {
    try {
        const res = await authFetch('/api/providers/active', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider_id: providerId })
        });
        const checked = await handleResponse(res);
        if (!checked) return;
        const data = await checked.json();
        if (data.success) {
            showToast(data.message);
            loadProviders();
        }
    } catch (e) {
        showToast('切换模型失败', 'error');
    }
}

// ========== 提示词相关 ==========
async function loadPrompt() {
    try {
        const res = await authFetch('/api/prompt');
        const checked = await handleResponse(res);
        if (!checked) return;
        const data = await checked.json();
        document.getElementById('promptEditor').value = data.prompt;
        updateCharCount();
    } catch (e) {
        showToast('加载提示词失败', 'error');
    }
}

async function savePrompt() {
    const prompt = document.getElementById('promptEditor').value;
    try {
        const res = await authFetch('/api/prompt', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt })
        });
        const checked = await handleResponse(res);
        if (!checked) return;
        const data = await checked.json();
        if (data.success) {
            showToast('提示词已保存，下次对话生效！');
            // 取消所有模板选中状态
            document.querySelectorAll('.template-btn').forEach(btn => btn.classList.remove('active'));
        }
    } catch (e) {
        showToast('保存失败', 'error');
    }
}

async function loadTemplates() {
    try {
        const res = await authFetch('/api/prompt/templates');
        const checked = await handleResponse(res);
        if (!checked) return;
        const data = await checked.json();
        templates = data.templates;

        const container = document.getElementById('templateSelector');
        container.innerHTML = templates.map(t => `
      <button class="template-btn" data-id="${t.id}" onclick="applyTemplate('${t.id}')">
        ${t.name}
      </button>
    `).join('');
    } catch (e) {
        console.error('加载模板失败', e);
    }
}

async function applyTemplate(templateId) {
    try {
        const res = await authFetch('/api/prompt/template', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ template_id: templateId })
        });
        const checked = await handleResponse(res);
        if (!checked) return;
        const data = await checked.json();
        if (data.success) {
            document.getElementById('promptEditor').value = data.prompt;
            updateCharCount();
            showToast('模板已应用！');

            // 更新选中状态
            document.querySelectorAll('.template-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.id === templateId);
            });
        }
    } catch (e) {
        showToast('应用模板失败', 'error');
    }
}

// ========== 历史记录相关 ==========
async function loadSessions() {
    try {
        const res = await authFetch('/api/history');
        const checked = await handleResponse(res);
        if (!checked) return;
        const data = await checked.json();

        const container = document.getElementById('sessionList');
        if (data.sessions.length === 0) {
            container.innerHTML = `
        <div class="empty-state">
          <div class="icon">📭</div>
          <p>暂无聊天记录</p>
        </div>
      `;
            return;
        }

        container.innerHTML = data.sessions.map(s => `
      <div class="session-item ${currentSessionId === s.id ? 'active' : ''}"
           onclick="selectSession('${s.id}')">
        <div class="session-info">
          <div class="session-icon ${s.type}">${s.type === 'group' ? '👥' : '👤'}</div>
          <div>
            <div class="session-name">${s.id}</div>
            <div class="session-count">${s.count} 条消息</div>
          </div>
        </div>
        <div class="session-actions">
          <button class="btn btn-danger btn-sm" onclick="event.stopPropagation(); clearSession('${s.id}')">
            删除
          </button>
        </div>
      </div>
    `).join('');
    } catch (e) {
        showToast('加载会话列表失败', 'error');
    }
}

async function selectSession(sessionId) {
    currentSessionId = sessionId;
    document.getElementById('currentSession').textContent = sessionId;

    // 更新选中状态
    document.querySelectorAll('.session-item').forEach(item => {
        item.classList.toggle('active', item.onclick.toString().includes(sessionId));
    });

    try {
        const res = await authFetch(`/api/history/${sessionId}`);
        const checked = await handleResponse(res);
        if (!checked) return;
        const data = await checked.json();

        const container = document.getElementById('messageList');
        if (data.messages.length === 0) {
            container.innerHTML = `
        <div class="empty-state">
          <div class="icon">💭</div>
          <p>该会话暂无消息</p>
        </div>
      `;
            return;
        }

        container.innerHTML = data.messages.map((msg, index) => `
      <div class="message-item ${msg.role} fade-in">
        <div class="message-avatar">${msg.role === 'user' ? '👤' : '🤖'}</div>
        <div class="message-content">
          <div class="message-role">${msg.role === 'user' ? '用户' : '助手'}</div>
          <div class="message-text">${escapeHtml(msg.content)}</div>
        </div>
        <div class="message-actions">
          <button class="btn btn-danger btn-sm" onclick="deleteMessage('${sessionId}', ${index})">
            🗑️
          </button>
        </div>
      </div>
    `).join('');

        // 滚动到底部
        container.scrollTop = container.scrollHeight;
    } catch (e) {
        showToast('加载消息失败', 'error');
    }
}

async function clearSession(sessionId) {
    if (!confirm(`确定要删除会话 ${sessionId} 的所有消息吗？`)) return;

    try {
        await authFetch(`/api/history/${sessionId}`, { method: 'DELETE' });
        showToast('会话已删除');

        if (currentSessionId === sessionId) {
            currentSessionId = null;
            document.getElementById('currentSession').textContent = '';
            document.getElementById('messageList').innerHTML = `
        <div class="empty-state">
          <div class="icon">💭</div>
          <p>选择一个会话查看消息</p>
        </div>
      `;
        }

        loadSessions();
    } catch (e) {
        showToast('删除失败', 'error');
    }
}

async function deleteMessage(sessionId, index) {
    try {
        await authFetch(`/api/history/${sessionId}/${index}`, { method: 'DELETE' });
        showToast('消息已删除');
        selectSession(sessionId);
        loadSessions();
    } catch (e) {
        showToast('删除失败', 'error');
    }
}

async function clearAllHistory() {
    if (!confirm('确定要清空所有聊天历史吗？此操作不可撤销！')) return;

    try {
        await authFetch('/api/history', { method: 'DELETE' });
        showToast('所有历史记录已清空');
        currentSessionId = null;
        document.getElementById('currentSession').textContent = '';
        document.getElementById('messageList').innerHTML = `
      <div class="empty-state">
        <div class="icon">💭</div>
        <p>选择一个会话查看消息</p>
      </div>
    `;
        loadSessions();
    } catch (e) {
        showToast('清空失败', 'error');
    }
}

// ========== 工具函数 ==========
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
