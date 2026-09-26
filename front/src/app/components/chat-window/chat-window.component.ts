import { Component, AfterViewChecked, ElementRef, ViewChild, Input, OnChanges, SimpleChanges, ChangeDetectorRef,Output,EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReviewFormComponent } from '../review-form/review-form.component';
import { TicketFormComponent }  from '../ticket-form/ticket-form.component';
import {StreamingStep } from '../main-page/main-page.component'
export interface Message {
  text: string;
  sender: 'user' | 'bot';
  timestamp?: Date;
  category?: string;
  deberta_category?: string;
  sub_category?: string;
  language?: string;
  score?: number;
  entities?: { entity: string; value: string }[];
  suggestions?: any[];
  isStreaming?: boolean;
  json?: any;
  _meta?: {
    status?: string;
    reason?: string;
  };
clarification?: {
  subcats: Array<{ id: number; label: string; confidence?: number; display_phrase?: string }>;
  segment: string;
};
activeForm?: 'review' | 'ticket' | null;
formSuccess?: boolean;
sections?:   { title: string; content: string; items?: string[] }[];
actions?:    { label: string; action: string }[];
}
export interface HeroData {
  fullName: string;
  agencyName: string;
  agencyLogo?: string;
}
@Component({
  selector: 'app-chat-window',
  templateUrl: './chat-window.component.html',
  standalone: true,
  styleUrls: ['./chat-window.component.scss'],
  imports: [CommonModule, ReviewFormComponent, TicketFormComponent]
})
export class ChatWindowComponent implements AfterViewChecked, OnChanges {
  @Input() messages: Message[] = [];
  @Input() isTyping = false;
  @Input() heroData: HeroData = { fullName: '', agencyName: '' };
  @Output() clarificationChoice = new EventEmitter<{ id: number; label: string; confidence?: number }>();
  @Input() streamingSteps: StreamingStep[] = [];
  @Input() tenantId = '';
  @Input() userId   = '';
  @Input() isVisitor = false;
  @ViewChild('scrollContainer') private scrollContainer!: ElementRef;
  @ViewChild('scrollAnchor') private scrollAnchor!: ElementRef;

  private autoScroll = true;
  private scrollTimeout: any;
  private lastMessageCount = 0;
  showHero = true;


  constructor(private cdr: ChangeDetectorRef) {}

  ngAfterViewChecked() {
    if (this.autoScroll && this.messages.length !== this.lastMessageCount) {
      this.lastMessageCount = this.messages.length;
      this.scrollToBottom();
    }
  }
onClarificationChoice(sub: { id: number; label: string; confidence?: number }): void {
  this.clarificationChoice.emit(sub);
}
onOpenTicketForm(msg: Message): void {
  msg.activeForm = 'ticket';
  msg.formSuccess = false;
}

onCallContact(action: any): void {
  if (action.contact) {
    window.location.href = `tel:${action.contact}`;
  }
}
ngOnChanges(changes: SimpleChanges) {
  if (changes['messages'] && !changes['messages'].firstChange) {
    const prev = changes['messages'].previousValue?.length ?? 0;
    const curr = this.messages.length;
    if (curr > prev) {
      setTimeout(() => this.scrollToBottom(), 50);
    }
  }
}
  scrollToBottom(): void {
    // Annuler les timeouts précédents
    if (this.scrollTimeout) {
      clearTimeout(this.scrollTimeout);
    }
    
    this.scrollTimeout = setTimeout(() => {
      try {
        if (this.scrollAnchor && this.scrollAnchor.nativeElement) {
          this.scrollAnchor.nativeElement.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'end' 
          });
        } else if (this.scrollContainer && this.scrollContainer.nativeElement) {
          this.scrollContainer.nativeElement.scrollTop = 
            this.scrollContainer.nativeElement.scrollHeight;
        }
        this.cdr.detectChanges();
      } catch (err) {
        console.warn('Scroll error:', err);
      }
    }, 50);
  }
trackByMessage(index: number, message: Message): string {
  return `${message.sender}_${message.timestamp?.getTime() || index}_${message.text?.substring(0, 20)}`;
}
  onScroll(): void {
    if (this.scrollContainer && this.scrollContainer.nativeElement) {
      const element = this.scrollContainer.nativeElement;
      const isNearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
      
      if (this.scrollTimeout) clearTimeout(this.scrollTimeout);
      this.scrollTimeout = setTimeout(() => {
        this.autoScroll = isNearBottom;
      }, 150);
    }
  }
onAction(action: { label: string; action: string }, msg: any): void {
  switch (action.action) {
    case 'open_review_form':
      if (!this.isVisitor) {
        msg.activeForm  = 'review';
        msg.formSuccess = false;
      }
      break;
    case 'open_ticket_form':
      if (!this.isVisitor) {
        msg.activeForm  = 'ticket';
        msg.formSuccess = false;
      }
      break;
  }
  this.cdr.detectChanges();
}
 
onFormSubmitted(msg: any): void {
  msg.formSuccess = true;
  msg.activeForm  = null;
  this.cdr.detectChanges();
}
  forceScrollToBottom(): void {
    this.autoScroll = true;
    this.scrollToBottom();
  }
}