// components/pages/super-admin/super-admin.component.ts
import { Component, OnInit, OnDestroy, signal, computed, inject, AfterViewInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  SuperAdminService,
  GlobalStats, TenantStats,
  TenantResponse, TenantPayload,
  UserItem, AdminPayload,
} from '../../../services/super-admin.service';
import { Router } from '@angular/router';
import { AgentService } from '../../../services/agent.service';
import {
  Firestore,
  collection,
  query,
  orderBy,
  limit,
  getDocs,
} from '@angular/fire/firestore';
import { Subject } from 'rxjs';

type View = 'dashboard' | 'tenants' | 'create-tenant' | 'edit-tenant'
           | 'admins' | 'create-admin' | 'users';

interface Toast { type: 'ok' | 'err'; msg: string; }

export interface ChatbotStats {
  totalSessions: number;
  totalMessages: number;
  avgMessagesPerSession: number;
  anonymousRate: number;
  topCategories: { name: string; count: number; percentage: number }[];
  generalInfoStats: {
    agencyDetailsViews: number;
    capabilitiesViews: number;
    handoffRequests: number;
    feedbackReceived: number;
    avgFeedbackScore: number;
  };
  weeklyConversations: { day: string; count: number }[];
  recentSessions: {
    session_id: string;
    tenant_id: string;
    title: string;
    message_count: number;
    updated_at: any;
    is_anonymous: boolean;
  }[];
}

@Component({
  selector: 'app-super-admin',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './super-admin.component.html',
  styleUrl: './super-admin.component.scss',
})
export class SuperAdminComponent implements OnInit, OnDestroy, AfterViewInit {
  private router       = inject(Router);
  private agentService = inject(AgentService);
  private sa           = inject(SuperAdminService);
  private firestore    = inject(Firestore);
  private destroy$     = new Subject<void>();

  view        = signal<View>('dashboard');
  loading     = signal(false);
  toast       = signal<Toast | null>(null);
  sidebarOpen = signal(false);

  stats        = signal<GlobalStats | null>(null);
  chatbotStats = signal<ChatbotStats | null>(null);
  statsLoading = signal(false);

  tenants    = signal<TenantResponse[]>([]);
  tenantForm: TenantPayload = this.blankTenant();
  editingId  = signal<string | null>(null);
  saving     = false;
  openKebab  = signal<string | null>(null);

  selectedTenant = signal<TenantResponse | null>(null);
  admins         = signal<UserItem[]>([]);
  adminForm: AdminPayload & { confirmPassword: string } = this.blankAdmin();
  savingAdmin    = false;

  allUsers      = signal<UserItem[]>([]);
  userSearch    = signal('');
  filteredUsers = computed(() => {
    const q = this.userSearch().toLowerCase().trim();
    if (!q) return this.allUsers();
    return this.allUsers().filter(u =>
      u.email.toLowerCase().includes(q) ||
      (u.first_name + ' ' + u.last_name).toLowerCase().includes(q) ||
      u.role.includes(q)
    );
  });

  private readonly ACCENT_PALETTE = [
    { accent: '#2563eb', light: '#dbeafe', dark: '#1e40af' },
    { accent: '#7c3aed', light: '#ede9fe', dark: '#5b21b6' },
    { accent: '#059669', light: '#d1fae5', dark: '#065f46' },
    { accent: '#d97706', light: '#fef3c7', dark: '#92400e' },
    { accent: '#dc2626', light: '#fee2e2', dark: '#991b1b' },
    { accent: '#0891b2', light: '#cffafe', dark: '#164e63' },
  ];

  ngOnInit()        { this.goTo('dashboard'); }
  ngAfterViewInit() {}
  ngOnDestroy()     { this.destroy$.next(); this.destroy$.complete(); }

