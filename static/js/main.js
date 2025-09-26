// 邮件智能日程管理系统 - 主要JavaScript文件

// 全局变量
window.mailScheduler = {
    config: {},
    currentUser: null,
    notifications: [],
    intervals: {},
    isChecking: false
};

// 页面加载完成后初始化
$(document).ready(function() {
    initializeApp();
});

/**
 * 初始化应用
 */
function initializeApp() {
    // 设置CSRF令牌
    setupCSRF();
    
    // 初始化工具提示
    initializeTooltips();
    
    // 设置全局AJAX错误处理
    setupAjaxErrorHandling();
    
    // 启动定时任务
    startPeriodicTasks();
    
    // 绑定全局事件
    bindGlobalEvents();
    
    console.log('邮件智能日程管理系统已初始化');
}

/**
 * 设置CSRF令牌
 */
function setupCSRF() {
    // 优先从cookie获取令牌，并使用后端校验的 Header 名称
    function getCookie(name){
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return null;
    }
    const token = getCookie('csrf_token') || $('meta[name=csrf-token]').attr('content');
    $.ajaxSetup({
        beforeSend: function(xhr, settings) {
            if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {
                if (token) xhr.setRequestHeader('X-CSRF-Token', token);
            }
        }
    });
}

/**
 * 初始化工具提示
 */
