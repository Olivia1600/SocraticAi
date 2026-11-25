let currentUser = null;
let authMode = "login";
let activeBookId = "";
let activeSessionId = null;
let activePracticeId = null;

const $ = (id) => document.getElementById(id);

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...options });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.error || "Request failed");
  }
  return data;
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function showAuth() {
  $("authScreen").classList.remove("hidden");
  $("appShell").classList.add("hidden");
  $("authName").focus();
}

function showApp(user) {
  currentUser = user;
  $("authScreen").classList.add("hidden");
  $("appShell").classList.remove("hidden");
  $("sidebarUser").textContent = `${user.name} · ${user.role}`;
  $("learnHeaderClass").textContent = user.class_name || "Physics 101";
  $("teacherClassTitle").textContent = user.class_name || "Physics 101";
  $("teacherNav").classList.toggle("hidden", user.role !== "teacher");
  loadBooks().catch((err) => toast(err.message));
}

$("authSwitch").addEventListener("click", () => {
  authMode = authMode === "login" ? "register" : "login";
  const reg = authMode === "register";
  $("registerFields").classList.toggle("hidden", !reg);
  $("authEyebrow").textContent = reg ? "Create account" : "Welcome back";
  $("authTitle").textContent = reg ? "Register" : "Sign in";
  $("authSubmit").textContent = reg ? "Create account" : "Sign in";
  $("authSwitch").textContent = reg
    ? "Already have an account? Sign in"
    : "New here? Create an account";
  $("authPassword").autocomplete = reg ? "new-password" : "current-password";
  $("authError").textContent = "";
});

