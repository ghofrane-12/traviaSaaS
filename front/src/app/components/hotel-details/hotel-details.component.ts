import { Component, OnInit, OnDestroy, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, ActivatedRoute } from '@angular/router';
import { HotelResult, ResultService } from '../../services/result.service';
import { Subscription } from 'rxjs';
import { PriceService, PriceDisplay } from '../../services/price.service';
import { BookingService } from '../../services/booking.service';
@Component({
  selector: 'app-hotel-details',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './hotel-details.component.html',
  styleUrls: ['./hotel-details.component.scss']
})
export class HotelDetailsComponent implements OnInit, OnDestroy {
  hotel: HotelResult | null = null;   
  selectedOffer: any = null;
  selectedImageIndex = 0;
  private sub!: Subscription;
  public showConfirmation = false;
  showToast = false;
  toastMessage = '';


  @Input() query?: any;

  constructor(
    private router: Router,
    private route: ActivatedRoute,
    private resultService: ResultService,
    public priceService: PriceService,
    private bookingService: BookingService
  ) {}

  ngOnInit() {
  const nav = this.router.getCurrentNavigation();
  const state = (nav?.extras?.state ?? window.history.state) as { hotel: HotelResult; query?: any } | undefined;

  if (state?.hotel) {
    this.hotel = state.hotel;
    this.query = state.query ?? null;
    console.log('[HotelDetails] query récupéré:', this.query); // ← vérifier
    return;
  }

    const hotelId = this.route.snapshot.paramMap.get('id');
    if (!hotelId) { this.router.navigate(['/results']); return; }

    const segments = this.resultService.getSegments();
    for (const seg of segments) {
      if (seg.result_type === 'hotel_search_results' && seg.results?.length) {
        const found = seg.results.find((h: any) => h.id === hotelId);
        if (found) {
          this.hotel = found;
          console.log('[HotelDetails] Hotel from segments:', this.hotel?.name);
          return;
        }
      }
    }

    this.sub = this.resultService.results$.subscribe(results => {
      if (!results) return;

      if (results.type === 'hotel_search_results') {
        const found = (results as any).results?.find((h: HotelResult) => h.id === hotelId);
        if (found) {
          this.hotel = found;
          console.log('[HotelDetails] Hotel from results$:', this.hotel?.name);
          return;
        }
      }

      if (hotelId === 'demo') {
        this.hotel = this.getDemoHotel();
        return;
      }

      this.router.navigate(['/results']);
    });

    if (hotelId === 'demo') {
      this.hotel = this.getDemoHotel();
    }
  }

  ngOnDestroy() { this.sub?.unsubscribe(); }


  getHotelName()    { return this.hotel?.name || 'Hôtel'; }
  getHotelAddress() { return this.hotel?.location?.address || this.hotel?.address || ''; }
  getHotelRating()  { return this.hotel?.rating || 0; }
  getHotelStars()   { return this.hotel?.stars || Math.floor(this.hotel?.rating || 0); }

  getCurrency(): string {
    return this.hotel?.currency || 'EUR';
  }

  getTargetCurrency(): string {
    return this.hotel?.target_currency || this.hotel?.currency || 'EUR';
  }

  get priceDisplay() {
    if (!this.hotel) return this.priceService.getHotelPrices(null as any);
    return this.priceService.getHotelPrices(this.hotel);
  }

  getOfferPricePerNightDisplay(offer: any): PriceDisplay {
    const priceObj = {
      price_per_night:          offer.price_per_night,
      currency:                 offer.currency,
      target_currency:          offer.target_currency || this.hotel?.target_currency || 'EUR',
      price_per_night_original: offer.price_per_night_original,
      original_currency:        offer.original_currency,
      margin_applied:           offer.margin_applied,
    };
    return this.priceService.getPriceDisplay(priceObj, 'price_per_night');
  }

  getOfferTotalDisplay(offer: any): PriceDisplay {
    const priceObj = {
      price:             offer.price,
      currency:          offer.currency,
      target_currency:   offer.target_currency || this.hotel?.target_currency || 'EUR',
      price_original:    offer.price_original,
      original_currency: offer.original_currency,
      margin_applied:    offer.margin_applied,
    };
    return this.priceService.getPriceDisplay(priceObj, 'price');
  }

  getDisplayCurrency(): string { return this.getCurrency(); }
  getPricePerNight(): number   { return this.hotel?.price_per_night || 0; }
  getTotalPrice(): number      { return this.hotel?.total_price || 0; }

  formatPrice(price: number): string {
    return this.priceService.format(price, this.getCurrency());
  }

  getImages(): string[] {
    const imgs = this.hotel?.images?.filter(Boolean) || [];
    if (imgs.length === 0 && this.hotel?.image) return [this.hotel.image];
    return imgs;
  }

