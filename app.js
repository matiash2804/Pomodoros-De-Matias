/* Pomodoro Matías Henquín — interfaz, estadísticas y temporizador */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { client: null, token: null, sessions: [], timer: null, timerTick: null, toastTick: null };
const TIMER_KEY = "pomodoro_timer_v2";
const PALETTE = ["#d6b467", "#b57753", "#a9a394", "#829879", "#b78c6a", "#777b76", "#d0c29d", "#956f58"];

document.addEventListener("DOMContentLoaded", initialize);

async function initialize() {
  bindEvents();
  showToday();
  restoreTimer();
  window.addEventListener("online", () => { refreshConnection(); if (state.token) flushCompletedCycle(); });
  window.addEventListener("offline", refreshConnection);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tickTimer(); });
  refreshConnection();
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    if (!response.ok) throw new Error("La configuración del servidor no está disponible.");
    const config = await response.json();
    if (!config.supabaseUrl || !config.supabaseAnonKey || !window.supabase?.createClient) {
      setConnection("error", "Falta configurar el acceso seguro");
      setLoginMessage("El proyecto todavía no está conectado a Supabase.", "error");
      return;
    }
    state.client = window.supabase.createClient(config.supabaseUrl, config.supabaseAnonKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
    });
    const { data: { session } } = await state.client.auth.getSession();
    state.token = session?.access_token || null;
    renderAuth(session);
    if (session) { await loadSessions(); await flushCompletedCycle(); }
    state.client.auth.onAuthStateChange((_event, nextSession) => {
      state.token = nextSession?.access_token || null;
      renderAuth(nextSession);
      if (nextSession) loadSessions().then(flushCompletedCycle);
      else { state.sessions = []; renderAll(); }
    });
  } catch (error) {
    setConnection("error", "Servidor no disponible");
    setLoginMessage(error.message || "No se pudo conectar. Intentá de nuevo.", "error");
  }
}

function bindEvents() {
  $("loginForm").addEventListener("submit", handleLogin);
  $("accountButton").addEventListener("click", async () => { await state.client?.auth.signOut(); });
  $("sessionForm").addEventListener("submit", saveSession);
  $("clearForm").addEventListener("click", resetForm);
  $("sessionCycles").addEventListener("input", updateTotal);
  $("sessionDuration").addEventListener("input", updateTotal);
  ["searchSessions", "filterFrom", "filterTo", "sortSessions"].forEach((id) => $(id).addEventListener("input", renderHistory));
  $("trendRange").addEventListener("change", renderTrendChart);
  $("exportCsv").addEventListener("click", () => downloadExport("csv"));
  $("exportXlsx").addEventListener("click", () => downloadExport("xlsx"));
  $("importCsv").addEventListener("click", () => $("csvFile").click());
  $("csvFile").addEventListener("change", importCsvFile);
  $("timerStart").addEventListener("click", startOrResumeTimer);
  $("timerPause").addEventListener("click", pauseTimer);
  $("timerReset").addEventListener("click", resetTimer);
  ["workMinutes", "shortBreak", "longBreak"].forEach((id) => $(id).addEventListener("change", handleTimerConfig));
  ["sessionDate", "sessionSubject", "sessionCycles", "sessionDuration"].forEach((id) => $(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter" && id !== "sessionSubject") { event.preventDefault(); $("sessionForm").requestSubmit(); }
  }));
}

async function handleLogin(event) {
  event.preventDefault();
  if (!state.client) return setLoginMessage("El servicio de acceso todavía no está listo.", "error");
  const email = $("email").value.trim();
  setLoginMessage("Enviando el enlace seguro…");
  const { error } = await state.client.auth.signInWithOtp({ email, options: { shouldCreateUser: false, emailRedirectTo: `${location.origin}/` } });
  setLoginMessage(error ? error.message : "Listo. Revisá tu correo y abrí el enlace desde este dispositivo.", error ? "error" : "success");
}

