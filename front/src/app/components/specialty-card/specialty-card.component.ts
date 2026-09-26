import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { SpecialtyResult } from '../../services/result.service';

@Component({
  selector: 'app-specialty-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './specialty-card.component.html',
  styleUrls: ['./specialty-card.component.scss']
})
export class SpecialtyCardComponent {
  @Input() specialty!: SpecialtyResult;
  @Output() viewDetails = new EventEmitter<SpecialtyResult>();

  getMainImage(): string {
    return this.specialty?.image || 'https://placehold.co/280x180/e8f0fe/1565c0?text=Specialty';
  }

  onImageError(event: Event): void {
    (event.target as HTMLImageElement).src = 'https://placehold.co/280x180/e8f0fe/1565c0?text=Specialty';
  }

getRecipeUrl(): string {
  if (this.specialty.actions && this.specialty.actions.length > 0) {
    const recipeAction = this.specialty.actions.find(a => a.action === 'recipe');
    if (recipeAction && recipeAction.url) {
      return recipeAction.url;
    }
  }
  if (this.specialty.id) {
    return `https://www.themealdb.com/meal/${this.specialty.id}`;
  }
  return '#';
}
}