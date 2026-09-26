import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, ActivatedRoute } from '@angular/router';
import { FlightResult, ResultService, SegmentResult } from '../../services/result.service';
import { UserService } from '../../services/user.service';
import { Subscription } from 'rxjs';
import { BookingService } from '../../services/booking.service';
import { PriceService } from '../../services/price.service';

@Component({
  selector: 'app-flight-details',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './flight-details.component.html',
  styleUrls: ['./flight-details.component.scss']
})
export class FlightDetailsComponent implements OnInit, OnDestroy {
  flight: FlightResult | null = null;   
  selectedFare: any = null;
  defaultCurrency: string = 'EUR';
  private sub!: Subscription;
  public showConfirmation = false;
  showToast = false;
  toastMessage = '';


  constructor(
    private router: Router,
    private route: ActivatedRoute,
    private resultService: ResultService,
    private userService: UserService,
    private bookingService: BookingService,
    public priceService: PriceService
  ) {}

  ngOnInit() {
    const config = this.userService.getUserDetails();
    this.defaultCurrency = config?.currency || 'EUR';

    const navigation = this.router.getCurrentNavigation();
    const state = navigation?.extras.state as { flight: FlightResult };

    if (state?.flight) {
      this.flight = this.normalizeFlightData(state.flight);
      return;
    }

    const flightId = this.route.snapshot.paramMap.get('id');
    if (!flightId) { this.router.navigate(['/results']); return; }

    const segments = this.resultService.getSegments();
    for (const seg of segments) {
      if (seg.result_type === 'flight_search_results' && seg.results?.length) {
        const found = seg.results.find((f: any) => f.id === flightId);
        if (found) {
          this.flight = this.normalizeFlightData(found);
          return;
        }
      }
    }

    this.sub = this.resultService.results$.subscribe(results => {
      if (!results) return;

      if (results.type === 'flight_search_results') {
        const found = (results as any).results?.find((f: FlightResult) => f.id === flightId);
        if (found) {
          this.flight = this.normalizeFlightData(found);
          return;
        }
      }

      if (flightId === 'demo') {
        this.flight = this.getDemoFlight();
        return;
      }

      this.router.navigate(['/results']);
    });

    if (flightId === 'demo') {
      this.flight = this.getDemoFlight();
    }
  }

  ngOnDestroy() {
    this.sub?.unsubscribe();
  }

  normalizeFlightData(flight: any): FlightResult {
    if (!flight.fare_options?.length) {
      const price = flight.price || flight.price_converted || 0;
      flight.fare_options = [{
        name: 'Standard',
        price,
        currency: flight.price?.currency || this.defaultCurrency,
        features: ['Bagage cabine inclus', 'Modification possible'],
        selected: false,
        recommended: true
      }];
    }
    if (!Array.isArray(flight.amenities)) flight.amenities = [];
    return flight;
  }


getBestPrice(): number {
  if (!this.flight) return 0;
  const fareOption = this.flight.fare_options?.[0];
  return this.priceService.getFlightPrices(this.flight, fareOption).base.amount;
}

 formatCurrency(code: string): string {
  return this.priceService.format(1, code).replace('1', '').trim();
}

getDefaultCurrencySymbol(): string {
  return this.formatCurrency(this.defaultCurrency);
}

  getAmenityIcon(icon: string): string {
    const icons: Record<string, string> = {
      luggage: '🧳', suitcase: '💼', wifi: '📶',
      meal: '🍽️', drink: '🥤', entertainment: '📺'
    };
    return icons[icon] || icon || '✓';
  }

  formatDate(date: string): string {
    if (!date) return '';
    return new Date(date).toLocaleDateString('fr-FR', {
      day: 'numeric', month: 'long', year: 'numeric'
    });
  }

  getAirlineInitial(): string {
    return this.flight?.airline?.charAt(0).toUpperCase() ?? '✈';
  }

  getStopsText(stops: number, stopDetails: any): string {
    if (stops === 0) return 'Vol direct';
    if (stopDetails) {
      if (typeof stopDetails === 'string') return stopDetails;
      if (typeof stopDetails === 'object') {
        const city = stopDetails.city || stopDetails.airport || '';
        const duration = stopDetails.duration ? ` (${stopDetails.duration})` : '';
        return `${stops} escale${stops > 1 ? 's' : ''} · ${city}${duration}`;
      }
    }
    return `${stops} escale${stops > 1 ? 's' : ''}`;
  }

  getStopDetailsText(stopDetails: any): string {
    if (!stopDetails) return '';
    if (typeof stopDetails === 'string') return stopDetails;
    if (typeof stopDetails === 'object') {
      const parts = [];
      if (stopDetails.city) parts.push(`Escale à ${stopDetails.city}`);
      else if (stopDetails.airport) parts.push(`Escale à ${stopDetails.airport}`);
      if (stopDetails.duration) parts.push(stopDetails.duration);
      return parts.join(' · ');
    }
    return '';
  }

  onSelectFare(fare: any) {
    this.selectedFare = fare;
    this.router.navigate(['/booking'], { state: { flight: this.flight, fare } });
  }

  goBack() { this.router.navigate(['/results']); }

async onBook(flight: FlightResult, fareIndex: number = 0): Promise<void> {
  if (this.showConfirmation) {
    this.toastMessage = '⚠️ Ce vol est déjà réservé !';
  } else {
    const bookingId = await this.bookingService.bookFlight(flight, fareIndex);
    if (bookingId) {
      this.showConfirmation = true;
      this.toastMessage = '✅ Vol réservé avec succès !';
    }
  }
  this.showToast = true;
  setTimeout(() => this.showToast = false, 3000);
}
  getDemoFlight(): FlightResult {
    return {
      id: 'demo', type: 'flight',
      airline: 'Air France', airline_logo: '',
      flight_number: 'AF 256', stops: 1, stop_details: 'CDG',
      duration: '16h 15m',
      departure: { airport: 'CDG', city: 'Paris', time: '08:30', date: '2026-04-22', cabinClass: 'ECONOMY' },
      arrival:   { airport: 'DPS', city: 'Bali',  time: '06:45', date: '2026-04-23', cabinClass: 'ECONOMY' },
      amenities: [
        { icon: 'luggage',  name: 'Bagage cabine', value: '10kg' },
        { icon: 'suitcase', name: 'Bagage soute',  value: '23kg' },
        { icon: 'wifi',     name: 'Wifi à bord',   value: 'Inclus' },
        { icon: 'meal',     name: 'Repas inclus',  value: 'Menu complet' }
      ],
      fare_options: [
        { name: 'Économique', price: 890,  currency: 'EUR', features: ['Siège standard', '1 bagage cabine'], selected: false, recommended: false },
        { name: 'Flex',       price: 1040, currency: 'EUR', features: ['Siège standard', 'Modification gratuite', '2 bagages'], selected: false, recommended: true },
        { name: 'Business',   price: 1390, currency: 'EUR', features: ['Siège business', 'Lounge accès', 'Priorité embarquement'], selected: false, recommended: false }
      ]
    };
  }
}