function initializeTooltips() {
    // 初始化Bootstrap工具提示
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

/**
 * 设置全局AJAX错误处理
 */
function setupAjaxErrorHandling() {
    $(document).ajaxError(function(event, xhr, settings, thrownError) {
        console.error('AJAX请求失败:', {
            url: settings.url,
            status: xhr.status,
            error: thrownError
        });
        
        if (xhr.status === 401) {
            showMessage('会话已过期，请重新登录', 'warning');
        } else if (xhr.status === 403) {
            showMessage('权限不足', 'danger');
        } else if (xhr.status === 500) {
            showMessage('服务器内部错误', 'danger');
        } else if (xhr.status === 0) {
            showMessage('网络连接失败', 'danger');
        }
    });
}

/**
 * 启动定时任务
 */
function startPeriodicTasks() {
    // 每30秒检查一次通知
    window.mailScheduler.intervals.notifications = setInterval(checkNotifications, 30000);
    
    // 移除自动系统状态检查，改为按需检查
    // 只在配置页面或用户主动操作时检查系统状态
    
    // 页面加载时检查一次系统状态（但不包含AI测试）
    checkSystemStatusWithoutAI();
}

/**
 * 绑定全局事件
 */
function bindGlobalEvents() {
    // 不再做全局代理绑定，避免与元素自身的 onclick 重复触发
    // 页面上的按钮已直接使用 onclick="checkEmail()"

    // 诊断：记录“检查新邮件”按钮是否被点击（不调用业务函数，不会重复执行）
    $(document).on('click', 'button[onclick*="checkEmail"]', function() {
        console.log('[checkEmail] button clicked');
        // 若全局函数可用，则直接调用；否则稍后重试一次，尽量保证能触发
        if (typeof window.checkEmail === 'function') {
            try { window.checkEmail(); } catch (e) { console.log('[checkEmail] call error:', e); }
        } else {
            console.log('[checkEmail] window.checkEmail not ready, retry shortly');
            setTimeout(function(){
                if (typeof window.checkEmail === 'function') {
                    try { window.checkEmail(); } catch (e) { console.log('[checkEmail] retry call error:', e); }
                } else {
                    console.log('[checkEmail] still not ready after retry');
                }
            }, 200);
        }
    });

    // 绑定ESC键关闭模态框
    $(document).on('keydown', function(e) {
        if (e.key === 'Escape') {
            $('.modal').modal('hide');
        }
    });
    
    // 绑定表单提交事件
    $(document).on('submit', 'form', function(e) {
        const $form = $(this);
        const $submitBtn = $form.find('button[type="submit"]');
        
        // 防止重复提交
        if ($submitBtn.hasClass('loading')) {
            e.preventDefault();
            return false;
        }
        
        $submitBtn.addClass('loading').prop('disabled', true);
        
        // 3秒后自动恢复按钮状态
        setTimeout(function() {
            $submitBtn.removeClass('loading').prop('disabled', false);
        }, 3000);
    });
}

/**
 * 显示消息提示
 * @param {string} message - 消息内容
 * @param {string} type - 消息类型 (success, danger, warning, info)
 * @param {number} duration - 显示时长（毫秒），0表示不自动消失
 */
function showMessage(message, type = 'info', duration = 5000) {
    // 忽略无效消息，避免出现“undefined”提示
    if (message === undefined || message === null) {
        return;
    }
    if (typeof message !== 'string') {
        try { message = String(message); } catch (_) { return; }
    }
    if (message.trim() === '' || message.trim().toLowerCase() === 'undefined') {
        return;
    }
    const alertId = 'alert-' + Date.now();
    const alertHtml = `
        <div class="alert alert-${type} alert-dismissible fade show" role="alert" id="${alertId}">
            <i class="fas fa-${getIconByType(type)} me-2"></i>
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
    `;
    
    $('#message-area').append(alertHtml);
    
    // 添加动画效果
    $(`#${alertId}`).addClass('fade-in');
    
    // 自动消失
    if (duration > 0) {
        setTimeout(function() {
            $(`#${alertId}`).fadeOut(300, function() {
                $(this).remove();
            });
        }, duration);
    }
}

/**
 * 根据消息类型获取图标
 * @param {string} type - 消息类型
 * @returns {string} 图标类名
 */
function getIconByType(type) {
    const icons = {
        success: 'check-circle',
        danger: 'exclamation-triangle',
        warning: 'exclamation-circle',
        info: 'info-circle'
    };
    return icons[type] || 'info-circle';
}

/**
 * 检查邮件
 */
function checkEmail() {
    if (window.mailScheduler.isChecking) {
        console.log('[checkEmail] blocked: already checking');
        return;
    }
    window.mailScheduler.isChecking = true;
    console.log('[checkEmail] start');
    console.log('[checkEmail] start');
    // 确保进度条容器存在（如果页面没有，动态插入到消息区域）
    let $progress = $('#check-progress');
    if ($progress.length === 0) {
        const progressHtml = `
            <div id="check-progress" class="mb-3">
                <div class="d-flex align-items-center mb-2">
                    <i class="fas fa-spinner fa-spin me-2 text-primary"></i>
                    <strong>正在检查新邮件...</strong>
                    <small class="ms-2 text-muted" id="check-progress-text"></small>
                </div>
                <div class="progress">
                    <div class="progress-bar" id="check-progress-bar" role="progressbar" style="width: 0%" aria-valuemin="0" aria-valuemax="100"></div>
                </div>
            </div>`;
        $('#message-area').prepend(progressHtml);
        $progress = $('#check-progress');
    }
    const $bar = $('#check-progress-bar');
    const $text = $('#check-progress-text');
    $progress.removeClass('d-none');
    $bar.css('width', '0%');
    $text.text('准备中...');

    // 按钮加载状态
    const $checkBtn = $('button[onclick*="checkEmail"]');
    const originalText = $checkBtn.html();
    $checkBtn.html('<i class="fas fa-spinner fa-spin me-1"></i>检查中...').prop('disabled', true);

    // 启动后台任务（显式要求JSON，兼容被代理篡改content-type的情况）
    $.ajax({
        url: '/api/check_email',
        method: 'POST',
        dataType: 'json'
    }).done(function(resp){
        let data = resp;
        if (typeof data === 'string') { try { data = JSON.parse(data); } catch(_) { data = {}; } }
        // 记录最近的task_id
        if (data && data.task_id) { window.mailScheduler.lastTaskId = data.task_id; }

        let startedPolling = false;
        function startPolling(taskId){
            if (!taskId) { return; }
            console.log('[checkEmail] start polling task:', taskId);
            startedPolling = true;
            let timer = setInterval(function(){
                $.ajax({ url: `/api/tasks/${taskId}/progress`, method:'GET', dataType:'json', cache:false })
                .done(function(res){
                    if (res && res.success && res.progress) {
                        const p = res.progress;
                        console.log('[checkEmail] progress:', p);
                        let percent = 0;
                        // 百分比映射：fetching(0-15) -> saving(15-35按saved/new_count) -> analyzing(35-85按analyzed/total) -> syncing(85-95按synced/total) -> done(100)
                        if (p.status === 'fetching' || p.status === 'starting') {
                            percent = 10;
                        }
                        if (p.status === 'saving') {
                            const base = 15;
                            const range = 20;
                            const totalToSave = Math.max(1, p.new_count || 1);
                            const saved = Math.min(totalToSave, p.saved || 0);
                            percent = base + Math.round((saved / totalToSave) * range);
                        }
                        if (p.status === 'analyzing') {
                            const base = 35;
                            const range = 50;
                            const total = Math.max(1, p.total || 1);
                            percent = base + Math.min(range, Math.round((p.analyzed / total) * range));
                        }
                        if (p.status === 'syncing') {
                            const base = 85;
                            const range = 10;
                            const total = Math.max(1, p.total || 1);
                            const synced = Math.min(total, p.synced || 0);
                            percent = base + Math.round((synced / total) * range);
                        }
                        if (p.status === 'done') {
                            percent = 100;
                        }
                        $bar.css('width', percent + '%');
                        $text.text(`新邮件: ${p.new_count}，保存: ${p.saved||0}/${p.new_count}，待分析: ${p.total}，已分析: ${p.analyzed}，失败: ${p.failed}，同步到Notion: ${p.synced||0}/${p.total}`);
                        if (p.status === 'done') {
                            clearInterval(timer);
                            showMessage(p.message || '处理完成', 'success');
                            $checkBtn.html(originalText).prop('disabled', false);
                            setTimeout(() => $progress.addClass('d-none'), 800);
                            if (typeof loadEmails === 'function') { loadEmails(1); }
                            if (typeof loadUpcomingEvents === 'function') { loadUpcomingEvents(); }
                        } else if (p.status === 'error') {
                            clearInterval(timer);
                            showMessage(p.message || '处理失败', 'danger');
                            $checkBtn.html(originalText).prop('disabled', false);
                        }
                    } else {
                        console.log('[checkEmail] invalid progress response:', res);
                    }
                }).fail(function(err){
                    console.log('[checkEmail] progress request failed:', err);
                });
            }, 1000);
        }

        if (data && data.success && data.task_id) {
            console.log('[checkEmail] got task_id:', data.task_id);
            startPolling(data.task_id);
            // 兜底：2秒后若未开始轮询，使用 lastTaskId 再试一次
            setTimeout(function(){
                if (!startedPolling && window.mailScheduler.lastTaskId) {
                    console.log('[checkEmail] fallback start polling with lastTaskId');
                    startPolling(window.mailScheduler.lastTaskId);
                }
            }, 2000);
        } else if (data && data.success && !data.task_id) {
            // 兼容旧返回：不隐藏进度，改为提示并轻量刷新
            $text.text('后台处理中（未返回任务ID），稍后刷新列表');
            $bar.css('width','10%');
            $checkBtn.html(originalText).prop('disabled', false);
            if (typeof loadEmails === 'function') { setTimeout(() => loadEmails(1), 1500); }
        } else if (data && !data.success) {
            showMessage('检查邮件失败: ' + (data.error||'未知错误'), 'danger');
            $checkBtn.html(originalText).prop('disabled', false);
        }
    }).fail(function(err){
        console.log('[checkEmail] request /api/check_email failed:', err);
        showMessage('检查邮件请求失败', 'danger');
        $checkBtn.html(originalText).prop('disabled', false);
        setTimeout(() => $progress.addClass('d-none'), 800);
    }).always(function(){
        window.mailScheduler.isChecking = false;
    });
}

/**
 * 检查通知
 */
function checkNotifications() {
    $.get('/api/notifications', function(data) {
        if (data.success && data.notifications) {
            data.notifications.forEach(function(notification) {
                if (!window.mailScheduler.notifications.includes(notification.id)) {
                    showNotification(notification);
                    window.mailScheduler.notifications.push(notification.id);
                }
            });
        }
    }).fail(function() {
        console.warn('获取通知失败');
    });
}

/**
 * 显示通知
 * @param {Object} notification - 通知对象
 */
function showNotification(notification) {
    // 如果浏览器支持通知API
    if ('Notification' in window && Notification.permission === 'granted') {
        const browserNotification = new Notification(notification.title, {
            body: notification.message,
            icon: '/static/images/icon.png',
            tag: notification.id
        });
        
        browserNotification.onclick = function() {
            window.focus();
            if (notification.url) {
                window.location.href = notification.url;
            }
        };
        
        // 5秒后自动关闭
        setTimeout(function() {
            browserNotification.close();
        }, 5000);
    }
    
    // 同时显示页面内通知
    showMessage(notification.message, notification.type || 'info', 8000);
}

/**
 * 请求通知权限
 */
function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission().then(function(permission) {
            if (permission === 'granted') {
                showMessage('通知权限已开启', 'success');
            } else {
                showMessage('通知权限被拒绝', 'warning');
            }
        });
    }
}

