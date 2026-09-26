import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, ActivatedRoute } from '@angular/router';
import { OfferService, OfferItem, OfferFilters } from '../../services/offer.service';
import { UserService } from '../../services/user.service';
import { OfferCardComponent } from '../../components/offer-card/offer-card.component';
import { BookingService } from '../../services/booking.service';
import { AgentService } from '../../services/agent.service';

@Component({
  selector: 'app-search',
  standalone: true,
  imports: [CommonModule, FormsModule, OfferCardComponent],
  templateUrl: './search.component.html',
  styleUrls: ['./search.component.scss']
})
export class SearchComponent implements OnInit {

  offers      : OfferItem[] = [];
  isLoading   = false;
  error       : string | null = null;
  agencyName  = '';
  totalOffers = 0;
  bookingStates = new Map<string, 'loading' | 'booked' | 'already_booked' | 'error'>();
  lastOfferId : string | null = null;
destinationSuggestions: string[] = [];
showDestinationSug = false;
private destinationTimeout: any;
  selectedType  = '';
  destination   = '';
  priceMin      = '';
  priceMax      = '';

  readonly offerTypes = [
    { value: '',        label: 'Tous',     icon: 'fa-globe' },
    { value: 'flight',  label: 'Vols',     icon: 'fa-plane' },
    { value: 'hotel',   label: 'Hôtels',   icon: 'fa-hotel' },
    { value: 'omra',    label: 'Omra',     icon: 'fa-kaaba' },
    { value: 'circuit', label: 'Circuits', icon: 'fa-map-marked-alt' },
  ];

  get filteredOffers() { return this.offers; }

  selectedOffer: OfferItem | null = null;

  constructor(
    public offerService:    OfferService,
    private userService:    UserService,
    private router:         Router,
    private route:          ActivatedRoute,
    private bookingService: BookingService,
    private agentService:   AgentService
  ) {}

  ngOnInit(): void {
    this.route.queryParams.subscribe(params => {
      if (params['tenant']) {
        sessionStorage.setItem('tenant_id', params['tenant']);
      }
      this.loadOffers();
    });
  }

  goBack(): void {
    const sessionId = this.agentService.getSessionId();
    this.router.navigate(['/chat'], {
      queryParams: sessionId ? { session_id: sessionId } : {}
    });
  }

  loadOffers(): void {
    const tenantId = this.userService.resolveTenantId();
    if (!tenantId) {
      this.error = 'Aucune agence sélectionnée.';
      return;
    }

    this.isLoading = true;
    this.error     = null;

    const filters: OfferFilters = {};
    if (this.selectedType) filters.offer_type  = this.selectedType;
    if (this.destination)  filters.destination = this.destination;
    if (this.priceMin)     filters.price_min   = +this.priceMin;
    if (this.priceMax)     filters.price_max   = +this.priceMax;

    this.offerService.getOffers(tenantId, filters).subscribe({
      next: (res) => {
        this.offers      = res.offers;
        this.agencyName  = res.agency_name;
        this.totalOffers = res.total;
        this.isLoading   = false;
      },
      error: (err) => {
        this.error     = 'Erreur lors du chargement des offres.';
        this.isLoading = false;
        console.error(err);
      }
    });
  }

  onFilterChange(): void { this.loadOffers(); }
onDestinationInput(event: Event): void {
  const val = (event.target as HTMLInputElement).value.trim();
  clearTimeout(this.destinationTimeout);

  if (!val) {
    this.destinationSuggestions = [];
    this.showDestinationSug = false;
    return;
  }

  this.destinationTimeout = setTimeout(() => {
    fetch(`https://photon.komoot.io/api/?q=${encodeURIComponent(val)}&limit=6&layer=city`)
      .then(r => r.json())
      .then(data => {
        this.destinationSuggestions = [...new Set(
          data.features
            ?.filter((f: any) =>
              f.properties.type === 'city' ||
              f.properties.type === 'town'
            )
            ?.map((f: any) => f.properties.name)
        )] as string[];
        this.showDestinationSug = this.destinationSuggestions.length > 0;
      })
      .catch(() => this.destinationSuggestions = []);
  }, 300);
}

selectDestination(city: string): void {
  this.destination = city;
  this.showDestinationSug = false;
  this.destinationSuggestions = [];
  this.onFilterChange();
}

hideDestinationSug(): void {
  setTimeout(() => this.showDestinationSug = false, 150);
}
  onTypeSelect(type: string): void {
    this.selectedType = type;
    this.loadOffers();
  }

  onViewDetail(offer: OfferItem): void { this.selectedOffer = offer; }
  closeDetail(): void { this.selectedOffer = null; }

  async onBook(offer: OfferItem): Promise<void> {
    if (!this.userService.isLoggedIn()) {
      this.router.navigate(['/login'], {
        queryParams: { tenant: this.userService.resolveTenantId(), returnUrl: '/search' }
      });
      return;
    }

    const state = this.bookingStates.get(offer.id);
    if (state === 'loading' || state === 'booked' || state === 'already_booked') return;

    this._setState(offer.id, 'loading');

    try {
      const result = await this.bookingService.bookOffer(offer);
      this._setState(offer.id, result
        ? (result.alreadyExisted ? 'already_booked' : 'booked')
        : 'error'
      );
    } catch {
      this._setState(offer.id, 'error');
    }
  }

  private _setState(offerId: string, state: 'loading' | 'booked' | 'already_booked' | 'error'): void {
    this.bookingStates = new Map(this.bookingStates).set(offerId, state);
    this.lastOfferId   = offerId;
  }
}