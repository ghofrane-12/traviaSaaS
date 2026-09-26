import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RestaurantResult } from '../../services/result.service';
import { BookingService } from '../../services/booking.service';
import { PriceService } from '../../services/price.service';

@Component({
  selector: 'app-restaurant-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './restaurant-card.component.html',
  styleUrls: ['./restaurant-card.component.scss']
})
export class RestaurantCardComponent {
  @Input() restaurant!: RestaurantResult;
  @Output() viewDetails = new EventEmitter<RestaurantResult>();
  public showConfirmation = false;
  showToast = false;
toastMessage = '';

  constructor(private bookingService: BookingService, private priceService: PriceService) {}

 private defaultImages: Record<string, string> = {
    'restaurant': 'https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=600&h=400&fit=crop',
    'café': 'https://images.unsplash.com/photo-1501339847302-ac426a4a7cbb?w=600&h=400&fit=crop',
    'bar': 'https://images.unsplash.com/photo-1514362545857-3bc16c4c7d1b?w=600&h=400&fit=crop',
    'fastfood': 'https://images.unsplash.com/photo-1551782450-17144efb9c50?w=600&h=400&fit=crop',
    'pizza': 'https://images.unsplash.com/photo-1513104890138-7c749659a591?w=600&h=400&fit=crop',
    'pizzeria': 'https://images.unsplash.com/photo-1513104890138-7c749659a591?w=600&h=400&fit=crop', 
    'sushi': 'https://images.unsplash.com/photo-1579871494447-9811cf80d66c?w=600&h=400&fit=crop',
    'burger': 'https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=600&h=400&fit=crop',
    'italien': 'https://images.unsplash.com/photo-1551183053-bf91a1d81141?w=600&h=400&fit=crop',
    'chinois': 'https://images.unsplash.com/photo-1563245372-f21724e3856d?w=600&h=400&fit=crop',
    'japonais': 'https://images.unsplash.com/photo-1553621042-f6e147245754?w=600&h=400&fit=crop',
    'mexicain': 'https://images.unsplash.com/photo-1569058242563-4b4d8d4b5d4d?w=600&h=400&fit=crop',
    'indien': 'https://images.unsplash.com/photo-1585937421612-70a008356fbe?w=600&h=400&fit=crop',
    'français': 'https://images.unsplash.com/photo-1598515214211-89d3c73ae83b?w=600&h=400&fit=crop',
    'boulangerie': 'https://images.unsplash.com/photo-1588964895597-cfccd6e2dbf9?w=600&h=400&fit=crop',
    'foodtruck': 'https://images.unsplash.com/photo-1560089000-7433a4ebbd64?w=600&h=400&fit=crop',
    'brasserie': 'https://images.unsplash.com/photo-1533777857889-4be7c70b33f7?w=600&h=400&fit=crop',
    'seafood': 'https://images.unsplash.com/photo-1534088568595-a310f17b4e0c?w=600&h=400&fit=crop',
    'vegan': 'https://images.unsplash.com/photo-1543353071-10c8b85d5b4f?w=600&h=400&fit=crop',
    'deli': 'https://images.unsplash.com/photo-1506354666786-959d6d497f1a?w=600&h=400&fit=crop',  
    'sandwich': 'https://images.unsplash.com/photo-1528735602780-2552fd46c7af?w=600&h=400&fit=crop',  
    'sandwicherie': 'https://images.unsplash.com/photo-1528735602780-2552fd46c7af?w=600&h=400&fit=crop',  
    'subway': 'https://images.unsplash.com/photo-1528735602780-2552fd46c7af?w=600&h=400&fit=crop',  
    'panini': 'https://images.unsplash.com/photo-1528735602780-2552fd46c7af?w=600&h=400&fit=crop',  
};
  private fallbackImage = 'https://images.unsplash.com/photo-1414235077428-338989a2e8c0?w=600&h=400&fit=crop';


getMainImage(): string {
  if (this.restaurant.image && this.restaurant.image.startsWith('http')) {
    return this.restaurant.image;
  }
  return this.getDefaultImageByCategory();
}
hasPrice(): boolean {
  const r = this.restaurant;
  return !!(
    (r.price && r.price > 0) ||
    (r.price_range?.min && r.price_range.min > 0)   );
}

getPriceDisplay(): string {
  const prices = this.priceService.getRestaurantPrices(this.restaurant);

  if (prices.price.amount > 0) return prices.price.formatted;

  if (prices.rangeMin !== '–' && prices.rangeMax !== '–' && prices.rangeMin !== prices.rangeMax) {
    return `${prices.rangeMin} – ${prices.rangeMax}`;
  }
  if (prices.rangeMin !== '–') return prices.rangeMin;

  return '';
}
getReservationUrl(): string {
  return this.restaurant.contact?.website || 
         this.getWebsite() || 
         `https://www.thefork.com/search?cityName=${this.restaurant.location?.locality || ''}`;
}

getRating(): number {
  return this.restaurant.rating || 0;
}

hasRating(): boolean {
  return !!(this.restaurant.rating && this.restaurant.rating > 0);
}

getStars(): number[] {
  return Array(Math.round(this.getRating())).fill(0);
}

getSpecialties(): string[] {
  return this.restaurant.specialties || [];
}

getServices(): string[] {
  return this.restaurant.services || [];
}

getDescription(): string {
  return this.restaurant.description || '';
}

getHours(): string {
  const h = this.restaurant.opening_hours;
  if (!h?.open) return '';
  let result = `${h.open} – ${h.close}`;
  if (h.open_eve) result += ` / ${h.open_eve} – ${h.close_eve}`;
  return result;
}

getClosedDays(): string {
  const days = this.restaurant.closed_days || [];
  return days.length ? `Fermé : ${days.join(', ')}` : '';
}

getTel(): string {
  return this.restaurant.contact?.tel || '';
}

getTel2(): string {
  return this.restaurant.contact?.tel2 || '';
}

onCall(): void {
  const tel = this.getTel();
  if (tel) window.open(`tel:${tel}`, '_self');
}


async onReserve(): Promise<void> {
  if (this.showConfirmation) {
    this.toastMessage = '⚠️ Ce restaurant est déjà réservé !';
  } else {
    const bookingId = await this.bookingService.bookRestaurant(this.restaurant);
    if (bookingId) {
      this.showConfirmation = true;
      this.toastMessage = '✅ Réservation enregistrée !';
    }
  }
  this.showToast = true;
  setTimeout(() => this.showToast = false, 3000);
}
  getDefaultImageByCategory(): string {
    const category = this.getMainCategory().toLowerCase();
    
    if (this.defaultImages[category]) {
      return this.defaultImages[category];
    }
    
    const keywordMap: Record<string, string> = {
      'café': 'café', 'coffee': 'café',
      'bar': 'bar', 'pub': 'bar',
      'fast': 'fastfood', 'fast food': 'fastfood',
      'pizza': 'pizza',
      'sushi': 'sushi',
      'burger': 'burger',
      'italien': 'italien', 'italian': 'italien',
      'chinois': 'chinois', 'chinese': 'chinois',
      'japonais': 'japonais', 'japanese': 'japonais',
      'mexicain': 'mexicain', 'mexican': 'mexicain',
      'indien': 'indien', 'indian': 'indien',
      'français': 'français', 'french': 'français',
      'boulangerie': 'boulangerie', 'bakery': 'boulangerie',
      'food truck': 'foodtruck',
      'brasserie': 'brasserie',
      'seafood': 'seafood', 'fruit de mer': 'seafood',
      'vegan': 'vegan'
    };
    
    for (const [keyword, mappedKey] of Object.entries(keywordMap)) {
      if (category.includes(keyword)) {
        return this.defaultImages[mappedKey] || this.fallbackImage;
      }
    }
    
    return this.fallbackImage;
  }