/**
 * 检查系统状态
 */
function checkSystemStatus() {
    // 默认只检查配置状态，不进行实际连接测试
    $.get('/api/system/status', function(data) {
        if (data.success) {
            updateSystemStatus(data.status);
        }
    }).fail(function() {
        console.warn('获取系统状态失败');
    });
}

function checkSystemStatusManual() {
    // 手动测试所有服务连接
    $.get('/api/system/status?manual=true', function(data) {
        if (data.success) {
            updateSystemStatus(data.status);
            showMessage('服务连接测试完成', 'success');
        }
    }).fail(function() {
        showMessage('系统状态检查失败', 'danger');
    });
}

function checkSystemStatusWithoutAI() {
    // 只检查邮件和Notion服务状态，不测试AI服务
    $.get('/api/system/status_basic', function(data) {
        if (data.success) {
            updateSystemStatus(data.status);
        }
    }).fail(function() {
        // 如果基础状态API不存在，则不显示任何状态
        console.warn('获取基础系统状态失败');
    });
}

/**
 * 更新系统状态显示
 * @param {Object} status - 系统状态对象
 */
function updateSystemStatus(status) {
    // 更新邮件服务状态
    if (status.email !== undefined) {
        const $emailStatus = $('#email-status');
        if ($emailStatus.length) {
            $emailStatus.removeClass('bg-success bg-danger bg-warning')
                       .addClass(status.email ? 'bg-success' : 'bg-danger')
                       .text(status.email ? '正常' : '异常');
        }
    }
    
    // 更新AI服务状态
    if (status.ai !== undefined) {
        const $aiStatus = $('#ai-status');
        if ($aiStatus.length) {
            $aiStatus.removeClass('bg-success bg-danger bg-warning')
                    .addClass(status.ai ? 'bg-success' : 'bg-danger')
                    .text(status.ai ? '正常' : '异常');
        }
    }
    
    // 更新Notion服务状态
    if (status.notion !== undefined) {
        const $notionStatus = $('#notion-status');
        if ($notionStatus.length) {
            $notionStatus.removeClass('bg-success bg-danger bg-warning')
                        .addClass(status.notion ? 'bg-success' : 'bg-warning')
                        .text(status.notion ? '正常' : '未配置');
        }
    }
}