function renderAuth(session) {
  $("authPanel").hidden = Boolean(session);
  $("appShell").hidden = !session;
  $("accountButton").hidden = !session;
  if (!session) setConnection(navigator.onLine ? "online" : "offline", navigator.onLine ? "Conectado" : "Sin conexión");
}

function setLoginMessage(message, type = "") {
  const node = $("loginMessage"); node.textContent = message; node.className = `form-message ${type}`;
}

function refreshConnection() {
  if (!navigator.onLine) return setConnection("offline", "Sin conexión");
  if (state.token) setConnection("online", "Conectado a tu cuenta");
  else setConnection("", "Conexión disponible");
}

function setConnection(mode, label) {
  const node = $("connection"); node.className = `connection ${mode}`; node.lastElementChild.textContent = label;
}

async function api(path, options = {}) {
  if (!state.token) throw new Error("Iniciá sesión para continuar.");
  const response = await fetch(path, { ...options, headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), Authorization: `Bearer ${state.token}`, ...options.headers } });
  if (response.status === 401) { await state.client?.auth.signOut(); throw new Error("La sesión venció. Volvé a ingresar."); }
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(payload?.detail || `No se pudo completar la operación (${response.status}).`);
  return payload;
}

async function loadSessions() {
  try {
    const payload = await api("/api/sessions");
    state.sessions = payload.sessions || [];
    renderAll(); refreshConnection();
  } catch (error) {
    setConnection("error", "No se pudieron cargar los datos"); showToast(error.message);
  }
}

async function saveSession(event) {
  event.preventDefault();
  const button = $("saveSession"); button.disabled = true; button.textContent = "Guardando…";
  const id = $("sessionId").value;
  const payload = {
    id_estudio: id || undefined,
    fecha: $("sessionDate").value,
    materia: $("sessionSubject").value.trim(),
    cantidad: Number($("sessionCycles").value),
    tiempo: Number($("sessionDuration").value)
  };
  try {
    const result = await api(id ? `/api/sessions/${encodeURIComponent(id)}` : "/api/sessions", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
    await loadSessions();
    resetForm();
    setFormMessage(id ? "Sesión actualizada y sincronizada." : "Sesión guardada y sincronizada.", "success");
    showToast(id ? "Sesión actualizada" : "Sesión guardada");
  } catch (error) { setFormMessage(error.message, "error"); }
  finally { button.disabled = false; button.textContent = "Guardar sesión"; }
}

async function deleteSession(id) {
  const record = state.sessions.find((item) => item.id_estudio === id);
  if (!record || !window.confirm(`¿Eliminar la sesión de ${record.materia} del ${formatDate(record.fecha)}? Esta acción no se puede deshacer.`)) return;
  try { await api(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }); await loadSessions(); showToast("Sesión eliminada y sincronizada"); }
  catch (error) { showToast(error.message); }
}

function renderAll() {
  updateTotal(); renderDatalist(); renderHistory(); renderStats(); renderTrendChart(); renderSubjectChart();
}

function resetForm() {
  $("sessionForm").reset(); $("sessionId").value = ""; $("sessionDate").value = localDateString();
  $("sessionCycles").value = "1"; $("sessionDuration").value = "25";
  $("formHeading").textContent = "Nueva sesión"; $("saveSession").textContent = "Guardar sesión"; updateTotal(); setFormMessage("");
}

function setFormMessage(message, type = "") { const node = $("saveMessage"); node.textContent = message; node.className = `form-message ${type}`; }
function updateTotal() { const amount = Math.max(0, Number($("sessionCycles").value) || 0) * Math.max(0, Number($("sessionDuration").value) || 0); $("sessionTotal").textContent = `${amount.toLocaleString("es-AR")} min`; }
function renderDatalist() {
  const subjects = [...new Set(state.sessions.map((item) => item.materia))].sort((a, b) => a.localeCompare(b, "es"));
  $("subjects").replaceChildren(...subjects.map((subject) => { const option = document.createElement("option"); option.value = subject; return option; }));
}

