import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { ActivityService, ActivityDetails } from '../../services/activity.service';
import { BookingService } from '../../services/booking.service';
import { ActivityResult } from '../../services/result.service';
import { PriceService } from '../../services/price.service';

@Component({
  selector: 'app-activity-details',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './activity-details.component.html',
  styleUrls: ['./activity-details.component.scss']
})
export class ActivityDetailsComponent implements OnInit, OnDestroy {
  activity?: ActivityDetails;
  loading = true;
  error: string | null = null;
  selectedImageIndex = 0;
  private eventId: string = '';
  private subCategory: string = ''; 
  showToast = false;
toastMessage = '';



  
  public readonly DEFAULT_IMAGE = 'https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=1024&h=768&fit=crop';
  public showConfirmation = false;
  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private activityService: ActivityService,
    private bookingService: BookingService,
    public priceService: PriceService 
  ) {}

  ngOnInit(): void {
    this.eventId = this.route.snapshot.paramMap.get('id') || '';
    this.subCategory = this.route.snapshot.queryParamMap.get('sub_category') || '';
    
    if (this.eventId) {
      this.loadActivityDetails();
    } else {
      this.error = 'ID d\'événement manquant';
      this.loading = false;
    }
  }

  ngOnDestroy(): void {}

hasExternalLinks(): boolean {
    const links = this.activity?.externalLinks;
    if (!links) return false;
    
    return !!(links.homepage?.length || 
              links.facebook?.length || 
              links.instagram?.length || 
              links.twitter?.length || 
              links.wiki?.length);
}
  loadActivityDetails(): void {
    this.loading = true;
    console.log('[ActivityDetails] ID reçu:', this.eventId);
    console.log('[ActivityDetails] SubCategory:', this.subCategory);
    
    this.activityService.getActivityDetails(this.eventId, this.subCategory).subscribe({
      next: (data) => {
        console.log('[ActivityDetails] Succès:', data);
        this.activity = data;
        this.loading = false;
      },
      error: (err) => {
        console.error('[ActivityDetails] Erreur:', err);
        this.error = `Erreur: ${err.status} - ${err.statusText || 'Problème réseau'}`;
        this.loading = false;
      }
    });
  }
getImages(): string[] {
  if (!this.activity) return [];
  const seen = new Set<string>();
  const images: string[] = [];

  for (const img of (this.activity.images || [])) {
    const url = typeof img === 'string' ? img : img?.url;
    if (url && url.startsWith('http') && !seen.has(url)) {
      seen.add(url);
      images.push(url);
    }
  }
  return images;
}

getDuration(): string {
  return this.activity?.duration || '';
}

getStatusText(): string {
  if (this.activity?.source === 'rag') return 'Disponible';
  const status = this.activity?.dates?.status?.code;
  if (status === 'onsale')     return 'Billets disponibles';
  if (status === 'offsale')    return 'Vente terminée';
  if (status === 'cancelled')  return 'Annulé';
  return 'Disponible';
}

getStatusClass(): string {
  if (this.activity?.source === 'rag') return 'status-onsale';
  const status = this.activity?.dates?.status?.code;
  if (status === 'onsale')    return 'status-onsale';
  if (status === 'offsale')   return 'status-offsale';
  if (status === 'cancelled') return 'status-cancelled';
  return 'status-onsale';
}

getTimeDisplay(): string {
  const start = this.activity?.dates?.start;
  if (!start) return '';
  if (start.timeTBA || !start.localTime) return 'Horaire à confirmer';
  return this.formatTime(start.localTime);
}

getMeetingPoint(): string {
  return this.activity?.meeting_point || this.activity?.venue?.name || '';
}
  getMainImage(): string {
    const images = this.getImages();
    if (images.length > 0 && images[this.selectedImageIndex]) {
      return images[this.selectedImageIndex];
    }
    return this.DEFAULT_IMAGE;
  }

  prevImage(): void {
    const images = this.getImages();
    if (images.length > 0) {
      this.selectedImageIndex = (this.selectedImageIndex - 1 + images.length) % images.length;
    }
  }

  nextImage(): void {
    const images = this.getImages();
    if (images.length > 0) {
      this.selectedImageIndex = (this.selectedImageIndex + 1) % images.length;
    }
  }

  selectImage(index: number): void {
    this.selectedImageIndex = index;
  }

  onImageError(event: Event): void {
    const img = event.target as HTMLImageElement;
    if (img.src !== this.DEFAULT_IMAGE) {
      img.src = this.DEFAULT_IMAGE;
    }
  }

  getVenueLocation(): string {
    const venue = this.activity?.venue;
    if (!venue) return 'Lieu non spécifié';
    const parts = [];
    if (venue.name) parts.push(venue.name);
    if (venue.address) parts.push(venue.address);
    if (venue.city) parts.push(venue.city);
    if (venue.country) parts.push(venue.country);
    return parts.join(', ');
  }

  getGoogleMapsUrl(): string {
    const venue = this.activity?.venue;
    if (!venue?.location?.latitude || !venue?.location?.longitude) {
      const address = encodeURIComponent(this.getVenueLocation());
      return `https://www.google.com/maps/search/?api=1&query=${address}`;
    }
    return `https://www.google.com/maps/search/?api=1&query=${venue.location.latitude},${venue.location.longitude}`;
  }

  goBack(): void {
    this.router.navigate(['/results']);
  }


async onBook(activity?: ActivityResult | ActivityDetails): Promise<void> {
  const a = activity || this.activity;
  if (!a) return;

  if (this.showConfirmation) {
    this.toastMessage = '⚠️ Cette activité est déjà réservée !';
  } else {
    const bookingId = await this.bookingService.bookActivity(a);
    if (bookingId) {
      this.showConfirmation = true;
      this.toastMessage = '✅ Réservation activité enregistrée !';
    }
  }
  this.showToast = true;
  setTimeout(() => this.showToast = false, 3000);
}

formatDate(dateStr: string | null | undefined): string {
    if (!dateStr) return 'Date à confirmer';
    try {
        const date = new Date(dateStr);
        if (isNaN(date.getTime())) return 'Date à confirmer';
        return date.toLocaleDateString('fr-FR', { 
            weekday: 'long', 
            day: 'numeric', 
            month: 'long', 
            year: 'numeric' 
        });
    } catch {
        return 'Date à confirmer';
    }
}

formatTime(timeStr: string | null | undefined): string {
    if (!timeStr) return 'Horaire à confirmer';
    const parts = timeStr.split(':');
    if (parts.length < 2) return timeStr;
    const [hours, minutes] = parts;
    return `${hours}h${minutes}`;
}
 getPriceRange(): string {
  const prices = this.priceService.getActivityPrices(this.activity);
  if (prices.from.amount > 0 && prices.rangeMax && prices.rangeMax !== '–') {
    return `${prices.rangeMin} – ${prices.rangeMax}`;
  }
  if (prices.from.amount > 0) return `À partir de ${prices.from.formatted}`;
  return 'Prix sur demande';
}



}