  getMainCategory(): string {
    const cats = this.restaurant?.categories || [];
    return cats[0]?.name || 'Restaurant';
  }

  getCategoryIconUrl(): string | null {
    const cats = this.restaurant.categories;
    if (!cats?.length) return null;
    const icon = cats[0]?.icon;
    if (!icon) return null;

    if (typeof icon === 'object') {
      if (icon.prefix && icon.suffix) return `${icon.prefix}64${icon.suffix}`;
      if (icon.url) return icon.url;
    }

    if (typeof icon === 'string' && icon.startsWith('http')) return icon;

    return null;
  }

  getCategoryAccentColor(): string {
    const cat = this.getMainCategory().toLowerCase();
    if (cat.includes('café') || cat.includes('coffee')) return '#f57f17';
    if (cat.includes('bar') || cat.includes('cocktail'))  return '#880e4f';
    if (cat.includes('sushi') || cat.includes('japonais')) return '#283593';
    if (cat.includes('burger') || cat.includes('fast'))   return '#bf360c';
    if (cat.includes('pizza') || cat.includes('italien')) return '#e65100';
    return '#1b5e20';
  }

  onImageError(event: Event): void {
    const img = event.target as HTMLImageElement;
    const currentSrc = img.src;
    
    if (currentSrc !== this.fallbackImage) {
      img.src = this.fallbackImage;
    }
  }

  onIconError(event: Event): void {
    (event.target as HTMLImageElement).style.display = 'none';
  }

  getDistanceText(distance: number): string {
    if (!distance) return '';
    if (distance < 1000) return `${Math.round(distance)}m`;
    return `${(distance / 1000).toFixed(1)}km`;
  }

  getLocationAddress(): string {
    if (!this.restaurant.location) return '';
    const parts = [];
    if (this.restaurant.location.address) parts.push(this.restaurant.location.address);
    if (this.restaurant.location.locality) parts.push(this.restaurant.location.locality);
    return parts.join(', ');
  }

  getWebsite(): string {
    return this.restaurant?.contact?.website || this.restaurant?.website || '';
  }

  hasWebsite(): boolean {
    return !!(this.restaurant?.contact?.website || this.restaurant?.website);
  }

  getInstagramUrl(): string {
    const handle = this.restaurant.social_media?.instagram;
    if (!handle) return '';
    if (handle.startsWith('http')) return handle;
    return `https://www.instagram.com/${handle}`;
  }

  hasInstagram(): boolean {
    return !!(this.restaurant.social_media && this.restaurant.social_media.instagram);
  }
}