function visibleSessions() {
  const search = $("searchSessions").value.trim().toLocaleLowerCase("es");
  const from = $("filterFrom").value, to = $("filterTo").value;
  const records = state.sessions.filter((item) => (!search || item.materia.toLocaleLowerCase("es").includes(search)) && (!from || item.fecha >= from) && (!to || item.fecha <= to));
  const sort = $("sortSessions").value;
  records.sort(sort === "oldest" ? (a,b) => a.fecha.localeCompare(b.fecha) : sort === "longest" ? (a,b) => b.total_minutos-a.total_minutos : sort === "subject" ? (a,b) => a.materia.localeCompare(b.materia,"es") || b.fecha.localeCompare(a.fecha) : (a,b) => b.fecha.localeCompare(a.fecha));
  return records;
}

function renderHistory() {
  if (!$("sessionsBody")) return;
  const records = visibleSessions();
  $("sessionsBody").replaceChildren(...records.map((item) => {
    const row = document.createElement("tr");
    const cells = [formatDate(item.fecha), item.materia, item.cantidad, `${item.tiempo} min`, formatDuration(item.total_minutos)];
    cells.forEach((value, index) => { const cell = document.createElement("td"); cell.textContent = value; if (index === 1) cell.className = "subject-cell"; if (index === 4) cell.className = "total-cell"; row.appendChild(cell); });
    const actions = document.createElement("td"), group = document.createElement("div"); group.className = "row-actions";
    const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Editar"; edit.addEventListener("click", () => editSession(item));
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Eliminar"; remove.addEventListener("click", () => deleteSession(item.id_estudio));
    group.append(edit, remove); actions.appendChild(group); row.appendChild(actions); return row;
  }));
  $("emptyHistory").hidden = records.length > 0;
  $("visibleCount").textContent = `${records.length.toLocaleString("es-AR")} ${records.length === 1 ? "sesión" : "sesiones"}`;
}

function editSession(item) {
  $("sessionId").value = item.id_estudio; $("sessionDate").value = item.fecha; $("sessionSubject").value = item.materia;
  $("sessionCycles").value = item.cantidad; $("sessionDuration").value = item.tiempo;
  $("formHeading").textContent = "Editar sesión"; $("saveSession").textContent = "Actualizar sesión"; updateTotal();
  $("registro").scrollIntoView({ behavior: "smooth", block: "start" }); $("sessionSubject").focus();
}

