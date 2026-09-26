import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { environment } from '../environments/environment';
import { FlightResult, HotelResult, ActivityResult, RestaurantResult } from './result.service';
import { UserService } from './user.service';
import { AgentService } from './agent.service';
import { ActivityDetails } from './activity.service';
import { OfferItem } from './offer.service';

export interface BookingResult {
  bookingId:     string;
  alreadyExisted: boolean;
}

@Injectable({ providedIn: 'root' })
export class BookingService {

  private _pendingKeys = new Set<string>();
  private _bookedKeys  = new Set<string>();

  constructor(
    private http:         HttpClient,
    private userService:  UserService,
    private agentService: AgentService,
    private router:       Router, 
  ) {}

  private _checkNotVisitor(): boolean {
    const user = this.userService.session();
    if (user?.role === 'visitor') {
      this.router.navigate(['/login']);
      return false;
    }
    return true;
  }

  private _dedupKey(payload: any): string {
    switch (payload.booking_type) {
      case 'flight':
        return `flight:${payload.user_id}:${payload.flight_number}:${payload.departure_date}`;
      case 'hotel':
        return `hotel:${payload.user_id}:${payload.hotel_name}:${payload.check_in}`;
      case 'tour':
        return `tour:${payload.user_id}:${payload.activity_name}:${payload.activity_date}`;
      case 'restaurant':
        return `restaurant:${payload.user_id}:${payload.activity_name}`;
      default:
        return `${payload.booking_type}:${payload.user_id}:${Date.now()}`;
    }
  }

  private async _send(payload: any): Promise<BookingResult | null> {
    const key = this._dedupKey(payload);
    if (this._bookedKeys.has(key)) {
      console.warn(`[BookingService] ⚠ Déjà réservé (session) | key=${key}`);
      return null;
    }
    if (this._pendingKeys.has(key)) {
      console.warn(`[BookingService] ⚠ Requête en cours | key=${key}`);
      return null;
    }
    this._pendingKeys.add(key);
    try {
      const res: any = await this.http
        .post(`${environment.apiUrl}bookings/create`, payload)
        .toPromise();
      this._bookedKeys.add(key);
      return {
        bookingId:      res.booking_id,
        alreadyExisted: res.status === 'already_exists',
      };
    } catch (err) {
      console.error('[BookingService] ❌ Erreur:', err);
      return null;
    } finally {
      this._pendingKeys.delete(key);
    }
  }

  async bookFlight(flight: FlightResult, fareIndex = 0): Promise<string | null> {
    const user = this.userService.session();
    if (!user) return null;
    if (!this._checkNotVisitor()) return null; 

    const fare = flight.fare_options?.[fareIndex];
    const payload = {
      tenant_id:      user.tenant_id,
      user_id:        user.user_id,
      session_id:     this.agentService.getSessionId(),
      booking_type:   'flight',
      origin:         flight.departure?.city    || flight.departure?.airport,
      destination:    flight.arrival?.city      || flight.arrival?.airport,
      departure_date: flight.departure?.date,
      return_date:    flight.arrival?.date      || null,
      adults:         1,
      cabin_class:    flight.departure?.cabinClass || flight.cabin_class || 'ECONOMY',
      airline:        flight.airline,
      flight_number:  flight.flight_number,
      price:          fare?.price    || flight.price?.min,
      currency:       fare?.currency || flight.currency || 'EUR',
      raw_data:       flight,
    };
    return (await this._send(payload))?.bookingId ?? null;
  }

