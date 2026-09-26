// activity-card.component.ts
import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { ActivityResult } from '../../services/result.service';

@Component({
  selector: 'app-activity-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './activity-card.component.html',
  styleUrls: ['./activity-card.component.scss']
})
export class ActivityCardComponent {
  @Input() activity!: ActivityResult;
  @Output() viewDetails = new EventEmitter<string>();
  private readonly DEFAULT_IMAGE = 'https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=600&h=400&fit=crop';
  constructor(
    private router: Router  ) {}
  getMainImage(): string {
    if (this.activity.image && this.activity.image.startsWith('http')) {
      return this.activity.image;
    }
    return this.DEFAULT_IMAGE;
  }
  
onViewDetails(): void {
    const subCategory = this.activity.sub_category || 'Tour/Excursion Search';
    
    console.log('[ActivityCard] Navigation:', {
        id: this.activity.id,
        name: this.activity.name,
        sub_category: subCategory
    });
    
    this.router.navigate(['/activity', this.activity.id], {
        queryParams: { sub_category: subCategory }
    });
}
  
  // =========================================================================
  // FORMATAGE DES DATES
  // =========================================================================
  
  formatDate(dateStr: string): string {
    if (!dateStr) return 'Date à confirmer';
    const date = new Date(dateStr);
    return date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
  }
  
  formatTime(timeStr: string): string {
    if (!timeStr) return 'Horaire à confirmer';
    if (timeStr.includes(':')) {
      const [hours, minutes] = timeStr.split(':');
      return `${hours}h${minutes}`;
    }
    return timeStr;
  }
  
  // =========================================================================
  // GESTION DU LIEU
  // =========================================================================
  
  getVenueDisplay(): string {
    if (this.activity.venue && this.activity.city) {
      return `${this.activity.venue}, ${this.activity.city}`;
    }
    if (this.activity.venue) {
      return this.activity.venue;
    }
    if (this.activity.city) {
      return this.activity.city;
    }
    return 'Lieu à confirmer';
  }
  
  // =========================================================================
  // GESTION DE LA DESCRIPTION
  // =========================================================================
  
  getDisplayDescription(): string {
    if (this.activity.full_details?.description_full) {
      return this.truncateText(this.activity.full_details.description_full, 120);
    }
    if (this.activity.description) {
      return this.truncateText(this.activity.description, 120);
    }
    return 'Découvrez cet événement unique à ne pas manquer !';
  }
  
  // =========================================================================
  // GESTION DES ARTISTES / INTERPRÈTES
  // =========================================================================
  
  getArtists(): string[] {
    return this.activity.full_details?.artists || [];
  }
  
  getArtistsDisplay(): string {
    const artists = this.getArtists();
    if (artists.length === 0) return '';
    if (artists.length === 1) return artists[0];
    return `${artists[0]} + ${artists.length - 1} autre(s)`;
  }
  
  // =========================================================================
  // GESTION DES TAGS
  // =========================================================================
  
  getTags(): string[] {
    const tags: string[] = [];
    
    if (this.activity.category && this.activity.category !== 'Événement') {
      tags.push(this.activity.category);
    }
    
    if (this.activity.on_sale) {
      tags.push('Billets disponibles');
    } else {
      tags.push('Complet');
    }
    
    const artistsDisplay = this.getArtistsDisplay();
    if (artistsDisplay) {
      tags.push(artistsDisplay);
    }
    
    return tags.slice(0, 3);
  }
  
  // =========================================================================
  // GESTION DU PRIX (à partir des détails complets)
  // =========================================================================
  
  hasPrice(): boolean {
    return false; 
  }
  
  formatPrice(): string {
    return 'Prix sur demande';
  }
  
  // =========================================================================
  // GESTION DE LA NOTE / RATING
  // =========================================================================
  
  getRating(): number | null {
    return null;
  }
  
  // =========================================================================
  // GESTION DE LA DURÉE
  // =========================================================================
  
  getDuration(): string | null {
    return null;
  }
  
  // =========================================================================
  // STATUT
  // =========================================================================
  
  getStatusText(): string {
    if (this.activity.on_sale) {
      return 'Disponible';
    }
    return 'Complet';
  }
  
  // =========================================================================
  // UTILITAIRES
  // =========================================================================
  
  getCategoryColor(category: string): string {
    const colors: Record<string, string> = {
      'Musique': '#e91e63',
      'Concert': '#e91e63',
      'Théâtre': '#9c27b0',
      'Spectacle': '#9c27b0',
      'Sport': '#4caf50',
      'Exposition': '#ff9800',
      'Festival': '#f44336',
      'Visite': '#2196f3',
      'Tour': '#2196f3',
      'Arts': '#9c27b0'
    };
    return colors[category] || '#607d8b';
  }
  
  handleImageError(event: any) {
    const img = event.target as HTMLImageElement;
    if (img.src !== this.DEFAULT_IMAGE) {
      img.src = this.DEFAULT_IMAGE;
    }
  }
  
  truncateText(text: string, maxLength: number): string {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
  }
}