  getMainImage(): string {
    const imgs = this.getImages();
    return imgs[this.selectedImageIndex] ||
      `https://placehold.co/800x420/e8f0fe/1565c0?text=${encodeURIComponent(this.getHotelName())}`;
  }

  onImageError(event: Event): void {
    (event.target as HTMLImageElement).src =
      `https://placehold.co/800x420/e8f0fe/1565c0?text=${encodeURIComponent(this.getHotelName())}`;
  }

  onThumbError(event: Event, index: number): void {
    (event.target as HTMLImageElement).src =
      `https://placehold.co/80x56/e8f0fe/1565c0?text=${index + 1}`;
  }

  selectImage(i: number) { this.selectedImageIndex = i; }

  nextImage() {
    const max = this.getImages().length - 1;
    this.selectedImageIndex = this.selectedImageIndex < max ? this.selectedImageIndex + 1 : 0;
  }

  prevImage() {
    const max = this.getImages().length - 1;
    this.selectedImageIndex = this.selectedImageIndex > 0 ? this.selectedImageIndex - 1 : max;
  }

  getStarsArray(n: number): number[] {
    return Array(Math.min(5, Math.max(0, Math.floor(n)))).fill(0);
  }

  getAmenities(): any[] { return this.hotel?.amenities || []; }

  getAmenityIcon(icon: string): string {
    const map: Record<string, string> = {
      wifi: '▦', breakfast: '◎', parking: '⊡', pool: '≋',
      spa: '✦', gym: '◈', restaurant: '◉', bar: '◐', luggage: '⊞',
    };
    return map[icon] || icon || '✓';
  }

  getGroupedOffers(): { code: string; name: string; offers: any[] }[] {
    if (!this.hotel?.offers?.length) return [];
    const map = new Map<string, { code: string; name: string; offers: any[] }>();
    for (const offer of this.hotel.offers) {
      const key = offer.board_code || offer.board_type || 'OTHER';
      if (!map.has(key)) {
        map.set(key, { code: offer.board_code || key, name: offer.board_type || key, offers: [] });
      }
      map.get(key)!.offers.push(offer);
    }
    return Array.from(map.values());
  }

  isBestOffer(offer: any): boolean {
    return this.hotel?.best_offer?.id === offer.id &&
           this.hotel?.best_offer?.board_code === offer.board_code;
  }

  formatCurrency(c: string): string {
    const s: Record<string, string> = { EUR: '€', USD: '$', GBP: '£', TND: 'DT', AED: 'د.إ' };
    return s[c] || c;
  }

  getOfferCurrency(offer: any): string {
    return offer.target_currency || offer.currency || this.getCurrency();
  }

  getOfferPrice(offer: any, field: string): number {
    return offer[field] || 0;
  }

  getBestOfferPriceFormatted(): string {
    if (!this.hotel?.best_offer) return '–';
    return this.priceService.format(this.hotel.best_offer.price || 0, this.getCurrency());
  }

  getBestOfferName(): string {
    return this.hotel?.best_offer?.name || '';
  }

  onSelectOffer(offer: any) {
    this.selectedOffer = offer;
    console.log('[HotelDetails] Offre sélectionnée:', offer);
  }

async onBook(hotel?: HotelResult): Promise<void> {
  const h = hotel || this.hotel;
  if (!h) return;

  if (this.showConfirmation) {
    this.toastMessage = '⚠️ Cet hôtel est déjà réservé !';
  } else {
    const q = this.query || h._query || null;
    const bookingId = await this.bookingService.bookHotel(h, q);
    if (bookingId) {
      this.showConfirmation = true;
      this.toastMessage = '✅ Réservation hôtel enregistrée !';
    }
  }
  this.showToast = true;
  setTimeout(() => this.showToast = false, 3000);
}



  goBack() { this.router.navigate(['/results']); }

  getDemoHotel(): HotelResult {
    return {
      id: 'demo', type: 'hotel',
      name: 'Medina & CarthageLand Yasmine Hammamet',
      location: { city: 'Hammamet', country: 'Tunisie', address: 'La Medina Yasmine, 8056 Hammamet' },
      rating: 4.5, rating_count: 1250,
      price_per_night: 101.75, total_price: 305.25,
      currency: 'EUR',
      images: ['https://booking.medinahotelsandresorts.com/cr.fwk/images/hotels/Hotel-578-20260106-123950.png'],
      amenities: [
        { icon: 'wifi',      name: 'WiFi gratuit',    included: true },
        { icon: 'pool',      name: 'Piscine',          included: true },
        { icon: 'breakfast', name: 'Petit-déjeuner',   included: true },
      ],
      actions: [
        { label: 'Voir détails', action: 'details', url: '/hotel/demo' },
        { label: 'Réserver',     action: 'book',    url: '/book/hotel/demo' },
      ],
    };
  }
}