  async bookHotel(hotel: HotelResult, query?: any): Promise<string | null> {
    const user = this.userService.session();
    if (!user) return null;
    if (!this._checkNotVisitor()) return null; 

    const bestOffer = hotel.best_offer || hotel.offers?.[0];
    const q        = query || hotel._query || null;
    const payload  = {
      tenant_id:    user.tenant_id,
      user_id:      user.user_id,
      session_id:   this.agentService.getSessionId(),
      booking_type: 'hotel',
      city:         hotel.location?.city || hotel.city,
      destination:  hotel.location?.city || hotel.city,
      check_in:     q?.check_in   || q?.arrival_date   || null,
      check_out:    q?.check_out  || q?.departure_date  || null,
      guests:       q?.guests     || q?.adults          || 1,
      hotel_name:   hotel.name,
      price:        bestOffer?.price || hotel.price_per_night,
      currency:     hotel.currency   || 'EUR',
      raw_data:     hotel,
    };
    return (await this._send(payload))?.bookingId ?? null;
  }

  async bookActivity(activity: ActivityResult | ActivityDetails): Promise<string | null> {
    const user = this.userService.session();
    if (!user) return null;
    if (!this._checkNotVisitor()) return null; 

    const isActivityResult = 'city' in activity;
    const payload = {
      tenant_id:     user.tenant_id,
      user_id:       user.user_id,
      session_id:    this.agentService.getSessionId(),
      booking_type:  'tour',
      activity_name: activity.name,
      activity_date: isActivityResult
        ? (activity as ActivityResult).date
        : (activity as ActivityDetails).dates?.start?.localDate || null,
      venue: isActivityResult
        ? (activity as ActivityResult).venue
        : (activity as ActivityDetails).venue?.name || null,
      activity_city: isActivityResult
        ? (activity as ActivityResult).city
        : (activity as ActivityDetails).venue?.city || null,
      destination: isActivityResult
        ? (activity as ActivityResult).city
        : (activity as ActivityDetails).venue?.city || null,
      price:    (activity as ActivityDetails).priceRanges?.[0]?.min      || null,
      currency: (activity as ActivityDetails).priceRanges?.[0]?.currency || 'EUR',
      raw_data: activity,
    };
    return (await this._send(payload))?.bookingId ?? null;
  }

  async bookRestaurant(restaurant: RestaurantResult): Promise<string | null> {
    const user = this.userService.session();
    if (!user) return null;
    if (!this._checkNotVisitor()) return null; 

    const payload = {
      tenant_id:     user.tenant_id,
      user_id:       user.user_id,
      session_id:    this.agentService.getSessionId(),
      booking_type:  'restaurant',
      activity_name: restaurant.name,
      activity_city: restaurant.location?.locality || restaurant.location?.region || null,
      destination:   restaurant.location?.locality || restaurant.location?.region || null,
      venue:         restaurant.location?.formatted_address || restaurant.location?.address || null,
      price:         restaurant.price || restaurant.price_range?.min || null,
      currency:      restaurant.currency || 'EUR',
      raw_data:      restaurant,
    };
    return (await this._send(payload))?.bookingId ?? null;
  }

  async bookOffer(offer: OfferItem): Promise<BookingResult | null> {
    const user = this.userService.session();
    if (!user) return null;
    if (!this._checkNotVisitor()) return null; 

    const bookingType =
      offer.type === 'flight' ? 'flight' :
      offer.type === 'hotel'  ? 'hotel'  : 'tour';

    const payload: any = {
      tenant_id:    user.tenant_id,
      user_id:      user.user_id,
      session_id:   this.agentService.getSessionId(),
      booking_type: bookingType,
      destination:  offer.destination,
      price:        offer.price,
      currency:     offer.currency ?? 'EUR',
      raw_data:     offer,
    };

    switch (bookingType) {
      case 'flight':
        payload.origin         = offer.origin        ?? null;
        payload.departure_date = offer.departure_date ?? null;
        payload.cabin_class    = 'ECONOMY';
        payload.adults         = 1;
        break;
      case 'hotel':
        payload.hotel_name = offer.title;
        payload.city       = offer.destination;
        payload.check_in   = offer.departure_date ?? null;
        break;
      case 'tour':
        payload.activity_name = offer.title;
        payload.activity_city = offer.destination;
        payload.activity_date = offer.departure_date ?? null;
        break;
    }

    return this._send(payload);
  }
}