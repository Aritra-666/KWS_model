

const API_URL = "/api/dashboard";  
const POLL_INTERVAL = 5000;


const PIPELINE_STEPS = [
  "listening",
  "keyword_detected",
  "streaming",
  "processing",
  "transcript_ready"
];


const TRANSCRIPT_STATUS_MAP = {
  idle:       "⚫ Idle",
  recording:  "🔴 Recording",
  processing: "🟡 Processing"
};


const dom = {};

function cacheDom() {

  dom.systemStatus   = document.getElementById("system-status");
  dom.dateTime        = document.getElementById("date-time");


  dom.stateLabel      = document.getElementById("state-label");
  dom.stateSubtext    = document.getElementById("state-subtext");
  dom.pipelineSteps   = document.querySelectorAll("[data-step]");


  dom.transcriptStatus = document.getElementById("transcript-status");
  dom.transcriptText   = document.getElementById("transcript-text");

 
  dom.deviceName    = document.getElementById("device-name");
  dom.deviceBadge   = document.getElementById("device-badge");
  dom.wifiValue     = document.getElementById("wifi-value");
  dom.wifiBadge     = document.getElementById("wifi-badge");
  dom.micValue      = document.getElementById("mic-value");
  dom.micBadge      = document.getElementById("mic-badge");
  dom.powerValue    = document.getElementById("power-value");
  dom.uptimeValue   = document.getElementById("uptime-value");


  dom.handoffLatency   = document.getElementById("perf-handoff");
  dom.firstTranscript  = document.getElementById("perf-first");
  dom.finalTranscript  = document.getElementById("perf-final");
  dom.cpuUsage         = document.getElementById("perf-cpu");
  dom.ramUsage         = document.getElementById("perf-ram");
}


function setText(el, text) {
  if (el) el.textContent = text;
}

function setBadge(el, isActive, onLabel, offLabel) {
  if (!el) return;
  el.textContent = isActive ? onLabel : offLabel;
  el.className   = isActive ? "badge-green" : "badge-gray";
}

function setPerfCell(el, value, level) {
  if (!el) return;
  el.textContent = value;
  el.className   = level === "green"  ? "green-text"
                 : level === "yellow" ? "yellow-text"
                 :                      "green-text";
}

function formatDateTime() {
  const now = new Date();
  const opts = { year: "numeric", month: "short", day: "numeric" };
  const date = now.toLocaleDateString("en-US", opts);
  const time = now.toLocaleTimeString("en-US", { hour12: false });
  return `${date}   ${time}`;
}

function updatePipeline(activeKey) {
  const activeIndex = PIPELINE_STEPS.indexOf(activeKey);
  dom.pipelineSteps.forEach(stepEl => {
    const stepKey   = stepEl.getAttribute("data-step");
    const stepIndex = PIPELINE_STEPS.indexOf(stepKey);
    const dot       = stepEl.querySelector(".dot");
    if (stepIndex <= activeIndex) {
      stepEl.classList.add("active-step");
      if (dot) dot.classList.add("green-dot");
    } else {
      stepEl.classList.remove("active-step");
      if (dot) dot.classList.remove("green-dot");
    }
  });

  const lines = document.querySelectorAll(".pipeline .line");
  lines.forEach((line, i) => {
    line.classList.toggle("line-active", i < activeIndex);
  });
}



function render(data) {
  if (data.system) {
    const online = data.system.online;
    if (dom.systemStatus) {
      dom.systemStatus.textContent = online ? "🟢 System Online" : "🔴 System Offline";
      dom.systemStatus.className   = online ? "status-online" : "status-offline";
    }
  }
  setText(dom.dateTime, formatDateTime());

  if (data.system) {
    setText(dom.stateLabel, data.system.stateLabel);
    setText(dom.stateSubtext, data.system.stateSubtext);
    updatePipeline(data.system.state);
  }

  if (data.transcript) {
    const statusText = TRANSCRIPT_STATUS_MAP[data.transcript.status] || "⚫ Idle";
    setText(dom.transcriptStatus, statusText);
    setText(dom.transcriptText, data.transcript.text || "Transcript will appear here...");
  }

  if (data.device) {
    const d = data.device;
    setText(dom.deviceName, d.name);
    setBadge(dom.deviceBadge, d.online, "Online", "Offline");
    setText(dom.wifiValue, d.wifi);
    setBadge(dom.wifiBadge, d.wifiGood, "Good", "Poor");
    setText(dom.micValue, d.mic);
    setBadge(dom.micBadge, d.micOn, "On", "Off");
    setText(dom.powerValue, d.power);
    setText(dom.uptimeValue, d.uptime);
  }

  if (data.performance) {
    const p = data.performance;
    setPerfCell(dom.handoffLatency,  p.handoffLatency.value,   p.handoffLatency.level);
    setPerfCell(dom.firstTranscript, p.firstTranscript.value,  p.firstTranscript.level);
    setPerfCell(dom.finalTranscript, p.finalTranscript.value,  p.finalTranscript.level);
    setPerfCell(dom.cpuUsage,        p.cpuUsage.value,         p.cpuUsage.level);
    setPerfCell(dom.ramUsage,        p.ramUsage.value,         p.ramUsage.level);
  }
}



async function fetchDashboard() {
  try {
    const res  = await fetch(API_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    render(data);
  } catch (err) {
    console.warn("[Dashboard] API fetch failed, using fallback data:", err.message);
    
    render(FALLBACK_DATA);
  }
}



const FALLBACK_DATA = {
  system: {
    online: true,
    state: "listening",
    stateLabel: "Listening...",
    stateSubtext: "Waiting for keyword"
  },
  transcript: {
    status: "idle",
    text: ""
  },
  device: {
    name: "ESP32-S3",
    online: true,
    wifi: "Connected",
    wifiGood: true,
    mic: "Active",
    micOn: true,
    power: "USB Powered",
    uptime: "2h 18m 36s"
  },
  performance: {
    handoffLatency:  { value: "82 ms",           level: "green" },
    firstTranscript: { value: "410 ms",          level: "green" },
    finalTranscript: { value: "1.32 s",          level: "yellow" },
    cpuUsage:        { value: "6.4 %",           level: "green" },
    ramUsage:        { value: "142 KB / 256 KB", level: "yellow" }
  }
};



document.addEventListener("DOMContentLoaded", () => {
  cacheDom();
  fetchDashboard();                        
  setInterval(fetchDashboard, POLL_INTERVAL); 
});
