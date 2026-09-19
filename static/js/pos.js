// ============================================================
// மாணிக்கம் ஸ்டோர்ஸ் POS — Shared JS Utilities
// Tamil Nadu · GST Ready · ₹ Indian Rupee
// ============================================================

// Indian Rupee formatter (₹1,00,000 Indian number system)
function fmt(amount) {
  if (amount == null || isNaN(amount)) return '₹0.00';
  return '₹' + Number(amount).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

// [P0-8] HTML-escape server-supplied data before ANY innerHTML interpolation.
// Escapes & < > " ' — every template must route dynamic text through this.
function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// [P0-8] Escape for building a JS string LITERAL (e.g. inside an inline
// onclick). This is NOT HTML escaping — when the literal sits inside an HTML
// attribute, apply escapeHtml() on top: escapeHtml(escJsString(value)).
// (Renamed from the misleading escHtml, which only escaped apostrophes.)
function escJsString(s) {
  return String(s ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\"');
}

// Live Clock (IST)
function updateClock() {
  const el = document.getElementById('timeDisplay');
  if (!el) return;
  const now = new Date();
  el.textContent = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
updateClock();
setInterval(updateClock, 1000);

// Sidebar State Handler
const sidebar = document.getElementById('sidebar');
const mainContent = document.getElementById('mainContent');

function closeMobileSidebar() {
  if (sidebar && sidebar.classList.contains('open')) {
    sidebar.classList.remove('open');
  }
  const backdrop = document.getElementById('modalBackdrop');
  if (backdrop && !document.querySelector('.modal.open')) {
    backdrop.classList.remove('open');
    document.body.style.overflow = '';
  }
}

function openMobileSidebar() {
  if (sidebar) {
    sidebar.classList.add('open');
    const backdrop = document.getElementById('modalBackdrop');
    if (backdrop) {
      backdrop.classList.add('open');
      document.body.style.overflow = 'hidden';
    }
  }
}

function toggleSidebarState() {
  if (window.innerWidth <= 768) {
    if (sidebar && sidebar.classList.contains('open')) {
      closeMobileSidebar();
    } else {
      openMobileSidebar();
    }
  } else {
    ensureDesktopSidebar();
  }
}

function ensureDesktopSidebar() {
  if (window.innerWidth > 768) {
    if (sidebar) {
      sidebar.classList.remove('collapsed');
      sidebar.classList.remove('open');
    }
    if (mainContent) mainContent.classList.remove('sidebar-collapsed');
    const backdrop = document.getElementById('modalBackdrop');
    if (backdrop && !document.querySelector('.modal.open')) {
      backdrop.classList.remove('open');
      document.body.style.overflow = '';
    }
  }
}

const sidebarToggle = document.getElementById('sidebarToggle');
if (sidebarToggle) {
  sidebarToggle.addEventListener('click', (e) => {
    e.preventDefault();
    closeMobileSidebar();
  });
}

const topbarToggleBtn = document.getElementById('topbarToggleBtn');
if (topbarToggleBtn) {
  topbarToggleBtn.addEventListener('click', (e) => {
    e.preventDefault();
    toggleSidebarState();
  });
}

// Close mobile sidebar when clicking any navigation link
document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => {
  item.addEventListener('click', () => {
    if (window.innerWidth <= 768) {
      closeMobileSidebar();
    }
  });
});

// Close mobile sidebar or modal on backdrop click
const modalBackdropEl = document.getElementById('modalBackdrop');
if (modalBackdropEl) {
  modalBackdropEl.addEventListener('click', () => {
    closeMobileSidebar();
    document.querySelectorAll('.modal.open').forEach(m => closeModal(m.id));
  });
}

// Escape key to close mobile sidebar or modal
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeMobileSidebar();
    document.querySelectorAll('.modal.open').forEach(m => closeModal(m.id));
  }
});

window.addEventListener('resize', ensureDesktopSidebar);
ensureDesktopSidebar();

// Toast notification
function showToast(message, type = 'info', duration = 3000) {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast toast--${type}`;
  const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
  toast.innerHTML = `<span class="toast-icon">${icons[type] || 'ℹ️'}</span><span class="toast-msg">${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => toast.classList.add('visible'), 10);
  setTimeout(() => { toast.classList.remove('visible'); setTimeout(() => toast.remove(), 300); }, duration);
}

// Language and Theme Toggle Implementation
let currentLanguage = localStorage.getItem('vgl_lang') || 'ta';

function getLangTxt(en, ta) {
  return currentLanguage === 'en' ? en : ta;
}

function payLabel(method) {
  const labels = {
    cash: getLangTxt('💵 Cash', '💵 ரொக்கம்'),
    upi: getLangTxt('📱 UPI', '📱 UPI'),
    card: getLangTxt('💳 Card', '💳 அட்டை'),
    mobile: getLangTxt('📱 UPI', '📱 UPI')
  };
  return labels[method] || method;
}

function toggleTheme() {
  const isLight = document.documentElement.classList.contains('theme-light');
  if (isLight) {
    document.documentElement.classList.remove('theme-light');
    localStorage.setItem('vgl_theme', 'dark');
    showThemeIcon('dark');
  } else {
    document.documentElement.classList.add('theme-light');
    localStorage.setItem('vgl_theme', 'light');
    showThemeIcon('light');
  }
}

function showThemeIcon(theme) {
  const darkIcon = document.querySelector('.theme-icon-dark');
  const lightIcon = document.querySelector('.theme-icon-light');
  if (!darkIcon || !lightIcon) return;
  if (theme === 'light') {
    darkIcon.classList.add('d-none');
    lightIcon.classList.remove('d-none');
  } else {
    darkIcon.classList.remove('d-none');
    lightIcon.classList.add('d-none');
  }
}

function toggleLanguage() {
  const isTa = document.documentElement.classList.contains('lang-ta');
  if (isTa) {
    document.documentElement.classList.remove('lang-ta');
    document.documentElement.classList.add('lang-en');
    document.documentElement.setAttribute('lang', 'en');
    localStorage.setItem('vgl_lang', 'en');
    currentLanguage = 'en';
  } else {
    document.documentElement.classList.remove('lang-en');
    document.documentElement.classList.add('lang-ta');
    document.documentElement.setAttribute('lang', 'ta');
    localStorage.setItem('vgl_lang', 'ta');
    currentLanguage = 'ta';
  }
  
  // Re-trigger dynamic render functions if present on the active page
  if (typeof loadDashboard === 'function') loadDashboard();
  if (typeof loadProducts === 'function') loadProducts();
  if (typeof loadSales === 'function') loadSales();
  if (typeof loadCustomers === 'function') loadCustomers();
  if (typeof renderCart === 'function') renderCart();
  if (typeof loadReports === 'function') loadReports();
}

// Sync UI on DOM Load
document.addEventListener('DOMContentLoaded', () => {
  const currentTheme = localStorage.getItem('vgl_theme') || 'dark';
  showThemeIcon(currentTheme);
});

