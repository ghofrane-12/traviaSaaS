import { Component, EventEmitter, Input, Output, OnInit, OnDestroy, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router,RouterModule , NavigationEnd } from '@angular/router';
import { Subscription } from 'rxjs';
import { filter } from 'rxjs/operators';
import { AgentService } from '../../services/agent.service';
import { UserService } from '../../services/user.service';
export interface Conversation {
  id: string;
  session_id: string;
  tenant_id: string;
  title: string;
  first_message: string;
  message_count: number;
  created_at: string;
  active: boolean;
}

@Component({
  selector: 'app-sidebar',
  templateUrl: './sidebar.component.html',
  standalone: true,
  imports: [CommonModule, RouterModule],
  styleUrls: ['./sidebar.component.scss']
})
export class SidebarComponent implements OnInit, OnDestroy {
  @Input() collapsed = false;
  @Input() mobileOpen = false; // CHANGED: Nouvel input
  @Output() toggle = new EventEmitter<boolean>();
  @Output() closeMobile = new EventEmitter<void>(); // CHANGED: Événement pour fermer
  @Output() conversationSelected = new EventEmitter<string>();
  @Input() tenantid = '';
  @Input() userId   = '';
  conversations: Conversation[] = [];
  todayConversations: Conversation[] = [];
  yesterdayConversations: Conversation[] = [];
  last7DaysConversations: Conversation[] = [];
  olderConversations: Conversation[] = [];
  activeMenuId: string | null = null;
  hoveredConvId: string | null = null;

  private currentTenantId: string = '';
  private routerSubscription: Subscription | null = null;
  private resizeListener: (() => void) | null = null;

  fullName: string = '';
  role: string = '';
  isMobile = false;
  isTablet = false;
  sidebarOpen = true;

  constructor(
    private agentService: AgentService,
    public userService: UserService,
    public router: Router
  ) {
    this.checkScreenSize()
    {if (this.isMobile) {
  this.collapsed = false; 
}};
  }

  ngOnInit(): void {
    this.fullName = this.userService.fullName();
    this.role = this.userService.role() || 'visiteur';
    this.loadConversations();

    // Écouter les changements de route pour fermer le menu mobile
    this.routerSubscription = this.router.events.pipe(
      filter(event => event instanceof NavigationEnd)
    ).subscribe(() => {
      if (this.isMobile && this.mobileOpen) {
        this.closeMobileSidebar();
      }
    });

    // Écouter les changements de taille d'écran
    this.resizeListener = this.onResize.bind(this);
    window.addEventListener('resize', this.resizeListener);
  }

  ngOnDestroy(): void {
    if (this.routerSubscription) {
      this.routerSubscription.unsubscribe();
    }
    if (this.resizeListener) {
      window.removeEventListener('resize', this.resizeListener);
    }
    document.body.style.overflow = '';
  }
  ngOnChanges(changes: any): void {
    if (changes.mobileOpen) {
      if (this.mobileOpen) {
        document.body.style.overflow = 'hidden';
      } else {
        document.body.style.overflow = '';
      }
    }
  }
  get tenantId(): string {
  return this.userService.resolveTenantId();
}
  private checkScreenSize(): void {
    const width = window.innerWidth;
    this.isMobile = width <= 768;
    this.isTablet = width > 768 && width <= 1024;

    if (this.isMobile) {
      this.mobileOpen = false;
      this.sidebarOpen = false;
    } else {
      this.sidebarOpen = !this.collapsed;
    }
  }

  private onResize(): void {
    this.checkScreenSize();
  }
goToHistory(): void {
  this.router.navigate(['/history']);
  
  if (this.isMobile && this.mobileOpen) {
    this.closeMobileSidebar();
  }
}
  async loadConversations(): Promise<void> {
    const user = this.userService.session();
    if (user) {
      this.currentTenantId = user.tenant_id;
      this.fullName = `${user.first_name} ${user.last_name}`;
      this.role = user.role;
    } else {
      this.currentTenantId = this.agentService.getTenantId();
      this.fullName = 'Visiteur';
      this.role = 'visiteur';
    }

    if (this.userService.isVisitor() || this.role === 'visiteur') {
      this.conversations = [];
      this.groupConversationsByDate();
      return;
    }
  try {
      const response = await this.agentService.getUserSessions(this.currentTenantId);
      if (response && response.sessions) {
        this.conversations = response.sessions.map((session: any) => ({
          id: session.session_id,
          session_id: session.session_id,
          tenant_id: this.currentTenantId,
          title: session.title || session.first_message || 'Nouvelle conversation',
          first_message: session.first_message || '',
          message_count: session.message_count || 0,
          created_at: session.updated_at || session.created_at,
          active: false
        }));
        this.groupConversationsByDate();
      }
    } catch (error) {
      console.error('Erreur chargement conversations:', error);
    }
  }