$("authForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("authError").textContent = "";
  $("authSubmit").disabled = true;
  try {
    const payload = {
      name: $("authName").value.trim(),
      password: $("authPassword").value,
    };
    if (authMode === "register") {
      payload.role = $("authRole").value;
      payload.class_name = $("authClass").value.trim() || "Physics 101";
    }
    const data = await api(`/api/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    showApp(data.user);
  } catch (err) {
    $("authError").textContent = err.message;
  } finally {
    $("authSubmit").disabled = false;
  }
});

$("logoutButton").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  currentUser = null;
  activeBookId = "";
  activeSessionId = null;
  showAuth();
});

async function loadBooks() {
  const books = await api("/api/textbooks");
  $("bookSelect").innerHTML = books.length
    ? books.map((b) => `<option value="${b.book_id}">${escapeHtml(b.title)}</option>`).join("")
    : `<option value="">No textbook uploaded</option>`;
  if (books.length) {
    activeBookId = books[0].book_id;
    $("bookSelect").value = activeBookId;
    await selectBook(activeBookId);
  }
}

async function selectBook(bookId) {
  activeBookId = bookId;
  activeSessionId = null;
  if (!bookId) {
    $("pdfFrame").removeAttribute("src");
    $("pdfMeta").textContent = "Select or upload a PDF";
    return;
  }
  const detail = await api(`/api/textbooks/${bookId}`);
  $("pdfFrame").src = detail.pdf_url;
  $("pdfMeta").textContent = `${detail.page_count} pages · ${detail.concepts.length} concepts`;
  $("conceptSelect").innerHTML =
    `<option value="">Auto-detect concept</option>` +
    detail.concepts
      .map((c) => `<option value="${c.concept_id}">Ch ${c.chapter_number}: ${escapeHtml(c.title)}</option>`)
      .join("");
  await restoreLatestSession(bookId);
}

async function restoreLatestSession(bookId) {
  const sessions = await api(`/api/tutor/sessions?book_id=${encodeURIComponent(bookId)}`);
  const box = $("chatMessages");
  if (!sessions.length) {
    box.innerHTML = `
      <div class="message tutor">
        <div class="avatar">S</div>
        <div class="bubble">Textbook loaded! Ask a question or pick a concept to begin.</div>
      </div>`;
    return;
  }
  const latest = sessions[0];
  activeSessionId = latest.session_id;
  $("scaffoldingPill").textContent = `Scaffolding: ${latest.scaffolding_level}/4`;
  if (latest.concept_title) {
    $("activeConceptPill").textContent = `Concept: ${latest.concept_title}`;
  }
  box.innerHTML = "";
  for (const m of latest.messages) {
    appendMessage(m.role, m.content);
    if (m.citations && m.citations.length) {
      renderCitations(m.citations);
    }
  }
}

function appendMessage(role, text) {
  const box = $("chatMessages");
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.innerHTML = `<div class="avatar">${role === "tutor" ? "S" : "Y"}</div><div class="bubble"></div>`;
  div.querySelector(".bubble").textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function renderCitations(citations) {
  const box = $("citationList");
  if (!citations || !citations.length) {
    box.innerHTML = `<p class="muted">No citations retrieved yet.</p>`;
    return;
  }
  box.innerHTML = citations
    .map(
      (c) => `
      <div class="citation-item">
        <strong>${escapeHtml(c.section)} (p. ${c.page_number}) · ${escapeHtml(c.content_type)}</strong>
        <div>${escapeHtml(c.excerpt)}</div>
      </div>`
    )
    .join("");
}

$("bookSelect").addEventListener("change", (e) => {
  selectBook(e.target.value).catch((err) => toast(err.message));
});

$("pdfInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await api("/api/textbooks/upload", { method: "POST", body: form });
    await loadBooks();
    activeBookId = res.textbook.book_id;
    $("bookSelect").value = activeBookId;
    await selectBook(activeBookId);
    toast(`Indexed ${res.textbook.concepts_count} concepts from ${res.textbook.filename}`);
  } catch (err) {
    toast(err.message);
  }
  e.target.value = "";
});

$("chatForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = $("chatInput").value.trim();
  if (!message) return;
  appendMessage("student", message);
  $("chatInput").value = "";
  try {
    const data = await api("/api/tutor/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        book_id: activeBookId || null,
        concept_id: $("conceptSelect").value || null,
        session_id: activeSessionId,
      }),
    });
    activeSessionId = data.session_id;
    appendMessage("tutor", data.reply);
    renderCitations(data.citations);
    $("activeConceptPill").textContent = `Concept: ${data.concept.title}`;
    $("scaffoldingPill").textContent = `Scaffolding: ${data.scaffolding_level}/4`;
    $("eloPill").textContent = `Elo: ${data.elo_score}`;
  } catch (err) {
    appendMessage("tutor", err.message);
  }
});

async function openPractice(conceptId = null, existingItem = null) {
  try {
    let item = existingItem;
    if (!item) {
      item = await api("/api/practice/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          book_id: activeBookId || null,
          concept_id: conceptId || $("conceptSelect").value || null,
        }),
      });
    }
    activePracticeId = item.question_id;
    $("practiceConcept").textContent = item.concept_title;
    $("practiceDifficulty").textContent = `Target difficulty Elo: ${item.difficulty_score}`;
    $("practiceQuestion").textContent = item.question_text;
    $("practiceAnswer").value = "";
    $("practiceFeedback").textContent = "";
    $("practiceHintBox").classList.add("hidden");
    $("practiceHintBox").textContent = "";
    if (typeof $("practiceDialog").showModal === "function") {
      $("practiceDialog").showModal();
    } else {
      $("practiceDialog").setAttribute("open", "");
    }
  } catch (err) {
    toast(err.message);
  }
}

$("practiceBtn").addEventListener("click", () => openPractice());
$("closePractice").addEventListener("click", () => $("practiceDialog").close());

$("hintBtn").addEventListener("click", async () => {
  if (!activePracticeId) return;
  try {
    const data = await api(`/api/practice/${activePracticeId}/hint`, { method: "POST" });
    $("practiceHintBox").textContent = data.hint;
    $("practiceHintBox").classList.remove("hidden");
  } catch (err) {
    toast(err.message);
  }
});

async function submitPractice(giveUp = false) {
  if (!activePracticeId) return;
  try {
    const data = await api(`/api/practice/${activePracticeId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        answer: $("practiceAnswer").value,
        give_up: giveUp,
      }),
    });
    $("practiceFeedback").textContent =
      `${data.feedback} (Elo: ${data.elo_score}, ${data.elo_delta >= 0 ? "+" : ""}${data.elo_delta})`;
    $("eloPill").textContent = `Elo: ${data.elo_score}`;
  } catch (err) {
    $("practiceFeedback").textContent = err.message;
  }
}

$("submitAnswer").addEventListener("click", () => submitPractice(false));
$("giveUpBtn").addEventListener("click", () => submitPractice(true));

