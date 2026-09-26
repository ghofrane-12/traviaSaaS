import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../environments/environment';
import { UserService } from './user.service';

export interface OfferItem {
  id:              string;
  type:            'flight' | 'hotel' | 'omra' | 'circuit';
  title:           string;
  destination:     string;
  origin?:         string;
  price:           number;
  currency:        string;
  departure_date?: string;
  duration_days?:  number;
  image_url?:      string;
  description?:    string;
  is_available:    boolean;
  raw?:            any;
}

export interface OffersResponse {
  tenant_id:   string;
  agency_name: string;
  total:       number;
  offers:      OfferItem[];
}

export interface OfferFilters {
  offer_type?:  string;
  destination?: string;
  price_min?:   number;
  price_max?:   number;
}

export interface OfferMappingCreate {
  offer_type:     string;
  field_map:      Record<string, string>;
  default_values: Record<string, any>;
}

export interface OfferMappingResponse {
  id:             string;
  tenant_id:      string;
  offer_type:     string;
  field_map:      Record<string, string>;
  default_values: Record<string, any>;
  created_at:     string;
  updated_at:     string;
}

const OFFER_TYPE_LABELS: Record<string, string> = {
  flight:  'Vol',
  hotel:   'Hôtel',
  omra:    'Omra',
  circuit: 'Circuit'
};

const OFFER_TYPE_ICONS: Record<string, string> = {
  flight:  'fa-plane',
  hotel:   'fa-hotel',
  omra:    'fa-kaaba',
  circuit: 'fa-map-marked-alt'
};

@Injectable({ providedIn: 'root' })
export class OfferService {

  private apiUrl = environment.apiUrl;

  constructor(
    private http: HttpClient,
    private userService: UserService
  ) {}


  getOffers(tenantId: string, filters: OfferFilters = {}): Observable<OffersResponse> {
    let params = new HttpParams().set('tenant_id', tenantId);

    if (filters.offer_type)  params = params.set('offer_type',  filters.offer_type);
    if (filters.destination) params = params.set('destination', filters.destination);
    if (filters.price_min)   params = params.set('price_min',   filters.price_min.toString());
    if (filters.price_max)   params = params.set('price_max',   filters.price_max.toString());

    return this.http.get<OffersResponse>(`${this.apiUrl}offers/`, { params });
  }


  getMappings(): Observable<OfferMappingResponse[]> {
    return this.http.get<OfferMappingResponse[]>(`${this.apiUrl}offers/mappings`);
  }

  getMapping(offerType: string): Observable<OfferMappingResponse> {
    return this.http.get<OfferMappingResponse>(`${this.apiUrl}offers/mapping/${offerType}`);
  }

  saveMapping(payload: OfferMappingCreate): Observable<OfferMappingResponse> {
    return this.http.post<OfferMappingResponse>(`${this.apiUrl}offers/mapping`, payload);
  }


  getTypeLabel(type: string): string {
    return OFFER_TYPE_LABELS[type] ?? type;
  }

  getTypeIcon(type: string): string {
    return OFFER_TYPE_ICONS[type] ?? 'fa-tag';
  }

  formatPrice(price: number, currency: string): string {
    return new Intl.NumberFormat('fr-FR', {
      style:    'currency',
      currency: currency ?? 'EUR'
    }).format(price);
  }

  formatDate(dateStr: string): string {
    if (!dateStr) return '';
    return new Date(dateStr).toLocaleDateString('fr-FR', {
      day:   '2-digit',
      month: 'long',
      year:  'numeric'
    });
  }
}