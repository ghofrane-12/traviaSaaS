import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { AgentService } from '../../services/agent.service';
import { UserService } from '../../services/user.service';
import { Conversation } from '../sidebar/sidebar.component';

@Component({
  selector: 'app-history',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './history.component.html',
  styleUrls: ['./history.component.scss']
})
export class HistoryComponent implements OnInit, OnDestroy {
  allConversations: Conversation[] = [];
  groupedConversations = {
    today: [] as Conversation[],
    yesterday: [] as Conversation[],
    thisWeek: [] as Conversation[],
    thisMonth: [] as Conversation[],
    older: [] as Conversation[]
  };
  
  isLoading = false;
  error: string | null = null;
  activeMenuId: string | null = null;
  private refreshInterval: any;

  constructor(
    private agentService: AgentService,
    private userService: UserService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.loadAllConversations();
    
    this.refreshInterval = setInterval(() => {
      if (!this.isLoading) {
        this.loadAllConversations();
      }
    }, 30000);
  }

  ngOnDestroy(): void {
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
    }
  }

  async loadAllConversations(): Promise<void> {
    this.isLoading = true;
    this.error = null;

    try {
      const user = this.userService.session();
      if (!user) {
        throw new Error('Utilisateur non connecté');
      }

      const tenantId = user.tenant_id;
      const response = await this.agentService.getUserSessions(tenantId);
      
      if (response && response.sessions) {
        this.allConversations = response.sessions.map((session: any) => ({
          id: session.session_id,
          session_id: session.session_id,
          tenant_id: tenantId,
          title: session.title || session.first_message || 'Nouvelle conversation',
          first_message: session.first_message || '',
          message_count: session.message_count || 0,
          created_at: session.updated_at || session.created_at,
          active: false
        }));

        this.allConversations.sort((a, b) => 
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        );

        this.groupConversationsByPeriod();
      } else {
        this.allConversations = [];
      }
    } catch (error) {
      console.error('Erreur lors du chargement de l\'historique:', error);
      this.error = 'Impossible de charger l\'historique des conversations. Veuillez réessayer.';
    } finally {
      this.isLoading = false;
    }
  }

  private groupConversationsByPeriod(): void {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    
    const thisWeekStart = new Date(today);
    thisWeekStart.setDate(today.getDate() - today.getDay());
    
    const thisMonthStart = new Date(now.getFullYear(), now.getMonth(), 1);
    
    this.groupedConversations = {
      today: [],
      yesterday: [],
      thisWeek: [],
      thisMonth: [],
      older: []
    };

    for (const conv of this.allConversations) {
      const convDate = new Date(conv.created_at);
      const convDateOnly = new Date(convDate.getFullYear(), convDate.getMonth(), convDate.getDate());
      
      if (convDateOnly.getTime() === today.getTime()) {
        this.groupedConversations.today.push(conv);
      } else if (convDateOnly.getTime() === yesterday.getTime()) {
        this.groupedConversations.yesterday.push(conv);
      } else if (convDateOnly >= thisWeekStart) {
        this.groupedConversations.thisWeek.push(conv);
      } else if (convDateOnly >= thisMonthStart) {
        this.groupedConversations.thisMonth.push(conv);
      } else {
        this.groupedConversations.older.push(conv);
      }
    }
  }

  openConversation(conversation: Conversation): void {
    this.router.navigate(['/chat'], {
      queryParams: { session_id: conversation.session_id },
      queryParamsHandling: 'replace'
    });
  }

  goBack(): void {
    this.router.navigate(['/chat']);
  }

  goToChat(): void {
    this.router.navigate(['/chat'], {
      queryParams: { new: '1' }
    });
  }

  toggleMenu(convId: string, event: Event): void {
    event.stopPropagation();
    this.activeMenuId = this.activeMenuId === convId ? null : convId;
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
        await this.loadAllConversations();
      }
    }
  }

  async deleteConversation(conversation: Conversation): Promise<void> {
    this.activeMenuId = null;
    
    if (confirm(`Supprimer définitivement la conversation "${conversation.title}" ?`)) {
      const success = await this.agentService.deleteConversation(
        conversation.session_id,
        conversation.tenant_id
      );
      
      if (success) {
        await this.loadAllConversations();
      }
    }
  }

  getInitials(name: string): string {
    if (!name) return 'U';
    const parts = name.split(' ');
    if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
    return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
  }
}