async function loadProgress() {
  const data = await api("/api/practice/progress");
  const assignBox = $("assignmentList");
  assignBox.innerHTML = data.assignments.length
    ? data.assignments
        .map(
          (a) => `
        <article class="card">
          <p class="eyebrow">${escapeHtml(a.status)}</p>
          <h3>${escapeHtml(a.title)}</h3>
          <p class="muted">${escapeHtml(a.concept_title)} · Diff ${a.difficulty_score}</p>
          ${
            a.status === "pending"
              ? `<button class="btn-secondary" data-open-assignment="${a.question_id}">Start practice</button>`
              : `<span class="badge">Completed</span>`
          }
        </article>`
        )
        .join("")
    : `<article class="card"><p class="muted">No teacher assignments yet.</p></article>`;

  assignBox.querySelectorAll("[data-open-assignment]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = data.assignments.find((a) => a.question_id === btn.dataset.openAssignment);
      if (item) openPractice(item.concept_id, item);
    });
  });

  const progBox = $("progressCards");
  progBox.innerHTML = data.mastery.length
    ? data.mastery
        .map((m) => {
          const pct = Math.max(0, Math.min(100, Math.round(((m.elo_score - 600) / 800) * 100)));
          return `
            <article class="card">
              <p class="eyebrow">${escapeHtml(m.status)} · ${m.attempts_count} attempts</p>
              <h3>${escapeHtml(m.concept_title)}</h3>
              <div class="meter"><span style="width:${pct}%"></span></div>
              <strong>Elo ${m.elo_score}</strong>
              <p class="muted">Avg hints: ${m.hints_used_avg}</p>
            </article>`;
        })
        .join("")
    : `<article class="card"><p class="muted">Complete practice items to build concept Elo.</p></article>`;
}

async function loadTeacher() {
  if (!currentUser || currentUser.role !== "teacher") return;
  const data = await api("/api/teacher/dashboard");
  const table = $("heatmapTable");
  if (!data.concepts.length || !data.heatmap.length) {
    table.innerHTML = `<tr><td class="muted">Upload a textbook and enroll students to populate the heatmap.</td></tr>`;
  } else {
    const header = `<tr><th>Student</th><th>Avg Elo</th>${data.concepts
      .map((c) => `<th>${escapeHtml(c.title)}</th>`)
      .join("")}</tr>`;
    const rows = data.heatmap
      .map(
        (row) => `
        <tr>
          <td><strong>${escapeHtml(row.student_name)}</strong></td>
          <td>${row.average_elo}</td>
          ${row.cells
            .map(
              (cell) =>
                `<td class="band-${cell.band}">${cell.elo_score} <small>(${cell.band})</small></td>`
            )
            .join("")}
        </tr>`
      )
      .join("");
    table.innerHTML = `<thead>${header}</thead><tbody>${rows}</tbody>`;
  }

  $("clusterList").innerHTML = data.misconception_clusters.length
    ? data.misconception_clusters
        .map(
          (cl) => `
          <div class="list-row">
            <strong>${escapeHtml(cl.concept_title)}</strong> — ${escapeHtml(cl.tag)}
            <div class="muted">${cl.affected_students} student(s) (${cl.affected_pct}%) · ${cl.occurrences} occurrences</div>
          </div>`
        )
        .join("")
    : `<p class="muted">No misconceptions logged yet.</p>`;

  $("alertList").innerHTML = data.intervention_alerts.length
    ? data.intervention_alerts
        .map(
          (al) => `
          <div class="list-row band-struggling">
            <strong>${escapeHtml(al.student_name)}</strong> on ${escapeHtml(al.concept_title)}
            <div>Elo ${al.elo_score} · Reasons: ${escapeHtml(al.reasons.join(", "))}</div>
          </div>`
        )
        .join("")
    : `<p class="muted">No active intervention alerts.</p>`;

  $("assignConceptSelect").innerHTML =
    `<option value="">Select concept</option>` +
    data.concepts
      .map((c) => `<option value="${c.concept_id}">${escapeHtml(c.title)}</option>`)
      .join("");
}

$("refreshTeacherBtn").addEventListener("click", () => {
  loadTeacher().catch((err) => toast(err.message));
});

$("assignForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const conceptId = $("assignConceptSelect").value;
  if (!conceptId) return;
  try {
    const res = await api("/api/teacher/assignments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        concept_id: conceptId,
        title: $("assignTitleInput").value.trim() || null,
      }),
    });
    $("assignFeedback").textContent = `Assigned to ${res.assigned_count} student(s).`;
    $("assignTitleInput").value = "";
  } catch (err) {
    $("assignFeedback").textContent = err.message;
  }
});

document.querySelectorAll(".nav").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    $(`${btn.dataset.view}View`).classList.remove("hidden");
    if (btn.dataset.view === "progress") loadProgress().catch((err) => toast(err.message));
    if (btn.dataset.view === "teacher") loadTeacher().catch((err) => toast(err.message));
  });
});

(async function init() {
  try {
    const data = await api("/api/auth/me");
    if (data.authenticated) showApp(data.user);
    else showAuth();
  } catch {
    showAuth();
  }
})();