function renderStats() {
  const records = state.sessions;
  const minutes = records.reduce((sum, item) => sum + Number(item.total_minutos), 0);
  $("totalHours").textContent = `${Math.floor(minutes / 60).toLocaleString("es-AR")}h ${String(minutes % 60).padStart(2, "0")}m`;
  $("totalMinutes").textContent = `${minutes.toLocaleString("es-AR")} minutos registrados`;
  $("sessionCount").textContent = records.length.toLocaleString("es-AR");
  $("cycleCount").textContent = records.reduce((sum, item) => sum + Number(item.cantidad), 0).toLocaleString("es-AR");
  const dates = [...new Set(records.map((item) => item.fecha))].sort();
  $("streakCount").textContent = `${studyStreak(dates)} d`;
  const subjectTotals = sumBy(records, (item) => item.materia, (item) => Number(item.total_minutos));
  const top = [...subjectTotals].sort((a,b) => b[1]-a[1])[0];
  $("topSubject").textContent = top?.[0] || "—";
  $("topSubjectTime").textContent = top ? `${formatDuration(top[1])} acumulados` : "Todavía sin sesiones";
  const today = localDateString();
  const first = dates[0];
  const daySpan = first ? Math.max(1, Math.floor((dateUtc(today)-dateUtc(first))/86400000)+1) : 0;
  $("dailyAverage").textContent = daySpan ? formatDuration(Math.round(minutes/daySpan)) : "—";
  const now = new Date(); const monthStart = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-01`;
  const currentMonth = records.filter((item) => item.fecha.startsWith(monthStart.slice(0,7))).reduce((sum,item)=>sum+item.total_minutos,0);
  const previous = new Date(now.getFullYear(),now.getMonth()-1,1); const previousKey = `${previous.getFullYear()}-${String(previous.getMonth()+1).padStart(2,"0")}`;
  const previousMonth = records.filter((item)=>item.fecha.startsWith(previousKey)).reduce((sum,item)=>sum+item.total_minutos,0);
  $("monthTotal").textContent = formatDuration(currentMonth);
  $("monthComparison").textContent = previousMonth ? `${currentMonth >= previousMonth ? "+" : "−"}${Math.abs(Math.round((currentMonth-previousMonth)/previousMonth*100))}% vs. mes anterior` : "tiempo acumulado este mes";
  const weekTotals = rollingWeeks(records, 12);
  const allWeeks = sumBy(records, (item) => { const date = new Date(`${item.fecha}T12:00:00`); date.setDate(date.getDate() - ((date.getDay()+6)%7)); return localDateString(date); }, (item) => item.total_minutos);
  const best = Math.max(0, ...allWeeks.values());
  $("bestWeek").textContent = best ? formatDuration(best) : "—";
  const weeksSpan = first ? Math.max(1, Math.ceil((dateUtc(today)-dateUtc(first)+86400000)/(7*86400000))) : 0;
  $("weeklyAverage").textContent = `Promedio semanal: ${weeksSpan ? formatDuration(Math.round(minutes/weeksSpan)) : "—"}`;
  renderWeekChart(weekTotals); renderMonthChart(records);
}

function studyStreak(dates) {
  if (!dates.length) return 0;
  const set = new Set(dates); const today = localDateString();
  let cursor = dateUtc(set.has(today) ? today : shiftDate(today,-1));
  if (!set.has(new Date(cursor).toISOString().slice(0,10))) return 0;
  let count=0; while(set.has(new Date(cursor).toISOString().slice(0,10))){count++;cursor-=86400000;} return count;
}

function renderTrendChart() {
  const days = Number($("trendRange").value); const end = localDateString(); const start = shiftDate(end,-days+1);
  const totals = new Map(); state.sessions.forEach((item)=>totals.set(item.fecha,(totals.get(item.fecha)||0)+item.total_minutos/60));
  const values = Array.from({length:days},(_,index)=>{const date=shiftDate(start,index);return{date,value:totals.get(date)||0};});
  $("trendChart").innerHTML = lineSvg(values);
}

function lineSvg(values) {
  const width=680,height=205,left=35,right=10,top=12,bottom=26,plotW=width-left-right,plotH=height-top-bottom,max=Math.max(1,Math.ceil(Math.max(...values.map((item)=>item.value))*1.15));
  const points=values.map((item,index)=>({x:left+(values.length===1?plotW/2:index*plotW/(values.length-1)),y:top+plotH-item.value/max*plotH,...item}));
  const path=points.map((p,index)=>`${index?"L":"M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area=`${path} L${points.at(-1).x},${top+plotH} L${points[0].x},${top+plotH} Z`;
  const grid=[0,.5,1].map((ratio)=>{const y=top+plotH*(1-ratio);return `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" stroke="rgba(255,255,255,.07)" stroke-dasharray="3 5"/><text x="${left-7}" y="${y+3}" fill="#77736a" font-size="8" text-anchor="end">${(max*ratio).toFixed(ratio?1:0)}h</text>`;}).join("");
  const step=Math.max(1,Math.floor(values.length/6)); const labels=points.filter((_p,index)=>index%step===0||index===points.length-1).map((p)=>`<text x="${p.x}" y="${height-5}" fill="#77736a" font-size="8" text-anchor="middle">${p.date.slice(5)}</text>`).join("");
  const circles=points.filter((p,index)=>p.value>0&&(index%Math.max(1,Math.floor(values.length/10))===0||index===points.length-1)).map((p)=>`<circle cx="${p.x}" cy="${p.y}" r="3" fill="#e4c880"><title>${formatDate(p.date)} · ${p.value.toFixed(1)} h</title></circle>`).join("");
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="trendFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#d6b467" stop-opacity=".23"/><stop offset="1" stop-color="#d6b467" stop-opacity="0"/></linearGradient></defs>${grid}<path d="${area}" fill="url(#trendFill)"/><path d="${path}" fill="none" stroke="#d6b467" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/>${circles}${labels}</svg>`;
}

function renderSubjectChart() {
  const entries=[...sumBy(state.sessions,(item)=>item.materia,(item)=>Number(item.total_minutos))].sort((a,b)=>b[1]-a[1]);
  const total=entries.reduce((sum,item)=>sum+item[1],0);
  if(!entries.length){$("subjectChart").innerHTML='<div class="donut-center"><strong>0</strong><small>HORAS</small></div>';$("subjectLegend").textContent="Todavía no hay sesiones";return;}
  let cursor=0;const stops=entries.map((item,index)=>{const start=cursor;cursor+=item[1]/total*100;return`${PALETTE[index%PALETTE.length]} ${start.toFixed(1)}% ${cursor.toFixed(1)}%`;}).join(",");
  $("subjectChart").innerHTML=`<div class="donut" style="background:conic-gradient(${stops})"><div class="donut-center"><strong>${formatDuration(total)}</strong><small>TOTAL</small></div></div>`;
  $("subjectLegend").replaceChildren(...entries.map(([name,value],index)=>{const row=document.createElement("div");row.className="legend-row";const dot=document.createElement("i");dot.style.background=PALETTE[index%PALETTE.length];const label=document.createElement("span");label.textContent=name;const amount=document.createElement("strong");amount.textContent=`${formatDuration(value)} · ${Math.round(value/total*100)}%`;row.append(dot,label,amount);return row;}));
}

function renderWeekChart(weeks) { $("weekChart").innerHTML=barSvg(weeks.map((item)=>({label:item.label,value:item.minutes/60})),"h"); }
function renderMonthChart(records) {
  const now=new Date();const months=Array.from({length:6},(_,index)=>new Date(now.getFullYear(),now.getMonth()-5+index,1));
  const values=months.map((date)=>{const key=`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}`;return{label:new Intl.DateTimeFormat("es-AR",{month:"short"}).format(date).replace(".",""),value:records.filter((item)=>item.fecha.startsWith(key)).reduce((sum,item)=>sum+item.total_minutos/60,0)};});
  $("monthChart").innerHTML=barSvg(values,"h");
}
function barSvg(values,unit){
  const width=540,height=165,left=30,right=8,top=10,bottom=27,plotH=height-top-bottom,gap=12,barW=(width-left-right-gap*(values.length-1))/values.length,max=Math.max(1,...values.map((item)=>item.value));
  const grid=[0,.5,1].map((ratio)=>{const y=top+plotH*(1-ratio);return`<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" stroke="rgba(255,255,255,.07)" stroke-dasharray="3 5"/>`;}).join("");
  const bars=values.map((item,index)=>{const heightValue=item.value/max*(plotH-4),x=left+index*(barW+gap),y=top+plotH-heightValue;return`<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(1,heightValue)}" rx="4" fill="${PALETTE[index%PALETTE.length]}" opacity="${item.value?".88":".2"}"><title>${item.label}: ${item.value.toFixed(1)} ${unit}</title></rect><text x="${x+barW/2}" y="${height-6}" fill="#77736a" font-size="8" text-anchor="middle">${item.label}</text>`;}).join("");
  return`<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">${grid}${bars}</svg>`;
}