  goTo(v: View, tenant?: TenantResponse) {
    this.view.set(v);
    this.sidebarOpen.set(false);

    if (v === 'dashboard')     { this.loadStats(); }
    if (v === 'tenants')        this.loadTenants();
    if (v === 'create-tenant')  this.tenantForm = this.blankTenant();
if (v === 'edit-tenant' && tenant) {
  this.editingId.set(tenant.tenant_id);
  this.tenantForm = {
    name:                tenant.name,
    logo_url:            tenant.logo_url ?? '',
    currency:            tenant.currency,
    margin_percentage:   tenant.margin_percentage as number,
    tone:                tenant.tone,
    facebook_page_id:    tenant.facebook_page_id ?? '',
    facebook_page_token: tenant.facebook_page_token ?? '',  
  };
}
    if (v === 'admins' && tenant) {
      this.selectedTenant.set(tenant);
      this.loadAdmins(tenant.tenant_id);
    }
    if (v === 'create-admin') this.adminForm = this.blankAdmin();
    if (v === 'users')        this.loadUsers();
  }

  goToChat() {
    const sessionId = this.agentService.getSessionId();
    this.router.navigate(['/chat'], {
      queryParams: sessionId ? { session_id: sessionId } : {}
    });
  }

  toggleSidebar() { this.sidebarOpen.update(v => !v); }

  toggleKebab(tenantId: string) {
    this.openKebab.update(v => v === tenantId ? null : tenantId);
  }

  closeKebab() { this.openKebab.set(null); }

  loadStats() {
    this.loading.set(true);
    this.sa.getStats().subscribe({
      next: s => {
        this.stats.set(s);
        this.loading.set(false);
        this.loadChatbotStats();
      },
      error: () => {
        this.loading.set(false);
        this.notify('err', 'Erreur chargement stats');
        this.loadChatbotStats();
      },
    });
  }

  refreshDashboard() { this.loadStats(); }