/**
 * 格式化日期时间
 * @param {string|Date} datetime - 日期时间
 * @param {string} format - 格式类型
 * @returns {string} 格式化后的字符串
 */
function formatDateTime(datetime, format = 'full') {
    const date = new Date(datetime);
    
    if (isNaN(date.getTime())) {
        return '无效日期';
    }
    
    const now = new Date();
    const diffMs = now - date;
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    
    switch (format) {
        case 'relative':
            if (diffDays === 0) {
                return '今天 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
            } else if (diffDays === 1) {
                return '昨天 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
            } else if (diffDays === -1) {
                return '明天 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
            } else if (diffDays > 0 && diffDays <= 7) {
                return diffDays + '天前';
            } else if (diffDays < 0 && diffDays >= -7) {
                return Math.abs(diffDays) + '天后';
            } else {
                return date.toLocaleDateString('zh-CN');
            }
        
        case 'short':
            return date.toLocaleDateString('zh-CN') + ' ' + 
                   date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        
        case 'date':
            return date.toLocaleDateString('zh-CN');
        
        case 'time':
            return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        
        default:
            return date.toLocaleString('zh-CN');
    }
}

/**
 * 防抖函数
 * @param {Function} func - 要防抖的函数
 * @param {number} wait - 等待时间（毫秒）
 * @returns {Function} 防抖后的函数
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * 节流函数
 * @param {Function} func - 要节流的函数
 * @param {number} limit - 时间限制（毫秒）
 * @returns {Function} 节流后的函数
 */
