import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HotelResult } from '../../services/result.service';
import { PriceService } from '../../services/price.service';
import { BookingService } from '../../services/booking.service';
@Component({
  selector: 'app-hotel-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './hotel-card.component.html',
  styleUrls: ['./hotel-card.component.scss']
})
export class HotelCardComponent {
  @Input() hotel!: HotelResult;
  @Output() viewDetails = new EventEmitter<HotelResult>();
  @Output() bookNow = new EventEmitter<HotelResult>();
  public showConfirmation = false;
  showToast = false;

  constructor(public priceService: PriceService, public bookingService: BookingService) {}

  getMainImage(): string {
    const img = this.hotel?.images?.[0] || this.hotel?.image;
    return img || `https://placehold.co/220x180/e8f0fe/1565c0?text=${encodeURIComponent(this.hotel?.name || 'Hotel')}`;
  }

  onImageError(event: Event): void {
    (event.target as HTMLImageElement).src =
      `https://placehold.co/220x180/e8f0fe/1565c0?text=${encodeURIComponent(this.hotel?.name || 'Hotel')}`;
  }

  getStarsArray(): number[] {
    const n = this.hotel?.stars || Math.floor(this.hotel?.rating || 0);
    return Array(Math.min(5, Math.max(0, n))).fill(0);
  }

  getTopAmenities(): any[] {
    return (this.hotel?.amenities || []).slice(0, 3);
  }
  get priceDisplay() {
    return this.priceService.getHotelPrices(this.hotel);
  }
  getBoardTypes(): string[] {
    if (!this.hotel?.offers?.length) return [];
    const boards = new Set(this.hotel.offers.map(o => o.board_code).filter(Boolean));
    return Array.from(boards).slice(0, 3);
  }
  getDisplayCurrency(): string {
    return this.hotel?.currency || 'EUR';
  }

  getPricePerNight(): number {
    return this.hotel?.price_per_night || 0;
  }

  getTotalPrice(): number {
    return this.hotel?.total_price || 0;
  }

  formatPrice(price: number): string {
    if (!price && price !== 0) return '–';
    const currency = this.getDisplayCurrency();
    return this.priceService.format(price, currency, 0);
  }

  getAmenityIcon(icon: string): string {
    const map: Record<string, string> = {
      wifi: '▦', breakfast: '◎', parking: '⊡', pool: '≋',
      spa: '✦', gym: '◈', restaurant: '◉', bar: '◐', luggage: '⊞'
    };
    return map[icon] || icon || '✓';
  }

  onViewDetails(): void { this.viewDetails.emit(this.hotel); }
async onBook(hotel?: HotelResult): Promise<void> {
  const h = hotel || this.hotel;
  if (!h) return;

  const q = h._query || null;

  const bookingId = await this.bookingService.bookHotel(h, q);
  if (bookingId){
     this.showConfirmation = true;
       this.showToast = true;
  setTimeout(() => this.showToast = false, 3000);}
}}