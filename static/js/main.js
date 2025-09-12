// 邮件智能日程管理系统 - 主要JavaScript文件

// 全局变量
window.mailScheduler = {
    config: {},
    currentUser: null,
    notifications: [],
    intervals: {}
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
    // 从meta标签获取CSRF令牌
    const token = $('meta[name=csrf-token]').attr('content');
    if (token) {
        $.ajaxSetup({
            beforeSend: function(xhr, settings) {
                if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {
                    xhr.setRequestHeader('X-CSRFToken', token);
                }
            }
        });
    }
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
    // 绑定检查邮件按钮
    $(document).on('click', '[onclick*="checkEmail"]', function(e) {
        e.preventDefault();
        checkEmail();
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
    showMessage('正在检查新邮件...', 'info');
    
    // 显示加载状态
    const $checkBtn = $('button[onclick*="checkEmail"]');
    const originalText = $checkBtn.html();
    $checkBtn.html('<i class="fas fa-spinner fa-spin me-1"></i>检查中...').prop('disabled', true);
    
    $.post('/api/check_email', function(data) {
        if (data.success) {
            showMessage(data.message, 'success');
            
            // 刷新相关页面数据
            if (typeof loadRecentEmails === 'function') {
                loadRecentEmails();
            }
            if (typeof loadUpcomingEvents === 'function') {
                loadUpcomingEvents();
            }
        } else {
            showMessage('检查邮件失败: ' + data.error, 'danger');
        }
    }).fail(function() {
        showMessage('检查邮件请求失败', 'danger');
    }).always(function() {
        // 恢复按钮状态
        $checkBtn.html(originalText).prop('disabled', false);
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