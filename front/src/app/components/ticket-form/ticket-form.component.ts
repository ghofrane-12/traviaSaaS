// components/ticket-form/ticket-form.component.ts
import {
  Component, Input, Output, EventEmitter,
  OnInit, ChangeDetectorRef
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule }  from '@angular/forms';
import { HttpClient }   from '@angular/common/http';
import { environment } from '../../environments/environment';

@Component({
  selector:    'app-ticket-form',
  standalone:  true,
  imports:     [CommonModule, FormsModule],
  templateUrl: './ticket-form.component.html',
  styleUrls:   ['./ticket-form.component.scss'],
})
export class TicketFormComponent implements OnInit {

  @Input() tenantId  = '';
  @Input() userId    = '';


  @Input() mode: 'inline' | 'modal' = 'inline';

  @Output() submitted = new EventEmitter<void>();

  subject    = '';
  message    = '';
  phone      = '';
  submitting = false;
  success    = false;
  error      = '';

  showModal  = false;

  constructor(
    private http: HttpClient,
    private cdr:  ChangeDetectorRef,
  ) {}

  ngOnInit(): void {}


  openModal(): void {
    this.subject   = '';
    this.message   = '';
    this.phone     = '';
    this.success   = false;
    this.error     = '';
    this.showModal = true;
  }

  closeModal(): void { this.showModal = false; }


  submit(): void {
    if (!this.subject.trim() || !this.message.trim()) {
      this.error = 'Veuillez remplir le sujet et le message.';
      return;
    }
    this.submitting = true;
    this.error      = '';

this.http.post(`${environment.apiUrl}general/tickets`, {
      tenant_id: this.tenantId,
      user_id:   this.userId,
      subject:   this.subject.trim(),
      message:   this.message.trim(),
      phone:     this.phone.trim(),
    }).subscribe({
      next: () => {
        this.submitting = false;
        this.success    = true;
        this.submitted.emit();
        this.cdr.detectChanges();
        if (this.mode === 'modal') {
          setTimeout(() => this.closeModal(), 2500);
        }
      },
      error: () => {
        this.submitting = false;
        this.error      = 'Erreur lors de l\'envoi. Veuillez réessayer.';
        this.cdr.detectChanges();
      },
    });
  }
}