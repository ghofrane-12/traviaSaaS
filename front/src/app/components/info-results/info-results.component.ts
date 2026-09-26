// info-results/info-results.component.ts
import { Component, Input, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { ReviewFormComponent } from '../review-form/review-form.component';
import { TicketFormComponent }  from '../ticket-form/ticket-form.component';
export interface InfoSection {
  title:    string;
  content:  string;
  items?:   string[];
}

export interface InfoAction {
  label:    string;
  action:   string;
  contact?: string;  
  url?:     string;  
}

export interface InfoResult {
  type: 'info_results';
  message:   string;
  sections?: InfoSection[];
  actions?:  InfoAction[];
  suggestions?:    any[];
  loyalty_summary?: any;
  follow_up?: string;
}


export type InfoResultInput =
  | InfoResult
  | {
      result_type: string;   
      message:     string;
      sections?:   InfoSection[];
      actions?:    InfoAction[];
      suggestions?:    any[];
      loyalty_summary?: any;
      follow_up?:  string;
      [key: string]: any;
    };

@Component({
  selector:    'app-info-results',
  standalone:  true,
  imports:     [CommonModule, ReviewFormComponent, TicketFormComponent],
  templateUrl: './info-results.component.html',
  styleUrls:   ['./info-results.component.scss']
})
export class InfoResultsComponent implements OnChanges {

  @Input() data!: InfoResultInput;
  @Input() tenantId = '';
  @Input() userId   = '';
  @Input() isVisitor = false;

  
  activeForm:  'review' | 'ticket' | null = null;
  formSuccess  = false;
  normalized: InfoResult = {
    type:     'info_results',
    message:  '',
    sections: [],
    actions:  [],
  };

  constructor(private router: Router) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['data'] && this.data) {
      this.normalized = this._normalize(this.data);
    }
  }


  private _normalize(input: InfoResultInput): InfoResult {
    if ('type' in input && input.type === 'info_results') {
      return input as InfoResult;
    }
    return {
      type:            'info_results',
      message:         input.message         ?? '',
      sections:        input.sections        ?? [],
      actions:         input.actions         ?? [],
      suggestions:     input.suggestions     ?? [],
      loyalty_summary: input.loyalty_summary ?? null,
      follow_up:       input.follow_up       ?? '',
    };
  }


onAction(action: InfoAction & { contact?: string }): void {
  switch (action.action) {
    case 'open_review_form':
      if (!this.isVisitor) {
        this.activeForm  = 'review';
        this.formSuccess = false;
      }
      break;
    case 'open_ticket_form':
      if (!this.isVisitor) {
        this.activeForm  = 'ticket';
        this.formSuccess = false;
      }
      break;
    case 'call':
      if ((action as any).contact) {
        window.location.href = `tel:${(action as any).contact}`;
      }
      break;
    case 'modify_search':
    case 'modify_dates':
    case 'change_destination':
      this.router.navigate(['/chat']);
      break;
    case 'contact_agent':
      window.location.href = 'mailto:support@voyagebot.com';
      break;
    default:
      console.log('Unknown action:', action.action);
  }
}
onFormSubmitted(): void {
  this.formSuccess = true;
  this.activeForm  = null;
}
  getIconForTitle(title: string): string {
    if (title.includes('Introduction'))                         return '📖';
    if (title.includes('Compensation'))                        return '💰';
    if (title.includes('Suggestions') || title.includes('Conseils')) return '💡';
    if (title.includes('Questions'))                           return '❓';
    if (title.includes('Informations'))                        return '📋';
    if (title.includes('Erreur'))                              return '⚠️';
    return 'ℹ️';
  }
}