(() => {
  const { createApp, ref, computed, onMounted } = Vue;
  const {
    DataBoard,
    Message,
    Calendar,
    List,
    Setting,
    CollectionTag,
  } = ElementPlusIconsVue;

  async function apiGet(url) {
    const resp = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: {
        "Accept": "application/json",
      },
    });
    if (!resp.ok) {
      throw new Error(`请求失败: ${resp.status}`);
    }
    return await resp.json();
  }

  function getCsrfTokenFromCookie() {
    const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function apiPost(url, payload = {}) {
    const csrf = getCsrfTokenFromCookie();
    const resp = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: JSON.stringify(payload || {}),
    });
    const text = await resp.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {
      data = { success: false, error: text || `请求失败: ${resp.status}` };
    }
    if (!resp.ok && !data.error) {
      data.error = `请求失败: ${resp.status}`;
    }
    return data;
  }

  const App = {
    setup() {
      const loading = ref(false);
      const currentView = ref("dashboard");

      const stats = ref({
        total_emails: 0,
        total_events: 0,
        important_events: 0,
        pending_reminders: 0,
      });
      const emails = ref([]);
      const events = ref([]);
      const tasks = ref([]);
      const streamRunning = ref(false);
      const streamStatusText = ref("");
      const streamLogs = ref([]);
      const streamParams = ref({
        days_back: 1,
        max_count: 20,
        analysis_workers: 3,
      });
      let streamEventSource = null;
      let taskPollTimer = null;
      const statusBasic = ref({
        email: false,
        ai: false,
        notion: false,
      });
      const configDraft = ref({
        email: {
          auto_fetch: true,
          fetch_interval: 1800,
        },
        ai: {
          provider: "",
          model: "",
          api_key: "",
          base_url: "",
        },
      });
      const configSaving = ref(false);
      const emailSearch = ref("");
      const selectedEmailIds = ref([]);
      const bulkRunning = ref(false);
      const emailDialogVisible = ref(false);
      const emailDetail = ref({});
      const reanalyzingEmail = ref(false);
      const tagDraft = ref({
        level3Text: "",
        level4Text: "",
        other2Text: "",
        subscriptionsText: "",
      });
      const tagSaving = ref(false);

      const filteredEmails = computed(() => {
        const kw = String(emailSearch.value || "").trim().toLowerCase();
        if (!kw) return emails.value;
        return emails.value.filter((e) => {
          const subject = String(e.subject || "").toLowerCase();
          const sender = String(e.sender || "").toLowerCase();
          return subject.includes(kw) || sender.includes(kw);
        });
      });

      function eventTagType(level) {
        if (level === "important") return "danger";
        if (level === "subscribed") return "success";
        if (level === "normal") return "primary";
        return "info";
      }

      function eventImportanceText(level) {
        if (level === "important") return "重要";
        if (level === "subscribed") return "订阅";
        if (level === "normal") return "普通";
        return "不重要";
      }

      async function loadDashboard() {
        const data = await apiGet("/api/statistics");
        if (data.success && data.statistics) {
          stats.value = data.statistics;
        }
      }

      async function loadEmails() {
        const data = await apiGet("/api/emails?page=1&per_page=100");
        if (data.success) {
          emails.value = data.emails || [];
        }
      }

      async function loadEvents() {
        const data = await apiGet("/api/events/upcoming?days=30");
        if (data.success) {
          events.value = data.events || [];
        }
      }

      async function loadTasks() {
        const data = await apiGet("/api/tasks/active");
        if (data.success) {
          tasks.value = data.tasks || [];
        }
      }

      async function loadConfig() {
        try {
          const data = await apiGet("/api/config");
          const cfg = data || {};
          const email = cfg.email || {};
          const ai = cfg.ai || {};
          configDraft.value = {
            email: {
              ...email,
              auto_fetch: Boolean(email.auto_fetch),
              fetch_interval: Number(email.fetch_interval || 1800),
            },
            ai: {
              ...ai,
              provider: String(ai.provider || ""),
              model: String(ai.model || ""),
              api_key: String(ai.api_key || ""),
              base_url: String(ai.base_url || ""),
            },
          };
        } catch (err) {
          ElementPlus.ElMessage.error(err.message || "加载配置失败");
        }
      }

      async function saveBetaConfig() {
        configSaving.value = true;
        try {
          const payload = {
            email: {
              ...configDraft.value.email,
              fetch_interval: Number(configDraft.value.email.fetch_interval || 1800),
            },
            ai: {
              ...configDraft.value.ai,
            },
          };
          const data = await apiPost("/api/config", payload);
          if (data.success) {
            ElementPlus.ElMessage.success("配置保存成功");
            await loadConfigStatus();
          } else {
            ElementPlus.ElMessage.error(data.error || "保存失败");
          }
        } finally {
          configSaving.value = false;
        }
      }

      function displayLevel2Tag(tags) {
        if (!tags || !tags.level2) return "";
        if (tags.level2 === "其他" && tags.level2_custom) {
          return `其他[${tags.level2_custom}]`;
        }
        return tags.level2;
      }

      function pushStreamLog(text) {
        const line = String(text || "").trim();
        if (!line) return;
        streamLogs.value.push(line);
        if (streamLogs.value.length > 120) {
          streamLogs.value = streamLogs.value.slice(-120);
        }
      }

      async function checkStreamStatus() {
        try {
          const data = await apiGet("/api/emails/stream-status");
          const st = (data && data.status) || {};
          const running = Boolean(st.running || st.active || st.status === "running");
          streamRunning.value = running;
          if (running) {
            const msg = st.message || "流式任务执行中";
            streamStatusText.value = msg;
          } else if (!streamStatusText.value) {
            streamStatusText.value = "";
          }
        } catch (_) {}
      }

      function stopStreamSource() {
        if (streamEventSource) {
          streamEventSource.close();
          streamEventSource = null;
        }
      }

      async function startStreamFetch() {
        if (streamRunning.value) {
          ElementPlus.ElMessage.warning("流式任务已在运行中");
          return;
        }
        streamLogs.value = [];
        streamStatusText.value = "正在启动流式处理...";
        streamRunning.value = true;
        try {
          stopStreamSource();
          const qs = new URLSearchParams({
            start: "1",
            days_back: String(streamParams.value.days_back || 1),
            analysis_workers: String(streamParams.value.analysis_workers || 3),
          });
          if (streamParams.value.max_count) {
            qs.set("max_count", String(streamParams.value.max_count));
          }
          streamEventSource = new EventSource(`/api/emails/fetch-stream?${qs.toString()}`);
          streamEventSource.onmessage = async (evt) => {
            try {
              const payload = JSON.parse(evt.data || "{}");
              const status = String(payload.status || "");
              const msg = payload.message || payload.detail || status;
              pushStreamLog(msg);
              if (msg) streamStatusText.value = msg;
              if (status === "done" || status === "completed") {
                streamRunning.value = false;
                streamStatusText.value = "流式处理完成";
                stopStreamSource();
                await loadEmails();
                await loadTasks();
              } else if (status === "error") {
                streamRunning.value = false;
                streamStatusText.value = msg || "流式处理失败";
                stopStreamSource();
                ElementPlus.ElMessage.error(streamStatusText.value);
                await loadTasks();
              }
            } catch (err) {
              pushStreamLog(`日志解析失败: ${err.message}`);
            }
          };
          streamEventSource.onerror = () => {
            if (!streamRunning.value) return;
            streamRunning.value = false;
            streamStatusText.value = "流式连接中断";
            stopStreamSource();
          };
        } catch (err) {
          streamRunning.value = false;
          streamStatusText.value = "启动流式处理失败";
          ElementPlus.ElMessage.error(err.message || "启动失败");
        }
      }

      async function stopStreamFetch() {
        const data = await apiPost("/api/emails/stop-stream", {});
        if (data.success) {
          streamRunning.value = false;
          streamStatusText.value = data.message || "已发送终止请求";
          pushStreamLog(streamStatusText.value);
          stopStreamSource();
          await loadTasks();
        } else {
          ElementPlus.ElMessage.error(data.error || "停止流式失败");
        }
      }

      async function openEmailDetail(emailId) {
        try {
          const data = await apiGet(`/api/email/${emailId}`);
          emailDetail.value = data || {};
          emailDialogVisible.value = true;
        } catch (err) {
          ElementPlus.ElMessage.error(err.message || "获取邮件详情失败");
        }
      }

      function onEmailSelectionChange(rows) {
        selectedEmailIds.value = (rows || [])
          .map((r) => Number(r && r.id))
          .filter((x) => Number.isFinite(x) && x > 0);
      }

      async function reanalyzeById(emailId) {
        const data = await apiPost(`/api/email/${emailId}/reanalyze`, {});
        if (data.success) {
          ElementPlus.ElMessage.success(`邮件 ${emailId} 已重分析`);
          await loadEmails();
          await loadEvents();
        } else {
          ElementPlus.ElMessage.error(data.error || `邮件 ${emailId} 重分析失败`);
        }
      }

      async function subscribeTag(level, value) {
        const val = String(value || "").trim();
        if (!val) return;
        const data = await apiPost("/api/tags/subscribe", {
          level: Number(level),
          value: val,
          apply_now: true,
        });
        if (data.success) {
          ElementPlus.ElMessage.success(data.already_exists ? "该标签已订阅" : "订阅成功");
        } else {
          ElementPlus.ElMessage.error(data.error || "订阅失败");
        }
      }

      async function bulkReanalyzeSelected() {
        if (!selectedEmailIds.value.length) return;
        bulkRunning.value = true;
        let ok = 0;
        let fail = 0;
        for (const eid of selectedEmailIds.value) {
          try {
            const data = await apiPost(`/api/email/${eid}/reanalyze`, {});
            if (data.success) ok += 1;
            else fail += 1;
          } catch (_) {
            fail += 1;
          }
        }
        bulkRunning.value = false;
        ElementPlus.ElMessage({
          type: fail > 0 ? "warning" : "success",
          message: `批量重分析完成：成功 ${ok}，失败 ${fail}`,
        });
        await loadEmails();
        await loadEvents();
      }

      async function bulkSubscribeByLevel(level) {
        if (!selectedEmailIds.value.length) return;
        const list = emails.value.filter((e) => selectedEmailIds.value.includes(Number(e.id)));
        const valSet = new Set();
        for (const e of list) {
          const t = e && e.tags ? e.tags : {};
          if (Number(level) === 3 && t.level3) valSet.add(String(t.level3).trim());
          if (Number(level) === 4 && t.level4) valSet.add(String(t.level4).trim());
          if (Number(level) === 2 && t.level2) {
            valSet.add(displayLevel2Tag(t));
          }
        }
        const values = Array.from(valSet).filter(Boolean);
        if (!values.length) {
          ElementPlus.ElMessage.warning("所选邮件没有可订阅标签");
          return;
        }
        let ok = 0;
        let fail = 0;
        for (const v of values) {
          const data = await apiPost("/api/tags/subscribe", {
            level: Number(level),
            value: v,
            apply_now: false,
          });
          if (data.success) ok += 1;
          else fail += 1;
        }
        ElementPlus.ElMessage({
          type: fail > 0 ? "warning" : "success",
          message: `批量订阅完成：成功 ${ok}，失败 ${fail}`,
        });
        await loadTagSettings();
      }

      async function reanalyzeEmail() {
        const emailId = emailDetail.value && emailDetail.value.id;
        if (!emailId) return;
        reanalyzingEmail.value = true;
        try {
          const data = await apiPost(`/api/email/${emailId}/reanalyze`, {});
          if (data.success) {
            ElementPlus.ElMessage.success("邮件已重新分析");
            await openEmailDetail(emailId);
            await loadEmails();
            await loadEvents();
          } else {
            ElementPlus.ElMessage.error(data.error || "重新分析失败");
          }
        } finally {
          reanalyzingEmail.value = false;
        }
      }

      async function updateEventImportance(row, level) {
        const eventId = row && row.id;
        if (!eventId) return;
        const data = await apiPost(`/api/events/${eventId}`, {
          importance_level: level,
        });
        if (data.success) {
          row.importance_level = level;
          ElementPlus.ElMessage.success("事件重要性已更新");
        } else {
          ElementPlus.ElMessage.error(data.error || "更新失败");
          await loadEvents();
        }
      }

      async function deleteEvent(row) {
        const eventId = row && row.id;
        if (!eventId) return;
        try {
          await ElementPlus.ElMessageBox.confirm("确认删除该事件？", "删除确认", {
            type: "warning",
            confirmButtonText: "删除",
            cancelButtonText: "取消",
          });
        } catch (_) {
          return;
        }
        const csrf = getCsrfTokenFromCookie();
        const resp = await fetch(`/api/events/${eventId}`, {
          method: "DELETE",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            ...(csrf ? { "X-CSRF-Token": csrf } : {}),
          },
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.success) {
          ElementPlus.ElMessage.success("事件已删除");
          await loadEvents();
          await loadDashboard();
        } else {
          ElementPlus.ElMessage.error(data.error || "删除失败");
        }
      }

      function canStopTask(task) {
        const s = String((task && task.status) || "").toLowerCase();
        return s && !["done", "completed", "error", "cancelled", "canceled"].includes(s);
      }

      async function stopTask(task) {
        const taskId = task && task.task_id;
        if (!taskId) return;
        const data = await apiPost(`/api/tasks/${encodeURIComponent(taskId)}/stop`, {});
        if (data.success) {
          ElementPlus.ElMessage.success(data.message || "已发送终止请求");
          await loadTasks();
        } else {
          ElementPlus.ElMessage.error(data.error || "终止任务失败");
        }
      }

      async function loadConfigStatus() {
        const data = await apiGet("/api/system/status_basic");
        if (data.success) {
          statusBasic.value = data.status || statusBasic.value;
        }
      }

      function splitLinesToList(text) {
        return String(text || "")
          .split(/\r?\n/)
          .map((x) => x.trim())
          .filter(Boolean);
      }

      function subsTextToList(text) {
        const out = [];
        for (const line of splitLinesToList(text)) {
          const m = line.match(/^([234])\s*:\s*(.+)$/);
          if (m) out.push({ level: Number(m[1]), value: String(m[2] || "").trim() });
          else out.push({ level: 3, value: line });
        }
        return out;
      }

      function subsListToText(subs) {
        return (subs || [])
          .map((s) => {
            if (s && typeof s === "object") return `${Number(s.level || 3)}:${String(s.value || "").trim()}`;
            return `3:${String(s || "").trim()}`;
          })
          .filter((x) => x !== "3:")
          .join("\n");
      }

      async function loadTagSettings() {
        const data = await apiGet("/api/tags");
        if (!data.success) {
          ElementPlus.ElMessage.error(data.error || "读取标签配置失败");
          return;
        }
        const lib = data.library || {};
        tagDraft.value.level3Text = (lib.level3 || []).join("\n");
        tagDraft.value.level4Text = (lib.level4 || []).join("\n");
        tagDraft.value.other2Text = (lib.other_level2 || []).join("\n");
        tagDraft.value.subscriptionsText = subsListToText(data.subscriptions || []);
      }

      async function saveTagSettings() {
        tagSaving.value = true;
        try {
          const payload = {
            library: {
              level3: splitLinesToList(tagDraft.value.level3Text),
              level4: splitLinesToList(tagDraft.value.level4Text),
              other_level2: splitLinesToList(tagDraft.value.other2Text),
            },
            subscriptions: subsTextToList(tagDraft.value.subscriptionsText),
          };
          const data = await apiPost("/api/tags", payload);
          if (data.success) {
            ElementPlus.ElMessage.success("标签配置已保存");
            await loadTagSettings();
          } else {
            ElementPlus.ElMessage.error(data.error || "保存标签配置失败");
          }
        } finally {
          tagSaving.value = false;
        }
      }

      async function reapplySubscriptions() {
        const data = await apiPost("/api/tags/reapply-subscriptions", {});
        if (data.success) {
          ElementPlus.ElMessage.success(data.message || "已应用到历史事件");
          await loadEvents();
        } else {
          ElementPlus.ElMessage.error(data.error || "应用失败");
        }
      }

      async function refreshCurrent() {
        loading.value = true;
        try {
          if (currentView.value === "dashboard") {
            await loadDashboard();
          } else if (currentView.value === "emails") {
            await loadEmails();
          } else if (currentView.value === "events") {
            await loadEvents();
          } else if (currentView.value === "tasks") {
            await loadTasks();
          } else if (currentView.value === "config") {
            await loadConfigStatus();
            await loadConfig();
          } else if (currentView.value === "tags") {
            await loadTagSettings();
          }
        } catch (err) {
          ElementPlus.ElMessage.error(err.message || "加载失败");
        } finally {
          loading.value = false;
        }
      }

      async function handleMenuSelect(key) {
        currentView.value = key;
        await refreshCurrent();
      }

      function goto(url) {
        window.location.href = url;
      }

      function goClassic() {
        window.location.href = "/";
      }

      onMounted(async () => {
        await checkStreamStatus();
        await refreshCurrent();
        taskPollTimer = setInterval(async () => {
          if (currentView.value === "tasks") {
            await loadTasks();
          }
          if (currentView.value === "emails") {
            await checkStreamStatus();
          }
        }, 5000);
      });

      return {
        loading,
        currentView,
        stats,
        emails,
        events,
        tasks,
        streamRunning,
        streamStatusText,
        streamLogs,
        streamParams,
        statusBasic,
        configDraft,
        configSaving,
        emailSearch,
        selectedEmailIds,
        bulkRunning,
        emailDialogVisible,
        emailDetail,
        reanalyzingEmail,
        tagDraft,
        tagSaving,
        filteredEmails,
        handleMenuSelect,
        refreshCurrent,
        eventTagType,
        eventImportanceText,
        displayLevel2Tag,
        startStreamFetch,
        stopStreamFetch,
        onEmailSelectionChange,
        openEmailDetail,
        reanalyzeById,
        subscribeTag,
        bulkReanalyzeSelected,
        bulkSubscribeByLevel,
        reanalyzeEmail,
        updateEventImportance,
        deleteEvent,
        canStopTask,
        stopTask,
        loadConfig,
        saveBetaConfig,
        loadTagSettings,
        saveTagSettings,
        reapplySubscriptions,
        goto,
        goClassic,
      };
    },
  };

  const app = createApp(App);
  app.config.compilerOptions.delimiters = ["[[", "]]"];
  app.use(ElementPlus);
  app.component("DataBoard", DataBoard);
  app.component("Message", Message);
  app.component("Calendar", Calendar);
  app.component("List", List);
  app.component("Setting", Setting);
  app.component("CollectionTag", CollectionTag);
  app.mount("#beta-app");
})();