  private groupConversationsByDate(): void {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    const last7Days = new Date(today);
    last7Days.setDate(last7Days.getDate() - 7);

    const last30Days = new Date(today);
    last30Days.setDate(last30Days.getDate() - 30);

    this.todayConversations = [];
    this.yesterdayConversations = [];
    this.last7DaysConversations = [];
    this.olderConversations = [];

    for (const conv of this.conversations) {
      const convDate = new Date(conv.created_at);
      convDate.setHours(0, 0, 0, 0);

      if (convDate.getTime() === today.getTime()) {
        this.todayConversations.push(conv);
      } else if (convDate.getTime() === yesterday.getTime()) {
        this.yesterdayConversations.push(conv);
      } else if (convDate >= last7Days) {
        this.last7DaysConversations.push(conv);
      } else if (convDate >= last30Days) {
        this.olderConversations.push(conv);
      }
    }
  }

async newConversation(): Promise<void> {
  this.conversations.forEach(c => c.active = false);
  
  this.router.navigate(['/chat'], { 
    queryParams: { new: '1' }, 
  });

  if (this.isMobile && this.mobileOpen) {
    this.closeMobileSidebar();
  }
}

  async selectConversation(conversation: Conversation): Promise<void> {
    this.conversations.forEach(c => c.active = false);
    conversation.active = true;
    this.conversationSelected.emit(conversation.session_id);

    this.router.navigate(['/chat'], { 
      queryParams: { session_id: conversation.session_id },
      queryParamsHandling: 'replace' 
    });

    if (this.isMobile && this.mobileOpen) {
      this.closeMobileSidebar();
    }
  }
  toggleMenu(convId: string, event: Event): void {
    event.stopPropagation();
    if (this.activeMenuId === convId) {
      this.activeMenuId = null;
    } else {
      this.activeMenuId = convId;
    }
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: Event): void {
    const target = event.target as HTMLElement;
    if (!target.closest('.conv-menu')) {
      this.activeMenuId = null;
    }

    if (this.isMobile && this.mobileOpen) {
      const isSidebar = target.closest('.sidebar');
      const isToggleBtn = target.closest('.mobile-toggle-btn') || target.closest('.toggle-btn');

      if (!isSidebar && !isToggleBtn) {
        this.closeMobileSidebar();
      }
    }
  }

openMobileSidebar(): void {
    if (this.isMobile) {
      this.closeMobile.emit(); 
    }
  }

 closeMobileSidebar(): void {
    if (this.isMobile) {
      this.closeMobile.emit();
    }
  }

  async renameConversation(conversation: Conversation): Promise<void> {
    this.activeMenuId = null;
    const newTitle = prompt('Nouveau titre:', conversation.title);
    if (newTitle && newTitle.trim()) {
      const success = await this.agentService.renameConversation(
        conversation.session_id,
        conversation.tenant_id,
        newTitle.trim()
      );
      if (success) {
        conversation.title = newTitle.trim();
        this.groupConversationsByDate();
      }
    }
  }

  async deleteConversation(conversation: Conversation): Promise<void> {
    this.activeMenuId = null;
    if (confirm(`Supprimer la conversation "${conversation.title}" ?`)) {
      const success = await this.agentService.deleteConversation(
        conversation.session_id,
        conversation.tenant_id
      );
      if (success) {
        this.conversations = this.conversations.filter(c => c.id !== conversation.id);
        this.groupConversationsByDate();
        if (conversation.active) {
          this.newConversation();
        }
      }
    }
  }
toggleSidebar(): void {
  if (this.isMobile) {
    this.mobileOpen = !this.mobileOpen;

    this.collapsed = false;

    this.closeMobile.emit();
  } else {
    this.collapsed = !this.collapsed;
    this.toggle.emit(this.collapsed);
  }
}

  closeSidebar(): void {
    this.closeMobileSidebar();
  }

  openSidebar(): void {
    this.openMobileSidebar();
  }

  setHovered(convId: string | null): void {
    this.hoveredConvId = convId;
  }

  isVisitor(): boolean {
    return this.role === 'visiteur';
  }

  getInitials(): string {
    if (!this.fullName || this.fullName === 'Visiteur') return 'V';
    const parts = this.fullName.split(' ');
    if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
    return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
  }

  getRoleLabel(): string {
    const role = this.role?.toLowerCase();
    if (role === 'super_admin') return 'Super Admin';
    if (role === 'admin') return 'Administrateur';
    if (role === 'visiteur') return 'Visiteur';
    return 'Agent';
  }

  logout(): void {
    this.userService.logout();
    this.router.navigate(['/login']);
  }
}