function sumBy(records,keyFn,valueFn){const totals=new Map();records.forEach((item)=>{const key=keyFn(item);totals.set(key,(totals.get(key)||0)+valueFn(item));});return totals;}
function rollingWeeks(records,count){const today=localDateString(),day=new Date(`${today}T12:00:00`).getDay(),mondayOffset=(day+6)%7;const thisMonday=shiftDate(today,-mondayOffset);return Array.from({length:count},(_,index)=>{const start=shiftDate(thisMonday,-7*(count-index-1)),end=shiftDate(start,6),minutes=records.filter((item)=>item.fecha>=start&&item.fecha<=end).reduce((sum,item)=>sum+item.total_minutos,0);return{label:new Intl.DateTimeFormat("es-AR",{day:"2-digit",month:"2-digit"}).format(new Date(`${start}T12:00:00`)),minutes};});}

async function downloadExport(type){
  try{const response=await fetch(`/api/export/${type}`,{headers:{Authorization:`Bearer ${state.token}`}});if(!response.ok){const err=await response.json();throw new Error(err.detail||"No se pudo generar la exportación.");}const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=type==="csv"?"pomodoro-sesiones.csv":"pomodoro-sesiones.xlsx";link.click();URL.revokeObjectURL(url);}
  catch(error){showToast(error.message);}
}

