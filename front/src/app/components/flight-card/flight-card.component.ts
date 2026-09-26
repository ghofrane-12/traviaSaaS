import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { FlightResult } from '../../services/result.service';
import { UserService } from '../../services/user.service';
@Component({
  selector: 'app-flight-card',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './flight-card.component.html',
  styleUrls: ['./flight-card.component.scss']
})
export class FlightCardComponent {
  @Input() flight!: FlightResult;

  constructor(private router: Router,private userService: UserService) {}

  get defaultCurrencySymbol(): string {
    const c = this.userService.getUserDetails()?.currency || 'EUR';
    return c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c;
  }
   getCabinClassDisplay(): string {
    if (this.flight.cabin_class) {
      return this.formatCabinClass(this.flight.cabin_class);
    }
    
    const depClass = this.flight.departure?.cabinClass;
    const arrClass = this.flight.arrival?.cabinClass;
    
    if (depClass && arrClass && depClass !== arrClass) {
      return `${this.formatCabinClass(depClass)} → ${this.formatCabinClass(arrClass)}`;
    }
    
    if (depClass) {
      return this.formatCabinClass(depClass);
    }
    
    if (arrClass) {
      return this.formatCabinClass(arrClass);
    }
    
    return 'Économique';
  }

  formatCabinClass(cabinClass: string): string {
    const classes: Record<string, string> = {
      'ECONOMY': 'Économique',
      'PREMIUM_ECONOMY': 'Premium Économique',
      'BUSINESS': 'Affaires',
      'FIRST': 'Première classe'
    };
    return classes[cabinClass] || cabinClass;
  }

  getCabinClassColor(): string {
    const cabinClass = this.flight.cabin_class || this.flight.departure?.cabinClass || 'ECONOMY';
    const colors: Record<string, string> = {
      'ECONOMY': '#4caf50',
      'PREMIUM_ECONOMY': '#2196f3',
      'BUSINESS': '#ff9800',
      'FIRST': '#9c27b0'
    };
    return colors[cabinClass] || '#607d8b';
  }
  getCurrency(): string {
    const raw = this.flight.fare_options?.[0]?.currency
            ?? this.flight.price?.currency
            ?? this.userService.getUserDetails()?.currency
            ?? 'EUR';
    return raw === 'EUR' ? '€' : raw === 'USD' ? '$' : raw === 'GBP' ? '£' : raw;
  }

  getBestPrice(): number {
    if (this.flight.fare_options && this.flight.fare_options.length > 0) {
      return Math.min(...this.flight.fare_options.map(f => f.price));
    }
    if (this.flight.price_per_night) return this.flight.price_per_night;
    if (this.flight.total_price) return this.flight.total_price;
    return 0;
  }
  getAirlineFirstChar(): string {
    return this.flight?.airline?.charAt(0) || '✈';
  }

  getDepartureTime(): string {
    return this.flight?.departure?.time || '--:--';
  }

  getDepartureAirport(): string {
    return this.flight?.departure?.airport || 'N/A';
  }

  getDepartureCity(): string {
    return this.flight?.departure?.city || '';
  }

  getArrivalTime(): string {
    return this.flight?.arrival?.time || '--:--';
  }

  getArrivalAirport(): string {
    return this.flight?.arrival?.airport || 'N/A';
  }

  getArrivalCity(): string {
    return this.flight?.arrival?.city || '';
  }
  onSelectFlight() {
    console.log('[FlightCard] Navigation vers /flight/', this.flight.id);
    console.log('[FlightCard] Données du vol:', this.flight);
    
    if (!this.flight.id) {
      console.error('[FlightCard] Pas d\'ID de vol!');
      return;
    }
    
    this.router.navigate(['/flight', this.flight.id], { 
      state: { flight: this.flight }
    });
  }

  getAmenityIcon(icon: string): string {
    const icons: Record<string, string> = {
      luggage: '🧳', suitcase: '💼', wifi: '📶',
      meal: '🍽️', drink: '🥤', entertainment: '📺'
    };
    return icons[icon] || '✓';
  }
}