function throttle(func, limit) {
    let inThrottle;
    return function() {
        const args = arguments;
        const context = this;
        if (!inThrottle) {
            func.apply(context, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

/**
 * 复制文本到剪贴板
 * @param {string} text - 要复制的文本
 * @returns {Promise<boolean>} 是否成功
 */
async function copyToClipboard(text) {
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return true;
        } else {
            // 降级方案
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.left = '-999999px';
            textArea.style.top = '-999999px';
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            
            const result = document.execCommand('copy');
            textArea.remove();
            return result;
        }
    } catch (error) {
        console.error('复制失败:', error);
        return false;
    }
}

/**
 * 下载文件
 * @param {string} url - 文件URL
 * @param {string} filename - 文件名
 */
function downloadFile(url, filename) {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

/**
 * 验证邮箱格式
 * @param {string} email - 邮箱地址
 * @returns {boolean} 是否有效
 */
function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

/**
 * 验证URL格式
 * @param {string} url - URL地址
 * @returns {boolean} 是否有效
 */
function validateUrl(url) {
    try {
        new URL(url);
        return true;
    } catch {
        return false;
    }
}

/**
 * 获取URL参数
 * @param {string} name - 参数名
 * @returns {string|null} 参数值
 */
function getUrlParameter(name) {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get(name);
}

/**
 * 设置URL参数
 * @param {string} name - 参数名
 * @param {string} value - 参数值
 */
function setUrlParameter(name, value) {
    const url = new URL(window.location);
    url.searchParams.set(name, value);
    window.history.pushState({}, '', url);
}

/**
 * 页面卸载时清理资源
 */
$(window).on('beforeunload', function() {
    // 清理定时器
    Object.values(window.mailScheduler.intervals).forEach(function(interval) {
        if (interval) {
            clearInterval(interval);
        }
    });
    
    // 清理事件监听器
    $(document).off();
});

// 导出全局函数
window.showMessage = showMessage;
window.checkEmail = checkEmail;
window.formatDateTime = formatDateTime;
window.debounce = debounce;
window.throttle = throttle;
window.copyToClipboard = copyToClipboard;
window.downloadFile = downloadFile;
window.validateEmail = validateEmail;
window.validateUrl = validateUrl;
window.getUrlParameter = getUrlParameter;
window.setUrlParameter = setUrlParameter;
window.requestNotificationPermission = requestNotificationPermission;
// 批量操作：重新分析失败邮件
window.reanalyzeFailedEmails = function reanalyzeFailedEmails(){
    if (!confirm('确定只重新分析失败/未分析的邮件吗？')) { return; }
    // 复用统一进度条UI
    let $progress = $('#check-progress');
    if ($progress.length === 0) {
        const progressHtml = `
            <div id="check-progress" class="mb-3">
                <div class="d-flex align-items-center mb-2">
                    <i class="fas fa-spinner fa-spin me-2 text-primary"></i>
                    <strong>正在重新分析失败的邮件...</strong>
                    <small class="ms-2 text-muted" id="check-progress-text"></small>
                </div>
                <div class="progress">
                    <div class="progress-bar" id="check-progress-bar" role="progressbar" style="width: 0%" aria-valuemin="0" aria-valuemax="100"></div>
                </div>
            </div>`;
        $('#message-area').prepend(progressHtml);
        $progress = $('#check-progress');
    }
    const $bar = $('#check-progress-bar');
    const $text = $('#check-progress-text');
    $progress.removeClass('d-none');
    $bar.css('width', '0%');
    $text.text('准备中...');

    $.ajax({ url:'/api/emails/reanalyze_failed', method:'POST', dataType:'json' })
    .done(function(resp){
        if (!(resp && resp.success && resp.task_id)) {
            showMessage('创建任务失败', 'danger');
            return;
        }
        const taskId = resp.task_id;
        let timer = setInterval(function(){
            $.getJSON(`/api/tasks/${taskId}/progress`, function(res){
                if (!(res && res.success && res.progress)) return;
                const p = res.progress;
                let percent = 0;
                if (p.status === 'analyzing') {
                    const base = 10, range = 80;
                    const total = Math.max(1, p.total || 1);
                    percent = base + Math.min(range, Math.round((p.analyzed / total) * range));
                } else if (p.status === 'syncing') {
                    const base = 90, range = 9;
                    const total = Math.max(1, p.total || 1);
                    const synced = Math.min(total, p.synced || 0);
                    percent = base + Math.round((synced / total) * range);
                } else if (p.status === 'done') {
                    percent = 100;
                }
                $bar.css('width', percent + '%');
                $text.text(`重分析进度：${p.analyzed}/${p.total}，失败：${p.failed}，同步到Notion：${p.synced||0}/${p.total}`);
                if (p.status === 'done') {
                    clearInterval(timer);
                    showMessage(p.message || '完成', 'success');
                    setTimeout(() => $progress.addClass('d-none'), 800);
                    if (typeof loadEmails === 'function') { loadEmails(1); }
                } else if (p.status === 'error') {
                    clearInterval(timer);
                    showMessage(p.message || '任务失败', 'danger');
                }
            }).fail(function(err){ console.log('progress failed', err); });
        }, 1000);
    }).fail(function(){ showMessage('创建重分析任务失败', 'danger'); });
};

// 页面加载完成后请求通知权限
$(document).ready(function() {
    // 延迟3秒后请求通知权限，避免打扰用户
    setTimeout(function() {
        if ('Notification' in window && Notification.permission === 'default') {
            // 可以在这里添加一个友好的提示，询问用户是否开启通知
            console.log('可以请求通知权限');
        }
    }, 3000);
});