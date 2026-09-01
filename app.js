
/**
 * Instant Group Decision Maker — Frontend Application Controller
 * Handles SPA navigation, anonymous voter sessions, API sync, and live polling.
 */

const API_BASE_URL = `${window.location.origin}/api`;

const app = {
  // Application State
  state: {
    voterId: null,
    nickname: null,
    roomCode: null,
    roomData: null,
    currentSwipeIndex: 0,
    quadraticAllocations: {}, // { [optionId]: credits }
    maxCredits: 16,
    pollingInterval: null,
  },

  // Initialize Application
  init() {
    this.initVoterIdentity();
    this.checkUrlParams();
  },

  // Anonymous Client Identity Token (localStorage)
  initVoterIdentity() {
    let storedVoterId = localStorage.getItem("qd_voter_id");
    if (!storedVoterId) {
      storedVoterId = "voter_" + Math.random().toString(36).substring(2, 9) + "_" + Date.now().toString(36);
      localStorage.setItem("qd_voter_id", storedVoterId);
    }
    this.state.voterId = storedVoterId;
  },

  // Auto-fill room code if joined via URL share link
  checkUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const roomParam = params.get("room");
    if (roomParam) {
      this.navigate("join");
      const joinInput = document.getElementById("join-room-code");
      if (joinInput) joinInput.value = roomParam.toUpperCase();
    }
  },

  // SPA View Router
  navigate(viewName) {
    const views = ["landing", "create", "join", "swipe", "quadratic", "results"];
    views.forEach((v) => {
      const el = document.getElementById(`view-${v}`);
      if (el) el.classList.add("hidden");
    });

    const targetEl = document.getElementById(`view-${viewName}`);
    if (targetEl) targetEl.classList.remove("hidden");

    // Manage Polling
    if (viewName === "results") {
      this.startResultsPolling();
    } else {
      this.stopResultsPolling();
    }

    // Room badge sync
    const badge = document.getElementById("room-badge");
    const badgeCode = document.getElementById("badge-room-code");
    if (this.state.roomCode) {
      badge.classList.remove("hidden");
      badgeCode.innerText = this.state.roomCode;
    } else {
      badge.classList.add("hidden");
    }
  },

  // Toast Notification System
  showToast(message, type = "info") {
    const toast = document.getElementById("toast");
    const msgEl = document.getElementById("toast-message");
    const iconEl = document.getElementById("toast-icon");

    const icons = {
      success: '<i class="fa-solid fa-circle-check text-sage-500"></i>',
      error: '<i class="fa-solid fa-circle-exclamation text-rose-500"></i>',
      info: '<i class="fa-solid fa-circle-info text-charcoal-200"></i>',
    };

    iconEl.innerHTML = icons[type] || icons.info;
    msgEl.innerText = message;

    toast.classList.remove("opacity-0", "pointer-events-none");
    toast.classList.add("opacity-100");

    setTimeout(() => {
      toast.classList.remove("opacity-100");
      toast.classList.add("opacity-0", "pointer-events-none");
    }, 3200);
  },

  // Dynamic Options Builder (Create Room View)
  addOptionInput() {
    const container = document.getElementById("options-inputs-container");
    const count = container.querySelectorAll(".option-row").length + 1;

    const row = document.createElement("div");
    row.className = "flex gap-2 option-row animate-fade-in";
    row.innerHTML = `
      <input type="text" required placeholder="Option ${count}" class="option-input flex-1 px-4 py-2.5 rounded-xl bg-surface border border-charcoal-200 text-charcoal-900 placeholder-charcoal-400 focus:outline-none focus:border-sage-500 focus:ring-2 focus:ring-sage-500/10 shadow-sm text-sm">
      <button type="button" onclick="this.parentElement.remove()" class="px-3 rounded-xl bg-surface border border-charcoal-200 hover:bg-charcoal-50 text-charcoal-400 hover:text-rose-600 transition">
        <i class="fa-solid fa-trash text-xs"></i>
      </button>
    `;
    container.appendChild(row);
  },

  // 1. Handle Room Creation
  async handleCreateRoom(e) {
    e.preventDefault();
    const hostName = document.getElementById("create-host-name").value.trim();
    const question = document.getElementById("create-question").value.trim();
    const votingMethod = document.querySelector('input[name="create-voting-method"]:checked').value;

    const optionInputs = document.querySelectorAll(".option-input");
    const options = Array.from(optionInputs)
      .map((i) => i.value.trim())
      .filter((val) => val.length > 0);

    if (options.length < 2) {
      this.showToast("Please provide at least 2 distinct options.", "error");
      return;
    }

    const submitBtn = document.getElementById("btn-create-submit");
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Launching...`;

    try {
      const response = await fetch(`${API_BASE_URL}/rooms`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host_name: hostName,
          question: question,
          voting_method: votingMethod,
          options: options,
        }),
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Failed to create room.");

      this.state.roomCode = data.room_code;
      this.state.nickname = hostName;

      // Automatically register host as a participant
      await fetch(`${API_BASE_URL}/rooms/${data.room_code}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          voter_id: this.state.voterId,
          nickname: hostName,
        }),
      });

      this.showToast(`Room ${data.room_code} created!`, "success");
      await this.loadAndStartRoom(data.room_code);
    } catch (err) {
      this.showToast(err.message, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = `<span>Create & Launch Room</span><i class="fa-solid fa-arrow-right text-xs"></i>`;
    }
  },

  // 2. Handle Joining an Existing Room
  async handleJoinRoom(e) {
    e.preventDefault();
    const code = document.getElementById("join-room-code").value.trim().toUpperCase();
    const nickname = document.getElementById("join-nickname").value.trim();

    if (!code || !nickname) {
      this.showToast("Room code and nickname are required.", "error");
      return;
    }

    const submitBtn = document.getElementById("btn-join-submit");
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Joining...`;

    try {
      const response = await fetch(`${API_BASE_URL}/rooms/${code}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          voter_id: this.state.voterId,
          nickname: nickname,
        }),
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to join room.");

      this.state.roomCode = code;
      this.state.nickname = nickname;

      this.showToast(`Joined as ${nickname}`, "success");
      await this.loadAndStartRoom(code);
    } catch (err) {
      this.showToast(err.message, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = `<span>Enter Room</span><i class="fa-solid fa-arrow-right text-xs"></i>`;
    }
  },

  // 3. Load Room Configuration & Route to Proper Voting Engine
  async loadAndStartRoom(code) {
    try {
      const response = await fetch(`${API_BASE_URL}/rooms/${code}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Room details not found.");

      this.state.roomData = data;
      this.state.roomCode = data.room_code;

      if (data.voting_method === "swipe") {
        this.setupSwipeDeck();
        this.navigate("swipe");
      } else {
        this.setupQuadraticDeck();
        this.navigate("quadratic");
      }
    } catch (err) {
      this.showToast(err.message, "error");
    }
  },

  // --- SWIPE VOTING ENGINE ---
  setupSwipeDeck() {
    this.state.currentSwipeIndex = 0;
    document.getElementById("swipe-room-question").innerText = this.state.roomData.question;
    this.renderCurrentSwipeCard();
  },

  renderCurrentSwipeCard() {
    const options = this.state.roomData.options;
    const index = this.state.currentSwipeIndex;

    if (index >= options.length) {
      this.showToast("All votes cast!", "success");
      this.navigate("results");
      return;
    }

    document.getElementById("swipe-progress-pill").innerText = `Option ${index + 1} of ${options.length}`;
    document.getElementById("swipe-option-title").innerText = options[index].option_text;
  },

  async submitSwipeVote(isLike) {
    const option = this.state.roomData.options[this.state.currentSwipeIndex];
    try {
      const response = await fetch(`${API_BASE_URL}/rooms/${this.state.roomCode}/vote/swipe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          voter_id: this.state.voterId,
          option_id: option.option_id,
          is_like: isLike,
        }),
      });

      if (!response.ok) throw new Error("Vote could not be registered.");

      this.state.currentSwipeIndex++;
      this.renderCurrentSwipeCard();
    } catch (err) {
      this.showToast(err.message, "error");
    }
  },

  // --- QUADRATIC VOTING ENGINE (16 Credits Budget) ---
  setupQuadraticDeck() {
    document.getElementById("quad-room-question").innerText = this.state.roomData.question;
    const list = document.getElementById("quadratic-options-list");
    list.innerHTML = "";

    this.state.quadraticAllocations = {};
    this.state.roomData.options.forEach((opt) => {
      this.state.quadraticAllocations[opt.option_id] = 0;
    });

    this.state.roomData.options.forEach((opt) => {
      const item = document.createElement("div");
      item.className = "p-3.5 rounded-2xl bg-surface border border-charcoal-200 shadow-card flex items-center justify-between gap-3";
      item.innerHTML = `
        <div class="flex-1 min-w-0">
          <h4 class="font-bold text-sm text-charcoal-900 truncate">${opt.option_text}</h4>
          <p class="text-[11px] font-mono text-sage-600 font-semibold" id="weight-display-${opt.option_id}">Impact: +0.0 pts</p>
        </div>
        
        <div class="flex items-center gap-2 bg-canvas px-2 py-1.5 rounded-xl border border-charcoal-100">
          <button type="button" onclick="app.adjustQuadraticCredit(${opt.option_id}, -1)" class="w-7 h-7 rounded-lg bg-surface border border-charcoal-200 text-charcoal-700 hover:bg-charcoal-50 font-bold flex items-center justify-center transition active:scale-95 shadow-sm">
            <i class="fa-solid fa-minus text-xs"></i>
          </button>
          
          <span id="credit-count-${opt.option_id}" class="w-6 text-center font-mono font-bold text-sm text-charcoal-900">0</span>
          
          <button type="button" onclick="app.adjustQuadraticCredit(${opt.option_id}, 1)" class="w-7 h-7 rounded-lg bg-sage-600 text-white hover:bg-sage-700 font-bold flex items-center justify-center transition active:scale-95 shadow-sm">
            <i class="fa-solid fa-plus text-xs"></i>
          </button>
        </div>
      `;
      list.appendChild(item);
    });

    this.updateQuadraticBudgetUI();
  },

  adjustQuadraticCredit(optionId, delta) {
    const current = this.state.quadraticAllocations[optionId] || 0;
    const totalSpent = Object.values(this.state.quadraticAllocations).reduce((a, b) => a + b, 0);

    if (delta > 0 && totalSpent >= this.state.maxCredits) {
      this.showToast("Total budget exhausted (16 credits max).", "info");
      return;
    }

    const target = current + delta;
    if (target < 0 || target > 16) return;

    this.state.quadraticAllocations[optionId] = target;

    // Update individual option card
    document.getElementById(`credit-count-${optionId}`).innerText = target;
    const effectiveImpact = Math.sqrt(target).toFixed(1);
    document.getElementById(`weight-display-${optionId}`).innerText = `Impact: +${effectiveImpact} pts`;

    this.updateQuadraticBudgetUI();
  },

  updateQuadraticBudgetUI() {
    const totalSpent = Object.values(this.state.quadraticAllocations).reduce((a, b) => a + b, 0);
    const remaining = this.state.maxCredits - totalSpent;

    document.getElementById("quad-credits-remaining").innerText = remaining;
    const percentage = (remaining / this.state.maxCredits) * 100;
    document.getElementById("quad-budget-bar").style.width = `${percentage}%`;
  },

  async submitAllQuadraticVotes() {
    const submitBtn = document.getElementById("btn-submit-quad");
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Submitting...`;

    try {
      const entries = Object.entries(this.state.quadraticAllocations);
      for (const [optionId, credits] of entries) {
        if (credits > 0) {
          const res = await fetch(`${API_BASE_URL}/rooms/${this.state.roomCode}/vote/quadratic`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              voter_id: this.state.voterId,
              option_id: parseInt(optionId),
              raw_credits: credits,
            }),
          });
          if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "Failed to record vote.");
          }
        }
      }

      this.showToast("All quadratic votes registered!", "success");
      this.navigate("results");
    } catch (err) {
      this.showToast(err.message, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = `<span>Submit All Votes</span><i class="fa-solid fa-check text-xs"></i>`;
    }
  },

  // --- LIVE RESULTS & LEADERBOARD POLLING ENGINE ---
  startResultsPolling() {
    this.fetchResults();
    if (!this.state.pollingInterval) {
      this.state.pollingInterval = setInterval(() => this.fetchResults(), 2000);
    }
  },

  stopResultsPolling() {
    if (this.state.pollingInterval) {
      clearInterval(this.state.pollingInterval);
      this.state.pollingInterval = null;
    }
  },

  async fetchResults() {
    if (!this.state.roomCode) return;

    try {
      const response = await fetch(`${API_BASE_URL}/rooms/${this.state.roomCode}/results`);
      if (!response.ok) return;

      const data = await response.json();
      this.renderResultsUI(data);
    } catch (err) {
      console.warn("Polling error:", err);
    }
  },

  renderResultsUI(data) {
    document.getElementById("results-room-question").innerText = data.question;
    document.getElementById("results-participant-count").innerText = `${data.total_participants} Voter${data.total_participants === 1 ? "" : "s"}`;

    // Update Winner Podium
    const winnerTitle = document.getElementById("winner-title");
    const winnerScore = document.getElementById("winner-score");

    if (data.winner) {
      winnerTitle.innerText = data.winner.option_text;
      winnerScore.innerText = `Winning Score: ${data.winner.score.toFixed(1)} pts`;
    } else {
      winnerTitle.innerText = "Awaiting First Vote";
      winnerScore.innerText = "Score: 0.0 pts";
    }

    // Leaderboard Bars
    const list = document.getElementById("leaderboard-items");
    list.innerHTML = "";

    const highestScore = data.leaderboard.length > 0 && data.leaderboard[0].score > 0 ? data.leaderboard[0].score : 1;

    data.leaderboard.forEach((item, index) => {
      const percentage = Math.round((item.score / highestScore) * 100);
      const isWinner = index === 0 && item.score > 0;

      const row = document.createElement("div");
      row.className = "p-3 rounded-2xl bg-surface border border-charcoal-200 shadow-soft space-y-1.5";
      row.innerHTML = `
        <div class="flex items-center justify-between text-xs font-bold">
          <span class="flex items-center gap-2 text-charcoal-900">
            <span class="text-charcoal-400 font-mono text-[11px]">#${index + 1}</span>
            <span class="truncate max-w-[180px]">${item.option_text}</span>
            ${isWinner ? '<i class="fa-solid fa-crown text-gold-500 text-xs"></i>' : ""}
          </span>
          <span class="font-mono text-sage-700">${item.score.toFixed(1)} pts</span>
        </div>
        
        <div class="w-full h-1.5 bg-charcoal-100 rounded-full overflow-hidden">
          <div class="h-full ${isWinner ? "bg-sage-600" : "bg-charcoal-500"} transition-all duration-500 rounded-full" style="width: ${item.score > 0 ? percentage : 0}%"></div>
        </div>
      `;
      list.appendChild(row);
    });
  },

  // --- SHARE MODAL & QR CODE ---
  openShareModal() {
    const modal = document.getElementById("share-modal");
    const codeDisplay = document.getElementById("share-code-display");
    const qrContainer = document.getElementById("qrcode-container");

    codeDisplay.innerText = this.state.roomCode;
    qrContainer.innerHTML = "";

    const shareUrl = `${window.location.origin}${window.location.pathname}?room=${this.state.roomCode}`;

    new QRCode(qrContainer, {
      text: shareUrl,
      width: 140,
      height: 140,
      colorDark: "#1F2421",
      colorLight: "#FFFFFF",
      correctLevel: QRCode.CorrectLevel.M,
    });

    modal.classList.remove("hidden");
  },

  closeShareModal() {
    document.getElementById("share-modal").classList.add("hidden");
  },

  async copyInviteLink() {
    const shareUrl = `${window.location.origin}${window.location.pathname}?room=${this.state.roomCode}`;
    try {
      await navigator.clipboard.writeText(shareUrl);
      this.showToast("Invite link copied to clipboard!", "success");
    } catch {
      this.showToast(`Copy manually: ${shareUrl}`, "info");
    }
  },
};

// Bootstrap App on DOM Load
document.addEventListener("DOMContentLoaded", () => app.init());