async function importCsvFile(event){
  const file=event.target.files?.[0];if(!file)return;
  if(!file.name.toLowerCase().endsWith(".csv")){showToast("Elegí un archivo CSV.");event.target.value="";return;}
  const form=new FormData();form.append("file",file);
  try{const result=await api("/api/import/csv",{method:"POST",body:form});await loadSessions();showToast(`Importación: ${result.imported} nuevas · ${result.updated} actualizadas · ${result.rejected} rechazadas.`);}
  catch(error){showToast(error.message);}
  finally{event.target.value="";}
}

function localDateString(date=new Date()){return`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;}
function showToday(){$("todayLabel").textContent=new Intl.DateTimeFormat("es-AR",{weekday:"long",day:"numeric",month:"long"}).format(new Date());$("sessionDate").value=localDateString();}
function dateUtc(value){const[y,m,d]=value.split("-").map(Number);return Date.UTC(y,m-1,d);}
function shiftDate(value,days){const date=new Date(`${value}T12:00:00`);date.setDate(date.getDate()+days);return localDateString(date);}
function formatDate(value){return new Intl.DateTimeFormat("es-AR",{day:"2-digit",month:"short",year:"numeric",timeZone:"UTC"}).format(new Date(`${value}T00:00:00Z`));}
function formatDuration(minutes){minutes=Math.round(Number(minutes)||0);const hours=Math.floor(minutes/60),rest=minutes%60;return hours?`${hours} h ${String(rest).padStart(2,"0")} min`:`${rest} min`;}
function showToast(message){const node=$("toast");node.textContent=message;node.classList.add("show");clearTimeout(state.toastTick);state.toastTick=setTimeout(()=>node.classList.remove("show"),3000);}

/* El temporizador usa marcas de tiempo absolutas: una pestaña en segundo plano no pierde minutos. */
function timerConfig(){return{work:Number($("workMinutes").value)||25,short:Number($("shortBreak").value)||5,long:Number($("longBreak").value)||15};}
function restoreTimer(){
  try{state.timer=JSON.parse(localStorage.getItem(TIMER_KEY)||"null");}catch{state.timer=null;}
  if(!state.timer)state.timer={phase:"work",running:false,remainingMs:25*60000,cycle:0};
  $("workMinutes").value=state.timer.config?.work||25;$("shortBreak").value=state.timer.config?.short||5;$("longBreak").value=state.timer.config?.long||15;
  if(state.timer.running&&state.timer.endsAt&&Date.now()>=state.timer.endsAt)tickTimer();else renderTimer();
  state.timerTick=setInterval(tickTimer,250);
}
function persistTimer(){localStorage.setItem(TIMER_KEY,JSON.stringify(state.timer));}
function handleTimerConfig(){
  const config=timerConfig();if(state.timer.running)return;
  state.timer.config=config;if(state.timer.phase==="work")state.timer.remainingMs=config.work*60000;
  else state.timer.remainingMs=(state.timer.phase==="long"?config.long:config.short)*60000;
  persistTimer();renderTimer();
}
function startOrResumeTimer(){
  if (!state.token) return showToast("Iniciá sesión para que el ciclo se guarde en tu historial.");
  if($("timerSubject").value.trim().length<1)return showToast("Elegí una materia antes de empezar.");
  state.timer.config=timerConfig();state.timer.subject=$("timerSubject").value.trim();
  if(state.timer.phase==="work")state.timer.remainingMs=state.timer.remainingMs||state.timer.config.work*60000;
  state.timer.endsAt=Date.now()+state.timer.remainingMs;state.timer.running=true;persistTimer();renderTimer();tickTimer();
}
function pauseTimer(){if(!state.timer.running)return;state.timer.remainingMs=Math.max(0,state.timer.endsAt-Date.now());state.timer.running=false;delete state.timer.endsAt;persistTimer();renderTimer();}
function resetTimer(){const config=timerConfig();state.timer={...state.timer,phase:"work",running:false,remainingMs:config.work*60000,cycle:state.timer.cycle||0,config};delete state.timer.endsAt;persistTimer();renderTimer();}
function tickTimer(){
  if(!state.timer?.running)return renderTimer();
  const remaining=state.timer.endsAt-Date.now();
  if(remaining<=0){const finishedPhase=state.timer.phase;
    if(finishedPhase==="work"&&!state.token)return;
    state.timer.running=false;delete state.timer.endsAt;
    if(finishedPhase==="work"){const subject=state.timer.subject;const duration=state.timer.config.work;state.timer.cycle=(state.timer.cycle||0)+1;state.timer.phase=state.timer.cycle%4===0?"long":"short";state.timer.remainingMs=(state.timer.phase==="long"?state.timer.config.long:state.timer.config.short)*60000;if(subject)state.timer.pendingCompletion={id_estudio:crypto.randomUUID(),fecha:localDateString(),materia:subject,cantidad:1,tiempo:duration};persistTimer();renderTimer();if(subject&&state.token)flushCompletedCycle();else showToast("El ciclo terminó y quedó pendiente de sincronización.");}
    else{state.timer.phase="work";state.timer.remainingMs=state.timer.config.work*60000;persistTimer();renderTimer();showToast("Descanso terminado. Cuando quieras, empezá otro ciclo.");}
    return;
  }
  state.timer.remainingMs=remaining;renderTimer();
}
async function flushCompletedCycle(){
  if(!state.token||!state.timer?.pendingCompletion||state.flushingCompletion)return;
  state.flushingCompletion=true;
  try{const result=await api("/api/sessions",{method:"POST",body:JSON.stringify(state.timer.pendingCompletion)});state.sessions=[result.session,...state.sessions.filter((item)=>item.id_estudio!==result.session.id_estudio)];state.timer.pendingCompletion=null;persistTimer();renderAll();showToast("Ciclo completado y sincronizado con tu historial.");}
  catch(error){showToast(`El ciclo se completó; la sincronización se reintentará al volver la conexión. ${error.message}`);}
  finally{state.flushingCompletion=false;}
}
function renderTimer(){
  if(!state.timer)return;const timer=state.timer,config=timer.config||timerConfig();const ms=timer.running?Math.max(0,timer.endsAt-Date.now()):timer.remainingMs;
  const total=(timer.phase==="work"?config.work:timer.phase==="long"?config.long:config.short)*60000;
  const seconds=Math.ceil(ms/1000);$("timerClock").textContent=`${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`;
  $("timerPhase").textContent=timer.phase==="work"?"ENFOQUE":timer.phase==="long"?"DESCANSO LARGO":"DESCANSO CORTO";
  $("timerCaption").textContent=timer.running?"MOMENTO PRESENTE":timer.phase==="work"?"LISTA PARA EMPEZAR":"PAUSA CONSCIENTE";
  $("timerProgress").style.strokeDashoffset=String(622.04*(1-ms/total));
  $("timerStart").hidden=timer.running;$("timerPause").hidden=!timer.running;
  $("timerStart").textContent=timer.phase==="work"?(timer.remainingMs<total?"Reanudar enfoque":"Iniciar enfoque"):"Iniciar descanso";
  ["workMinutes","shortBreak","longBreak","timerSubject"].forEach((id)=>$(id).disabled=timer.running);
}