  async loadChatbotStats() {
    this.statsLoading.set(true);
    try {
      const tenantList = this.tenants().length
        ? this.tenants()
        : await this.fetchTenantsList();

      console.log(`[SuperAdmin] Chargement stats pour ${tenantList.length} tenant(s):`,
        tenantList.map(t => t.tenant_id));

      if (!tenantList.length) {
        console.warn('[SuperAdmin] Aucun tenant trouvé — vérifiez les permissions API.');
        this.loadDemoStats();
        return;
      }

      let allSessions: any[] = [];

      for (const t of tenantList) {
        try {
          const metaRef = collection(
            this.firestore,
            'conversations', t.tenant_id, '_sessions_meta'
          );
          const snap = await getDocs(
            query(metaRef, orderBy('updated_at', 'desc'), limit(200))
          );
          console.log(`[SuperAdmin] Tenant ${t.tenant_id} → ${snap.docs.length} session(s) trouvée(s)`);
          snap.docs.forEach(d => {
            allSessions.push({
              ...d.data(),
              _tenant_id:  t.tenant_id,
              _session_id: d.id,
            });
          });
        } catch (e) {

          console.warn(`[SuperAdmin] Pas de sessions pour tenant ${t.tenant_id}:`, e);
        }
      }

      console.log(`[SuperAdmin] Total sessions agrégées: ${allSessions.length}`);

      const totalSessions   = allSessions.length;
      const totalMessages   = allSessions.reduce(
        (s: number, sess: any) => s + (sess.message_count ?? 0), 0
      );
      const avgMsgPerSess   = totalSessions
        ? Math.round((totalMessages / totalSessions) * 10) / 10
        : 0;
      const anonymousCount  = allSessions.filter((s: any) => s.is_anonymous).length;
      const anonymousRate   = totalSessions
        ? Math.round((anonymousCount / totalSessions) * 100)
        : 0;

      const dayLabels   = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
      const weeklyMap: Record<string, number> = {};
      dayLabels.forEach(d => (weeklyMap[d] = 0));

      const sevenDaysAgo = new Date();
      sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);

      allSessions.forEach((sess: any) => {
        const raw = sess.created_at;
        if (!raw) return;
        const date: Date = raw.toDate ? raw.toDate() : new Date(raw);
        if (date >= sevenDaysAgo) {
          const label = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam'][date.getDay()];
          weeklyMap[label] = (weeklyMap[label] ?? 0) + 1;
        }
      });

      const weeklyConversations = dayLabels.map(day => ({
        day,
        count: weeklyMap[day] ?? 0,
      }));

      const categoryKeywords: Record<string, string[]> = {
        'Reservation':        ['réserv', 'book', 'hotel', 'vol ', 'flight', 'car', 'voiture', 'tour', 'excursion', 'ticket'],
        'Destination Info':   ['météo', 'weather', 'culture', 'gastro', 'internet', 'saison', 'season', 'destination'],
        'Requirements':       ['visa', 'passeport', 'passport', 'vaccin', 'health', 'bagage', 'luggage', 'accessib'],
        'Booking Changes':    ['annul', 'modif', 'change', 'cancel', 'remboursement', 'refund'],
        'Security':           ['urgence', 'emergency', 'fraude', 'fraud', 'perdu', 'lost', 'assurance', 'insurance', 'volé'],
        'Claims':             ['récla', 'claim', 'retard', 'delay', 'surbook', 'overbooking', 'endommagé', 'damage', 'plainte'],
        'Logistics':          ['check-in', 'checkin', 'aéroport', 'airport', 'late check', 'embarquement'],
        'Special Requests':   ['enfant', 'child', 'animal', 'pet', 'bébé', 'baby'],
        'General Information':['agence', 'contact', 'avis', 'feedback', 'review', 'note', 'chatbot', 'capable', 'aide', 'humain'],
      };

      const categoryMap: Record<string, number> = {};
      Object.keys(categoryKeywords).forEach(k => (categoryMap[k] = 0));

      allSessions.forEach((sess: any) => {
        const text = (
          (sess.title ?? '') + ' ' + (sess.last_message_preview ?? '')
        ).toLowerCase();

        let matched = false;
        for (const [cat, keywords] of Object.entries(categoryKeywords)) {
          if (keywords.some(kw => text.includes(kw))) {
            categoryMap[cat]++;
            matched = true;
            break;
          }
        }
        if (!matched) categoryMap['General Information']++;
      });

      const totalCat = Object.values(categoryMap).reduce((s, v) => s + v, 0) || 1;
      const topCategories = Object.entries(categoryMap)
        .map(([name, count]) => ({
          name,
          count,
          percentage: Math.round((count / totalCat) * 100),
        }))
        .sort((a, b) => b.count - a.count);

      const genInfoSessions = allSessions.filter((sess: any) => {
        const text = (
          (sess.title ?? '') + ' ' + (sess.last_message_preview ?? '')
        ).toLowerCase();
        return categoryKeywords['General Information'].some(kw => text.includes(kw));
      });

      const agencyDetailsSessions = genInfoSessions.filter((sess: any) => {
        const text = ((sess.title ?? '') + ' ' + (sess.last_message_preview ?? '')).toLowerCase();
        return text.includes('agence') || text.includes('contact') || text.includes('adresse') || text.includes('horaire');
      });

      const capabilitiesSessions = genInfoSessions.filter((sess: any) => {
        const text = ((sess.title ?? '') + ' ' + (sess.last_message_preview ?? '')).toLowerCase();
        return text.includes('chatbot') || text.includes('capable') || text.includes('aide') || text.includes('faire');
      });

      const feedbackSessions = genInfoSessions.filter((sess: any) => {
        const text = ((sess.title ?? '') + ' ' + (sess.last_message_preview ?? '')).toLowerCase();
        return text.includes('avis') || text.includes('feedback') || text.includes('review') || text.includes('note');
      });

      const handoffSessions = allSessions.filter((sess: any) => {
        const text = ((sess.title ?? '') + ' ' + (sess.last_message_preview ?? '')).toLowerCase();
        return text.includes('humain') || text.includes('agent') || text.includes('conseiller') || text.includes('parler à');
      });

      const recentSessions = [...allSessions]
        .sort((a: any, b: any) => {
          const da  = a.updated_at?.toDate?.() ?? new Date(a.updated_at ?? 0);
          const db_ = b.updated_at?.toDate?.() ?? new Date(b.updated_at ?? 0);
          return db_.getTime() - da.getTime();
        })
        .slice(0, 10)
        .map((sess: any) => ({
          session_id:    sess._session_id,
          tenant_id:     sess._tenant_id,
          title:         sess.title ?? 'Sans titre',
          message_count: sess.message_count ?? 0,
          updated_at:    sess.updated_at,
          is_anonymous:  sess.is_anonymous ?? false,
        }));

      this.chatbotStats.set({
        totalSessions,
        totalMessages,
        avgMessagesPerSession: avgMsgPerSess,
        anonymousRate,
        topCategories,
        generalInfoStats: {
          agencyDetailsViews: agencyDetailsSessions.length,
          capabilitiesViews:  capabilitiesSessions.length,
          handoffRequests:    handoffSessions.length,
          feedbackReceived:   feedbackSessions.length,
          avgFeedbackScore: this.stats()?.avg_rating ?? 0,
        },
        weeklyConversations,
        recentSessions,
      });

      setTimeout(() => this.renderCharts(), 350);

    } catch (err) {
      console.error('[SuperAdmin] Firebase stats error:', err);
      this.loadDemoStats();
    } finally {
      this.statsLoading.set(false);
    }
  }

  private fetchTenantsList(): Promise<TenantResponse[]> {
    return new Promise(resolve => {
      this.sa.listTenants().subscribe({
        next: list => {
          this.tenants.set(list); 
          resolve(list);
        },
        error: (e) => {
          console.error('[SuperAdmin] Impossible de charger la liste des tenants:', e);
          resolve([]);
        },
      });
    });
  }

  loadDemoStats() {
    this.chatbotStats.set({
      totalSessions:         127,
      totalMessages:         843,
      avgMessagesPerSession: 6.6,
      anonymousRate:         34,
      topCategories: [
        { name: 'Reservation',          count: 38, percentage: 30 },
        { name: 'Destination Info',      count: 25, percentage: 20 },
        { name: 'General Information',   count: 20, percentage: 16 },
        { name: 'Requirements',          count: 15, percentage: 12 },
        { name: 'Booking Changes',       count: 12, percentage:  9 },
        { name: 'Security',              count:  8, percentage:  6 },
        { name: 'Claims',                count:  5, percentage:  4 },
        { name: 'Logistics',             count:  3, percentage:  2 },
        { name: 'Special Requests',      count:  1, percentage:  1 },
      ],
      generalInfoStats: {
        agencyDetailsViews: 8,
        capabilitiesViews:  6,
        handoffRequests:    4,
        feedbackReceived:   6,
        avgFeedbackScore:   0,
      },
      weeklyConversations: [
        { day: 'Lun', count: 18 },
        { day: 'Mar', count: 24 },
        { day: 'Mer', count: 21 },
        { day: 'Jeu', count: 30 },
        { day: 'Ven', count: 27 },
        { day: 'Sam', count: 14 },
        { day: 'Dim', count:  9 },
      ],
      recentSessions: [],
    });
    setTimeout(() => this.renderCharts(), 350);
  }

  renderCharts() {
    this.renderWeeklyChart();
    this.renderCategoryChart();
    this.renderDonutChart();
  }

  renderWeeklyChart() {
    const canvas = document.getElementById('weeklyChart') as HTMLCanvasElement;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const stats = this.chatbotStats();
    if (!stats) return;

    const data   = stats.weeklyConversations;
    const maxVal = Math.max(...data.map(d => d.count), 1);
    const dpr    = window.devicePixelRatio || 1;
    const w      = canvas.offsetWidth || 600;
    const h      = 160;
    canvas.width  = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const padL = 10, padR = 10, padT = 10, padB = 30;
    const chartW = w - padL - padR;
    const chartH = h - padT - padB;
    const gap    = chartW / data.length;
    const barW   = gap * 0.55;

    ctx.strokeStyle = 'rgba(59,110,248,0.08)';
    ctx.lineWidth   = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padT + (chartH / 4) * i;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    }

    data.forEach((d, i) => {
      const x   = padL + i * gap + (gap - barW) / 2;
      const bH  = (d.count / maxVal) * chartH;
      const y   = padT + chartH - bH;
      const r   = 4;

      const grad = ctx.createLinearGradient(0, y, 0, y + bH);
      grad.addColorStop(0, '#3b6ef8');
      grad.addColorStop(1, 'rgba(59,110,248,0.3)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.lineTo(x + barW - r, y);
      ctx.quadraticCurveTo(x + barW, y, x + barW, y + r);
      ctx.lineTo(x + barW, y + bH);
      ctx.lineTo(x, y + bH);
      ctx.lineTo(x, y + r);
      ctx.quadraticCurveTo(x, y, x + r, y);
      ctx.closePath();
      ctx.fill();

      ctx.fillStyle = '#3b6ef8';
      ctx.font      = 'bold 10px DM Sans, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(String(d.count), x + barW / 2, y - 4);

      ctx.fillStyle = '#9ca3af';
      ctx.font      = '11px DM Sans, sans-serif';
      ctx.fillText(d.day, x + barW / 2, h - padB + 18);
    });
  }

  renderCategoryChart() {
    const canvas = document.getElementById('categoryChart') as HTMLCanvasElement;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const stats = this.chatbotStats();
    if (!stats) return;

    const data   = stats.topCategories.slice(0, 7);
    const maxVal = Math.max(...data.map(d => d.count), 1);
    const dpr    = window.devicePixelRatio || 1;
    const w      = canvas.offsetWidth || 400;
    const h      = 180;
    canvas.width  = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const padL   = 130, padR = 60, padT = 8, padB = 8;
    const chartW = w - padL - padR;
    const rowH   = (h - padT - padB) / data.length;
    const barH   = rowH * 0.55;
    const colors = ['#3b6ef8','#8b5cf6','#06b6d4','#10b981','#f59e0b','#ef4444','#ec4899'];

    data.forEach((d, i) => {
      const y    = padT + i * rowH + (rowH - barH) / 2;
      const barW = (d.count / maxVal) * chartW;
      const label = d.name.length > 16 ? d.name.slice(0, 16) + '…' : d.name;

      ctx.fillStyle = '#4b5563';
      ctx.font      = '11px DM Sans, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(label, padL - 8, y + barH / 2 + 4);

      ctx.fillStyle = 'rgba(59,110,248,0.06)';
      ctx.beginPath();
      (ctx as any).roundRect(padL, y, chartW, barH, 3);
      ctx.fill();

      ctx.fillStyle   = colors[i % colors.length];
      ctx.globalAlpha = 0.85;
      ctx.beginPath();
      (ctx as any).roundRect(padL, y, barW, barH, 3);
      ctx.fill();
      ctx.globalAlpha = 1;

      ctx.fillStyle = '#0f1623';
      ctx.font      = 'bold 11px DM Sans, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(`${d.count}`, padL + barW + 6, y + barH / 2 + 4);
    });
  }

  renderDonutChart() {
    const canvas = document.getElementById('donutChart') as HTMLCanvasElement;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const stats = this.chatbotStats();
    if (!stats) return;

    const dpr    = window.devicePixelRatio || 1;
    const size   = Math.min(canvas.offsetWidth, 180) || 180;
    canvas.width  = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, size, size);

    const cx     = size / 2;
    const cy     = size / 2;
    const outerR = size * 0.42;
    const innerR = size * 0.26;
    const colors = ['#3b6ef8','#8b5cf6','#06b6d4','#10b981','#f59e0b','#ef4444','#ec4899','#84cc16','#f97316'];
    const data   = stats.topCategories;
    const total  = data.reduce((s, d) => s + d.count, 0) || 1;

    let startAngle = -Math.PI / 2;
    data.forEach((d, i) => {
      const angle = (d.count / total) * 2 * Math.PI;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, outerR, startAngle, startAngle + angle);
      ctx.closePath();
      ctx.fillStyle   = colors[i % colors.length];
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth   = 2;
      ctx.stroke();
      startAngle += angle;
    });

    ctx.beginPath();
    ctx.arc(cx, cy, innerR, 0, Math.PI * 2);
    ctx.fillStyle = '#ffffff';
    ctx.fill();

    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle    = '#0f1623';
    ctx.font         = `bold ${Math.round(size * 0.14)}px DM Sans, sans-serif`;
    ctx.fillText(total.toLocaleString(), cx, cy - 6);
    ctx.fillStyle = '#9ca3af';
    ctx.font      = `${Math.round(size * 0.08)}px DM Sans, sans-serif`;
    ctx.fillText('sessions', cx, cy + 12);
  }

  loadTenants() {
    this.loading.set(true);
    this.sa.listTenants().subscribe({
      next:  list => { this.tenants.set(list); this.loading.set(false); },
      error: () => { this.loading.set(false); this.notify('err', 'Erreur chargement tenants'); },
    });
  }

  submitTenant() {
    if (!this.tenantForm.name.trim()) return;
    this.saving   = true;
    const eid = this.editingId();
    const req = eid
      ? this.sa.updateTenant(eid, this.tenantForm)
      : this.sa.createTenant(this.tenantForm);

    req.subscribe({
      next: (t) => {
        this.saving = false;
        this.notify('ok', eid
          ? `"${t.name}" mis à jour`
          : `"${t.name}" créé — URL: /chat?tenant=${t.tenant_id}`);
        this.loadStats();
        this.goTo('tenants');
      },
      error: (e) => {
        this.saving = false;
        this.notify('err', e?.error?.detail ?? 'Erreur enregistrement');
      },
    });
  }

  confirmDeleteTenant(t: TenantResponse) {
    if (!confirm(`Supprimer l'agence "${t.name}" ? Cette action est irréversible.`)) return;
    this.sa.deleteTenant(t.tenant_id).subscribe({
      next:  () => { this.notify('ok', `"${t.name}" supprimé`); this.loadTenants(); this.loadStats(); },
      error: () => this.notify('err', 'Erreur suppression'),
    });
  }


  loadAdmins(tenantId: string) {
    this.loading.set(true);
    this.sa.listAdmins(tenantId).subscribe({
      next:  list => { this.admins.set(list); this.loading.set(false); },
      error: () => { this.loading.set(false); this.notify('err', 'Erreur chargement admins'); },
    });
  }

  submitAdmin() {
    const tid = this.selectedTenant()?.tenant_id;
    if (!tid) return;
    if (this.adminForm.password !== this.adminForm.confirmPassword) {
      return void this.notify('err', 'Les mots de passe ne correspondent pas');
    }
    if (this.adminForm.password.length < 8) {
      return void this.notify('err', 'Mot de passe : 8 caractères minimum');
    }
    this.savingAdmin = true;
    const { confirmPassword, ...payload } = this.adminForm;
    this.sa.createAdmin(tid, payload).subscribe({
      next: (u) => {
        this.savingAdmin = false;
        this.notify('ok', `Admin ${u.email} créé`);
        this.goTo('admins', this.selectedTenant()!);
      },
      error: (e) => {
        this.savingAdmin = false;
        this.notify('err', e?.error?.detail ?? 'Erreur création admin');
      },
    });
  }

  confirmDeleteAdmin(admin: UserItem) {
    const tid = this.selectedTenant()?.tenant_id;
    if (!tid) return;
    if (!confirm(`Supprimer ${admin.email} ?`)) return;
    this.sa.deleteAdmin(tid, admin.user_id).subscribe({
      next:  () => { this.notify('ok', 'Admin supprimé'); this.loadAdmins(tid); },
      error: () => this.notify('err', 'Erreur suppression'),
    });
  }

  loadUsers() {
    this.loading.set(true);
    this.sa.listAllUsers().subscribe({
      next:  list => { this.allUsers.set(list); this.loading.set(false); },
      error: () => { this.loading.set(false); this.notify('err', 'Erreur chargement users'); },
    });
  }

  private getTenantPalette(tenantId: string) {
    const hash = tenantId.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0);
    return this.ACCENT_PALETTE[hash % this.ACCENT_PALETTE.length];
  }

  getTenantColor(tenantId: string):      string { return this.getTenantPalette(tenantId).accent; }
  getTenantColorLight(tenantId: string): string { return this.getTenantPalette(tenantId).light;  }
  getTenantColorDark(tenantId: string):  string { return this.getTenantPalette(tenantId).dark;   }

  getAdminCount(t: TenantResponse): number {
    const s = this.stats()?.tenants?.find((x: TenantStats) => x.tenant_id === t.tenant_id);
    return s?.admin_count ?? 0;
  }

  getClientCount(t: TenantResponse): number {
    const s = this.stats()?.tenants?.find((x: TenantStats) => x.tenant_id === t.tenant_id);
    return s?.client_count ?? 0;
  }

  notify(type: 'ok' | 'err', msg: string) {
    this.toast.set({ type, msg });
    setTimeout(() => this.toast.set(null), 3800);
  }

  initials(name: string) {
    return name.trim().split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2) || '?';
  }

  tenantInitials(t: TenantResponse | TenantStats) { return this.initials(t.name); }

  roleBadge(role: string) {
    const map: Record<string, string> = {
      super_admin: 'badge-sa',
      admin:       'badge-admin',
      sub_admin:   'badge-sub',
      client:      'badge-client',
      visitor:     'badge-visitor',
    };
    return map[role] ?? 'badge-client';
  }

  fmtDate(d?: string) {
    if (!d) return '—';
    return new Date(d).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  fmtTimestamp(ts: any): string {
    if (!ts) return '—';
    const date = ts.toDate ? ts.toDate() : new Date(ts);
    return date.toLocaleDateString('fr-FR', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
    });
  }

  tenantUrl(id: string) { return `${window.location.origin}/chat?tenant=${id}`; }

  copyUrl(id: string) {
    navigator.clipboard.writeText(this.tenantUrl(id)).then(
      () => this.notify('ok', 'URL copiée !'),
      () => this.notify('err', 'Impossible de copier')
    );
  }

  getStarArray(rating: number): { full: boolean; half: boolean }[] {
    return Array.from({ length: 5 }, (_, i) => ({
      full: i + 1 <= Math.floor(rating),
      half: i + 1 === Math.ceil(rating) && rating % 1 >= 0.5,
    }));
  }

  getCategoryColor(index: number): string {
    const colors = ['#3b6ef8','#8b5cf6','#06b6d4','#10b981','#f59e0b','#ef4444','#ec4899','#84cc16','#f97316'];
    return colors[index % colors.length];
  }

blankTenant(): TenantPayload {
  return {
    name:                '',
    logo_url:            '',
    currency:            'EUR',
    margin_percentage:   0,
    tone:                'formal',
    facebook_page_id:    '',
    facebook_page_token: '', 
  };
}

  blankAdmin(): AdminPayload & { confirmPassword: string } {
    return { email: '', password: '', confirmPassword: '', first_name: '', last_name: '', can_create_admin: false };
  }
}