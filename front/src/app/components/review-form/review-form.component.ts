// components/review-form/review-form.component.ts
import {
  Component, Input, Output, EventEmitter,
  OnInit, ChangeDetectorRef
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule }  from '@angular/forms';
import { HttpClient }   from '@angular/common/http';
import { environment } from '../../environments/environment';

@Component({
  selector:    'app-review-form',
  standalone:  true,
  imports:     [CommonModule, FormsModule],
  templateUrl: './review-form.component.html',
  styleUrls:   ['./review-form.component.scss'],
})
export class ReviewFormComponent implements OnInit {

  @Input() tenantId  = '';
  @Input() userId    = '';

 
  @Input() mode: 'inline' | 'modal' = 'inline';

  @Output() submitted = new EventEmitter<void>();

  rating     = 0;
  comment    = '';
  hoveredStar = 0;
  submitting = false;
  success    = false;
  error      = '';

  showModal  = false;

  readonly stars = [1, 2, 3, 4, 5];

  constructor(
    private http: HttpClient,
    private cdr:  ChangeDetectorRef,
  ) {}

ngOnInit(): void {
  if (this.mode === 'modal') {
    this.showModal = true;
  }
}


  setRating(s: number):      void { this.rating      = s; }
  setHovered(s: number):     void { this.hoveredStar = s; }
  clearHovered():            void { this.hoveredStar = 0; }
  isActive(s: number):    boolean { return s <= (this.hoveredStar || this.rating); }


  openModal(): void {
    this.rating    = 0;
    this.comment   = '';
    this.success   = false;
    this.error     = '';
    this.showModal = true;
  }

closeModal(): void {
  this.showModal = false;
  this.submitted.emit();  
}

  submit(): void {
    if (!this.rating) {
      this.error = 'Veuillez sélectionner une note.';
      return;
    }
    this.submitting = true;
    this.error      = '';

this.http.post(`${environment.apiUrl}general/reviews`, {
      tenant_id: this.tenantId,
      user_id:   this.userId,
      rating:    this.rating,
      comment:   this.comment.trim(),
    }).subscribe({
      next: () => {
        this.submitting = false;
        this.success    = true;
        this.submitted.emit();
        this.cdr.detectChanges();
        if (this.mode === 'modal') {
          setTimeout(() => this.closeModal(), 2000);
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